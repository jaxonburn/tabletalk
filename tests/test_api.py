from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from tabletalk_api import main
from tabletalk_api.observability import DashboardMetrics
from tabletalk_api.seed import seed_attendees
from tabletalk_shared.models import EventConfig, MatchPlan


def test_enrollment_stores_validated_profile(api: tuple[TestClient, main.Services]) -> None:
    client, services = api
    data = seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags"})
    response = client.post("/api/enrollments", json=data)
    assert response.status_code == 201
    assert response.json()["name"] == data["name"]
    assert response.json()["id"]
    assert isinstance(services.elasticsearch.create, AsyncMock)
    services.elasticsearch.create.assert_awaited_once()


@pytest.mark.parametrize("path", ["/api/conference", "/api/host/state"])
def test_polling_refreshes_dashboard_event_snapshot(
    api: tuple[TestClient, main.Services], monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    client, _ = api
    attendees = seed_attendees()
    telemetry = Mock(spec=DashboardMetrics)
    monkeypatch.setattr(main, "dashboard_metrics", telemetry)
    monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=attendees))
    monkeypatch.setattr(main, "list_runs", AsyncMock(return_value=[]))
    assert client.get(path).status_code == 200
    telemetry.record_event.assert_called_once_with(len(attendees), EventConfig(), published=False)


def test_public_readiness_identifies_both_backend_revisions(
    api: tuple[TestClient, main.Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = api
    monkeypatch.setenv("APP_VERSION", "api-revision")
    search = AsyncMock()
    monkeypatch.setattr(services.elasticsearch, "search", search)
    assert isinstance(services.matcher.request, AsyncMock)
    services.matcher.request.return_value = httpx.Response(
        200,
        json={"status": "ok", "version": "matcher-revision"},
        request=httpx.Request("GET", "http://matcher:8001/health"),
    )
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "tabletalk-api",
        "version": "api-revision",
        "matcher_version": "matcher-revision",
    }
    search.assert_awaited_once()


@pytest.mark.parametrize("goals", [[], None, ["Meet peers"], ["Learn", "Unknown goal"]])
def test_new_enrollment_does_not_use_stored_profile_migration(
    api: tuple[TestClient, main.Services], goals: list[str] | None
) -> None:
    client, services = api
    data = seed_attendees()[0].model_dump(
        exclude={"id", "conference_id", "focus_goal", "goal_answers"}
    )
    response = client.post("/api/enrollments", json=data | {"goals": goals})
    assert response.status_code == 422
    assert isinstance(services.elasticsearch.create, AsyncMock)
    services.elasticsearch.create.assert_not_awaited()


def test_completed_plan_closes_enrollment(
    api: tuple[TestClient, main.Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = api
    monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=Mock(spec=MatchPlan)))
    response = client.post(
        "/api/enrollments",
        json=seed_attendees()[0].model_dump(exclude={"id", "conference_id", "topic_tags"}),
    )
    assert response.status_code == 409
    assert "Enrollment is closed" in response.json()["detail"]
    assert isinstance(services.elasticsearch.create, AsyncMock)
    services.elasticsearch.create.assert_not_awaited()


def test_seeding_does_not_duplicate_existing_profiles(
    api: tuple[TestClient, main.Services], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, services = api
    monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=seed_attendees()))
    response = client.post("/api/demo/seed")
    assert response.status_code == 200
    assert response.json() == {"created": 0}
    assert isinstance(services.elasticsearch.create, AsyncMock)
    services.elasticsearch.create.assert_not_awaited()


@pytest.mark.parametrize("upstream_status,expected_status", [(409, 409), (500, 502), (503, 502)])
def test_matcher_errors_are_not_success_shaped(
    api: tuple[TestClient, main.Services], upstream_status: int, expected_status: int
) -> None:
    client, services = api
    assert isinstance(services.matcher.request, AsyncMock)
    services.matcher.request.return_value = httpx.Response(
        upstream_status, json={"detail": "Enroll at least two attendees."}
    )
    response = client.post("/api/matches")
    assert response.status_code == expected_status
    assert "detail" in response.json()


@pytest.mark.parametrize(
    "error,status",
    [(httpx.ConnectError("unavailable"), 503), (httpx.ReadTimeout("too slow"), 504)],
)
def test_matcher_connection_failures_are_actionable(
    api: tuple[TestClient, main.Services], error: httpx.RequestError, status: int
) -> None:
    client, services = api
    assert isinstance(services.matcher.request, AsyncMock)
    services.matcher.request.side_effect = error
    response = client.post("/api/matches")
    assert response.status_code == status
    assert "matching service" in response.json()["detail"]
