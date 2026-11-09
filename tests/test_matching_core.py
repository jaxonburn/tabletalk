from itertools import combinations
from unittest.mock import AsyncMock, Mock

import pytest
from elasticsearch import AsyncElasticsearch
from fastapi.testclient import TestClient
from tabletalk_matcher import main
from tabletalk_matcher.affinity import (
    Affinity,
    affinity_query,
    lexical_directed_scores,
    shared_phrases,
)
from tabletalk_matcher.matching import InvalidPlanError, build_plan, validate_seating
from tabletalk_matcher.optimizer import assign_tables, table_score
from tabletalk_matcher.sizing import SeatingError, TablePlan, plan_table_sizes
from tabletalk_shared.fixtures import load_fixture
from tabletalk_shared.models import Attendee, MatchPlan, TableSpec


def tables(*seats: int) -> list[TableSpec]:
    return [TableSpec(number=index, seats=count) for index, count in enumerate(seats, start=1)]


def fixture_plan(seed: int = 0) -> tuple[MatchPlan, dict[str, str]]:
    fixture = load_fixture()
    attendees = [entry.attendee for entry in fixture.attendees]
    affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
    themes = {entry.attendee.id: entry.theme for entry in fixture.attendees}
    return build_plan(attendees, fixture.tables, affinity, seed=seed), themes


class TestTableSizing:
    @pytest.mark.parametrize("count", range(2, 31))
    def test_mixed_tables_seat_everyone_within_capacity(self, count: int) -> None:
        layout = tables(4, 6, 8, 4, 8)
        plans = plan_table_sizes(layout, count)
        seats = {table.number: table.seats for table in layout}
        assert sum(plan.size for plan in plans) == count
        assert all(2 <= plan.size <= seats[plan.number] for plan in plans)
        assert [plan.number for plan in plans] == sorted(plan.number for plan in plans)

    def test_prefers_fewer_larger_tables(self) -> None:
        assert plan_table_sizes(tables(2, 10), 3) == [TablePlan(number=2, seats=10, size=3)]

    def test_balances_sizes_across_used_tables(self) -> None:
        sizes = [plan.size for plan in plan_table_sizes(tables(5, 5, 5), 14)]
        assert sizes == [5, 5, 4]

    def test_reports_infeasible_minimum(self) -> None:
        with pytest.raises(SeatingError, match="minimum"):
            plan_table_sizes(tables(4, 4), 5, min_table_size=3)

    def test_rejects_more_people_than_seats(self) -> None:
        with pytest.raises(SeatingError, match="exceed"):
            plan_table_sizes(tables(4, 4), 9)

    def test_rejects_duplicate_table_numbers(self) -> None:
        with pytest.raises(SeatingError, match="unique"):
            plan_table_sizes([TableSpec(number=1, seats=4), TableSpec(number=1, seats=4)], 4)


class TestOptimizer:
    def planted(self) -> tuple[list[str], Affinity]:
        clusters = [["a1", "a2", "a3"], ["b1", "b2", "b3"], ["c1", "c2", "c3"]]
        scores = {frozenset(pair): 1.0 for cluster in clusters for pair in combinations(cluster, 2)}
        # Interleave IDs so a naive in-order fill would mix every cluster.
        ids = [member for trio in zip(*clusters, strict=True) for member in trio]
        return ids, Affinity(scores)

    def test_recovers_planted_clusters(self) -> None:
        ids, affinity = self.planted()
        seating = assign_tables(ids, [3, 3, 3], affinity)
        assert sorted(seating) == [["a1", "a2", "a3"], ["b1", "b2", "b3"], ["c1", "c2", "c3"]]

    def test_same_seed_gives_the_same_seating(self) -> None:
        attendees = [entry.attendee for entry in load_fixture().attendees]
        affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
        ids = [attendee.id for attendee in attendees]
        first = assign_tables(ids, [5, 5, 4], affinity, seed=3)
        assert assign_tables(list(reversed(ids)), [5, 5, 4], affinity, seed=3) == first

    def test_restarts_never_do_worse_than_a_single_start(self) -> None:
        attendees = [entry.attendee for entry in load_fixture().attendees]
        affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
        ids = [attendee.id for attendee in attendees]

        def total(seating: list[list[str]]) -> float:
            return sum(table_score(table, affinity, 1.0) for table in seating)

        single = assign_tables(ids, [4, 4, 3, 3], affinity, restarts=0)
        assert total(assign_tables(ids, [4, 4, 3, 3], affinity)) >= total(single)

    def test_rejects_sizes_that_do_not_seat_everyone(self) -> None:
        with pytest.raises(ValueError, match="exactly once"):
            assign_tables(["a", "b", "c"], [2], Affinity({}))


class TestValidation:
    layout = [TablePlan(number=1, seats=4, size=2), TablePlan(number=2, seats=4, size=2)]

    def test_rejects_duplicates_and_missing_people(self) -> None:
        with pytest.raises(InvalidPlanError, match="exactly once"):
            validate_seating(["a", "b", "c", "d"], self.layout, [["a", "b"], ["a", "c"]], [])

    def test_rejects_unknown_tables(self) -> None:
        with pytest.raises(InvalidPlanError, match="does not exist"):
            validate_seating(["a", "b", "c", "d"], self.layout, [["a", "b"], ["c", "d"]], tables(4))


class TestFixtureQuality:
    def test_three_table_fixture_seats_everyone_in_the_suggested_layout(self) -> None:
        plan, _ = fixture_plan()
        assert [len(group.members) for group in plan.groups] == [5, 5, 4]
        assert plan.metrics is not None
        assert plan.metrics.seated_count == plan.attendee_count == 14

    def test_matching_regroups_planted_themes(self) -> None:
        plan, themes = fixture_plan()
        for theme in ("platform", "security", "data-ml"):
            tables_for_theme = [
                sum(themes[member.id] == theme for member in group.members) for group in plan.groups
            ]
            assert max(tables_for_theme) >= 3, (theme, tables_for_theme)

    def test_matching_beats_random_seating(self) -> None:
        plan, _ = fixture_plan()
        metrics = plan.metrics
        assert metrics is not None
        assert metrics.average_pair_affinity > metrics.baseline_average_pair_affinity * 1.5
        assert metrics.weakest_best_tablemate > metrics.baseline_weakest_best_tablemate
        assert metrics.learn_coverage is not None and metrics.learn_coverage >= 0.75

    def test_every_person_gets_grounded_notes(self) -> None:
        plan, _ = fixture_plan()
        everyone = {member.name.split()[0] for group in plan.groups for member in group.members}
        for group in plan.groups:
            tablemates = {member.name.split()[0] for member in group.members}
            assert group.summary
            assert len(group.starters) == 3
            assert {note.attendee_id for note in group.member_notes} == {
                member.id for member in group.members
            }
            for note in group.member_notes:
                assert note.summary
                assert 2 <= len(note.starters) <= 3
                text = " ".join([note.summary, *note.starters])
                # Notes may only mention people who are actually at this table.
                assert {name for name in everyone if name in text} <= tablemates


class TestSharedPhrases:
    def test_prefers_the_longest_shared_phrase_as_written(self) -> None:
        offer = "Running Backstage and measuring developer experience"
        ask = "How are teams measuring developer experience on a platform?"
        assert shared_phrases(offer, ask) == ["measuring developer experience"]

    def test_ignores_lone_words_that_change_meaning_across_answers(self) -> None:
        assert shared_phrases("Golden datasets for evals", "Designing a golden path") == []

    def test_keeps_acronyms_and_folds_plurals(self) -> None:
        assert shared_phrases("SBOMs for AI agents", "Who signs their SBOM with AI?") == [
            "SBOMs",
            "AI",
        ]

    def test_does_not_join_words_across_sentences(self) -> None:
        assert shared_phrases("We use Kafka. Streaming works", "kafka streaming") == [
            "Kafka",
            "streaming",
        ]


class TestElasticsearchQuery:
    def test_query_is_scoped_to_the_snapshot_and_excludes_the_attendee(self) -> None:
        attendee = load_fixture().attendees[1].attendee
        query = affinity_query(attendee, ["fx-03", "fx-01"])["bool"]
        assert {"terms": {"id": ["fx-01", "fx-03"]}} in query["filter"]
        assert query["must_not"] == [{"term": {"id": attendee.id}}]
        fields = {next(iter(clause["match"])) for clause in query["should"] if "match" in clause}
        assert "goal_answers.Share something I've learned" in fields

    def test_affinity_is_symmetric_and_bounded(self) -> None:
        attendees = [entry.attendee for entry in load_fixture().attendees]
        affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
        for left, right in combinations(attendees, 2):
            assert affinity(left.id, right.id) == affinity(right.id, left.id)
            assert 0.0 <= affinity(left.id, right.id) <= 1.0


def test_runs_endpoint_scores_with_elasticsearch_using_the_requested_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attendees = [entry.attendee for entry in load_fixture().attendees[:6]]
    lexical = lexical_directed_scores(attendees)
    searched: list[str] = []

    async def search(**kwargs: object) -> dict[str, object]:
        query = kwargs["query"]
        assert isinstance(query, dict)
        me = query["bool"]["must_not"][0]["term"]["id"]
        searched.append(me)
        hits = [{"_id": other, "_score": score} for other, score in lexical[me].items()]
        return {"hits": {"hits": hits}}

    client = Mock(spec=AsyncElasticsearch)
    client.search = AsyncMock(side_effect=search)
    monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=attendees))
    main.app.dependency_overrides[main.get_client] = lambda: client
    request = {"tables": [{"number": 8, "seats": 3}, {"number": 9, "seats": 3}], "seed": 5}
    try:
        response = TestClient(main.app).post("/runs", json=request)
        too_small = TestClient(main.app).post("/runs", json={"tables": [{"number": 1, "seats": 4}]})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert sorted(set(searched)) == sorted(attendee.id for attendee in attendees)
    plan = MatchPlan.model_validate(response.json())
    assert [group.table_number for group in plan.groups] == [8, 9]
    assert [len(group.members) for group in plan.groups] == [3, 3]
    assert plan.seed == 5
    assert too_small.status_code == 409
    assert "exceed" in too_small.json()["detail"]


def test_attendee_fixture_is_valid_enrollment_data() -> None:
    fixture = load_fixture()
    assert len(fixture.attendees) == 14
    assert all(isinstance(entry.attendee, Attendee) for entry in fixture.attendees)
    assert len({entry.attendee.id for entry in fixture.attendees}) == 14
