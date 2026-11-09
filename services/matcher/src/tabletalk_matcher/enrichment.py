"""Step 8 enrichment: embeddings and LLM topic tags, computed once and stored in Elasticsearch.

Enrichment runs at match time for anyone in the snapshot who lacks it for the current
model, so enrollment never waits on a third-party API. Any provider failure leaves the
run on plain BM25 instead of failing it.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from elasticsearch import AsyncElasticsearch
from opentelemetry import trace
from tabletalk_shared.llm import STRING, STRING_LIST, Embedder, LLMClient, LLMError, strict_object
from tabletalk_shared.models import Attendee
from tabletalk_shared.storage import ATTENDEES_INDEX, VECTOR_FIELDS

from tabletalk_matcher.affinity import connect_text, learn_text, share_text

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

TAGS_PROMPT_VERSION = "topic-tags-v1"
TAG_BATCH = 10
MAX_TAGS = 6
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 +#./-]{0,38}[a-z0-9+#]$")

TAGS_SYSTEM_PROMPT = """\
You turn conference attendees' free-text answers into short canonical topic tags so
people who mean the same thing match even when they word it differently.
Rules: 2-6 tags per attendee; lowercase; 1-4 words each; prefer widely used names
("kubernetes autoscaling", "vulnerability management", "llm evaluation", "developer
experience"); no names of people or companies; no generic words like "learning"."""

TAGS_SCHEMA = strict_object(
    {
        "attendees": {
            "type": "array",
            "items": strict_object({"attendee_id": STRING, "tags": STRING_LIST}),
        }
    }
)


def topic_text(attendee: Attendee) -> str:
    return " ".join([*attendee.interests, connect_text(attendee)]).strip()


@dataclass
class Enrichment:
    """What the matcher can use for this snapshot."""

    vectors: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)
    embedded: int = 0
    tagged: int = 0

    @property
    def retrieval(self) -> str:
        parts = ["bm25"]
        if self.vectors:
            parts.append("vectors")
        if self.tags:
            parts.append("tags")
        return "+".join(parts)


def clean_tags(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    tags = []
    for item in raw:
        if not isinstance(item, str):
            continue
        tag = " ".join(item.lower().split())
        if TAG_PATTERN.match(tag) and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


async def _stored(client: AsyncElasticsearch, ids: list[str]) -> dict[str, dict[str, Any]]:
    response = await client.search(
        index=ATTENDEES_INDEX,
        query={"terms": {"id": ids}},
        size=len(ids),
        # Elasticsearch 9 leaves dense vectors out of _source unless asked explicitly.
        source={
            "includes": ["id", "embedding_model", "tags_model", "topic_tags", *VECTOR_FIELDS],
            "exclude_vectors": False,
        },
    )
    return {hit["_id"]: hit["_source"] for hit in response["hits"]["hits"]}


async def _bulk_update(client: AsyncElasticsearch, docs: dict[str, dict[str, Any]]) -> None:
    if not docs:
        return
    operations: list[dict[str, Any]] = []
    for attendee_id, doc in docs.items():
        operations += [{"update": {"_index": ATTENDEES_INDEX, "_id": attendee_id}}, {"doc": doc}]
    response = await client.bulk(operations=operations, refresh="wait_for")
    if response.get("errors"):
        raise RuntimeError("Some enrichment updates were rejected by Elasticsearch.")


async def _embed_missing(
    attendees: list[Attendee], stored: dict[str, dict[str, Any]], embedder: Embedder
) -> dict[str, dict[str, Any]]:
    texts: list[str] = []
    slots: list[tuple[str, str]] = []
    for attendee in attendees:
        if stored.get(attendee.id, {}).get("embedding_model") == embedder.model:
            continue
        for vector_field, text in (
            ("learn_vector", learn_text(attendee)),
            ("share_vector", share_text(attendee)),
            ("topic_vector", topic_text(attendee)),
        ):
            if text:
                texts.append(text[:2000])
                slots.append((attendee.id, vector_field))
    if not texts:
        return {}
    vectors = await embedder.embed(texts)
    updates: dict[str, dict[str, Any]] = {}
    for (attendee_id, vector_field), vector in zip(slots, vectors, strict=True):
        updates.setdefault(attendee_id, {"embedding_model": embedder.model})[vector_field] = vector
    return updates


async def _tag_batch(llm: LLMClient, batch: list[Attendee]) -> dict[str, list[str]]:
    payload = [
        {
            "attendee_id": attendee.id,
            "interests": attendee.interests,
            "learn": learn_text(attendee),
            "share": share_text(attendee),
            "connect": connect_text(attendee),
        }
        for attendee in batch
    ]
    data = await llm.generate_json(
        system=TAGS_SYSTEM_PROMPT,
        user="Tag each attendee:\n" + json.dumps(payload, ensure_ascii=False),
        schema_name="topic_tags",
        schema=TAGS_SCHEMA,
        max_tokens=120 * len(batch),
    )
    wanted = {attendee.id for attendee in batch}
    results: dict[str, list[str]] = {}
    for entry in data.get("attendees") or []:
        if (
            isinstance(entry, dict)
            and entry.get("attendee_id") in wanted
            and (tags := clean_tags(entry.get("tags")))
        ):
            results[entry["attendee_id"]] = tags
    return results


async def _tag_missing(
    attendees: list[Attendee], stored: dict[str, dict[str, Any]], llm: LLMClient
) -> dict[str, dict[str, Any]]:
    marker = f"{llm.model}:{TAGS_PROMPT_VERSION}"
    missing = [a for a in attendees if stored.get(a.id, {}).get("tags_model") != marker]
    batches = [missing[start : start + TAG_BATCH] for start in range(0, len(missing), TAG_BATCH)]
    limit = asyncio.Semaphore(4)

    async def run(batch: list[Attendee]) -> dict[str, list[str]]:
        async with limit:
            try:
                return await _tag_batch(llm, batch)
            except LLMError as error:
                logger.warning("Topic tagging batch failed (%s).", type(error).__name__)
                return {}

    tagged: dict[str, list[str]] = {}
    for result in await asyncio.gather(*(run(batch) for batch in batches)):
        tagged |= result
    return {
        attendee_id: {"topic_tags": tags, "tags_model": marker}
        for attendee_id, tags in tagged.items()
    }


async def enrich(
    client: AsyncElasticsearch,
    attendees: list[Attendee],
    *,
    embedder: Embedder | None,
    llm: LLMClient | None,
) -> tuple[list[Attendee], Enrichment]:
    """Backfill vectors and tags for the snapshot, then return what matching can use."""
    result = Enrichment()
    if not attendees or (embedder is None and llm is None):
        return attendees, result
    with tracer.start_as_current_span("matching.enrich") as span:
        ids = [attendee.id for attendee in attendees]
        stored = await _stored(client, ids)
        updates: dict[str, dict[str, Any]] = {}
        if embedder is not None:
            try:
                for attendee_id, doc in (await _embed_missing(attendees, stored, embedder)).items():
                    updates.setdefault(attendee_id, {}).update(doc)
            except LLMError as error:
                logger.warning("Embedding failed (%s); using BM25 only.", type(error).__name__)
        if llm is not None:
            for attendee_id, doc in (await _tag_missing(attendees, stored, llm)).items():
                updates.setdefault(attendee_id, {}).update(doc)
        await _bulk_update(client, updates)
        result.embedded = sum(1 for doc in updates.values() if "embedding_model" in doc)
        result.tagged = sum(1 for doc in updates.values() if "topic_tags" in doc)
        for attendee_id in ids:
            merged = {**stored.get(attendee_id, {}), **updates.get(attendee_id, {})}
            if embedder is not None and merged.get("embedding_model") == embedder.model:
                found = {name: merged[name] for name in VECTOR_FIELDS if merged.get(name)}
                if found:
                    result.vectors[attendee_id] = found
            if llm is not None and merged.get("topic_tags"):
                result.tags[attendee_id] = list(merged["topic_tags"])
        span.set_attribute("matching.embedded", result.embedded)
        span.set_attribute("matching.tagged", result.tagged)
    enriched = [
        attendee.model_copy(update={"topic_tags": result.tags.get(attendee.id, [])})
        for attendee in attendees
    ]
    return enriched, result
