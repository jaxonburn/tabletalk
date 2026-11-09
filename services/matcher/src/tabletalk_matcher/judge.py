"""LLM-as-judge: a second opinion on each table's conversation potential.

The judge only scores; it never changes seats. The same people are also seated at
random (same table sizes) and judged identically, so hosts can see whether the matcher
beats chance by the judge's standard too.
"""

import asyncio
import json
import random
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from opentelemetry import trace
from tabletalk_shared.llm import STRING, LLMClient, LLMError, strict_object
from tabletalk_shared.models import Attendee, Judgement, MatchPlan, TableVerdict

from tabletalk_matcher.affinity import connect_text, learn_text, share_text
from tabletalk_matcher.llm_notes import clip

tracer = trace.get_tracer(__name__)
JUDGE_PROMPT_VERSION = "table-judge-v2"

JUDGE_SYSTEM_PROMPT = """\
You evaluate lunch tables at a tech conference for conversation potential.
Everyone works in tech, so broad overlap ("cloud", "AI", "DevOps") is expected and is
NOT enough for a high score. First find specific pairings: one person's stated
experience (share) directly answers another person's stated question (learn), or two
people have hands-on experience with the same specific technology or problem.
Score 1-5 using only the facts provided:
5 = two or more specific pairings, and everyone at the table has someone to talk to;
4 = one or more specific pairings, but someone is left without a clear connection;
3 = a shared specific technology or problem, but nobody directly answers a question;
2 = only broad overlap;
1 = nothing in common.
Be strict and consistent. Rationale: one sentence naming the strongest specific pairing
by first name, or saying there is none."""

JUDGE_SCHEMA = strict_object({"score": {"type": "integer"}, "rationale": STRING})


def _table_payload(members: list[Attendee]) -> str:
    people = [
        {
            "first_name": member.name.split()[0],
            "role": member.role,
            "interests": member.interests,
            "learn": learn_text(member),
            "share": share_text(member),
            "connect": connect_text(member),
        }
        for member in members
    ]
    return "Score this table:\n" + json.dumps(people, ensure_ascii=False, indent=2)


def parse_verdict(data: dict[str, Any], table_number: int | None) -> TableVerdict:
    score = data.get("score")
    rationale = data.get("rationale")
    if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
        raise LLMError("The judge returned a score outside 1-5.")
    if not isinstance(rationale, str) or not rationale.strip():
        raise LLMError("The judge returned no rationale.")
    return TableVerdict(table_number=table_number, score=score, rationale=clip(rationale, 240))


async def _judge_tables(
    client: LLMClient, tables: list[tuple[int | None, list[Attendee]]], concurrency: int
) -> list[TableVerdict]:
    limit = asyncio.Semaphore(concurrency)

    async def run(number: int | None, members: list[Attendee]) -> TableVerdict | None:
        async with limit:
            for _ in range(2):
                try:
                    data = await client.generate_json(
                        system=JUDGE_SYSTEM_PROMPT,
                        user=_table_payload(members),
                        schema_name="table_verdict",
                        schema=JUDGE_SCHEMA,
                        max_tokens=300,
                    )
                    return parse_verdict(data, number)
                except LLMError:
                    continue
        return None

    results = await asyncio.gather(*(run(number, members) for number, members in tables))
    return [verdict for verdict in results if verdict is not None]


def random_tables(plan: MatchPlan, seed: int) -> list[list[Attendee]]:
    people = [member for group in plan.groups for member in group.members]
    random.Random(seed).shuffle(people)
    tables, start = [], 0
    for group in plan.groups:
        tables.append(people[start : start + len(group.members)])
        start += len(group.members)
    return tables


async def judge_plan(
    plan: MatchPlan, client: LLMClient, *, seed: int = 0, concurrency: int = 6
) -> Judgement:
    with tracer.start_as_current_span("matching.judge") as span:
        span.set_attribute("llm.model", client.model)
        matched, baseline = await asyncio.gather(
            _judge_tables(
                client,
                [(group.table_number, group.members) for group in plan.groups],
                concurrency,
            ),
            _judge_tables(client, [(None, table) for table in random_tables(plan, seed)], 2),
        )
        span.set_attribute("matching.judged_tables", len(matched))
    if not matched:
        raise LLMError("The judge could not score any table.")
    return Judgement(
        model=f"{client.provider}:{client.model}",
        prompt_version=JUDGE_PROMPT_VERSION,
        tables=matched,
        average=round(mean(verdict.score for verdict in matched), 2),
        baseline_average=(
            round(mean(verdict.score for verdict in baseline), 2) if baseline else None
        ),
        judged_at=datetime.now(UTC),
    )
