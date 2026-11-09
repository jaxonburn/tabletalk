from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, Mock

import pytest
from elasticsearch import AsyncElasticsearch
from pydantic import ValidationError
from tabletalk_api.seed import seed_attendees
from tabletalk_shared import storage
from tabletalk_shared.models import Attendee


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
    members = legacy_documents[:2]
    document = {
        "groups": [{"id": "table-1", "members": members, "reasons": []}],
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
    assert members[0]["goals"] == ["Learn", "Meet peers"]


def test_unknown_stored_goal_is_not_silently_reinterpreted(
    legacy_documents: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        Attendee.model_validate(legacy_documents[0] | {"goals": ["Unknown goal"]})
