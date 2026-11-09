import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from elasticsearch import AsyncElasticsearch
from fastapi import Depends, FastAPI, HTTPException, Request
from opentelemetry import metrics
from tabletalk_shared.models import MAX_ATTENDEES, Attendee, MatchPlan
from tabletalk_shared.storage import (
    ATTENDEES_INDEX,
    check_readiness,
    create_client,
    ensure_indices,
    list_attendees,
    read_plan,
    save_plan,
)
from tabletalk_shared.telemetry import configure_telemetry, register_storage_errors

from tabletalk_matcher.matching import build_plan, candidate_query

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with create_client() as client:
        await ensure_indices(client)
        app.state.elasticsearch = client
        try:
            yield
        finally:
            shutdown_telemetry()


app = FastAPI(title="TableTalk Matching Service", lifespan=lifespan)
shutdown_telemetry = configure_telemetry(app, "tabletalk-matcher")
register_storage_errors(app)
group_counter = metrics.get_meter(__name__).create_counter("tabletalk.matching.groups_created")


def get_client(request: Request) -> AsyncElasticsearch:
    client = request.app.state.elasticsearch
    if not isinstance(client, AsyncElasticsearch):
        raise RuntimeError("Elasticsearch client is not initialized.")
    return client


Client = Annotated[AsyncElasticsearch, Depends(get_client)]


@app.get("/health")
async def health(client: Client) -> dict[str, str]:
    await check_readiness(client)
    return {
        "status": "ok",
        "service": "tabletalk-matcher",
        "version": os.getenv("APP_VERSION", "development"),
    }


@app.post("/match", response_model=MatchPlan)
async def match(client: Client) -> MatchPlan:
    existing = await read_plan(client)
    if existing is not None:
        return existing
    attendees = await list_attendees(client)
    if len(attendees) < 2:
        raise HTTPException(409, "Enroll at least two attendees before making lunch groups.")
    if len(attendees) > MAX_ATTENDEES:
        raise HTTPException(409, f"This demo supports up to {MAX_ATTENDEES} attendees.")

    snapshot_ids = [attendee.id for attendee in attendees]

    async def find_candidate(members: list[Attendee], excluded: set[str]) -> Attendee:
        query = candidate_query(members, excluded)
        query["bool"]["filter"].append({"terms": {"id": snapshot_ids}})
        response = await client.search(
            index=ATTENDEES_INDEX,
            query=query,
            size=1,
            sort=[{"_score": "desc"}, {"id": "asc"}],
        )
        hits = response["hits"]["hits"]
        if not hits:
            raise HTTPException(409, "Attendee data changed while matching. Please try again.")
        return Attendee.model_validate(hits[0]["_source"])

    plan = await save_plan(client, await build_plan(attendees, find_candidate))
    group_counter.add(len(plan.groups))
    logger.info("Lunch groups ready: attendees=%d groups=%d", plan.attendee_count, len(plan.groups))
    return plan
