import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from math import ceil
from typing import Any

from opentelemetry import trace
from tabletalk_shared.models import GROUP_SIZE, Attendee, Goal, LunchGroup, MatchPlan, MatchReason
from tabletalk_shared.storage import conference_filter

CandidateSearch = Callable[[list[Attendee], set[str]], Awaitable[Attendee]]
tracer = trace.get_tracer(__name__)
COMPLEMENTARY_GOALS: dict[Goal, Goal] = {
    "Learn": "Share something I've learned",
    "Share something I've learned": "Learn",
    "Meet connections": "Meet connections",
}
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "build",
    "can",
    "for",
    "from",
    "help",
    "how",
    "i",
    "in",
    "is",
    "it",
    "learn",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "using",
    "want",
    "with",
    "you",
}


def candidate_query(members: list[Attendee], excluded: set[str]) -> dict[str, Any]:
    should: list[dict[str, Any]] = []
    for member in members:
        should.extend(
            [
                {"terms": {"interests.exact": member.interests, "boost": 4}},
                {
                    "terms": {
                        "goals": [COMPLEMENTARY_GOALS[goal] for goal in member.goals],
                        "boost": 2,
                    }
                },
                {"match": {"interests": {"query": " ".join(member.interests), "boost": 2}}},
                {"match": {"can_help": {"query": member.want_to_learn, "boost": 3}}},
                {"match": {"want_to_learn": {"query": member.can_help, "boost": 3}}},
            ]
        )
    return {
        "bool": {
            "filter": conference_filter(),
            "must_not": [{"terms": {"id": sorted(excluded)}}],
            "should": should,
            "minimum_should_match": 0,
        }
    }


def significant_words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if word not in STOP_WORDS}


def explain_pair(left: Attendee, right: Attendee) -> MatchReason | None:
    shared = sorted(set(left.interests) & set(right.interests))
    reasons = []
    if shared:
        reasons.append(f"Shared interests: {', '.join(shared)}.")
    aligned_goals = [
        f"{goal} / {COMPLEMENTARY_GOALS[goal]}"
        for goal in left.goals
        if COMPLEMENTARY_GOALS[goal] in right.goals
    ]
    if aligned_goals:
        reasons.append(f"Aligned goals: {', '.join(aligned_goals)}.")
    for mentor, learner in ((left, right), (right, left)):
        overlap = significant_words(mentor.can_help) & significant_words(learner.want_to_learn)
        if overlap:
            reasons.append(
                f"{mentor.name} can help {learner.name} explore {', '.join(sorted(overlap)[:3])}."
            )
    if not reasons:
        return None
    return MatchReason(attendee_ids=(left.id, right.id), text=" ".join(reasons))


def group_sizes(count: int) -> list[int]:
    if count < 2:
        raise ValueError("At least two attendees are needed to make a lunch group.")
    group_count = ceil(count / GROUP_SIZE)
    size, larger_groups = divmod(count, group_count)
    return [size + (index < larger_groups) for index in range(group_count)]


async def build_plan(attendees: list[Attendee], search: CandidateSearch) -> MatchPlan:
    sizes = group_sizes(len(attendees))
    remaining = {attendee.id: attendee for attendee in attendees}
    assigned: set[str] = set()
    groups = []
    with tracer.start_as_current_span("matching.build_groups") as span:
        span.set_attribute("matching.attendee_count", len(attendees))
        for index, size in enumerate(sizes, start=1):
            seed_id = min(remaining)
            members = [remaining.pop(seed_id)]
            assigned.add(seed_id)
            while len(members) < size:
                candidate = await search(members, assigned)
                if candidate.id not in remaining:
                    raise RuntimeError("Search returned an attendee outside the matching snapshot.")
                members.append(remaining.pop(candidate.id))
                assigned.add(candidate.id)
            reasons = [
                reason
                for position, left in enumerate(members)
                for right in members[position + 1 :]
                if (reason := explain_pair(left, right)) is not None
            ]
            groups.append(LunchGroup(id=f"table-{index}", members=members, reasons=reasons))
        span.set_attribute("matching.group_count", len(groups))
    return MatchPlan(
        groups=groups,
        generated_at=datetime.now(UTC).isoformat(),
        attendee_count=len(attendees),
    )
