import os
from collections.abc import AsyncIterator
from typing import Any

from elasticsearch import AsyncElasticsearch, BadRequestError, ConflictError, NotFoundError
from elasticsearch.helpers import async_scan

from tabletalk_shared.models import (
    CONFERENCE_ID,
    LUNCH_SLOT,
    MAX_ATTENDEES,
    Attendee,
    MatchPlan,
)

ATTENDEES_INDEX = "tabletalk-attendees-v1"
PLANS_INDEX = "tabletalk-plans-v1"
PLAN_ID = f"{CONFERENCE_ID}:{LUNCH_SLOT}"


def create_client() -> AsyncElasticsearch:
    return AsyncElasticsearch(
        os.getenv("ELASTICSEARCH_URL", "http://localhost:9200"),
        request_timeout=10,
        max_retries=2,
        retry_on_timeout=True,
    )


async def ensure_indices(client: AsyncElasticsearch) -> None:
    mappings: dict[str, dict[str, Any]] = {
        ATTENDEES_INDEX: {
            "dynamic": "strict",
            "properties": {
                "id": {"type": "keyword"},
                "conference_id": {"type": "keyword"},
                "lunch_slot": {"type": "keyword"},
                "name": {"type": "keyword", "index": False},
                "role": {"type": "text"},
                "goals": {"type": "keyword"},
                "interests": {"type": "text", "fields": {"exact": {"type": "keyword"}}},
                "can_help": {"type": "text", "analyzer": "english"},
                "want_to_learn": {"type": "text", "analyzer": "english"},
            },
        },
        PLANS_INDEX: {"dynamic": False, "properties": {}},
    }
    for index, mapping in mappings.items():
        try:
            await client.indices.create(
                index=index,
                mappings=mapping,
                settings={"number_of_shards": 1, "number_of_replicas": 0},
            )
        except BadRequestError as error:
            # Only the concurrent startup/create race is safe to ignore.
            if (
                isinstance(error.body, dict)
                and error.body.get("error", {}).get("type") == "resource_already_exists_exception"
            ):
                continue
            raise


def conference_filter() -> list[dict[str, Any]]:
    return [
        {"term": {"conference_id": CONFERENCE_ID}},
        {"term": {"lunch_slot": LUNCH_SLOT}},
    ]


async def scan_attendees(client: AsyncElasticsearch) -> AsyncIterator[Attendee]:
    async for hit in async_scan(
        client,
        index=ATTENDEES_INDEX,
        query={"query": {"bool": {"filter": conference_filter()}}},
    ):
        yield Attendee.model_validate(hit["_source"])


async def list_attendees(client: AsyncElasticsearch) -> list[Attendee]:
    attendees = [attendee async for attendee in scan_attendees(client)]
    return sorted(attendees, key=lambda attendee: attendee.id)


async def read_plan(client: AsyncElasticsearch) -> MatchPlan | None:
    try:
        result = await client.get(index=PLANS_INDEX, id=PLAN_ID)
    except NotFoundError:
        return None
    return MatchPlan.model_validate(result["_source"])


async def save_plan(client: AsyncElasticsearch, plan: MatchPlan) -> MatchPlan:
    try:
        await client.create(
            index=PLANS_INDEX, id=PLAN_ID, document=plan.model_dump(mode="json"), refresh="wait_for"
        )
    except ConflictError:
        existing = await read_plan(client)
        if existing is None:
            raise RuntimeError("The winning match plan could not be read.") from None
        return existing
    return plan


async def attendee_count(client: AsyncElasticsearch) -> int:
    response = await client.count(
        index=ATTENDEES_INDEX,
        query={"bool": {"filter": conference_filter()}},
    )
    return int(response["count"])


async def validate_capacity(client: AsyncElasticsearch, incoming: int = 1) -> bool:
    return await attendee_count(client) + incoming <= MAX_ATTENDEES
