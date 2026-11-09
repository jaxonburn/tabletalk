from collections import Counter
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from tabletalk_api import main
from tabletalk_matcher.affinity import learn_text, share_text, shared_phrases
from tabletalk_matcher.evaluation import adjusted_rand_index, evaluate, theme_purity
from tabletalk_shared.fixtures import load_fixture, parse_fixture
from tabletalk_shared.models import Enrollment, TableSpec
from tabletalk_shared.personas import (
    THEMES_BY_KEY,
    dumps_fixture,
    fixture_document,
    generate_offline,
    generate_with_llm,
    persona_from_llm,
)

Api = tuple[TestClient, main.Services]


class TestOfflineGenerator:
    def test_is_deterministic_and_unique(self) -> None:
        first = generate_offline(30, seed=4)
        assert first == generate_offline(30, seed=4)
        assert first != generate_offline(30, seed=5)
        assert len({person.attendee.id for person in first}) == 30
        assert len({person.attendee.name for person in first}) == 30

    def test_people_are_valid_enrollments(self) -> None:
        for person in generate_offline(40, seed=1):
            data = person.attendee.model_dump(exclude={"id", "conference_id", "topic_tags"})
            Enrollment.model_validate(data)
            assert set(person.attendee.goal_answers) == set(person.attendee.goals)

    def test_spreads_themes_and_plants_some_bridges(self) -> None:
        counts = Counter(person.theme for person in generate_offline(120, seed=2))
        assert set(counts) == {*THEMES_BY_KEY, "bridge"}
        assert 10 <= counts["bridge"] <= 40

    def test_respects_requested_themes(self) -> None:
        people = generate_offline(12, seed=3, themes=["security", "frontend"])
        assert {person.theme for person in people} <= {"security", "frontend", "bridge"}

    def test_someone_can_answer_most_learn_questions(self) -> None:
        people = [person.attendee for person in generate_offline(60, seed=6)]
        learners = [person for person in people if learn_text(person)]
        answered = [
            learner
            for learner in learners
            if any(
                shared_phrases(share_text(other), learn_text(learner))
                for other in people
                if other is not learner
            )
        ]
        assert len(answered) / len(learners) > 0.8


class TestLlmPersonas:
    def raw(self, **changes: Any) -> dict[str, Any]:
        base = {
            "theme": "security",
            "name": "Ana Silva",
            "role": "Security engineer",
            "interests": ["sbom", "containers"],
            "goals": ["Learn", "Meet connections"],
            "focus_goal": "Learn",
            "learn": "How do you triage CVE alerts?",
            "share": "Unused answer for an unselected goal.",
            "connect": "Happy to talk supply chain security.",
            "can_help": "Image signing",
            "want_to_learn": "CVE triage",
        }
        return base | changes

    def test_keeps_answers_only_for_selected_goals(self) -> None:
        person = persona_from_llm(self.raw(), "llm-1")
        assert person is not None
        assert set(person.attendee.goal_answers) == {"Learn", "Meet connections"}

    @pytest.mark.parametrize(
        "changes",
        [
            {"goals": ["Hire"]},
            {"name": ""},
            {"name": "I’m Priya, platform engineer"},
            {"name": "Priya"},
            {"interests": []},
            {"focus_goal": "Invest"},
        ],
    )
    def test_drops_people_that_fail_enrollment_rules(self, changes: dict[str, Any]) -> None:
        assert persona_from_llm(self.raw(**changes), "llm-1") is None

    async def test_batches_requests_and_removes_duplicates(self) -> None:
        calls: list[dict[str, Any]] = []

        class Client:
            provider = "fake"
            model = "fake-1"

            async def generate_json(self, **kwargs: Any) -> dict[str, Any]:
                calls.append(kwargs)
                index = len(calls)
                return {
                    "attendees": [
                        self_raw | {"name": f"Person {'ABCDEFGH'[index]}{'abcdefgh'[n]}"}
                        for n, self_raw in enumerate([TestLlmPersonas().raw()] * 8)
                    ]
                    + [TestLlmPersonas().raw(name="Person Aa")]
                }

        people = await generate_with_llm(Client(), 20, seed=9)
        assert len(calls) == 3
        assert "security" in calls[0]["user"]
        assert len(people) == 20
        assert len({person.attendee.name for person in people}) == 20
        assert people[0].attendee.id.startswith("llm-")
        again = await generate_with_llm(Client(), 4, seed=9)
        assert not {p.attendee.id for p in again} & {p.attendee.id for p in people}


def test_generated_fixture_files_round_trip() -> None:
    people = generate_offline(11, seed=8)
    document = fixture_document(people, "test", seats_per_table=4)
    assert [table["seats"] for table in document["tables"]] == [4, 4, 4]
    loaded = parse_fixture(dumps_fixture(document))
    assert [entry.attendee for entry in loaded.attendees] == [p.attendee for p in people]
    assert [entry.theme for entry in loaded.attendees] == [p.theme for p in people]


class TestEvaluation:
    def test_adjusted_rand_index(self) -> None:
        truth = ["a", "a", "b", "b", "c", "c"]
        assert adjusted_rand_index(truth, ["1", "1", "2", "2", "3", "3"]) == 1.0
        assert adjusted_rand_index(truth, ["3", "3", "1", "1", "2", "2"]) == 1.0
        assert adjusted_rand_index(truth, ["1", "2", "3", "1", "2", "3"]) < 0

    def test_theme_purity_ignores_bridges(self) -> None:
        themes = {"a": "x", "b": "x", "c": "bridge", "d": "y"}
        assert theme_purity([["a", "b", "c"], ["d"]], themes) == 1.0

    def test_matching_beats_random_seating_on_generated_people(self) -> None:
        tables = [TableSpec(number=n, seats=5) for n in range(1, 7)]
        result = evaluate(generate_offline(30, seed=0), tables, seed=0)
        assert result.average_fit > 1.5 * result.baseline_average_fit
        assert result.theme_ari > result.random_theme_ari + 0.3
        assert result.theme_purity > result.random_theme_purity

    def test_bundled_sample_regroups_its_themes_exactly(self) -> None:
        fixture = load_fixture()
        assert evaluate(list(fixture.attendees), list(fixture.tables)).theme_ari == 1.0


class TestDemoEndpoint:
    def test_generates_offline_people(self, api: Api) -> None:
        client, services = api
        response = client.post(
            "/api/host/demo/attendees", json={"source": "generated", "count": 10, "seed": 3}
        )
        assert response.json() == {"created": 10, "generator": "offline"}
        assert isinstance(services.elasticsearch.create, AsyncMock)
        assert services.elasticsearch.create.await_count == 10

    def test_falls_back_to_offline_when_no_llm_is_configured(self, api: Api) -> None:
        client, _ = api
        body = {"source": "generated", "count": 2, "use_llm": True}
        assert client.post("/api/host/demo/attendees", json=body).json()["generator"] == "offline"

    def test_uses_the_llm_when_asked_and_configured(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, services = api
        services.llm = AsyncMock(provider="fake", model="m")
        people = generate_offline(3, seed=1)
        monkeypatch.setattr(main, "generate_with_llm", AsyncMock(return_value=people))
        body = {"source": "generated", "count": 3, "use_llm": True}
        assert client.post("/api/host/demo/attendees", json=body).json() == {
            "created": 3,
            "generator": "llm:fake",
        }

    def test_rejects_unknown_themes(self, api: Api) -> None:
        client, _ = api
        body = {"source": "generated", "themes": ["cooking"]}
        assert client.post("/api/host/demo/attendees", json=body).status_code == 422

    def test_host_state_reports_ai_status_without_secrets(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "list_runs", AsyncMock(return_value=[]))
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-very-secret")
        response = client.get("/api/host/state")
        assert response.json()["llm"] == {
            "provider": "openai",
            "model": "gpt-test",
            "enabled": True,
            "problem": None,
            "embedding_model": None,
        }
        assert "sk-very-secret" not in response.text
