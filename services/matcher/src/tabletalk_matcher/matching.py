"""Turn a snapshot of attendees into a validated, explained lunch plan."""

import random
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import combinations
from statistics import mean

from opentelemetry import trace
from tabletalk_shared.models import (
    Attendee,
    LunchGroup,
    MatchMetrics,
    MatchPlan,
    TableSpec,
)

from tabletalk_matcher.affinity import Affinity, learn_text
from tabletalk_matcher.narratives import (
    member_note,
    table_reasons,
    table_starters,
    table_summary,
    teaching_overlap,
)
from tabletalk_matcher.optimizer import PairScore, assign_tables
from tabletalk_matcher.sizing import TablePlan, plan_table_sizes

tracer = trace.get_tracer(__name__)
BASELINE_SAMPLES = 20


class InvalidPlanError(RuntimeError):
    """The optimizer produced seating that breaks a hard rule. Never publish it."""


def build_plan(
    attendees: list[Attendee],
    tables: list[TableSpec],
    affinity: Affinity,
    *,
    seed: int = 0,
    min_table_size: int = 2,
) -> MatchPlan:
    with tracer.start_as_current_span("matching.build_plan") as span:
        span.set_attribute("matching.attendee_count", len(attendees))
        by_id = {attendee.id: attendee for attendee in attendees}
        if len(by_id) != len(attendees):
            raise InvalidPlanError("The attendee snapshot contains duplicate IDs.")
        layout = plan_table_sizes(tables, len(attendees), min_table_size)
        with tracer.start_as_current_span("matching.optimize"):
            seating = assign_tables(
                sorted(by_id), [table.size for table in layout], affinity, seed=seed
            )
        validate_seating(sorted(by_id), layout, seating, tables)

        groups = []
        for table, member_ids in zip(layout, seating, strict=True):
            members = [by_id[member_id] for member_id in member_ids]
            groups.append(
                LunchGroup(
                    id=f"table-{table.number}",
                    table_number=table.number,
                    members=members,
                    reasons=table_reasons(members),
                    summary=table_summary(members),
                    starters=table_starters(members),
                    member_notes=[member_note(member, members) for member in members],
                    narrative_source="template",
                )
            )
        metrics = measure(groups, affinity, seed)
        span.set_attribute("matching.group_count", len(groups))
    return MatchPlan(
        groups=groups,
        generated_at=datetime.now(UTC).isoformat(),
        attendee_count=len(attendees),
        seed=seed,
        metrics=metrics,
    )


def validate_seating(
    attendee_ids: Sequence[str],
    layout: Sequence[TablePlan],
    seating: Sequence[Sequence[str]],
    tables: Sequence[TableSpec],
) -> None:
    seated = [member for table in seating for member in table]
    if sorted(seated) != sorted(attendee_ids):
        raise InvalidPlanError("Every attendee must be seated exactly once.")
    seats = {table.number: table.seats for table in tables}
    for table, members in zip(layout, seating, strict=True):
        if table.number not in seats:
            raise InvalidPlanError(f"Table {table.number} does not exist.")
        if len(members) != table.size or len(members) > seats[table.number]:
            raise InvalidPlanError(f"Table {table.number} does not match its planned size.")


def measure(groups: list[LunchGroup], affinity: PairScore, seed: int) -> MatchMetrics:
    seating = [[member.id for member in group.members] for group in groups]
    sizes = [len(table) for table in seating]
    average, weakest = _seating_quality(seating, affinity)

    rng = random.Random(seed)
    everyone = sorted(member for table in seating for member in table)
    samples = []
    for _ in range(BASELINE_SAMPLES):
        rng.shuffle(everyone)
        shuffled, start = [], 0
        for size in sizes:
            shuffled.append(everyone[start : start + size])
            start += size
        samples.append(_seating_quality(shuffled, affinity))

    learners = [
        (member, group.members)
        for group in groups
        for member in group.members
        if learn_text(member)
    ]
    covered = sum(
        any(teaching_overlap(other, member) for other in members if other is not member)
        for member, members in learners
    )
    return MatchMetrics(
        attendee_count=len(everyone),
        seated_count=len(everyone),
        table_sizes=sizes,
        average_pair_affinity=round(average, 4),
        weakest_best_tablemate=round(weakest, 4),
        learn_coverage=round(covered / len(learners), 4) if learners else None,
        baseline_average_pair_affinity=round(mean(sample[0] for sample in samples), 4),
        baseline_weakest_best_tablemate=round(mean(sample[1] for sample in samples), 4),
    )


def _seating_quality(seating: list[list[str]], affinity: PairScore) -> tuple[float, float]:
    pairs = [affinity(left, right) for table in seating for left, right in combinations(table, 2)]
    best = [
        max(affinity(member, other) for other in table if other != member)
        for table in seating
        for member in table
        if len(table) > 1
    ]
    return (mean(pairs) if pairs else 0.0, min(best) if best else 0.0)
