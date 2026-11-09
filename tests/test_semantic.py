import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from elasticsearch import AsyncElasticsearch
from fastapi.testclient import TestClient
from tabletalk_api import main as api_main
from tabletalk_matcher import main as matcher_main
from tabletalk_matcher.affinity import (
    DEFAULT_WEIGHTS,
    affinity_query,
    combine_directed,
    knn_query,
    semantic_directed_scores,
)
from tabletalk_matcher.enrichment import clean_tags, enrich
from tabletalk_matcher.evaluation import evaluate
from tabletalk_matcher.judge import parse_verdict, random_tables
from tabletalk_shared.fixtures import load_fixture
from tabletalk_shared.llm import (
    VECTOR_DIMS,
    EmbeddingConfig,
    LLMError,
    OpenAIEmbedder,
    create_embedder,
)
from tabletalk_shared.models import Attendee, Judgement, MatchRun, TableVerdict

Api = tuple[TestClient, api_main.Services]


def people(count: int = 6) -> list[Attendee]:
    return [entry.attendee for entry in load_fixture().attendees[:count]]


def unit(index: int) -> list[float]:
    vector = [0.0] * VECTOR_DIMS
    vector[index] = 1.0
    return vector


class TestEmbeddings:
    async def test_requests_fixed_dimensions_and_keeps_input_order(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            rows = [{"index": i, "embedding": unit(i)} for i in range(len(body["input"]))][::-1]
            return httpx.Response(200, json={"data": rows, "usage": {"prompt_tokens": 3}})

        config = EmbeddingConfig(model="emb-test", api_key="k", base_url="https://x")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            vectors = await OpenAIEmbedder(config, http).embed(["a", "b", "c"])
        assert seen[0] == {"model": "emb-test", "input": ["a", "b", "c"], "dimensions": 512}
        assert [vector.index(1.0) for vector in vectors] == [0, 1, 2]

    async def test_wrong_sizes_and_errors_raise(self) -> None:
        def short(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

        config = EmbeddingConfig(model="emb-test", api_key="k", base_url="https://x")
        async with httpx.AsyncClient(transport=httpx.MockTransport(short)) as http:
            with pytest.raises(LLMError, match="size"):
                await OpenAIEmbedder(config, http).embed(["a"])

    def test_disabled_without_model_or_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "k")
        assert not EmbeddingConfig.from_env().enabled
        monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
        assert EmbeddingConfig.from_env().enabled
        assert create_embedder(EmbeddingConfig(), httpx.AsyncClient()) is None


class TestHybridScoring:
    def test_semantic_rows_use_min_max_and_blend_with_keywords(self) -> None:
        bm25 = {"a": {"b": 10.0, "c": 5.0}}
        semantic = {"a": {"b": 1.0, "c": 3.0}}
        combined = combine_directed(bm25, semantic)
        alpha = DEFAULT_WEIGHTS.semantic
        assert combined["a"]["b"] == pytest.approx((1 - alpha) * 1.0 + alpha * 0.0)
        assert combined["a"]["c"] == pytest.approx((1 - alpha) * 0.5 + alpha * 1.0)

    def test_without_vectors_scoring_is_unchanged(self) -> None:
        bm25 = {"a": {"b": 4.0, "c": 2.0}}
        assert combine_directed(bm25, None) == {"a": {"b": 1.0, "c": 0.5}}

    def test_python_twin_matches_elasticsearch_cosine_scoring(self) -> None:
        a, b = people(2)
        vectors = {
            a.id: {"learn_vector": unit(0), "share_vector": unit(1), "topic_vector": unit(2)},
            b.id: {"learn_vector": unit(1), "share_vector": unit(0), "topic_vector": unit(2)},
        }
        scores = semantic_directed_scores([a, b], vectors)
        w = DEFAULT_WEIGHTS
        # learn->share and share->learn are identical vectors (cos 1); topics identical too.
        assert scores[a.id][b.id] == pytest.approx(2 * w.semantic_learn_share + w.semantic_topic)

    def test_knn_query_pairs_my_question_with_their_experience(self) -> None:
        clauses = knn_query("me", {"learn_vector": unit(0)}, ["me", "you"])
        assert [clause["field"] for clause in clauses] == ["share_vector"]
        assert clauses[0]["filter"]["bool"]["must_not"] == [{"term": {"id": "me"}}]
        assert {"terms": {"id": ["me", "you"]}} in clauses[0]["filter"]["bool"]["filter"]

    def test_topic_tags_join_the_keyword_query(self) -> None:
        tagged = people(1)[0].model_copy(update={"topic_tags": ["kubernetes autoscaling"]})
        should = affinity_query(tagged, [tagged.id])["bool"]["should"]
        assert {"terms": {"topic_tags": ["kubernetes autoscaling"], "boost": 2.0}} in should

    def test_hybrid_evaluation_runs_end_to_end(self) -> None:
        fixture = load_fixture()
        vectors = {
            entry.attendee.id: {
                "topic_vector": unit(
                    ["platform", "security", "data-ml", "bridge"].index(entry.theme)
                )
            }
            for entry in fixture.attendees
        }
        result = evaluate(list(fixture.attendees), list(fixture.tables), 0, vectors)
        assert result.attendees == 14
        assert result.neighbor_accuracy >= 0.9


class TestTags:
    def test_cleans_and_limits_tags(self) -> None:
        raw = ["Kubernetes  Autoscaling", "kubernetes autoscaling", "x", "ok!", 7, *"abcdefgh"]
        assert clean_tags(raw) == ["kubernetes autoscaling"]
        assert len(clean_tags([f"topic {i}" for i in range(10)])) == 6


class FakeES:
    """Just enough of AsyncElasticsearch for enrichment."""

    def __init__(self, stored: dict[str, dict[str, Any]]) -> None:
        self.stored = stored
        self.bulk_calls: list[list[dict[str, Any]]] = []

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        # Like Elasticsearch 9, vectors are only returned when explicitly requested.
        assert kwargs["source"]["exclude_vectors"] is False
        ids = kwargs["query"]["terms"]["id"]
        return {
            "hits": {
                "hits": [{"_id": i, "_source": self.stored[i]} for i in ids if i in self.stored]
            }
        }

    async def bulk(self, **kwargs: Any) -> dict[str, Any]:
        self.bulk_calls.append(kwargs["operations"])
        return {"errors": False}


class FakeEmbedder:
    model = "emb-test"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise LLMError("down")
        return [unit(i % VECTOR_DIMS) for i in range(len(texts))]


class FakeTagger:
    provider = "fake"
    model = "tagger"

    async def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        payload = json.loads(kwargs["user"].split("\n", 1)[1])
        return {
            "attendees": [
                {"attendee_id": p["attendee_id"], "tags": ["Shared Topic", p["interests"][0]]}
                for p in payload
            ]
        }


class TestEnrichment:
    async def test_backfills_vectors_and_tags_then_reuses_them(self) -> None:
        group = people(4)
        es = FakeES({})
        embedder = FakeEmbedder()
        enriched, result = await enrich(
            Mock(spec=AsyncElasticsearch, search=es.search, bulk=es.bulk),
            group,
            embedder=embedder,
            llm=FakeTagger(),
        )
        assert result.retrieval == "bm25+vectors+tags"
        assert result.embedded == 4 and result.tagged == 4
        assert all("shared topic" in attendee.topic_tags for attendee in enriched)
        assert set(result.vectors[group[0].id]) >= {"learn_vector", "topic_vector"}
        docs = es.bulk_calls[0][1::2]
        assert all(doc["doc"]["embedding_model"] == "emb-test" for doc in docs)

        stored = {
            op["update"]["_id"]: doc["doc"]
            for op, doc in zip(es.bulk_calls[0][::2], es.bulk_calls[0][1::2], strict=True)
        }
        cached = FakeES(stored)
        again = FakeEmbedder()
        _, second = await enrich(
            Mock(spec=AsyncElasticsearch, search=cached.search, bulk=cached.bulk),
            group,
            embedder=again,
            llm=FakeTagger(),
        )
        assert again.calls == 0 and cached.bulk_calls == []
        assert second.retrieval == "bm25+vectors+tags"
        # Cached vectors must reach scoring, not just the label.
        assert (
            second.vectors[group[0].id]["learn_vector"]
            == result.vectors[group[0].id]["learn_vector"]
        )

    async def test_missing_vectors_are_not_reported_as_vector_scoring(self) -> None:
        stored = {a.id: {"embedding_model": "emb-test"} for a in people(3)}
        es = FakeES(stored)
        _, result = await enrich(
            Mock(spec=AsyncElasticsearch, search=es.search, bulk=es.bulk),
            people(3),
            embedder=FakeEmbedder(),
            llm=None,
        )
        assert result.vectors == {} and result.retrieval == "bm25"

    async def test_provider_failures_fall_back_to_keywords(self) -> None:
        es = FakeES({})
        _, result = await enrich(
            Mock(spec=AsyncElasticsearch, search=es.search, bulk=es.bulk),
            people(3),
            embedder=FakeEmbedder(fail=True),
            llm=None,
        )
        assert result.retrieval == "bm25"
        assert result.vectors == {}


class TestJudge:
    @pytest.mark.parametrize(
        "data",
        [
            {"score": 0, "rationale": "x"},
            {"score": 6, "rationale": "x"},
            {"score": True, "rationale": "x"},
            {"score": 3, "rationale": " "},
        ],
    )
    def test_rejects_invalid_verdicts(self, data: dict[str, Any]) -> None:
        with pytest.raises(LLMError):
            parse_verdict(data, 1)

    def test_random_baseline_keeps_table_sizes_and_people(self) -> None:
        from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
        from tabletalk_matcher.matching import build_plan

        fixture = load_fixture()
        attendees = [entry.attendee for entry in fixture.attendees]
        plan = build_plan(
            attendees,
            fixture.tables,
            Affinity.from_directed(attendees, lexical_directed_scores(attendees)),
        )
        tables = random_tables(plan, seed=3)
        assert [len(t) for t in tables] == [len(g.members) for g in plan.groups]
        assert sorted(a.id for t in tables for a in t) == sorted(a.id for a in attendees)

    def test_matcher_judge_requires_an_llm(self) -> None:
        matcher_main.app.state.llm = None
        plan = {"groups": [], "generated_at": "x", "attendee_count": 0}
        response = TestClient(matcher_main.app).post("/judge", json={"plan": plan})
        assert response.status_code == 409

    def test_api_stores_the_judgement_on_the_run(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, services = api
        from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
        from tabletalk_matcher.matching import build_plan

        attendees = people(4)
        plan = build_plan(
            attendees,
            load_fixture().tables,
            Affinity.from_directed(attendees, lexical_directed_scores(attendees)),
        )
        run = MatchRun(
            id="r1", created_at=datetime.now(UTC), seed=2, tables=[], min_table_size=2, plan=plan
        )
        monkeypatch.setattr(api_main, "read_run", AsyncMock(return_value=run))
        save = AsyncMock()
        monkeypatch.setattr(api_main, "save_judgement", save)
        judgement = Judgement(
            model="openai:m",
            prompt_version="v",
            tables=[TableVerdict(table_number=1, score=4, rationale="Ana can help Ben.")],
            average=4.0,
            baseline_average=2.5,
            judged_at=datetime.now(UTC),
        )
        assert isinstance(services.matcher.request, AsyncMock)
        services.matcher.request.return_value = httpx.Response(
            200, json=judgement.model_dump(mode="json")
        )
        response = client.post("/api/host/runs/r1/judge")
        assert response.status_code == 200
        assert response.json()["average"] == 4.0
        call = services.matcher.request.await_args
        assert call is not None and call.args[:2] == ("POST", "/judge")
        assert call.kwargs["json"]["seed"] == 2
        save.assert_awaited_once()


def test_keyword_only_runs_ignore_tags_from_earlier_semantic_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tagged = [a.model_copy(update={"topic_tags": ["shared tag"]}) for a in people(4)]
    queries: list[dict[str, Any]] = []

    async def search(**kwargs: Any) -> dict[str, Any]:
        queries.append(kwargs["query"])
        me = kwargs["query"]["bool"]["must_not"][0]["term"]["id"]
        return {"hits": {"hits": [{"_id": a.id, "_score": 1.0} for a in tagged if a.id != me]}}

    client = Mock(spec=AsyncElasticsearch)
    client.search = AsyncMock(side_effect=search)
    monkeypatch.setattr(matcher_main, "list_attendees", AsyncMock(return_value=tagged))
    matcher_main.app.dependency_overrides[matcher_main.get_client] = lambda: client
    matcher_main.app.state.llm = FakeTagger()
    matcher_main.app.state.embedder = None
    try:
        response = TestClient(matcher_main.app).post(
            "/runs",
            json={
                "tables": [{"number": 1, "seats": 4}],
                "use_semantic": False,
                "use_llm": False,
            },
        )
    finally:
        matcher_main.app.dependency_overrides.clear()
        matcher_main.app.state.llm = None
    assert response.status_code == 200
    assert response.json()["retrieval"] == "bm25"
    assert queries and not any("topic_tags" in json.dumps(query) for query in queries)


def test_vector_search_failures_fall_back_to_keyword_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from elasticsearch import BadRequestError
    from tabletalk_matcher.enrichment import Enrichment

    group = people(4)
    vectors = {a.id: {"learn_vector": unit(0), "share_vector": unit(1)} for a in group}

    async def fake_enrich(*_args: Any, **_kwargs: Any) -> tuple[list[Attendee], Enrichment]:
        return group, Enrichment(vectors=vectors)

    async def search(**kwargs: Any) -> dict[str, Any]:
        if "knn" in kwargs:
            raise BadRequestError("knn unsupported", Mock(status=400), {})
        me = kwargs["query"]["bool"]["must_not"][0]["term"]["id"]
        return {"hits": {"hits": [{"_id": a.id, "_score": 1.0} for a in group if a.id != me]}}

    client = Mock(spec=AsyncElasticsearch)
    client.search = AsyncMock(side_effect=search)
    monkeypatch.setattr(matcher_main, "list_attendees", AsyncMock(return_value=group))
    monkeypatch.setattr(matcher_main, "enrich", fake_enrich)
    matcher_main.app.dependency_overrides[matcher_main.get_client] = lambda: client
    matcher_main.app.state.embedder = FakeEmbedder()
    try:
        response = TestClient(matcher_main.app).post(
            "/runs", json={"tables": [{"number": 1, "seats": 4}], "use_llm": False}
        )
    finally:
        matcher_main.app.dependency_overrides.clear()
        matcher_main.app.state.embedder = None
    assert response.status_code == 200
    assert response.json()["retrieval"] == "bm25"
