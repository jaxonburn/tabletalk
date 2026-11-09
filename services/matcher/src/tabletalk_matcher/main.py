import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from elastic_transport import TransportError
from elasticsearch import ApiError, AsyncElasticsearch
from fastapi import Depends, FastAPI, HTTPException, Request
from opentelemetry import metrics, trace
from tabletalk_shared.llm import (
    Embedder,
    EmbeddingConfig,
    LLMClient,
    LLMConfig,
    LLMError,
    create_embedder,
    create_llm_client,
)
from tabletalk_shared.models import (
    MAX_ATTENDEES,
    Attendee,
    Judgement,
    JudgeRequest,
    MatchPlan,
    RunRequest,
)
from tabletalk_shared.storage import (
    ATTENDEES_INDEX,
    check_readiness,
    create_client,
    ensure_indices,
    list_attendees,
)
from tabletalk_shared.telemetry import configure_telemetry, register_storage_errors

from tabletalk_matcher.affinity import Affinity, affinity_query, combine_directed, knn_query
from tabletalk_matcher.enrichment import enrich
from tabletalk_matcher.judge import judge_plan
from tabletalk_matcher.llm_notes import narrate_plan
from tabletalk_matcher.matching import build_plan
from tabletalk_matcher.sizing import SeatingError

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)
AFFINITY_CONCURRENCY = 8


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = LLMConfig.from_env()
    if config.problem:
        logger.warning("AI table notes are disabled: %s", config.problem)
    async with create_client() as client, httpx.AsyncClient() as http:
        await ensure_indices(client)
        app.state.elasticsearch = client
        app.state.llm = create_llm_client(config, http)
        app.state.embedder = create_embedder(EmbeddingConfig.from_env(), http)
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


@app.post("/runs", response_model=MatchPlan)
async def compute_run(request: RunRequest, client: Client, http_request: Request) -> MatchPlan:
    """Compute a plan for the current snapshot. The API decides whether to save or publish it."""
    attendees = await list_attendees(client)
    if len(attendees) < 2:
        raise HTTPException(409, "Enroll at least two attendees before making lunch groups.")
    if len(attendees) > MAX_ATTENDEES:
        raise HTTPException(409, f"This demo supports up to {MAX_ATTENDEES} attendees.")

    llm: LLMClient | None = getattr(http_request.app.state, "llm", None)
    embedder: Embedder | None = getattr(http_request.app.state, "embedder", None)
    vectors: dict[str, dict[str, list[float]]] = {}
    retrieval = "bm25"
    keyword_attendees = [attendee.model_copy(update={"topic_tags": []}) for attendee in attendees]
    # Tags saved by earlier semantic runs must not leak into a keyword-only run.
    attendees = keyword_attendees
    if request.use_semantic and (embedder is not None or llm is not None):
        try:
            attendees, enrichment = await enrich(
                client, keyword_attendees, embedder=embedder, llm=llm
            )
            vectors, retrieval = enrichment.vectors, enrichment.retrieval
        except (ApiError, TransportError) as error:
            # Hosted clusters can differ (privileges, Serverless features): keep BM25.
            logger.warning("Enrichment storage failed (%s); using BM25 only.", type(error).__name__)
            attendees = keyword_attendees
    try:
        affinity = await elasticsearch_affinity(client, attendees, vectors)
    except (ApiError, TransportError) as error:
        if not vectors:
            raise
        logger.warning("Vector search failed (%s); using BM25 only.", type(error).__name__)
        retrieval = retrieval.replace("+vectors", "")
        affinity = await elasticsearch_affinity(client, attendees, None)
    try:
        plan = build_plan(
            attendees,
            request.tables,
            affinity,
            seed=request.seed,
            min_table_size=request.min_table_size,
        )
    except SeatingError as error:
        raise HTTPException(409, str(error)) from error
    plan = plan.model_copy(update={"retrieval": retrieval})
    if request.use_llm and llm is not None:
        plan = await narrate_plan(plan, llm)
    group_counter.add(len(plan.groups))
    logger.info(
        "Lunch groups computed: attendees=%d groups=%d llm_tables=%d",
        plan.attendee_count,
        len(plan.groups),
        sum(group.narrative_source == "llm" for group in plan.groups),
    )
    return plan


@app.post("/judge", response_model=Judgement)
async def judge(request: JudgeRequest, http_request: Request) -> Judgement:
    """Score a plan's tables (and a random seating of the same people) with the LLM."""
    llm: LLMClient | None = getattr(http_request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(409, "The AI judge needs an LLM provider (set LLM_PROVIDER).")
    try:
        return await judge_plan(request.plan, llm, seed=request.seed)
    except LLMError as error:
        raise HTTPException(502, f"The AI judge failed: {error}") from error


async def elasticsearch_affinity(
    client: AsyncElasticsearch,
    attendees: list[Attendee],
    vectors: dict[str, dict[str, list[float]]] | None = None,
) -> Affinity:
    snapshot_ids = [attendee.id for attendee in attendees]
    limit = asyncio.Semaphore(AFFINITY_CONCURRENCY)

    async def scores_for(attendee: Attendee) -> tuple[str, dict[str, float]]:
        async with limit:
            response = await client.search(
                index=ATTENDEES_INDEX,
                query=affinity_query(attendee, snapshot_ids),
                size=len(snapshot_ids),
                source=False,
            )
        return attendee.id, {
            hit["_id"]: float(hit["_score"] or 0) for hit in response["hits"]["hits"]
        }

    async def semantic_for(attendee: Attendee) -> tuple[str, dict[str, float]]:
        clauses = knn_query(attendee.id, (vectors or {}).get(attendee.id, {}), snapshot_ids)
        if not clauses:
            return attendee.id, {}
        async with limit:
            response = await client.search(
                index=ATTENDEES_INDEX, knn=clauses, size=len(snapshot_ids), source=False
            )
        return attendee.id, {
            hit["_id"]: float(hit["_score"] or 0) for hit in response["hits"]["hits"]
        }

    with tracer.start_as_current_span("matching.affinity") as span:
        span.set_attribute("matching.attendee_count", len(attendees))
        span.set_attribute("matching.semantic", bool(vectors))
        directed = dict(await asyncio.gather(*(scores_for(attendee) for attendee in attendees)))
        semantic = (
            dict(await asyncio.gather(*(semantic_for(attendee) for attendee in attendees)))
            if vectors
            else None
        )
    return Affinity.from_directed(attendees, combine_directed(directed, semantic))
