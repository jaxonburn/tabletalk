import pytest
from pydantic import ValidationError
from tabletalk_api.seed import seed_attendees
from tabletalk_matcher.matching import build_plan, explain_pair, group_sizes
from tabletalk_shared.models import Attendee, Enrollment, Goal


@pytest.mark.parametrize("count", [2, 4, 5, 9, 13, 100])
def test_balanced_groups_never_leave_singletons(count: int) -> None:
    sizes = group_sizes(count)
    assert sum(sizes) == count
    assert min(sizes) >= 2
    assert max(sizes) <= 4
    assert max(sizes) - min(sizes) <= 1


@pytest.mark.parametrize("count", [0, 1])
def test_matching_requires_at_least_two_people(count: int) -> None:
    with pytest.raises(ValueError, match="At least two"):
        group_sizes(count)


async def test_every_attendee_is_assigned_once_and_search_excludes_assigned_people() -> None:
    attendees = seed_attendees()[:9]
    already_seen: set[str] = set()

    async def find_candidate(members: list[Attendee], excluded: set[str]) -> Attendee:
        assert already_seen <= excluded
        assert {member.id for member in members} <= excluded
        candidate = next(attendee for attendee in attendees if attendee.id not in excluded)
        already_seen.update(excluded | {candidate.id})
        return candidate

    plan = await build_plan(attendees, find_candidate)
    ids = [member.id for group in plan.groups for member in group.members]
    assert len(ids) == len(set(ids)) == 9
    assert set(ids) == {attendee.id for attendee in attendees}
    assert [len(group.members) for group in plan.groups] == [3, 3, 3]


async def test_search_cannot_assign_someone_outside_the_snapshot() -> None:
    attendees = seed_attendees()

    async def invalid_candidate(_members: list[Attendee], _excluded: set[str]) -> Attendee:
        return attendees[0]

    with pytest.raises(RuntimeError, match="outside the matching snapshot"):
        await build_plan(attendees[:2], invalid_candidate)


def test_explanations_include_real_shared_topics_and_complementary_goals() -> None:
    left, right = seed_attendees()[:2]
    left.goals = ["Learn"]
    right.goals = ["Share something I've learned"]
    reason = explain_pair(left, right)
    assert reason is not None
    assert "kubernetes" in reason.text
    assert "Learn / Share something I've learned" in reason.text
    assert "observability" in reason.text


def test_unrelated_profiles_do_not_get_invented_explanations() -> None:
    left, right = seed_attendees()[0], seed_attendees()[8]
    left.goals = ["Learn"]
    right.goals = ["Meet connections"]
    assert explain_pair(left, right) is None


def test_enrollment_normalizes_and_deduplicates_interests() -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id"})
    data["interests"] = [" Kubernetes ", "KUBERNETES", "Python"]
    assert Enrollment.model_validate(data).interests == ["kubernetes", "python"]


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
        exclude={"id", "conference_id", "focus_goal", "goal_answers"}
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
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data | changes)


def test_existing_frontend_enrollment_does_not_require_step_two_yet() -> None:
    data = seed_attendees()[0].model_dump(
        exclude={"id", "conference_id", "focus_goal", "goal_answers"}
    )
    enrollment = Enrollment.model_validate(data)
    assert enrollment.focus_goal is None
    assert enrollment.goal_answers == {}


def test_enrollment_requires_a_lunch_goal() -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id", "goals"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data)


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
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data | changes)
