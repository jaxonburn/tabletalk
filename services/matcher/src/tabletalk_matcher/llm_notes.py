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
from tabletalk_matcher.narratives import MAX_TABLE_NAME, first_name

tracer = trace.get_tracer(__name__)

PROMPT_VERSION = "table-notes-v2"
MAX_SUMMARY = 300
MAX_STARTER = 200
MAX_TAGLINE_LINE = 28

SYSTEM_PROMPT = """\
You write warm, specific lunch-table introductions for attendees at a tech conference.
Use ONLY the facts provided about the people at this table. Never invent employers,
projects, numbers, or personal details. Refer to people by first name only, and only
to people at this table. Keep a friendly, practical tone with no hype or flattery.
Everything you write is shown directly to attendees."""

USER_INSTRUCTIONS = """\
Write notes for this lunch table.
- table_name: a short, friendly name for the table built from what it shares, such as
  "The Kubernetes table" or "The Platform builders' table" (under 36 characters, no names).
- tagline: exactly 2 very short lines that set the tone, like ["Small bets.", "Big ideas."]
  (each under 24 characters).
- table_summary: 1-2 sentences on what this table has in common or can trade.
- conversation_starters: exactly 3 open questions the whole table could discuss.
- members: one entry for EVERY attendee_id below, each with
  - summary: 1-2 sentences in second person ("You and Priya both...") about who to
    talk to and why, based on the evidence;
  - starters: 2 short personal openers, each naming a specific tablemate.
Keep each summary under 280 characters and each starter under 180 characters."""

NARRATIVE_SCHEMA = strict_object(
    {
        "table_name": STRING,
        "tagline": STRING_LIST,
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
    name: str = ""
    tagline: tuple[str, ...] = ()


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

    result = TableNotes(
        summary=clip(summary, MAX_SUMMARY),
        starters=starters,
        member_notes=notes,
        name=_table_name(data.get("table_name"), group),
        tagline=_tagline(data.get("tagline"), group),
    )
    _reject_outsiders(result, group.members, outsider_names)
    return result


def _table_name(value: Any, group: LunchGroup) -> str:
    # A weak name isn't worth discarding good notes for; keep the template's instead.
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_TABLE_NAME:
        return group.name
    return " ".join(value.split())


def _tagline(value: Any, group: LunchGroup) -> tuple[str, ...]:
    lines = [
        " ".join(line.split()) for line in value or [] if isinstance(line, str) and line.strip()
    ]
    if len(lines) < 2 or any(len(line) > MAX_TAGLINE_LINE for line in lines[:2]):
        return tuple(group.tagline)
    return tuple(lines[:2])


def _reject_outsiders(notes: TableNotes, members: list[Attendee], outsiders: set[str]) -> None:
    # Names are matched as written (capitalized) so ordinary words like "grace" or
    # "will" don't count as mentioning Grace or Will from another table.
    forbidden = outsiders - {first_name(member) for member in members}
    if not forbidden:
        return
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(forbidden))) + r")\b")
    texts = [notes.summary, *notes.starters, notes.name, *notes.tagline]
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
        # Template names are already distinct; an AI name is used only if it stays that way.
        names = {group.name for group in plan.groups}
        for group, task in zip(plan.groups, tasks, strict=True):
            if task in done and task.exception() is None:
                notes = task.result()
                name = group.name
                if notes.name and notes.name not in names:
                    names.add(notes.name)
                    name = notes.name
                group = group.model_copy(
                    update={
                        "summary": notes.summary,
                        "starters": notes.starters,
                        "member_notes": notes.member_notes,
                        "narrative_source": "llm",
                        "name": name,
                        "tagline": list(notes.tagline) or group.tagline,
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
