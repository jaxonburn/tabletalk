from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from elasticsearch import ApiError, AsyncElasticsearch
from pydantic import ValidationError
from tabletalk_api.seed import seed_attendees
from tabletalk_shared import storage
from tabletalk_shared.models import Attendee


@pytest.mark.parametrize("api_key", [None, ""])
def test_local_connection_does_not_send_an_api_key(
    monkeypatch: pytest.MonkeyPatch, api_key: str | None
) -> None:
    monkeypatch.delenv("ELASTICSEARCH_URL", raising=False)
    if api_key is None:
        monkeypatch.delenv("ELASTICSEARCH_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ELASTICSEARCH_API_KEY", api_key)
    constructor = Mock()
    monkeypatch.setattr(storage, "AsyncElasticsearch", constructor)
    storage.create_client()
    assert constructor.call_args.args == ("http://localhost:9200",)
    assert constructor.call_args.kwargs["api_key"] is None


def test_cloud_connection_uses_the_encoded_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_URL", "https://elasticsearch.example.com:443")
    monkeypatch.setenv("ELASTICSEARCH_API_KEY", "encoded-test-key")
    constructor = Mock()
    monkeypatch.setattr(storage, "AsyncElasticsearch", constructor)
    storage.create_client()
    assert constructor.call_args.args == ("https://elasticsearch.example.com:443",)
    assert constructor.call_args.kwargs["api_key"] == "encoded-test-key"


def test_api_key_is_rejected_before_it_can_be_sent_without_tls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELASTICSEARCH_URL", "http://elasticsearch.example.com:9200")
    monkeypatch.setenv("ELASTICSEARCH_API_KEY", "encoded-test-key")
    constructor = Mock()
    monkeypatch.setattr(storage, "AsyncElasticsearch", constructor)
    with pytest.raises(ValueError, match="must use HTTPS"):
        storage.create_client()
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "flavor,settings",
    [("serverless", None), ("default", {"number_of_shards": 1, "number_of_replicas": 0})],
)
async def test_index_settings_respect_serverless_managed_shards(
    flavor: str, settings: dict[str, int] | None
) -> None:
    client = Mock(spec=AsyncElasticsearch)
    client.info = AsyncMock(return_value={"version": {"build_flavor": flavor}})
    client.indices = Mock()
    client.indices.create = AsyncMock()
    await storage.ensure_indices(client)
    assert client.indices.create.await_count == 2
    assert all(
        call.kwargs["settings"] == settings for call in client.indices.create.await_args_list
    )


async def test_readiness_searches_both_indices_instead_of_serverless_cluster_health() -> None:
    client = Mock(spec=AsyncElasticsearch)
    client.search = AsyncMock()
    await storage.check_readiness(client)
    client.search.assert_awaited_once_with(
        index=[storage.ATTENDEES_INDEX, storage.PLANS_INDEX],
        size=0,
        allow_partial_search_results=False,
    )
    client.search.side_effect = ApiError("unavailable", Mock(), {})
    with pytest.raises(ApiError):
        await storage.check_readiness(client)


@pytest.fixture
def legacy_documents() -> list[dict[str, object]]:
    legacy_goals: list[list[str] | None] = [
        ["Learn", "Meet peers"],
        ["Find a cofounder", "Hire", "Get hired"],
        ["Sell / partner", "Invest"],
        [],
        None,
    ]
    documents = []
    for profile, goals in zip(seed_attendees(), legacy_goals, strict=False):
        document = profile.model_dump(exclude={"focus_goal", "goal_answers"})
        if goals is None:
            document.pop("goals")
        else:
            document["goals"] = goals
        documents.append(document)
    return documents


async def test_reading_stored_legacy_attendees_preserves_profiles(
    monkeypatch: pytest.MonkeyPatch, legacy_documents: list[dict[str, object]]
) -> None:
    async def scan(*_args: object, **_kwargs: object) -> AsyncIterator[dict[str, object]]:
        for document in legacy_documents:
            yield {"_source": document}

    monkeypatch.setattr(storage, "async_scan", scan)
    attendees = await storage.list_attendees(Mock(spec=AsyncElasticsearch))
    assert [attendee.goals for attendee in attendees] == [
        ["Learn", "Meet connections"],
        ["Meet connections"],
        ["Meet connections"],
        [],
        [],
    ]
    assert [attendee.id for attendee in attendees] == [doc["id"] for doc in legacy_documents]
    assert legacy_documents[0]["goals"] == ["Learn", "Meet peers"]


async def test_reading_saved_legacy_plan_normalizes_nested_members(
    legacy_documents: list[dict[str, object]],
) -> None:
    document = {
        "groups": [{"id": "table-1", "members": legacy_documents[:2], "reasons": []}],
        "generated_at": "2026-11-09T14:00:00+00:00",
        "attendee_count": 2,
    }
    client = Mock(spec=AsyncElasticsearch)
    client.get = AsyncMock(return_value={"_source": document})
    plan = await storage.read_plan(client)
    assert plan is not None
    assert plan.groups[0].members[0].goals == ["Learn", "Meet connections"]
    assert plan.groups[0].members[1].goals == ["Meet connections"]
    assert plan.groups[0].id == "table-1"
    assert legacy_documents[0]["goals"] == ["Learn", "Meet peers"]


def test_unknown_stored_goal_is_not_silently_reinterpreted(
    legacy_documents: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        Attendee.model_validate(legacy_documents[0] | {"goals": ["Unknown goal"]})
