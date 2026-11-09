import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from tabletalk_api import main
from tabletalk_api.observability import DashboardMetrics
from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
from tabletalk_matcher.matching import build_plan
from tabletalk_shared.fixtures import load_fixture
from tabletalk_shared.models import (
    Attendee,
    AutoMatchState,
    Enrollment,
    EventConfig,
    MatchRun,
)

Api = tuple[TestClient, main.Services]
NOW = datetime(2026, 11, 10, 18, 40, tzinfo=UTC)


def onboarding(**changes: object) -> dict[str, object]:
    """The new Learn / Share / Connect onboarding payload (no can_help/want_to_learn)."""
    return {
        "name": "Alex Chen",
        "role": "Platform engineer",
        "interests": ["kubernetes"],
        "goals": ["Learn", "Meet connections"],
        "focus_goal": "Learn",
        "goal_answers": {
            "Learn": "How do you autoscale Kubernetes for flash sales?",
            "Meet connections": "Happy to talk platform teams.",
        },
    } | changes


def people(count: int) -> list[Attendee]:
    return [entry.attendee for entry in load_fixture().attendees[:count]]


def run_for(attendees: list[Attendee]) -> MatchRun:
    event = EventConfig()
    affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
    plan = build_plan(attendees, event.tables, affinity).model_copy(update={"run_id": "run-1"})
    return MatchRun(
        id="run-1", created_at=NOW, seed=1, tables=event.tables, min_table_size=2, plan=plan
    )


class TestOnboarding:
    def test_answers_per_goal_replace_the_old_offer_and_need_fields(self) -> None:
        enrollment = Enrollment.model_validate(onboarding())
        assert enrollment.can_help == "" and enrollment.want_to_learn == ""

    def test_something_to_match_on_is_required(self) -> None:
        with pytest.raises(ValidationError, match="Answer each lunch goal"):
            Enrollment.model_validate(onboarding(focus_goal=None, goal_answers={}))

    def test_older_clients_with_offer_and_need_still_work(self) -> None:
        data = onboarding(focus_goal=None, goal_answers={}, can_help="Go", want_to_learn="K8s")
        assert Enrollment.model_validate(data).can_help == "Go"

    def test_conference_shares_the_goal_prompts(self, api: Api) -> None:
        client, _ = api
        prompts = client.get("/api/conference").json()["goal_prompts"]
        assert set(prompts) == {"Learn", "Share something I've learned", "Meet connections"}
        assert prompts["Learn"]["question"].endswith("?")


class TestAttendeeToken:
    def test_enrollment_returns_a_token_and_stores_only_its_link(self, api: Api) -> None:
        client, _ = api
        first = client.post("/api/enrollments", json=onboarding()).json()
        second = client.post("/api/enrollments", json=onboarding()).json()
        assert len(first["attendee_token"]) >= 40
        assert first["attendee_token"] != second["attendee_token"]
        save = getattr(main, "save_token")  # noqa: B009 - patched by the api fixture
        assert isinstance(save, AsyncMock)
        assert save.await_args_list[0].args[1:] == (first["attendee_token"], first["id"])

    def test_me_requires_a_known_token(self, api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = api
        assert client.get("/api/me").status_code == 401
        monkeypatch.setattr(main, "find_attendee_by_token", AsyncMock(return_value=None))
        assert client.get("/api/me", headers={"X-Attendee-Token": "nope"}).status_code == 401

    def test_me_before_publishing_has_no_table(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        me = people(1)[0]
        monkeypatch.setattr(main, "find_attendee_by_token", AsyncMock(return_value=me))
        body = client.get("/api/me", headers={"X-Attendee-Token": "t"}).json()
        assert body["attendee"]["id"] == me.id
        assert body["status"] == "open"
        assert body["assignment"] is None

    def test_me_after_publishing_shows_my_table_without_others_answers(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        run = run_for(people(4))
        group = run.plan.groups[0]
        me = group.members[0]
        monkeypatch.setattr(main, "find_attendee_by_token", AsyncMock(return_value=me))
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=run.plan))
        response = client.get("/api/me", headers={"X-Attendee-Token": "t"})
        body = response.json()
        assignment = body["assignment"]
        assert body["status"] == "published"
        assert assignment["table_number"] == group.table_number
        assert [mate["first_name"] for mate in assignment["tablemates"]] == [
            member.name.split()[0] for member in group.members[1:]
        ]
        my_note = next(n for n in group.member_notes if n.attendee_id == me.id)
        assert assignment["summary"] == my_note.summary
        # Tablemates are first name, role, company, and interests only. Learn questions may
        # be quoted as conversation starters, but Share/Connect answers never are.
        assert {key for mate in assignment["tablemates"] for key in mate} == {
            "first_name",
            "role",
            "company",
            "interests",
        }
        assert assignment["seats"] == next(
            t.seats for t in EventConfig().tables if t.number == group.table_number
        )
        assert assignment["location"] == EventConfig().location
        assert body["checked_in_at"] is None
        for other in group.members[1:]:
            for goal, answer in other.goal_answers.items():
                if goal != "Learn":
                    assert answer not in response.text
            assert other.id not in response.text


class TestEditAndCheckIn:
    def test_attendee_can_edit_while_open_and_keeps_their_id(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        me = people(1)[0]
        monkeypatch.setattr(main, "find_attendee_by_token", AsyncMock(return_value=me))
        changed = onboarding(name="Alex Chen", company="Adobe", interests=["observability"])
        response = client.put("/api/me", json=changed, headers={"X-Attendee-Token": "t"})
        assert response.status_code == 200
        body = response.json()
        assert body["attendee"]["id"] == me.id
        assert body["attendee"]["company"] == "Adobe"
        saved = getattr(main, "update_attendee")  # noqa: B009 - patched by the api fixture
        assert saved.await_args.args[1].id == me.id
        assert saved.await_args.args[1].interests == ["observability"]

    def test_editing_after_publishing_is_refused(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        run = run_for(people(4))
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=run.plan))
        monkeypatch.setattr(
            main, "find_attendee_by_token", AsyncMock(return_value=run.plan.groups[0].members[0])
        )
        response = client.put("/api/me", json=onboarding(), headers={"X-Attendee-Token": "t"})
        assert response.status_code == 409
        assert getattr(main, "update_attendee").await_count == 0  # noqa: B009

    def test_edit_requires_a_token(self, api: Api) -> None:
        client, _ = api
        assert client.put("/api/me", json=onboarding()).status_code == 401

    def test_check_in_needs_a_published_table(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "find_attendee_by_token", AsyncMock(return_value=people(1)[0]))
        response = client.post("/api/me/check-in", headers={"X-Attendee-Token": "t"})
        assert response.status_code == 409

    def test_check_in_is_recorded_and_returned(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        run = run_for(people(4))
        me = run.plan.groups[0].members[0]
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=run.plan))
        monkeypatch.setattr(main, "find_attendee_by_token", AsyncMock(return_value=me))
        monkeypatch.setattr(main, "read_check_in", AsyncMock(return_value=NOW))
        response = client.post("/api/me/check-in", headers={"X-Attendee-Token": "t"})
        assert response.status_code == 200
        assert response.json()["checked_in_at"].startswith("2026-11-10T18:40")
        save = getattr(main, "save_check_in")  # noqa: B009 - patched by the api fixture
        assert save.await_args.args[1] == me.id

    def test_host_sees_check_ins(self, api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = api
        monkeypatch.setattr(main, "list_runs", AsyncMock(return_value=[]))
        monkeypatch.setattr(main, "list_check_ins", AsyncMock(return_value={"a1": NOW}))
        assert "a1" in client.get("/api/host/state").json()["check_ins"]

    def test_goal_prompts_include_labels_for_the_onboarding_cards(self, api: Api) -> None:
        client, _ = api
        body = client.get("/api/conference").json()
        assert body["goal_prompts"]["Learn"]["label"]
        assert body["goal_prompts"]["Meet connections"]["short"]
        assert body["location"] == EventConfig().location


class TestAutomationSettings:
    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"auto_match": True}, "cutoff"),
            (
                {"auto_publish": True, "enrollment_closes_at": "2026-11-10T11:40:00-07:00"},
                "automatic matching",
            ),
        ],
    )
    def test_invalid_combinations_are_rejected(
        self, api: Api, payload: dict[str, object], message: str
    ) -> None:
        client, _ = api
        response = client.put("/api/host/event", json=payload)
        assert response.status_code == 422
        assert message in response.text


@pytest.fixture
def due(api: Api, monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    event = EventConfig(enrollment_closes_at=NOW, auto_match=True)
    mocks = {
        "read_event": AsyncMock(return_value=event),
        "create_run": AsyncMock(return_value=run_for(people(4))),
        "publish": AsyncMock(),
        "save_auto_state": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(main, name, mock)
    return mocks


class TestCutoffWatcher:
    async def test_does_nothing_before_the_cutoff(
        self, api: Api, due: dict[str, AsyncMock]
    ) -> None:
        assert await main.run_due_cutoff(api[1], NOW - timedelta(seconds=1)) is None
        due["create_run"].assert_not_awaited()

    async def test_does_nothing_unless_auto_match_is_on(
        self, api: Api, due: dict[str, AsyncMock]
    ) -> None:
        due["read_event"].return_value = EventConfig(enrollment_closes_at=NOW)
        assert await main.run_due_cutoff(api[1], NOW) is None
        due["create_run"].assert_not_awaited()

    async def test_makes_a_draft_at_the_cutoff(self, api: Api, due: dict[str, AsyncMock]) -> None:
        state = await main.run_due_cutoff(api[1], NOW + timedelta(seconds=5))
        assert state is not None
        assert state.outcome == "done" and state.run_id == "run-1" and not state.published
        due["publish"].assert_not_awaited()
        due["save_auto_state"].assert_awaited_once()

    async def test_publishes_when_asked(self, api: Api, due: dict[str, AsyncMock]) -> None:
        due["read_event"].return_value = EventConfig(
            enrollment_closes_at=NOW, auto_match=True, auto_publish=True
        )
        state = await main.run_due_cutoff(api[1], NOW)
        assert state is not None and state.published
        due["publish"].assert_awaited_once()

    async def test_runs_once_per_cutoff_even_after_a_restart(
        self, api: Api, due: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        done = AutoMatchState(cutoff=NOW, outcome="done", run_id="run-1", message="ok", at=NOW)
        monkeypatch.setattr(main, "read_auto_state", AsyncMock(return_value=done))
        assert await main.run_due_cutoff(api[1], NOW + timedelta(hours=1)) is None
        due["create_run"].assert_not_awaited()

    async def test_a_new_cutoff_is_eligible_again(
        self, api: Api, due: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        earlier = NOW - timedelta(hours=1)
        old = AutoMatchState(cutoff=earlier, outcome="failed", message="x", at=earlier)
        monkeypatch.setattr(main, "read_auto_state", AsyncMock(return_value=old))
        assert await main.run_due_cutoff(api[1], NOW) is not None
        due["create_run"].assert_awaited_once()

    async def test_never_runs_after_groups_are_published(
        self, api: Api, due: dict[str, AsyncMock], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=run_for(people(4)).plan))
        assert await main.run_due_cutoff(api[1], NOW) is None
        due["create_run"].assert_not_awaited()

    async def test_waits_for_in_flight_enrollments_before_matching(
        self, api: Api, due: dict[str, AsyncMock]
    ) -> None:
        services = api[1]
        await services.mutation_lock.acquire()
        task = asyncio.create_task(main.run_due_cutoff(services, NOW))
        await asyncio.sleep(0.02)
        due["create_run"].assert_not_awaited()
        services.mutation_lock.release()
        assert (await task) is not None
        due["create_run"].assert_awaited_once()

    async def test_records_failures_for_the_host(self, api: Api, due: dict[str, AsyncMock]) -> None:
        due["create_run"].side_effect = HTTPException(409, "Enroll at least two attendees.")
        state = await main.run_due_cutoff(api[1], NOW)
        assert state is not None and state.outcome == "failed"
        assert "Enroll at least two attendees." in state.message
        due["save_auto_state"].assert_awaited_once()

    async def test_the_loop_survives_errors(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = 0

        async def flaky(_services: main.Services, _now: datetime) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("storage blip")

        monkeypatch.setattr(main, "run_due_cutoff", flaky)
        refresh = AsyncMock()
        monkeypatch.setattr(main, "refresh_dashboard_metrics", refresh)
        task = asyncio.create_task(main.cutoff_watcher(api[1], 0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls >= 2
        assert refresh.await_count >= 2

    async def test_background_refresh_does_not_need_browser_requests(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attendees = people(4)
        event = EventConfig(enrollment_closes_at=NOW)
        telemetry = Mock(spec=DashboardMetrics)
        monkeypatch.setattr(main, "dashboard_metrics", telemetry)
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=attendees))
        monkeypatch.setattr(main, "read_event", AsyncMock(return_value=event))
        monkeypatch.setattr(main, "utc_now", lambda: NOW)
        await main.refresh_dashboard_metrics(api[1])
        telemetry.record_event.assert_called_once_with(4, event, published=False)
        telemetry.record_assignment_health.assert_called_once_with(
            attendees, event, None, None, NOW
        )

    async def test_telemetry_errors_do_not_block_cutoff_processing(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cutoff = AsyncMock()
        monkeypatch.setattr(main, "run_due_cutoff", cutoff)
        monkeypatch.setattr(
            main, "refresh_dashboard_metrics", AsyncMock(side_effect=RuntimeError("storage blip"))
        )
        task = asyncio.create_task(main.cutoff_watcher(api[1], 0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cutoff.await_count >= 2

    def test_host_state_reports_what_the_watcher_did(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        state = AutoMatchState(
            cutoff=NOW, outcome="done", run_id="run-1", published=True, message="ok", at=NOW
        )
        monkeypatch.setattr(main, "read_auto_state", AsyncMock(return_value=state))
        monkeypatch.setattr(main, "list_runs", AsyncMock(return_value=[]))
        auto = client.get("/api/host/state").json()["auto"]
        assert auto["outcome"] == "done" and auto["published"] is True
