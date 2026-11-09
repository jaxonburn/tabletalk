import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

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
    url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
    api_key = os.getenv("ELASTICSEARCH_API_KEY") or None
    if api_key and urlsplit(url).scheme != "https":
        raise ValueError("ELASTICSEARCH_URL must use HTTPS when ELASTICSEARCH_API_KEY is set.")
    return AsyncElasticsearch(
        url,
        api_key=api_key,
        request_timeout=10,
        max_retries=2,
        retry_on_timeout=True,
    )


async def ensure_indices(client: AsyncElasticsearch) -> None:
    info = await client.info()
    serverless = info["version"].get("build_flavor") == "serverless"
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
                "focus_goal": {"type": "keyword"},
                "goal_answers": {
                    "type": "object",
                    "dynamic": "strict",
                    "properties": {
                        "Learn": {"type": "text", "analyzer": "english"},
                        "Share something I've learned": {"type": "text", "analyzer": "english"},
                        "Meet connections": {"type": "text", "analyzer": "english"},
                    },
                },
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
                settings=None if serverless else {"number_of_shards": 1, "number_of_replicas": 0},
            )
        except BadRequestError as error:
            # Add fields to existing indexes without resetting saved profiles or plans.
            if (
                isinstance(error.body, dict)
                and error.body.get("error", {}).get("type") == "resource_already_exists_exception"
            ):
                await client.indices.put_mapping(index=index, properties=mapping["properties"])
                continue
            raise


async def check_readiness(client: AsyncElasticsearch) -> None:
    await client.search(
        index=[ATTENDEES_INDEX, PLANS_INDEX], size=0, allow_partial_search_results=False
    )


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
