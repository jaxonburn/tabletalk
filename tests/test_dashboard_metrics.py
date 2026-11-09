from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import Gauge, Histogram, InMemoryMetricReader
from tabletalk_api.observability import DashboardMetrics
from tabletalk_api.seed import seed_attendees
from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
from tabletalk_matcher.matching import build_plan
from tabletalk_shared.models import (
    DEFAULT_TABLES,
    AutoMatchState,
    EventConfig,
    LunchGroup,
    MatchPlan,
)


@pytest.fixture
def instruments() -> Iterator[tuple[DashboardMetrics, InMemoryMetricReader]]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    try:
        yield DashboardMetrics(provider.get_meter("test")), reader
    finally:
        provider.shutdown()


def test_event_snapshot_includes_demo_attendees_and_updates_after_publication(
    instruments: tuple[DashboardMetrics, InMemoryMetricReader],
) -> None:
    telemetry, reader = instruments
    event = EventConfig()
    telemetry.record_event(14, event, published=False)
    telemetry.record_event(15, event, published=True)
    data = reader.get_metrics_data()
    assert data is not None
    values = {
        metric.name: metric.data.data_points[0].value
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if isinstance(metric.data, Gauge)
    }
    assert values == {
        "tabletalk.event.enrolled": 15,
        "tabletalk.event.capacity": event.capacity,
        "tabletalk.event.remaining_seats": event.capacity - 15,
        "tabletalk.event.published": 1,
    }


@pytest.mark.parametrize("published", [False, True])
def test_assignment_health_uses_actual_published_membership_not_declared_plan_counts(
    instruments: tuple[DashboardMetrics, InMemoryMetricReader], published: bool
) -> None:
    telemetry, reader = instruments
    attendees = seed_attendees()[:4]
    now = datetime(2026, 11, 9, tzinfo=UTC)
    event = EventConfig(enrollment_closes_at=now)
    incomplete = MatchPlan(
        attendee_count=4,
        generated_at=now.isoformat(),
        groups=[
            LunchGroup(id="one", members=attendees[:2], reasons=[]),
            LunchGroup(id="two", members=[attendees[2]], reasons=[]),
        ],
    )
    telemetry.record_assignment_health(
        attendees, event, incomplete if published else None, None, now
    )
    data = reader.get_metrics_data()
    assert data is not None
    values = {
        metric.name: metric.data.data_points[0].value
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if isinstance(metric.data, Gauge)
    }
    assert values["tabletalk.event.unassigned"] == (1 if published else 4)
    assert values["tabletalk.event.singleton_tables"] == (1 if published else 0)
    assert values["tabletalk.event.cutoff_timestamp"] == now.timestamp()
    assert values["tabletalk.event.snapshot_timestamp"] == now.timestamp()
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                assert all(not point.attributes for point in metric.data.data_points)


@pytest.mark.parametrize("current_cutoff,enabled", [(True, True), (False, True), (True, False)])
def test_auto_match_alert_only_tracks_failure_for_current_enabled_cutoff(
    instruments: tuple[DashboardMetrics, InMemoryMetricReader],
    current_cutoff: bool,
    enabled: bool,
) -> None:
    telemetry, reader = instruments
    now = datetime(2026, 11, 9, tzinfo=UTC)
    event = EventConfig(enrollment_closes_at=now, auto_match=enabled)
    failed = AutoMatchState(
        cutoff=now if current_cutoff else now - timedelta(hours=1),
        outcome="failed",
        message="Cannot seat this layout.",
        at=now,
    )
    telemetry.record_assignment_health(seed_attendees(), event, None, failed, now)
    data = reader.get_metrics_data()
    assert data is not None
    values = {
        metric.name: metric.data.data_points[0].value
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if isinstance(metric.data, Gauge)
    }
    assert values["tabletalk.event.auto_match_failed"] == int(current_cutoff and enabled)


@pytest.mark.parametrize("coverage", [None, 0.0, 1.0])
def test_saved_run_records_quality_baseline_and_skips_undefined_learn_coverage(
    instruments: tuple[DashboardMetrics, InMemoryMetricReader], coverage: float | None
) -> None:
    telemetry, reader = instruments
    attendees = seed_attendees()[:6]
    affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
    plan = build_plan(attendees, DEFAULT_TABLES, affinity)
    assert plan.metrics is not None
    plan.metrics.learn_coverage = coverage
    plan.retrieval = "bm25"
    telemetry.record_run(plan, 1.25)
    data = reader.get_metrics_data()
    assert data is not None
    recorded = {
        metric.name: metric.data.data_points
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if isinstance(metric.data, Histogram)
    }
    pair_scores = recorded["tabletalk.matching.pair_affinity"]
    assert [(dict(point.attributes or {}), point.sum) for point in pair_scores] == [
        (
            {"retrieval": "bm25", "strategy": "matched"},
            plan.metrics.average_pair_affinity,
        ),
        (
            {"retrieval": "bm25", "strategy": "random"},
            plan.metrics.baseline_average_pair_affinity,
        ),
    ]
    assert recorded["tabletalk.matching.duration"][0].sum == 1.25
    assert recorded["tabletalk.matching.seating_completeness"][0].sum == 1
    assert recorded["tabletalk.matching.table_size"][0].count == len(plan.groups)
    if coverage is None:
        assert "tabletalk.matching.learn_coverage" not in recorded
    else:
        assert recorded["tabletalk.matching.learn_coverage"][0].sum == coverage
    for points in recorded.values():
        for point in points:
            assert set(point.attributes or {}) <= {"retrieval", "strategy"}
