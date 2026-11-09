import pytest
from pydantic import ValidationError
from tabletalk_api.seed import seed_attendees
from tabletalk_matcher.matching import build_plan, explain_pair, group_sizes
from tabletalk_shared.models import Attendee, Enrollment


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
    left.goals = ["Hire"]
    right.goals = ["Get hired"]
    reason = explain_pair(left, right)
    assert reason is not None
    assert "kubernetes" in reason.text
    assert "Hire / Get hired" in reason.text
    assert "observability" in reason.text


def test_unrelated_profiles_do_not_get_invented_explanations() -> None:
    left, right = seed_attendees()[0], seed_attendees()[8]
    left.goals = []
    right.goals = []
    assert explain_pair(left, right) is None


def test_enrollment_normalizes_and_deduplicates_interests() -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id"})
    data["interests"] = [" Kubernetes ", "KUBERNETES", "Python"]
    assert Enrollment.model_validate(data).interests == ["kubernetes", "python"]


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "  "},
        {"interests": []},
        {"interests": ["a" * 51]},
        {"goals": ["Hire", "Get hired", "Learn", "Invest"]},
        {"goals": ["Unknown goal"]},
        {"lunch_slot": "another-conference-lunch"},
        {"can_help": "x" * 501},
    ],
)
def test_invalid_enrollment_is_rejected(changes: dict[str, object]) -> None:
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id"})
    with pytest.raises(ValidationError):
        Enrollment.model_validate(data | changes)
