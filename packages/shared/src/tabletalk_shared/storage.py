import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from elasticsearch import AsyncElasticsearch, BadRequestError, ConflictError, NotFoundError
from elasticsearch.helpers import async_scan

from tabletalk_shared.llm import VECTOR_DIMS
from tabletalk_shared.models import (
    CONFERENCE_ID,
    LUNCH_SLOT,
    MAX_ATTENDEES,
    Attendee,
    AutoMatchState,
    EventConfig,
    Judgement,
    MatchPlan,
    MatchRun,
    RunSummary,
)

ATTENDEES_INDEX = "tabletalk-attendees-v1"
# Holds the single published plan per lunch. Its existence closes enrollment.
PLANS_INDEX = "tabletalk-plans-v1"
EVENTS_INDEX = "tabletalk-events-v1"
RUNS_INDEX = "tabletalk-runs-v1"
# Maps sha256(attendee token) to an attendee ID. Plain tokens are never stored.
TOKENS_INDEX = "tabletalk-tokens-v1"
PLAN_ID = f"{CONFERENCE_ID}:{LUNCH_SLOT}"
EVENT_ID = PLAN_ID
AUTO_STATE_ID = f"{EVENT_ID}:auto-match"
VECTOR_FIELDS = ("learn_vector", "share_vector", "topic_vector")
# Matching-only fields stored on attendee documents but never returned as profiles.
ENRICHMENT_ONLY_FIELDS = [*VECTOR_FIELDS, "embedding_model", "tags_model"]


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
                "topic_tags": {"type": "keyword"},
                "tags_model": {"type": "keyword"},
                "embedding_model": {"type": "keyword"},
                **{
                    field: {
                        "type": "dense_vector",
                        "dims": VECTOR_DIMS,
                        "index": True,
                        "similarity": "cosine",
                    }
                    for field in VECTOR_FIELDS
                },
            },
        },
        PLANS_INDEX: {"dynamic": False, "properties": {}},
        EVENTS_INDEX: {"dynamic": False, "properties": {}},
        RUNS_INDEX: {
            "dynamic": False,
            "properties": {"id": {"type": "keyword"}, "created_at": {"type": "date"}},
        },
        TOKENS_INDEX: {"dynamic": False, "properties": {"attendee_id": {"type": "keyword"}}},
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
        source_excludes=ENRICHMENT_ONLY_FIELDS,
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


async def read_event(client: AsyncElasticsearch) -> EventConfig:
    try:
        result = await client.get(index=EVENTS_INDEX, id=EVENT_ID)
    except NotFoundError:
        return EventConfig()
    return EventConfig.model_validate(result["_source"])


async def save_event(client: AsyncElasticsearch, event: EventConfig) -> EventConfig:
    await client.index(
        index=EVENTS_INDEX, id=EVENT_ID, document=event.model_dump(mode="json"), refresh="wait_for"
    )
    return event


async def save_run(client: AsyncElasticsearch, run: MatchRun) -> MatchRun:
    await client.create(
        index=RUNS_INDEX, id=run.id, document=run.model_dump(mode="json"), refresh="wait_for"
    )
    return run


async def read_run(client: AsyncElasticsearch, run_id: str) -> MatchRun | None:
    try:
        result = await client.get(index=RUNS_INDEX, id=run_id)
    except NotFoundError:
        return None
    return MatchRun.model_validate(result["_source"])


async def list_runs(
    client: AsyncElasticsearch, published_run_id: str | None, limit: int = 50
) -> list[RunSummary]:
    response = await client.search(
        index=RUNS_INDEX,
        query={"match_all": {}},
        sort=[{"created_at": "desc"}],
        size=limit,
        source_excludes=["plan.groups"],
    )
    summaries = []
    for hit in response["hits"]["hits"]:
        source = hit["_source"]
        plan = source["plan"]
        metrics = plan.get("metrics")
        judgement = source.get("judgement") or {}
        summaries.append(
            RunSummary(
                id=source["id"],
                created_at=source["created_at"],
                seed=source["seed"],
                attendee_count=plan["attendee_count"],
                table_count=len(metrics["table_sizes"]) if metrics else 0,
                metrics=metrics,
                published=source["id"] == published_run_id,
                narrative_provider=plan.get("narrative_provider"),
                retrieval=plan.get("retrieval"),
                judge_average=judgement.get("average"),
                judge_baseline_average=judgement.get("baseline_average"),
            )
        )
    return summaries


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def save_token(client: AsyncElasticsearch, token: str, attendee_id: str) -> None:
    await client.create(
        index=TOKENS_INDEX,
        id=token_hash(token),
        document={"attendee_id": attendee_id},
        refresh="wait_for",
    )


async def find_attendee_by_token(client: AsyncElasticsearch, token: str) -> Attendee | None:
    try:
        link = await client.get(index=TOKENS_INDEX, id=token_hash(token))
        result = await client.get(
            index=ATTENDEES_INDEX,
            id=link["_source"]["attendee_id"],
            source_excludes=ENRICHMENT_ONLY_FIELDS,
        )
    except NotFoundError:
        return None
    return Attendee.model_validate(result["_source"])


async def read_auto_state(client: AsyncElasticsearch) -> AutoMatchState | None:
    try:
        result = await client.get(index=EVENTS_INDEX, id=AUTO_STATE_ID)
    except NotFoundError:
        return None
    return AutoMatchState.model_validate(result["_source"])


async def save_auto_state(client: AsyncElasticsearch, state: AutoMatchState) -> None:
    await client.index(
        index=EVENTS_INDEX,
        id=AUTO_STATE_ID,
        document=state.model_dump(mode="json"),
        refresh="wait_for",
    )


async def save_judgement(client: AsyncElasticsearch, run_id: str, judgement: Judgement) -> None:
    await client.update(
        index=RUNS_INDEX,
        id=run_id,
        doc={"judgement": judgement.model_dump(mode="json")},
        refresh="wait_for",
    )


async def attendee_count(client: AsyncElasticsearch) -> int:
    response = await client.count(
        index=ATTENDEES_INDEX,
        query={"bool": {"filter": conference_filter()}},
    )
    return int(response["count"])


async def validate_capacity(
    client: AsyncElasticsearch, incoming: int = 1, capacity: int = MAX_ATTENDEES
) -> bool:
    return await attendee_count(client) + incoming <= min(capacity, MAX_ATTENDEES)
