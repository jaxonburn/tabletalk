import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated
from uuid import uuid4

import httpx
from elasticsearch import AsyncElasticsearch
from fastapi import Depends, FastAPI, HTTPException, Request
from opentelemetry import metrics, trace
from tabletalk_shared.models import (
    MAX_ATTENDEES,
    Attendee,
    ConferenceState,
    Enrollment,
    MatchPlan,
)
from tabletalk_shared.storage import (
    ATTENDEES_INDEX,
    create_client,
    ensure_indices,
    list_attendees,
    read_plan,
    validate_capacity,
)
from tabletalk_shared.telemetry import configure_telemetry, register_storage_errors

from tabletalk_api.seed import seed_attendees

logger = logging.getLogger(__name__)


@dataclass
class Services:
    elasticsearch: AsyncElasticsearch
    matcher: httpx.AsyncClient
    # One API worker owns mutations in this local demo; see agent.md before scaling.
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with (
        create_client() as client,
        httpx.AsyncClient(
            base_url=os.getenv("MATCHER_URL", "http://localhost:8001"), timeout=90
        ) as matcher,
    ):
        await ensure_indices(client)
        app.state.services = Services(client, matcher)
        try:
            yield
        finally:
            shutdown_telemetry()


app = FastAPI(title="TableTalk Conference API", lifespan=lifespan)
shutdown_telemetry = configure_telemetry(app, "tabletalk-api")
register_storage_errors(app)
enrollment_counter = metrics.get_meter(__name__).create_counter("tabletalk.enrollments")
tracer = trace.get_tracer(__name__)


def get_services(request: Request) -> Services:
    services = request.app.state.services
    if not isinstance(services, Services):
        raise RuntimeError("Service clients are not initialized.")
    return services


Dependencies = Annotated[Services, Depends(get_services)]


@app.get("/health")
async def health(services: Dependencies) -> dict[str, str]:
    await services.elasticsearch.cluster.health(wait_for_status="yellow", timeout="3s")
    response = await call_matcher(services, "GET", "/health")
    response.raise_for_status()
    return {"status": "ok", "service": "tabletalk-api"}


@app.get("/api/conference", response_model=ConferenceState)
async def conference(services: Dependencies) -> ConferenceState:
    attendees, plan = await asyncio.gather(
        list_attendees(services.elasticsearch), read_plan(services.elasticsearch)
    )
    return ConferenceState(attendees=attendees, plan=plan)


async def require_open_enrollment(services: Services) -> None:
    if await read_plan(services.elasticsearch) is not None:
        raise HTTPException(
            409, "Groups are already assigned for this lunch. Enrollment is closed."
        )


@app.post("/api/enrollments", response_model=Attendee, status_code=201)
async def enroll(enrollment: Enrollment, services: Dependencies) -> Attendee:
    async with services.mutation_lock:
        await require_open_enrollment(services)
        if not await validate_capacity(services.elasticsearch):
            raise HTTPException(409, f"This demo is limited to {MAX_ATTENDEES} attendees.")
        attendee = Attendee(id=str(uuid4()), **enrollment.model_dump())
        with tracer.start_as_current_span("enrollment.save"):
            await services.elasticsearch.create(
                index=ATTENDEES_INDEX,
                id=attendee.id,
                document=attendee.model_dump(),
                refresh="wait_for",
            )
        enrollment_counter.add(1)
        logger.info("Enrollment created.")
        return attendee


@app.post("/api/demo/seed")
async def seed(services: Dependencies) -> dict[str, int]:
    async with services.mutation_lock:
        await require_open_enrollment(services)
        existing_ids = {attendee.id for attendee in await list_attendees(services.elasticsearch)}
        incoming = [attendee for attendee in seed_attendees() if attendee.id not in existing_ids]
        if not await validate_capacity(services.elasticsearch, len(incoming)):
            raise HTTPException(
                409, f"Seeding would exceed the {MAX_ATTENDEES}-attendee demo limit."
            )
        for attendee in incoming:
            await services.elasticsearch.create(
                index=ATTENDEES_INDEX,
                id=attendee.id,
                document=attendee.model_dump(),
            )
        await services.elasticsearch.indices.refresh(index=ATTENDEES_INDEX)
        logger.info("Demo attendees seeded: count=%d", len(incoming))
        return {"created": len(incoming)}


async def call_matcher(services: Services, method: str, path: str) -> httpx.Response:
    try:
        response = await services.matcher.request(method, path)
    except httpx.TimeoutException as error:
        logger.error("Matching service timed out.")
        raise HTTPException(504, "The matching service timed out. Please try again.") from error
    except httpx.RequestError as error:
        logger.error("Matching service connection failed.")
        raise HTTPException(
            503, "The matching service is unavailable. Please try again."
        ) from error
    if response.is_error:
        logger.warning("Matching service returned status=%d", response.status_code)
        if response.status_code == 409:
            raise HTTPException(409, response.json()["detail"])
        raise HTTPException(502, "The matching service could not complete the request.")
    return response


@app.post("/api/matches", response_model=MatchPlan)
async def generate_matches(services: Dependencies) -> MatchPlan:
    async with services.mutation_lock:
        response = await call_matcher(services, "POST", "/match")
        return MatchPlan.model_validate(response.json())
