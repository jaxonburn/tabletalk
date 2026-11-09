import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tabletalk_matcher import main as matcher_main
from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
from tabletalk_matcher.llm_notes import (
    PROMPT_VERSION,
    NarrativeError,
    narrate_plan,
    parse_notes,
    table_payload,
)
from tabletalk_matcher.matching import build_plan
from tabletalk_shared.fixtures import load_fixture
from tabletalk_shared.llm import (
    AnthropicClient,
    LLMConfig,
    LLMError,
    OpenAIClient,
    create_llm_client,
)
from tabletalk_shared.models import LunchGroup, MatchPlan

SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def config(provider: str, **overrides: Any) -> LLMConfig:
    values: dict[str, Any] = {
        "provider": provider,
        "model": "test-model",
        "api_key": "secret-key",
        "base_url": "https://llm.example",
    }
    values.update(overrides)
    return LLMConfig(**values)


def mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def ask(client: AnthropicClient | OpenAIClient) -> dict[str, Any]:
    return await client.generate_json(
        system="sys", user="user", schema_name="notes", schema=SCHEMA, max_tokens=50
    )


class TestConfig:
    def test_defaults_to_no_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        assert not LLMConfig.from_env().enabled

    def test_requires_model_and_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.delenv("LLM_MODEL", raising=False)
        assert LLMConfig.from_env().problem == "Set LLM_MODEL to the provider's model ID."
        monkeypatch.setenv("LLM_MODEL", "claude-test")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert LLMConfig.from_env().problem == "Set ANTHROPIC_API_KEY."
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        loaded = LLMConfig.from_env()
        assert loaded.enabled and loaded.base_url == "https://api.anthropic.com"

    def test_rejects_unknown_providers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "mystery")
        loaded = LLMConfig.from_env()
        assert not loaded.enabled and loaded.problem is not None

    async def test_no_client_without_a_working_config(self) -> None:
        async with httpx.AsyncClient() as http:
            assert create_llm_client(LLMConfig(), http) is None
            assert create_llm_client(config("openai", problem="Set LLM_MODEL"), http) is None


class TestAnthropic:
    async def test_forces_a_tool_call_and_returns_its_input(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={
                    "content": [
                        {"type": "text", "text": "Sure!"},
                        {"type": "tool_use", "name": "notes", "input": {"ok": True}},
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            )

        async with mock_http(handler) as http:
            result = await ask(AnthropicClient(config("anthropic"), http))
        assert result == {"ok": True}
        request = seen[0]
        assert str(request.url) == "https://llm.example/v1/messages"
        assert request.headers["x-api-key"] == "secret-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        body = json.loads(request.content)
        assert body["tool_choice"] == {"type": "tool", "name": "notes"}
        assert body["tools"][0]["input_schema"] == SCHEMA
        assert body["system"] == "sys"

    async def test_missing_tool_call_is_an_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]})

        async with mock_http(handler) as http:
            with pytest.raises(LLMError, match="requested structure"):
                await ask(AnthropicClient(config("anthropic"), http))

    async def test_http_errors_do_not_leak_the_key(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad key secret-key"})

        async with mock_http(handler) as http:
            with pytest.raises(LLMError) as raised:
                await ask(AnthropicClient(config("anthropic"), http))
        assert "secret-key" not in str(raised.value)
        assert "401" in str(raised.value)


class TestOpenAI:
    async def test_uses_a_strict_json_schema_and_parses_content(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps({"ok": 1})}}]}
            )

        async with mock_http(handler) as http:
            assert await ask(OpenAIClient(config("openai"), http)) == {"ok": 1}
        request = seen[0]
        assert str(request.url) == "https://llm.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-key"
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"] == {
            "name": "notes",
            "schema": SCHEMA,
            "strict": True,
        }
        assert body["messages"][0] == {"role": "system", "content": "sys"}

    @pytest.mark.parametrize(
        "message",
        [{"refusal": "no"}, {"content": "not json"}, {"content": "[1, 2]"}],
    )
    async def test_refusals_and_bad_json_are_errors(self, message: dict[str, str]) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": message}]})

        async with mock_http(handler) as http:
            with pytest.raises(LLMError):
                await ask(OpenAIClient(config("openai"), http))

    @pytest.mark.parametrize("body", [b"<html>proxy login</html>", b"[1, 2]"])
    async def test_non_object_bodies_are_errors(self, body: bytes) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=body)

        async with mock_http(handler) as http:
            with pytest.raises(LLMError, match="response"):
                await ask(OpenAIClient(config("openai"), http))

    async def test_timeouts_are_errors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        async with mock_http(handler) as http:
            with pytest.raises(LLMError, match="timed out"):
                await ask(OpenAIClient(config("openai"), http))


def fixture_plan() -> MatchPlan:
    fixture = load_fixture()
    attendees = [entry.attendee for entry in fixture.attendees]
    affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
    return build_plan(attendees, fixture.tables, affinity)


def good_notes(group: LunchGroup) -> dict[str, Any]:
    names = [member.name.split()[0] for member in group.members]
    return {
        "table_name": "The Builders' table",
        "tagline": ["Small bets.", "Big ideas."],
        "table_summary": f"{names[0]} and {names[1]} can trade ideas.",
        "conversation_starters": ["What surprised you?", "What would you redo?", "Best tool?"],
        "members": [
            {
                "attendee_id": member.id,
                "summary": f"You and {names[0 if index else 1]} overlap.",
                "starters": [f"Ask {names[0 if index else 1]} about it.", "Swap one tip."],
            }
            for index, member in enumerate(group.members)
        ],
    }


class FakeLLM:
    provider = "fake"
    model = "fake-1"

    def __init__(self, responses: list[Any] | None = None, delay: float = 0.0) -> None:
        self.responses = responses
        self.delay = delay
        self.calls: list[str] = []

    async def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs["user"])
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            assert isinstance(response, dict)
            return response
        payload = json.loads(kwargs["user"].split("\n\n", 1)[1])
        ids = [member["attendee_id"] for member in payload["members"]]
        group = next(g for g in fixture_plan().groups if [m.id for m in g.members] == ids)
        return good_notes(group)


class TestParseNotes:
    def test_accepts_notes_that_fit_the_table(self) -> None:
        group = fixture_plan().groups[0]
        notes = parse_notes(good_notes(group), group, outsider_names=set())
        assert [note.attendee_id for note in notes.member_notes] == [m.id for m in group.members]
        assert len(notes.starters) == 3

    def test_rejects_missing_or_extra_people(self) -> None:
        group = fixture_plan().groups[0]
        data = good_notes(group)
        data["members"] = data["members"][1:]
        with pytest.raises(NarrativeError, match="exactly the people"):
            parse_notes(data, group, set())
        data = good_notes(group)
        data["members"].append({**data["members"][0], "attendee_id": "someone-else"})
        with pytest.raises(NarrativeError):
            parse_notes(data, group, set())

    def test_rejects_mentions_of_people_at_other_tables(self) -> None:
        group = fixture_plan().groups[0]
        data = good_notes(group)
        data["conversation_starters"][0] = "Ask Zelda from table 3 about it."
        with pytest.raises(NarrativeError, match="isn't at this table"):
            parse_notes(data, group, {"Zelda"})

    def test_ordinary_words_are_not_mistaken_for_names(self) -> None:
        group = fixture_plan().groups[0]
        data = good_notes(group)
        data["conversation_starters"][0] = "How do you handle outages with grace? Will it scale?"
        notes = parse_notes(data, group, {"Grace", "Willa"})
        assert notes.starters[0].startswith("How do you handle")

    def test_clips_overlong_text_instead_of_failing(self) -> None:
        group = fixture_plan().groups[0]
        data = good_notes(group)
        data["table_summary"] = "word " * 200
        notes = parse_notes(data, group, set())
        assert len(notes.summary) <= 300 and notes.summary.endswith("…")

    def test_uses_the_models_table_name_and_tagline(self) -> None:
        group = fixture_plan().groups[0]
        notes = parse_notes(good_notes(group), group, set())
        assert notes.name == "The Builders' table"
        assert notes.tagline == ("Small bets.", "Big ideas.")

    def test_weak_names_and_taglines_fall_back_to_the_template(self) -> None:
        group = fixture_plan().groups[0]
        data = good_notes(group) | {"table_name": "x" * 80, "tagline": ["Only one line."]}
        notes = parse_notes(data, group, set())
        assert notes.name == group.name and notes.name
        assert list(notes.tagline) == group.tagline and len(notes.tagline) == 2

    def test_table_name_cannot_mention_other_tables_people(self) -> None:
        group = fixture_plan().groups[0]
        data = good_notes(group) | {"table_name": "Zelda's table"}
        with pytest.raises(NarrativeError, match="isn't at this table"):
            parse_notes(data, group, {"Zelda"})

    def test_payload_only_contains_this_tables_people(self) -> None:
        plan = fixture_plan()
        payload = table_payload(plan.groups[0])
        assert all(member.id in payload for member in plan.groups[0].members)
        assert not any(member.id in payload for member in plan.groups[1].members)
        assert "pair_reasons" in payload


class TestNarratePlan:
    async def test_llm_notes_replace_templates(self) -> None:
        plan = await narrate_plan(fixture_plan(), FakeLLM())
        assert {group.narrative_source for group in plan.groups} == {"llm"}
        assert plan.narrative_provider == "fake:fake-1"
        assert plan.prompt_version == PROMPT_VERSION

    async def test_ai_names_never_duplicate_another_table(self) -> None:
        plan = await narrate_plan(fixture_plan(), FakeLLM())
        names = [group.name for group in plan.groups]
        assert names[0] == "The Builders' table"
        assert len(set(names)) == len(names)

    async def test_invalid_output_is_retried_once_then_falls_back(self) -> None:
        base = fixture_plan()
        bad = {"table_summary": "x", "conversation_starters": [], "members": []}
        client = FakeLLM(responses=[bad, LLMError("boom")] + [None] * 0)
        plan = await narrate_plan(base.model_copy(update={"groups": base.groups[:1]}), client)
        assert len(client.calls) == 2
        assert plan.groups[0].narrative_source == "template"
        assert plan.groups[0].summary == base.groups[0].summary

    async def test_a_second_attempt_can_succeed(self) -> None:
        base = fixture_plan()
        one_table = base.model_copy(update={"groups": base.groups[:1]})
        client = FakeLLM(responses=[LLMError("flaky"), good_notes(base.groups[0])])
        plan = await narrate_plan(one_table, client)
        assert plan.groups[0].narrative_source == "llm"

    async def test_slow_providers_hit_the_deadline_and_keep_templates(self) -> None:
        plan = await narrate_plan(fixture_plan(), FakeLLM(delay=1), deadline_seconds=0.05)
        assert {group.narrative_source for group in plan.groups} == {"template"}

    async def test_a_plan_is_only_labelled_ai_when_ai_text_is_shown(self) -> None:
        failing = FakeLLM(responses=[LLMError("revoked key")] * 20)
        plan = await narrate_plan(fixture_plan(), failing)
        assert {group.narrative_source for group in plan.groups} == {"template"}
        assert plan.narrative_provider is None and plan.prompt_version is None


def test_matcher_runs_use_the_llm_only_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock, Mock

    from elasticsearch import AsyncElasticsearch

    attendees = [entry.attendee for entry in load_fixture().attendees[:6]]
    lexical = lexical_directed_scores(attendees)

    async def search(**kwargs: Any) -> dict[str, Any]:
        me = kwargs["query"]["bool"]["must_not"][0]["term"]["id"]
        return {"hits": {"hits": [{"_id": k, "_score": v} for k, v in lexical[me].items()]}}

    client = Mock(spec=AsyncElasticsearch)
    client.search = AsyncMock(side_effect=search)
    monkeypatch.setattr(matcher_main, "list_attendees", AsyncMock(return_value=attendees))

    class TableLLM(FakeLLM):
        async def generate_json(self, **kwargs: Any) -> dict[str, Any]:
            payload = json.loads(kwargs["user"].split("\n\n", 1)[1])
            ids = [member["attendee_id"] for member in payload["members"]]
            group = LunchGroup(id="t", members=[a for a in attendees if a.id in ids], reasons=[])
            return good_notes(group)

    matcher_main.app.dependency_overrides[matcher_main.get_client] = lambda: client
    matcher_main.app.state.llm = TableLLM()
    request = {
        "tables": [{"number": 1, "seats": 3}, {"number": 2, "seats": 3}],
        "use_semantic": False,
    }
    try:
        with_llm = TestClient(matcher_main.app).post("/runs", json=request)
        without = TestClient(matcher_main.app).post("/runs", json=request | {"use_llm": False})
    finally:
        matcher_main.app.dependency_overrides.clear()
        matcher_main.app.state.llm = None
    assert {g["narrative_source"] for g in with_llm.json()["groups"]} == {"llm"}
    assert with_llm.json()["narrative_provider"] == "fake:fake-1"
    assert {g["narrative_source"] for g in without.json()["groups"]} == {"template"}
