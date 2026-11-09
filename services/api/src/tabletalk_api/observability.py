"""Aggregate operational metrics for the Dash0 demo dashboards; no attendee data."""

from collections.abc import Sequence
from datetime import datetime

from opentelemetry.metrics import Meter
from tabletalk_shared.models import Attendee, AutoMatchState, EventConfig, MatchPlan


class DashboardMetrics:
    def __init__(self, meter: Meter) -> None:
        self.enrolled = meter.create_gauge(
            "tabletalk.event.enrolled",
            description="Current enrolled attendees, including demo data.",
        )
        self.capacity = meter.create_gauge("tabletalk.event.capacity")
        self.remaining = meter.create_gauge("tabletalk.event.remaining_seats")
        self.published = meter.create_gauge(
            "tabletalk.event.published",
            description="1 when assignments are published, otherwise 0.",
        )
        self.unassigned = meter.create_gauge(
            "tabletalk.event.unassigned",
            description="Enrolled attendees absent from the published plan; drafts do not count.",
        )
        self.singletons = meter.create_gauge(
            "tabletalk.event.singleton_tables",
            description="Published tables containing exactly one attendee.",
        )
        self.cutoff = meter.create_gauge(
            "tabletalk.event.cutoff_timestamp",
            unit="s",
            description="Enrollment cutoff as Unix seconds; 0 means no cutoff configured.",
        )
        self.snapshot = meter.create_gauge(
            "tabletalk.event.snapshot_timestamp",
            unit="s",
            description="Unix time of the last successful background assignment-health snapshot.",
        )
        self.auto_failed = meter.create_gauge(
            "tabletalk.event.auto_match_failed",
            description="1 for an unresolved automatic-match failure at the current cutoff.",
        )
        self.duration = meter.create_histogram(
            "tabletalk.matching.duration",
            unit="s",
            description="Successful run duration including matcher computation and persistence.",
            explicit_bucket_boundaries_advisory=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40, 60, 120),
        )
        score_bounds = tuple(index / 10 for index in range(11))
        self.pair_affinity = meter.create_histogram(
            "tabletalk.matching.pair_affinity",
            description="Per-run mean pair affinity, compared with random seating.",
            explicit_bucket_boundaries_advisory=score_bounds,
        )
        self.weakest_fit = meter.create_histogram(
            "tabletalk.matching.weakest_fit",
            description="Per-run weakest attendee's best-tablemate fit.",
            explicit_bucket_boundaries_advisory=score_bounds,
        )
        self.learn_coverage = meter.create_histogram(
            "tabletalk.matching.learn_coverage",
            description="Per-run Learn coverage; no observation when no learners are present.",
            explicit_bucket_boundaries_advisory=score_bounds,
        )
        self.seating_completeness = meter.create_histogram(
            "tabletalk.matching.seating_completeness",
            description="Fraction of the run's attendee snapshot seated.",
            explicit_bucket_boundaries_advisory=score_bounds,
        )
        self.table_size = meter.create_histogram(
            "tabletalk.matching.table_size",
            description="Attendees per table in successfully saved runs.",
            explicit_bucket_boundaries_advisory=(2, 3, 4, 5, 6, 8, 10, 12, 16, 20),
        )

    def record_event(self, enrolled: int, event: EventConfig, *, published: bool) -> None:
        self.enrolled.set(enrolled)
        self.capacity.set(event.capacity)
        self.remaining.set(event.capacity - enrolled)
        self.published.set(int(published))

    def record_assignment_health(
        self,
        attendees: Sequence[Attendee],
        event: EventConfig,
        plan: MatchPlan | None,
        auto: AutoMatchState | None,
        now: datetime,
    ) -> None:
        groups = plan.groups if plan is not None else []
        assigned = {member.id for group in groups for member in group.members}
        self.unassigned.set(len({attendee.id for attendee in attendees} - assigned))
        self.singletons.set(sum(len(group.members) == 1 for group in groups))
        cutoff = event.enrollment_closes_at
        self.cutoff.set(cutoff.timestamp() if cutoff is not None else 0)
        self.auto_failed.set(
            int(
                event.auto_match
                and plan is None
                and auto is not None
                and auto.cutoff == cutoff
                and auto.outcome == "failed"
            )
        )
        self.snapshot.set(now.timestamp())

    def record_run(self, plan: MatchPlan, elapsed_seconds: float) -> None:
        attributes = {"retrieval": plan.retrieval or "unspecified"}
        self.duration.record(elapsed_seconds, attributes)
        for group in plan.groups:
            self.table_size.record(len(group.members), attributes)
        scores = plan.metrics
        if scores is None:
            return
        for strategy, pair_fit, weakest in (
            ("matched", scores.average_pair_affinity, scores.weakest_best_tablemate),
            (
                "random",
                scores.baseline_average_pair_affinity,
                scores.baseline_weakest_best_tablemate,
            ),
        ):
            comparison = {**attributes, "strategy": strategy}
            self.pair_affinity.record(pair_fit, comparison)
            self.weakest_fit.record(weakest, comparison)
        if scores.learn_coverage is not None:
            self.learn_coverage.record(scores.learn_coverage, attributes)
        if scores.attendee_count > 0:
            self.seating_completeness.record(
                scores.seated_count / scores.attendee_count, attributes
            )
