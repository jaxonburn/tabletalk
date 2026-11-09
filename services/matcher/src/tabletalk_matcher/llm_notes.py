"""Optional LLM-written table notes, grounded in the matcher's evidence and strictly validated.

Seating is never decided here. If the provider is missing, slow, or returns anything
that fails validation, the deterministic template notes are kept for that table.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from tabletalk_shared.llm import (
    STRING,
    STRING_LIST,
    LLMClient,
    LLMError,
    strict_object,
)
from tabletalk_shared.models import Attendee, LunchGroup, MatchPlan, MemberNote

from tabletalk_matcher.affinity import CONNECT, LEARN, SHARE
from tabletalk_matcher.narratives import first_name

tracer = trace.get_tracer(__name__)

PROMPT_VERSION = "table-notes-v1"
MAX_SUMMARY = 300
MAX_STARTER = 200

SYSTEM_PROMPT = """\
You write warm, specific lunch-table introductions for attendees at a tech conference.
Use ONLY the facts provided about the people at this table. Never invent employers,
projects, numbers, or personal details. Refer to people by first name only, and only
to people at this table. Keep a friendly, practical tone with no hype or flattery.
Everything you write is shown directly to attendees."""

USER_INSTRUCTIONS = """\
Write notes for this lunch table.
- table_summary: 1-2 sentences on what this table has in common or can trade.
- conversation_starters: exactly 3 open questions the whole table could discuss.
- members: one entry for EVERY attendee_id below, each with
  - summary: 1-2 sentences in second person ("You and Priya both...") about who to
    talk to and why, based on the evidence;
  - starters: 2 short personal openers, each naming a specific tablemate.
Keep each summary under 280 characters and each starter under 180 characters."""

NARRATIVE_SCHEMA = strict_object(
    {
        "table_summary": STRING,
        "conversation_starters": STRING_LIST,
        "members": {
            "type": "array",
            "items": strict_object(
                {"attendee_id": STRING, "summary": STRING, "starters": STRING_LIST}
            ),
        },
    }
)


class NarrativeError(ValueError):
    """The model's output broke a rule, so it must not be shown to attendees."""


@dataclass(frozen=True)
class TableNotes:
    summary: str
    starters: list[str]
    member_notes: list[MemberNote]


def table_payload(group: LunchGroup) -> str:
    members = []
    for member in group.members:
        answers = {
            "learn": member.goal_answers.get(LEARN) or member.want_to_learn,
            "share": member.goal_answers.get(SHARE) or member.can_help,
            "connect": member.goal_answers.get(CONNECT),
        }
        members.append(
            {
                "attendee_id": member.id,
                "first_name": first_name(member),
                "role": member.role,
                "interests": member.interests,
                "goals": member.goals,
                **{key: value for key, value in answers.items() if value},
            }
        )
    evidence = {
        "pair_reasons": [reason.text for reason in group.reasons],
        "draft_summary": group.summary,
    }
    payload = {"table_number": group.table_number, "members": members, "evidence": evidence}
    return f"{USER_INSTRUCTIONS}\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


def clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _texts(value: Any, *, minimum: int, maximum: int, limit: int, field: str) -> list[str]:
    if not isinstance(value, list):
        raise NarrativeError(f"{field} must be a list.")
    texts = [clip(item, limit) for item in value if isinstance(item, str) and item.strip()]
    if len(texts) < minimum:
        raise NarrativeError(f"{field} needs at least {minimum} items.")
    return list(dict.fromkeys(texts))[:maximum]


def parse_notes(data: dict[str, Any], group: LunchGroup, outsider_names: set[str]) -> TableNotes:
    """Accept the model's notes only if they fit this table exactly."""
    summary = data.get("table_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise NarrativeError("table_summary is missing.")
    starters = _texts(
        data.get("conversation_starters"),
        minimum=2,
        maximum=3,
        limit=MAX_STARTER,
        field="conversation_starters",
    )

    raw_members = data.get("members")
    if not isinstance(raw_members, list):
        raise NarrativeError("members must be a list.")
    by_id: dict[str, dict[str, Any]] = {}
    for entry in raw_members:
        if not isinstance(entry, dict) or not isinstance(entry.get("attendee_id"), str):
            raise NarrativeError("Every member note needs an attendee_id.")
        if entry["attendee_id"] in by_id:
            raise NarrativeError("An attendee has two notes.")
        by_id[entry["attendee_id"]] = entry
    expected = [member.id for member in group.members]
    if set(by_id) != set(expected):
        raise NarrativeError("Member notes must cover exactly the people at this table.")

    templates = {note.attendee_id: note for note in group.member_notes}
    notes = []
    for attendee_id in expected:
        entry = by_id[attendee_id]
        note_summary = entry.get("summary")
        if not isinstance(note_summary, str) or not note_summary.strip():
            raise NarrativeError("A member summary is missing.")
        note_starters = _texts(
            entry.get("starters"), minimum=1, maximum=3, limit=MAX_STARTER, field="starters"
        )
        if len(note_starters) < 2 and attendee_id in templates:
            note_starters += [
                starter
                for starter in templates[attendee_id].starters
                if starter not in note_starters
            ][: 2 - len(note_starters)]
        notes.append(
            MemberNote(
                attendee_id=attendee_id,
                summary=clip(note_summary, MAX_SUMMARY),
                starters=note_starters,
            )
        )

    result = TableNotes(summary=clip(summary, MAX_SUMMARY), starters=starters, member_notes=notes)
    _reject_outsiders(result, group.members, outsider_names)
    return result


def _reject_outsiders(notes: TableNotes, members: list[Attendee], outsiders: set[str]) -> None:
    # Names are matched as written (capitalized) so ordinary words like "grace" or
    # "will" don't count as mentioning Grace or Will from another table.
    forbidden = outsiders - {first_name(member) for member in members}
    if not forbidden:
        return
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(forbidden))) + r")\b")
    texts = [notes.summary, *notes.starters]
    for note in notes.member_notes:
        texts += [note.summary, *note.starters]
    if any(pattern.search(text) for text in texts):
        raise NarrativeError("The notes mention someone who isn't at this table.")


async def notes_for_table(
    client: LLMClient, group: LunchGroup, outsider_names: set[str], attempts: int = 2
) -> TableNotes:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            data = await client.generate_json(
                system=SYSTEM_PROMPT,
                user=table_payload(group),
                schema_name="table_notes",
                schema=NARRATIVE_SCHEMA,
                max_tokens=1500,
            )
            return parse_notes(data, group, outsider_names)
        except (LLMError, NarrativeError) as error:
            last_error = error
    raise NarrativeError(str(last_error))


async def narrate_plan(
    plan: MatchPlan,
    client: LLMClient,
    *,
    concurrency: int = 4,
    deadline_seconds: float = 45.0,
) -> MatchPlan:
    """Replace template notes with LLM notes where they pass validation, table by table."""
    everyone = {first_name(member) for group in plan.groups for member in group.members}
    limit = asyncio.Semaphore(concurrency)

    async def run(group: LunchGroup) -> TableNotes:
        async with limit:
            outsiders = everyone - {first_name(member) for member in group.members}
            return await notes_for_table(client, group, outsiders)

    with tracer.start_as_current_span("matching.narrate") as span:
        span.set_attribute("llm.provider", client.provider)
        span.set_attribute("llm.model", client.model)
        span.set_attribute("llm.prompt_version", PROMPT_VERSION)
        tasks = [asyncio.create_task(run(group)) for group in plan.groups]
        done, pending = await asyncio.wait(tasks, timeout=deadline_seconds)
        for task in pending:
            task.cancel()
        groups = []
        for group, task in zip(plan.groups, tasks, strict=True):
            if task in done and task.exception() is None:
                notes = task.result()
                group = group.model_copy(
                    update={
                        "summary": notes.summary,
                        "starters": notes.starters,
                        "member_notes": notes.member_notes,
                        "narrative_source": "llm",
                    }
                )
            groups.append(group)
        written = sum(group.narrative_source == "llm" for group in groups)
        span.set_attribute("matching.llm_tables", written)
        span.set_attribute("matching.template_tables", len(groups) - written)
    update: dict[str, Any] = {"groups": groups}
    if written:
        # Only label the plan as AI-written if attendees will actually see AI text.
        update |= {
            "narrative_provider": f"{client.provider}:{client.model}",
            "prompt_version": PROMPT_VERSION,
        }
    return plan.model_copy(update=update)
