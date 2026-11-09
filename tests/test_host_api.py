import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from tabletalk_api import main
from tabletalk_api.observability import DashboardMetrics
from tabletalk_api.seed import seed_attendees
from tabletalk_matcher.affinity import Affinity, lexical_directed_scores
from tabletalk_matcher.matching import build_plan
from tabletalk_shared.fixtures import load_fixture
from tabletalk_shared.models import Announcement, Attendee, EventConfig, MatchRun, TableSpec

Api = tuple[TestClient, main.Services]


def people(count: int) -> list[Attendee]:
    return [entry.attendee for entry in load_fixture().attendees[:count]]


def make_run(attendees: list[Attendee], run_id: str = "run-1") -> MatchRun:
    event = EventConfig()
    affinity = Affinity.from_directed(attendees, lexical_directed_scores(attendees))
    plan = build_plan(attendees, event.tables, affinity).model_copy(update={"run_id": run_id})
    return MatchRun(
        id=run_id,
        created_at=datetime.now(UTC),
        seed=0,
        tables=event.tables,
        min_table_size=2,
        plan=plan,
    )


def enrollment() -> dict[str, object]:
    return seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags"})


class TestHostToken:
    def test_host_controls_are_open_when_no_token_is_configured(self, api: Api) -> None:
        client, _ = api
        assert client.post("/api/demo/seed").status_code == 200

    def test_configured_token_is_required(self, api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = api
        monkeypatch.setenv("HOST_TOKEN", "lunch-host")
        monkeypatch.setattr(main, "list_runs", AsyncMock(return_value=[]))
        assert client.get("/api/host/state").status_code == 401
        assert client.post("/api/demo/seed").status_code == 401
        wrong = client.get("/api/host/state", headers={"X-Host-Token": "nope"})
        assert wrong.status_code == 401
        right = client.get("/api/host/state", headers={"X-Host-Token": "lunch-host"})
        assert right.status_code == 200

    def test_attendee_endpoints_never_need_the_token(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setenv("HOST_TOKEN", "lunch-host")
        assert client.post("/api/enrollments", json=enrollment()).status_code == 201


class TestCutoff:
    def test_enrollment_closes_at_the_cutoff(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, services = api
        past = datetime.now(UTC) - timedelta(minutes=1)
        monkeypatch.setattr(
            main, "read_event", AsyncMock(return_value=EventConfig(enrollment_closes_at=past))
        )
        response = client.post("/api/enrollments", json=enrollment())
        assert response.status_code == 409
        assert "closed" in response.json()["detail"]
        assert isinstance(services.elasticsearch.create, AsyncMock)
        services.elasticsearch.create.assert_not_awaited()

    def test_conference_reports_status_and_cutoff(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        future = datetime.now(UTC) + timedelta(hours=1)
        event = EventConfig(
            tables=[TableSpec(number=1, seats=6), TableSpec(number=2, seats=6)],
            enrollment_closes_at=future,
        )
        monkeypatch.setattr(main, "read_event", AsyncMock(return_value=event))
        body = client.get("/api/conference").json()
        assert body["status"] == "open"
        assert body["capacity"] == 12
        assert body["group_size"] == 6
        assert datetime.fromisoformat(body["enrollment_closes_at"]) == future

    def test_public_conference_returns_only_aggregate_data(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        attendees = people(2)
        plan = make_run(attendees).plan
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=attendees))
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=plan))

        response = client.get("/api/conference")
        body = response.json()
        assert body["attendees"] == []
        assert body["attendee_count"] == 2
        assert body["plan"] is None
        assert body["published"] is True
        assert body["status"] == "published"
        for attendee in attendees:
            assert attendee.name not in response.text
            for answer in attendee.goal_answers.values():
                assert answer not in response.text

    def test_capacity_follows_the_host_tables(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        event = EventConfig(tables=[TableSpec(number=1, seats=4)])
        monkeypatch.setattr(main, "read_event", AsyncMock(return_value=event))
        monkeypatch.setattr(main, "validate_capacity", AsyncMock(return_value=False))
        response = client.post("/api/enrollments", json=enrollment())
        assert response.status_code == 409
        assert "4 seats" in response.json()["detail"]


class TestEventSetup:
    def test_saves_a_valid_layout(self, api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = api
        save = AsyncMock(side_effect=lambda _client, event: event)
        monkeypatch.setattr(main, "save_event", save)
        payload = {
            "tables": [{"number": 2, "seats": 5}, {"number": 1, "seats": 5}],
            "enrollment_closes_at": "2026-11-10T11:40:00-07:00",
            "min_table_size": 3,
        }
        response = client.put("/api/host/event", json=payload)
        assert response.status_code == 200
        assert [table["number"] for table in response.json()["tables"]] == [1, 2]
        save.assert_awaited_once()

    @pytest.mark.parametrize(
        "payload",
        [
            {"tables": [{"number": 1, "seats": 4}, {"number": 1, "seats": 4}]},
            {"tables": []},
            {"tables": [{"number": 1, "seats": 1}]},
            {"tables": [{"number": 1, "seats": 4}], "min_table_size": 5},
            {"enrollment_closes_at": "2026-11-10T11:40:00"},
        ],
    )
    def test_rejects_invalid_layouts(self, api: Api, payload: dict[str, object]) -> None:
        client, _ = api
        assert client.put("/api/host/event", json=payload).status_code == 422

    def test_cannot_shrink_below_current_enrollment(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(5)))
        response = client.put("/api/host/event", json={"tables": [{"number": 1, "seats": 4}]})
        assert response.status_code == 409
        assert "5 people" in response.json()["detail"]


class TestRuns:
    def test_produce_matches_sends_host_layout_and_stores_a_draft(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, services = api
        run = make_run(people(4))
        event = EventConfig(tables=[TableSpec(number=7, seats=4)], min_table_size=2)
        monkeypatch.setattr(main, "read_event", AsyncMock(return_value=event))
        save = AsyncMock(side_effect=lambda _client, saved: saved)
        monkeypatch.setattr(main, "save_run", save)
        telemetry = Mock(spec=DashboardMetrics)
        monkeypatch.setattr(main, "dashboard_metrics", telemetry)
        assert isinstance(services.matcher.request, AsyncMock)
        services.matcher.request.return_value = httpx.Response(
            200, json=run.plan.model_dump(mode="json")
        )

        response = client.post("/api/host/runs", json={"seed": 42})

        assert response.status_code == 201
        call = services.matcher.request.await_args
        assert call is not None
        assert call.args[:2] == ("POST", "/runs")
        assert call.kwargs["json"] == {
            "tables": [{"number": 7, "seats": 4}],
            "min_table_size": 2,
            "seed": 42,
            "use_llm": True,
            "use_semantic": True,
        }
        body = response.json()
        assert body["seed"] == 42
        assert body["plan"]["run_id"] == body["id"]
        save.assert_awaited_once()
        telemetry.record_run.assert_called_once()
        measured_plan, duration = telemetry.record_run.call_args.args
        assert measured_plan.run_id == body["id"]
        assert duration >= 0

    async def test_failed_persistence_does_not_record_a_successful_run(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, services = api
        run = make_run(people(4))
        telemetry = Mock(spec=DashboardMetrics)
        monkeypatch.setattr(main, "dashboard_metrics", telemetry)
        monkeypatch.setattr(main, "save_run", AsyncMock(side_effect=RuntimeError("save failed")))
        assert isinstance(services.matcher.request, AsyncMock)
        services.matcher.request.return_value = httpx.Response(
            200, json=run.plan.model_dump(mode="json")
        )
        with pytest.raises(RuntimeError, match="save failed"):
            await main.create_run(services, 0, use_llm=False)
        telemetry.record_run.assert_not_called()

    def test_publish_refuses_a_run_that_would_leave_someone_out(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=make_run(people(4))))
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(5)))
        save = AsyncMock()
        monkeypatch.setattr(main, "save_plan", save)
        response = client.post("/api/host/runs/run-1/publish")
        assert response.status_code == 409
        assert "new run" in response.json()["detail"]
        save.assert_not_awaited()

    def test_publish_refuses_a_run_made_for_an_older_table_setup(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=make_run(people(4))))
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(4)))
        moved = EventConfig(tables=[TableSpec(number=10, seats=8)])
        monkeypatch.setattr(main, "read_event", AsyncMock(return_value=moved))
        save = AsyncMock()
        monkeypatch.setattr(main, "save_plan", save)
        response = client.post("/api/host/runs/run-1/publish")
        assert response.status_code == 409
        assert "setup changed" in response.json()["detail"]
        save.assert_not_awaited()

    def test_publish_refuses_a_run_made_before_someone_edited_their_answers(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=make_run(people(4))))
        edited = people(4)
        edited[2] = edited[2].model_copy(update={"role": "Changed role"})
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=edited))
        save = AsyncMock()
        monkeypatch.setattr(main, "save_plan", save)
        response = client.post("/api/host/runs/run-1/publish")
        assert response.status_code == 409
        assert "edited their answers" in response.json()["detail"]
        save.assert_not_awaited()

    def test_publish_ignores_tags_the_matcher_added(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        tagged = [a.model_copy(update={"topic_tags": ["k8s"]}) for a in people(4)]
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=make_run(tagged)))
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(4)))
        monkeypatch.setattr(main, "save_plan", AsyncMock(side_effect=lambda _c, plan: plan))
        assert client.post("/api/host/runs/run-1/publish").status_code == 200

    def test_a_discarded_run_cannot_be_published(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, services = api
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=None))
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(4)))
        save = AsyncMock()
        monkeypatch.setattr(main, "save_plan", save)
        with pytest.raises(HTTPException) as error:
            asyncio.run(main.publish(services, make_run(people(4))))
        assert error.value.status_code == 409 and "discarded" in error.value.detail
        save.assert_not_awaited()

    def test_publish_saves_the_run_plan(self, api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = api
        run = make_run(people(4))
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=run))
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(4)))
        monkeypatch.setattr(main, "save_plan", AsyncMock(side_effect=lambda _c, plan: plan))
        response = client.post("/api/host/runs/run-1/publish")
        assert response.status_code == 200
        assert response.json()["run_id"] == "run-1"

    def test_publishing_twice_is_safe_but_a_different_run_is_rejected(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        published = make_run(people(4), "run-1")
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=published.plan))
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=published))
        assert client.post("/api/host/runs/run-1/publish").status_code == 200
        other = make_run(people(4), "run-2")
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=other))
        response = client.post("/api/host/runs/run-2/publish")
        assert response.status_code == 409
        assert "already published" in response.json()["detail"]

    def test_organizer_shortcut_never_waits_on_ai_notes(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, services = api
        run = make_run(people(4))
        monkeypatch.setattr(main, "save_run", AsyncMock(side_effect=lambda _c, saved: saved))
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(4)))
        monkeypatch.setattr(main, "save_plan", AsyncMock(side_effect=lambda _c, plan: plan))
        assert isinstance(services.matcher.request, AsyncMock)
        services.matcher.request.return_value = httpx.Response(
            200, json=run.plan.model_dump(mode="json")
        )
        assert client.post("/api/matches").status_code == 200
        call = services.matcher.request.await_args
        assert call is not None
        assert call.kwargs["json"]["use_llm"] is False

    def test_unknown_run_is_not_found(self, api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _ = api
        monkeypatch.setattr(main, "read_run", AsyncMock(return_value=None))
        assert client.get("/api/host/runs/missing").status_code == 404
        assert client.post("/api/host/runs/missing/publish").status_code == 404

    def test_fixture_demo_attendees_can_be_loaded(self, api: Api) -> None:
        client, services = api
        response = client.post("/api/host/demo/attendees", json={"source": "fixture"})
        assert response.json() == {"created": 14, "generator": "fixture"}
        assert isinstance(services.elasticsearch.create, AsyncMock)
        assert services.elasticsearch.create.await_count == 14


class TestOrganizerConsole:
    def test_event_details_are_saved_and_shown_to_attendees(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        details = {
            "name": "KubeCon Lunch",
            "date": "2026-11-20",
            "timezone": "America/New_York",
            "lunch_start": "12:00",
            "lunch_end": "13:00",
        }
        event = EventConfig.model_validate(details)
        monkeypatch.setattr(main, "read_event", AsyncMock(return_value=event))
        assert client.get("/api/conference").json()["name"] == "KubeCon Lunch"

    @pytest.mark.parametrize(
        "changes",
        [
            {"timezone": "Mars/Olympus"},
            {"lunch_start": "13:00", "lunch_end": "12:00"},
            {"date": "Nov 20"},
        ],
    )
    def test_invalid_event_details_are_rejected(self, changes: dict[str, str]) -> None:
        with pytest.raises(ValueError):
            EventConfig.model_validate(changes)

    def test_discarding_drafts_keeps_the_published_run(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        plan = make_run(people(4)).plan
        monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=plan))
        response = client.delete("/api/host/runs")
        assert response.json() == {"deleted": 2}
        delete = getattr(main, "delete_draft_runs")  # noqa: B009 - patched by the api fixture
        assert delete.await_args.args[1] == "run-1"

    def test_announcements_are_saved_with_their_audience_size(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(5)))
        body = {"title": "Lunch soon", "body": "See you in Hall B."}
        response = client.post("/api/host/announcements", json=body)
        assert response.status_code == 201
        assert response.json()["recipients"] == 5 and response.json()["send_at"] is None

    def test_matched_announcements_need_published_tables(
        self, api: Api, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, _ = api
        monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=people(5)))
        body = {"title": "Table ready", "body": "Go!", "audience": "matched"}
        assert client.post("/api/host/announcements", json=body).status_code == 409

    def test_attendees_see_only_due_announcements_for_their_audience(self) -> None:
        now = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
        made = now - timedelta(hours=1)
        announcements = [
            Announcement(id="a", title="All", body="x", created_at=made),
            Announcement(
                id="b", title="Later", body="x", created_at=made, send_at=now + timedelta(minutes=5)
            ),
            Announcement(id="c", title="Seated", body="x", audience="matched", created_at=made),
        ]
        assert [a.id for a in main.inbox(announcements, seated=False, now=now)] == ["a"]
        assert [a.id for a in main.inbox(announcements, seated=True, now=now)] == ["a", "c"]
