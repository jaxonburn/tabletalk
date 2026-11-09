from unittest.mock import AsyncMock, Mock

import pytest
from elasticsearch import AsyncElasticsearch
from fastapi import Request
from pydantic import ValidationError
from tabletalk_api.seed import seed_attendees
from tabletalk_matcher import main
from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
from tabletalk_matcher.matching import InvalidPlanError, build_plan
from tabletalk_matcher.narratives import explain_pair
from tabletalk_matcher.sizing import SeatingError, plan_table_sizes
from tabletalk_shared.models import (
    DEFAULT_TABLES,
    Attendee,
    Enrollment,
    Goal,
    MatchPlan,
    RunRequest,
)


async def test_rejected_plan_emits_alert_counter_without_returning_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=seed_attendees()[:4]))
    monkeypatch.setattr(main, "elasticsearch_affinity", AsyncMock(return_value=Affinity({})))
    monkeypatch.setattr(
        main, "build_plan", Mock(side_effect=InvalidPlanError("Every attendee exactly once."))
    )
    rejected = Mock()
    groups = Mock()
    monkeypatch.setattr(main, "invalid_plan_counter", rejected)
    monkeypatch.setattr(main, "group_counter", groups)
    with pytest.raises(InvalidPlanError):
        await main.compute_run(
            RunRequest(tables=DEFAULT_TABLES, use_llm=False, use_semantic=False),
            Mock(spec=AsyncElasticsearch),
            Request({"type": "http", "app": main.app}),
        )
    rejected.add.assert_called_once_with(1)
    groups.add.assert_not_called()


@pytest.mark.parametrize("count", [2, 4, 5, 9, 13, 100])
def test_balanced_groups_never_leave_singletons(count: int) -> None:
    sizes = [table.size for table in plan_table_sizes(DEFAULT_TABLES, count)]
    assert sum(sizes) == count
    assert min(sizes) >= 2
    assert max(sizes) <= 4
    assert max(sizes) - min(sizes) <= 1


@pytest.mark.parametrize("count", [0, 1])
def test_matching_requires_at_least_two_people(count: int) -> None:
    with pytest.raises(SeatingError, match="At least 2"):
        plan_table_sizes(DEFAULT_TABLES, count)


def test_every_attendee_is_assigned_exactly_once() -> None:
    attendees = seed_attendees()[:9]
    affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
    plan = build_plan(attendees, DEFAULT_TABLES, affinity)
    ids = [member.id for group in plan.groups for member in group.members]
    assert len(ids) == len(set(ids)) == 9
    assert set(ids) == {attendee.id for attendee in attendees}
    assert [len(group.members) for group in plan.groups] == [3, 3, 3]
    assert [group.table_number for group in plan.groups] == [1, 2, 3]
    assert plan.metrics is not None
    assert plan.metrics.seated_count == 9


def test_explanations_include_real_shared_topics_and_complementary_goals() -> None:
    left, right = seed_attendees()[:2]
    left.goals = ["Learn"]
    right.goals = ["Share something I've learned"]
    reason = explain_pair(left, right)
    assert reason is not None
    assert "kubernetes" in reason.text
    assert "Alex Chen wants to learn and Sam Rivera wants to share." in reason.text
    assert "observability" in reason.text


def test_unrelated_profiles_do_not_get_invented_explanations() -> None:
    left, right = seed_attendees()[0], seed_attendees()[8]
    left.goals = ["Learn"]
    right.goals = ["Meet connections"]
    assert explain_pair(left, right) is None


def test_enrollment_normalizes_and_deduplicates_interests() -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags"})
    data["interests"] = [" Kubernetes ", "KUBERNETES", "Python"]
    assert Enrollment.model_validate(data).interests == ["kubernetes", "python"]


def test_stored_attendees_and_plans_migrate_legacy_goals() -> None:
    stored_attendee = seed_attendees()[0].model_dump(exclude={"focus_goal", "goal_answers"})
    stored_attendee["goals"] = ["Meet peers"]

    attendee = Attendee.model_validate(stored_attendee)
    assert attendee.goals == ["Meet connections"]

    plan = MatchPlan.model_validate(
        {
            "groups": [{"id": "group-1", "members": [stored_attendee], "reasons": []}],
            "generated_at": "2026-11-01T12:00:00Z",
            "attendee_count": 1,
        }
    )
    assert plan.groups[0].members[0].goals == ["Meet connections"]


@pytest.mark.parametrize(
    "goals",
    [
        ["Learn"],
        ["Learn", "Share something I've learned"],
        ["Learn", "Share something I've learned", "Meet connections"],
    ],
)
def test_enrollment_accepts_single_and_multiple_lunch_goals(goals: list[Goal]) -> None:
    data = seed_attendees()[0].model_dump(
        exclude={"id", "conference_id", "topic_tags", "focus_goal", "goal_answers"}
    )
    assert Enrollment.model_validate(data | {"goals": goals}).goals == goals


def test_seed_profiles_include_single_multiple_and_all_lunch_goals() -> None:
    profiles = seed_attendees()
    assert {len(profile.goals) for profile in profiles} == {1, 2, 3}
    assert any(profile.goals == ["Learn"] for profile in profiles)
    assert any(profile.goals == ["Share something I've learned"] for profile in profiles)
    assert any(profile.goals == ["Meet connections"] for profile in profiles)


def test_seed_profiles_answer_every_selected_goal_and_choose_a_focus() -> None:
    for profile in seed_attendees():
        assert profile.focus_goal in profile.goals
        assert set(profile.goal_answers) == set(profile.goals)
        assert all(answer.strip() for answer in profile.goal_answers.values())


@pytest.mark.parametrize(
    "changes",
    [
        {"focus_goal": "Meet connections"},
        {"goal_answers": {}},
        {"goal_answers": {"Meet connections": "I'd enjoy talking about Kubernetes."}},
        {"focus_goal": None},
    ],
)
def test_goal_responses_require_a_selected_focus_and_an_answer_for_each_goal(
    changes: dict[str, object],
) -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data | changes)


def test_existing_frontend_enrollment_does_not_require_step_two_yet() -> None:
    data = seed_attendees()[0].model_dump(
        exclude={"id", "conference_id", "topic_tags", "focus_goal", "goal_answers"}
    )
    enrollment = Enrollment.model_validate(data)
    assert enrollment.focus_goal is None
    assert enrollment.goal_answers == {}


def test_enrollment_requires_a_lunch_goal() -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags", "goals"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data)


@pytest.mark.parametrize("goals", [[], ["Hire"], ["Meet peers"], ["Unknown goal"]])
def test_new_enrollments_do_not_coerce_missing_or_legacy_goals(goals: list[str]) -> None:
    # Mirrors the current frontend payload, which omits focus_goal and goal_answers.
    data = seed_attendees()[0].model_dump(
        exclude={"id", "conference_id", "topic_tags", "focus_goal", "goal_answers"}
    )
    with pytest.raises(ValidationError, match="goals"):
        Enrollment.model_validate(data | {"goals": goals})


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "  "},
        {"interests": []},
        {"interests": ["a" * 51]},
        {"goals": []},
        {"goals": ["Hire"]},
        {"goals": ["Unknown goal"]},
        {"lunch_slot": "another-conference-lunch"},
        {"can_help": "x" * 501},
    ],
)
def test_invalid_enrollment_is_rejected(changes: dict[str, object]) -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data | changes)
