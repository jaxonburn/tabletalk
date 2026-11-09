import asyncio
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Any, Literal
from uuid import uuid4

import httpx
from elasticsearch import AsyncElasticsearch
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from opentelemetry import metrics, trace
from pydantic import BaseModel, Field
from tabletalk_shared.fixtures import load_fixture
from tabletalk_shared.llm import (
    EmbeddingConfig,
    LLMClient,
    LLMConfig,
    LLMError,
    create_llm_client,
)
from tabletalk_shared.models import (
    Announcement,
    AnnouncementRequest,
    Attendee,
    AttendeeAnnouncement,
    AutoMatchState,
    ConferenceState,
    EnrolledAttendee,
    Enrollment,
    EnrollmentStatus,
    EventConfig,
    GoalPrompt,
    HostState,
    Judgement,
    JudgeRequest,
    LLMStatus,
    LunchGroup,
    MatchPlan,
    MatchRun,
    MyAssignment,
    MyStatus,
    RunRequest,
    Tablemate,
)
from tabletalk_shared.personas import THEMES_BY_KEY, generate_offline, generate_with_llm
from tabletalk_shared.storage import (
    ATTENDEES_INDEX,
    check_readiness,
    create_client,
    delete_draft_runs,
    ensure_indices,
    find_attendee_by_token,
    list_announcements,
    list_attendees,
    list_check_ins,
    list_runs,
    read_auto_state,
    read_check_in,
    read_event,
    read_plan,
    read_run,
    save_announcement,
    save_auto_state,
    save_check_in,
    save_event,
    save_judgement,
    save_plan,
    save_run,
    save_token,
    update_attendee,
    validate_capacity,
)
from tabletalk_shared.telemetry import configure_telemetry, register_storage_errors

from tabletalk_api.observability import DashboardMetrics
from tabletalk_api.seed import GOAL_PROMPTS, seed_attendees

logger = logging.getLogger(__name__)
# Organizer shortcut: the same snapshot always produces the same published plan.
DEFAULT_SEED = 0


@dataclass
class Services:
    elasticsearch: AsyncElasticsearch
    matcher: httpx.AsyncClient
    llm: LLMClient | None = None
    # One API worker owns mutations in this local demo; see agent.md before scaling.
    mutation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with (
        create_client() as client,
        httpx.AsyncClient(
            base_url=os.getenv("MATCHER_URL", "http://localhost:8001"), timeout=120
        ) as matcher,
        httpx.AsyncClient() as llm_http,
    ):
        await ensure_indices(client)
        services = Services(client, matcher, create_llm_client(LLMConfig.from_env(), llm_http))
        app.state.services = services
        watcher = asyncio.create_task(
            cutoff_watcher(services, float(os.getenv("AUTO_MATCH_POLL_SECONDS", "10")))
        )
        try:
            yield
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
            shutdown_telemetry()


app = FastAPI(title="TableTalk Conference API", lifespan=lifespan)
shutdown_telemetry = configure_telemetry(app, "tabletalk-api")
register_storage_errors(app)
meter = metrics.get_meter(__name__)
dashboard_metrics = DashboardMetrics(meter)
enrollment_counter = meter.create_counter("tabletalk.enrollments")
run_counter = meter.create_counter("tabletalk.matching.runs")
publish_counter = meter.create_counter("tabletalk.matching.published")
check_in_counter = meter.create_counter("tabletalk.check_ins")
tracer = trace.get_tracer(__name__)


def get_services(request: Request) -> Services:
    services = request.app.state.services
    if not isinstance(services, Services):
        raise RuntimeError("Service clients are not initialized.")
    return services


Dependencies = Annotated[Services, Depends(get_services)]


def require_host(request: Request) -> None:
    """Host controls are open locally unless HOST_TOKEN is set."""
    expected = os.getenv("HOST_TOKEN", "")
    if not expected:
        return
    provided = request.headers.get("X-Host-Token", "")
    if not secrets.compare_digest(provided.encode(), expected.encode()):
        raise HTTPException(401, "Enter the host token to use host controls.")


HostOnly = [Depends(require_host)]


def utc_now() -> datetime:
    return datetime.now(UTC)


def enrollment_status(
    event: EventConfig, plan: MatchPlan | None, now: datetime
) -> EnrollmentStatus:
    if plan is not None:
        return "published"
    if event.enrollment_closes_at is not None and now >= event.enrollment_closes_at:
        return "closed"
    return "open"


@app.get("/health")
@app.get("/api/health")
async def health(services: Dependencies) -> dict[str, str | None]:
    await check_readiness(services.elasticsearch)
    response = await call_matcher(services, "GET", "/health")
    response.raise_for_status()
    return {
        "status": "ok",
        "service": "tabletalk-api",
        "version": os.getenv("APP_VERSION", "development"),
        "matcher_version": response.json().get("version"),
    }


@app.get("/api/conference", response_model=ConferenceState)
async def conference(services: Dependencies) -> ConferenceState:
    attendees, plan, event = await asyncio.gather(
        list_attendees(services.elasticsearch),
        read_plan(services.elasticsearch),
        read_event(services.elasticsearch),
    )
    dashboard_metrics.record_event(len(attendees), event, published=plan is not None)
    return ConferenceState(
        attendees=[],  # Full profiles are host-only; personal table details use /api/me.
        attendee_count=len(attendees),
        plan=None,
        published=plan is not None,
        group_size=max(table.seats for table in event.tables),
        status=enrollment_status(event, plan, utc_now()),
        enrollment_closes_at=event.enrollment_closes_at,
        capacity=event.capacity,
        goal_prompts={goal: GoalPrompt(**prompt) for goal, prompt in GOAL_PROMPTS.items()},
        lunch_time=event.lunch_time,
        location=event.location,
        name=event.name,
    )


async def require_open_enrollment(services: Services) -> EventConfig:
    event, plan = await asyncio.gather(
        read_event(services.elasticsearch), read_plan(services.elasticsearch)
    )
    status = enrollment_status(event, plan, utc_now())
    if status == "published":
        raise HTTPException(
            409, "Groups are already assigned for this lunch. Enrollment is closed."
        )
    if status == "closed":
        raise HTTPException(409, "Enrollment for this lunch has closed.")
    return event


@app.post("/api/enrollments", response_model=EnrolledAttendee, status_code=201)
async def enroll(enrollment: Enrollment, services: Dependencies) -> EnrolledAttendee:
    async with services.mutation_lock:
        event = await require_open_enrollment(services)
        if not await validate_capacity(services.elasticsearch, capacity=event.capacity):
            raise HTTPException(409, f"This lunch is full ({event.capacity} seats).")
        attendee = Attendee(id=str(uuid4()), **enrollment.model_dump())
        token = secrets.token_urlsafe(32)
        with tracer.start_as_current_span("enrollment.save"):
            # The token link is written first, so an attendee never exists without one.
            await save_token(services.elasticsearch, token, attendee.id)
            await services.elasticsearch.create(
                index=ATTENDEES_INDEX,
                id=attendee.id,
                document=attendee.model_dump(),
                refresh="wait_for",
            )
        enrollment_counter.add(1)
        logger.info("Enrollment created.")
        return EnrolledAttendee(**attendee.model_dump(), attendee_token=token)


def first_name(attendee: Attendee) -> str:
    return attendee.name.split()[0]


AttendeeToken = Annotated[str | None, Header(alias="X-Attendee-Token")]


async def current_attendee(services: Services, token: str | None) -> Attendee:
    attendee = await find_attendee_by_token(services.elasticsearch, token) if token else None
    if attendee is None:
        raise HTTPException(401, "We couldn't find your enrollment on this device.")
    return attendee


def my_group(plan: MatchPlan | None, attendee_id: str) -> LunchGroup | None:
    groups = plan.groups if plan else []
    return next((g for g in groups if any(m.id == attendee_id for m in g.members)), None)


def inbox(
    announcements: list[Announcement], seated: bool, now: datetime
) -> list[AttendeeAnnouncement]:
    """Announcements this attendee should see now, newest first."""
    return [
        AttendeeAnnouncement(id=a.id, title=a.title, body=a.body, sent_at=a.send_at or a.created_at)
        for a in announcements
        if (a.send_at is None or a.send_at <= now) and (a.audience == "all" or seated)
    ]


async def my_status(services: Services, attendee: Attendee) -> MyStatus:
    event, plan, checked_in_at, announcements = await asyncio.gather(
        read_event(services.elasticsearch),
        read_plan(services.elasticsearch),
        read_check_in(services.elasticsearch, attendee.id),
        list_announcements(services.elasticsearch),
    )
    assignment = None
    if (group := my_group(plan, attendee.id)) is not None:
        note = next((n for n in group.member_notes if n.attendee_id == attendee.id), None)
        seats = {table.number: table.seats for table in event.tables}
        assignment = MyAssignment(
            table_number=group.table_number,
            tablemates=[
                Tablemate(
                    first_name=first_name(m), role=m.role, company=m.company, interests=m.interests
                )
                for m in group.members
                if m.id != attendee.id
            ],
            summary=note.summary if note else group.summary,
            starters=note.starters if note else group.starters,
            table_summary=group.summary,
            table_starters=group.starters,
            narrative_source=group.narrative_source,
            table_name=group.name,
            tagline=group.tagline,
            topics=group.topics,
            seats=seats.get(group.table_number) if group.table_number is not None else None,
            lunch_time=event.lunch_time,
            location=event.location,
        )
    return MyStatus(
        attendee=attendee,
        status=enrollment_status(event, plan, utc_now()),
        enrollment_closes_at=event.enrollment_closes_at,
        assignment=assignment,
        checked_in_at=checked_in_at if assignment else None,
        announcements=inbox(announcements, assignment is not None, utc_now()),
    )


@app.get("/api/me", response_model=MyStatus)
async def me(services: Dependencies, x_attendee_token: AttendeeToken = None) -> MyStatus:
    """An attendee's own enrollment and, once published, their table."""
    return await my_status(services, await current_attendee(services, x_attendee_token))


@app.put("/api/me", response_model=MyStatus)
async def update_me(
    enrollment: Enrollment, services: Dependencies, x_attendee_token: AttendeeToken = None
) -> MyStatus:
    """Edit your own answers while enrollment is open. Your ID and token stay the same."""
    async with services.mutation_lock:
        attendee = await current_attendee(services, x_attendee_token)
        await require_open_enrollment(services)
        updated = Attendee(id=attendee.id, **enrollment.model_dump())
        await update_attendee(services.elasticsearch, updated)
        logger.info("Enrollment updated.")
    return await my_status(services, updated)


@app.post("/api/me/check-in", response_model=MyStatus)
async def check_in(services: Dependencies, x_attendee_token: AttendeeToken = None) -> MyStatus:
    """Mark yourself seated at your published table. Repeating it is harmless."""
    attendee = await current_attendee(services, x_attendee_token)
    if my_group(await read_plan(services.elasticsearch), attendee.id) is None:
        raise HTTPException(409, "Your table isn't published yet.")
    await save_check_in(services.elasticsearch, attendee.id, utc_now())
    check_in_counter.add(1)
    return await my_status(services, attendee)


async def add_demo_attendees(services: Services, attendees: list[Attendee]) -> int:
    async with services.mutation_lock:
        event = await require_open_enrollment(services)
        existing_ids = {attendee.id for attendee in await list_attendees(services.elasticsearch)}
        incoming = [attendee for attendee in attendees if attendee.id not in existing_ids]
        if not await validate_capacity(
            services.elasticsearch, len(incoming), capacity=event.capacity
        ):
            raise HTTPException(
                409, f"Adding demo attendees would exceed the {event.capacity} available seats."
            )
        for attendee in incoming:
            await services.elasticsearch.create(
                index=ATTENDEES_INDEX,
                id=attendee.id,
                document=attendee.model_dump(),
            )
        await services.elasticsearch.indices.refresh(index=ATTENDEES_INDEX)
        logger.info("Demo attendees added: count=%d", len(incoming))
        return len(incoming)


@app.post("/api/demo/seed", dependencies=HostOnly)
async def seed(services: Dependencies) -> dict[str, int]:
    return {"created": await add_demo_attendees(services, seed_attendees())}


class DemoRequest(BaseModel):
    source: Literal["seed", "fixture", "generated"] = "fixture"
    count: int = Field(default=24, ge=1, le=100)
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    themes: list[str] | None = None
    use_llm: bool = False


class DemoResult(BaseModel):
    created: int
    generator: str


@app.post("/api/host/demo/attendees", response_model=DemoResult, dependencies=HostOnly)
async def host_demo_attendees(request: DemoRequest, services: Dependencies) -> DemoResult:
    if request.source == "seed":
        attendees, generator = seed_attendees(), "seed"
    elif request.source == "fixture":
        attendees, generator = [entry.attendee for entry in load_fixture().attendees], "fixture"
    else:
        unknown = [key for key in request.themes or [] if key not in THEMES_BY_KEY]
        if unknown:
            raise HTTPException(422, f"Unknown themes: {', '.join(unknown)}.")
        seed = request.seed if request.seed is not None else secrets.randbelow(1_000_000)
        if request.use_llm and services.llm is not None:
            try:
                people = await generate_with_llm(services.llm, request.count, seed, request.themes)
            except LLMError as error:
                logger.warning("LLM persona generation failed (%s).", type(error).__name__)
                raise HTTPException(502, f"AI persona generation failed: {error}") from error
            generator = f"llm:{services.llm.provider}"
        else:
            people, generator = generate_offline(request.count, seed, request.themes), "offline"
        attendees = [person.attendee for person in people]
    return DemoResult(created=await add_demo_attendees(services, attendees), generator=generator)


def llm_status() -> LLMStatus | None:
    config = LLMConfig.from_env()
    embeddings = EmbeddingConfig.from_env()
    if config.provider == "none" and config.problem is None and not embeddings.enabled:
        return None
    return LLMStatus(
        provider=config.provider,
        model=config.model,
        enabled=config.enabled,
        problem=config.problem,
        embedding_model=embeddings.model if embeddings.enabled else None,
    )


@app.get("/api/host/state", response_model=HostState, dependencies=HostOnly)
async def host_state(services: Dependencies) -> HostState:
    attendees, plan, event = await asyncio.gather(
        list_attendees(services.elasticsearch),
        read_plan(services.elasticsearch),
        read_event(services.elasticsearch),
    )
    dashboard_metrics.record_event(len(attendees), event, published=plan is not None)
    published_run_id = plan.run_id if plan else None
    now = utc_now()
    return HostState(
        event=event,
        status=enrollment_status(event, plan, now),
        capacity=event.capacity,
        seat_count=event.seat_count,
        attendees=attendees,
        runs=await list_runs(services.elasticsearch, published_run_id),
        published_run_id=published_run_id,
        server_time=now,
        llm=llm_status(),
        auto=await read_auto_state(services.elasticsearch),
        check_ins=await list_check_ins(services.elasticsearch),
        announcements=await list_announcements(services.elasticsearch),
    )


@app.put("/api/host/event", response_model=EventConfig, dependencies=HostOnly)
async def update_event(event: EventConfig, services: Dependencies) -> EventConfig:
    async with services.mutation_lock:
        if await read_plan(services.elasticsearch) is not None:
            raise HTTPException(409, "Groups are already published; the setup is locked.")
        enrolled = len(await list_attendees(services.elasticsearch))
        if enrolled > event.capacity:
            raise HTTPException(
                409, f"{enrolled} people are enrolled but these tables seat {event.capacity}."
            )
        return await save_event(services.elasticsearch, event)


@app.delete("/api/host/runs", dependencies=HostOnly)
async def discard_drafts(services: Dependencies) -> dict[str, int]:
    """Discard every unpublished draft run. The published run is never removed."""
    async with services.mutation_lock:
        plan = await read_plan(services.elasticsearch)
        published = plan.run_id if plan else None
        deleted = await delete_draft_runs(services.elasticsearch, published)
    logger.info("Draft runs discarded: count=%d", deleted)
    return {"deleted": deleted}


@app.post(
    "/api/host/announcements",
    response_model=Announcement,
    status_code=201,
    dependencies=HostOnly,
)
async def announce(request: AnnouncementRequest, services: Dependencies) -> Announcement:
    """Post an update to attendees' in-app inbox, now or at a scheduled time."""
    attendees, plan = await asyncio.gather(
        list_attendees(services.elasticsearch), read_plan(services.elasticsearch)
    )
    if request.audience == "matched" and plan is None:
        raise HTTPException(409, "Publish tables before messaging matched attendees.")
    now = utc_now()
    if request.send_at is not None and request.send_at <= now:
        request = request.model_copy(update={"send_at": None})
    recipients = (
        sum(len(group.members) for group in plan.groups)
        if request.audience == "matched" and plan
        else len(attendees)
    )
    if not recipients:
        raise HTTPException(409, "No attendees are in this audience yet.")
    announcement = Announcement(
        **request.model_dump(), id=uuid4().hex[:12], created_at=now, recipients=recipients
    )
    await save_announcement(services.elasticsearch, announcement)
    logger.info("Announcement saved: audience=%s", request.audience)
    return announcement


class RunOptions(BaseModel):
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    use_llm: bool = True
    use_semantic: bool = True


async def create_run(
    services: Services, seed: int, use_llm: bool = True, use_semantic: bool = True
) -> MatchRun:
    started = perf_counter()
    event = await read_event(services.elasticsearch)
    request = RunRequest(
        tables=event.tables,
        min_table_size=event.min_table_size,
        seed=seed,
        use_llm=use_llm,
        use_semantic=use_semantic,
    )
    response = await call_matcher(services, "POST", "/runs", request.model_dump(mode="json"))
    run_id = uuid4().hex[:12]
    plan = MatchPlan.model_validate(response.json()).model_copy(update={"run_id": run_id})
    run = MatchRun(
        id=run_id,
        created_at=utc_now(),
        seed=seed,
        tables=event.tables,
        min_table_size=event.min_table_size,
        plan=plan,
    )
    await save_run(services.elasticsearch, run)
    dashboard_metrics.record_run(plan, perf_counter() - started)
    run_counter.add(1)
    logger.info("Match run created: attendees=%d groups=%d", plan.attendee_count, len(plan.groups))
    return run


@app.post("/api/host/runs", response_model=MatchRun, status_code=201, dependencies=HostOnly)
async def produce_matches(options: RunOptions, services: Dependencies) -> MatchRun:
    if await read_plan(services.elasticsearch) is not None:
        raise HTTPException(409, "Groups are already published for this lunch.")
    seed = options.seed if options.seed is not None else secrets.randbelow(1_000_000)
    return await create_run(services, seed, options.use_llm, options.use_semantic)


@app.get("/api/host/runs/{run_id}", response_model=MatchRun, dependencies=HostOnly)
async def get_run(run_id: str, services: Dependencies) -> MatchRun:
    run = await read_run(services.elasticsearch, run_id)
    if run is None:
        raise HTTPException(404, "That match run doesn't exist.")
    return run


def answers(attendee: Attendee) -> dict[str, Any]:
    """What the attendee told us, without fields the matcher adds."""
    return attendee.model_dump(include=set(Enrollment.model_fields))


async def publish(services: Services, run: MatchRun) -> MatchPlan:
    """Publish a run, unless it no longer matches the room or would leave someone out."""
    existing = await read_plan(services.elasticsearch)
    if existing is not None:
        if existing.run_id == run.id:
            return existing
        raise HTTPException(409, "A different match run is already published.")
    # The cutoff watcher publishes a run it made earlier; it may have been discarded since.
    if await read_run(services.elasticsearch, run.id) is None:
        raise HTTPException(409, "That match run was discarded. Produce a new run to publish.")
    event = await read_event(services.elasticsearch)
    if run.tables != event.tables or run.min_table_size != event.min_table_size:
        raise HTTPException(
            409, "The table setup changed after this run. Produce a new run to publish."
        )
    seated = {member.id: member for group in run.plan.groups for member in group.members}
    attendees = {attendee.id: attendee for attendee in await list_attendees(services.elasticsearch)}
    if set(seated) != set(attendees):
        missing = len(set(attendees) - set(seated))
        raise HTTPException(
            409,
            f"{missing} attendee(s) enrolled after this run, and some may have left. "
            "Produce a new run so everyone gets a seat.",
        )
    # Runs keep a copy of each profile, so an edit after the run would publish old answers.
    if any(answers(seated[key]) != answers(attendee) for key, attendee in attendees.items()):
        raise HTTPException(
            409, "Someone edited their answers after this run. Produce a new run to publish."
        )
    plan = await save_plan(services.elasticsearch, run.plan)
    if plan.run_id != run.id:
        raise HTTPException(409, "A different match run is already published.")
    publish_counter.add(1)
    logger.info("Match run published.")
    return plan


@app.post("/api/host/runs/{run_id}/judge", response_model=Judgement, dependencies=HostOnly)
async def judge_run(run_id: str, services: Dependencies) -> Judgement:
    """Ask the LLM judge to score a run's tables against random seating. Read-only."""
    run = await read_run(services.elasticsearch, run_id)
    if run is None:
        raise HTTPException(404, "That match run doesn't exist.")
    request = JudgeRequest(plan=run.plan, seed=run.seed)
    response = await call_matcher(services, "POST", "/judge", request.model_dump(mode="json"))
    judgement = Judgement.model_validate(response.json())
    await save_judgement(services.elasticsearch, run_id, judgement)
    return judgement


@app.post("/api/host/runs/{run_id}/publish", response_model=MatchPlan, dependencies=HostOnly)
async def publish_run(run_id: str, services: Dependencies) -> MatchPlan:
    async with services.mutation_lock:
        run = await read_run(services.elasticsearch, run_id)
        if run is None:
            raise HTTPException(404, "That match run doesn't exist.")
        return await publish(services, run)


@app.post("/api/matches", response_model=MatchPlan, dependencies=HostOnly)
async def generate_matches(services: Dependencies) -> MatchPlan:
    """Organizer shortcut: produce a deterministic run and publish it immediately."""
    async with services.mutation_lock:
        existing = await read_plan(services.elasticsearch)
        if existing is not None:
            return existing
        # No AI here: this holds the mutation lock and must stay deterministic.
        run = await create_run(services, DEFAULT_SEED, use_llm=False, use_semantic=False)
        return await publish(services, run)


async def call_matcher(
    services: Services, method: str, path: str, json: dict[str, Any] | None = None
) -> httpx.Response:
    try:
        response = await services.matcher.request(method, path, json=json)
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


async def run_due_cutoff(services: Services, now: datetime) -> AutoMatchState | None:
    """Match (and optionally publish) once when an auto-match cutoff has passed.

    The outcome is stored per cutoff, so restarts catch up without running twice.
    Changing the cutoff makes the new time eligible again.
    """
    event = await read_event(services.elasticsearch)
    cutoff = event.enrollment_closes_at
    if not event.auto_match or cutoff is None or now < cutoff:
        return None
    if await read_plan(services.elasticsearch) is not None:
        return None
    previous = await read_auto_state(services.elasticsearch)
    if previous is not None and previous.cutoff == cutoff:
        return None

    # Let any enrollment that started before the cutoff finish writing, so the
    # snapshot includes everyone. No new enrollment can start after the cutoff.
    async with services.mutation_lock:
        pass

    run: MatchRun | None = None
    try:
        run = await create_run(services, secrets.randbelow(1_000_000))
        published = False
        if event.auto_publish:
            async with services.mutation_lock:
                await publish(services, run)
            published = True
        message = (
            "Matched and published automatically at the cutoff."
            if published
            else "Matched automatically at the cutoff. Review the draft, then publish."
        )
        state = AutoMatchState(
            cutoff=cutoff,
            outcome="done",
            run_id=run.id,
            published=published,
            message=message,
            at=now,
        )
    except HTTPException as error:
        state = AutoMatchState(
            cutoff=cutoff,
            outcome="failed",
            run_id=run.id if run else None,
            message=f"Automatic matching failed: {error.detail}",
            at=now,
        )
    await save_auto_state(services.elasticsearch, state)
    logger.info(
        "Cutoff auto-match finished: outcome=%s published=%s", state.outcome, state.published
    )
    return state


async def refresh_dashboard_metrics(services: Services) -> None:
    attendees, plan, event, auto = await asyncio.gather(
        list_attendees(services.elasticsearch),
        read_plan(services.elasticsearch),
        read_event(services.elasticsearch),
        read_auto_state(services.elasticsearch),
    )
    dashboard_metrics.record_event(len(attendees), event, published=plan is not None)
    dashboard_metrics.record_assignment_health(attendees, event, plan, auto, utc_now())


async def cutoff_watcher(services: Services, poll_seconds: float) -> None:
    """Background loop in the single API worker. See agent.md before adding workers."""
    while True:
        try:
            await run_due_cutoff(services, utc_now())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Storage hiccups are retried on the next poll; nothing personal is logged.
            logger.warning("Cutoff check failed (%s); retrying.", type(error).__name__)
        try:
            await refresh_dashboard_metrics(services)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Assignment telemetry refresh failed (%s); retrying.", type(error).__name__
            )
        await asyncio.sleep(poll_seconds)
