from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from elasticsearch import AsyncElasticsearch
from fastapi.testclient import TestClient
from tabletalk_api import main
from tabletalk_shared.models import EventConfig


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, main.Services]]:
    storage = Mock(spec=AsyncElasticsearch)
    storage.create = AsyncMock()
    storage.indices = Mock()
    storage.indices.refresh = AsyncMock()
    matcher = Mock(spec=httpx.AsyncClient)
    matcher.request = AsyncMock()
    services = main.Services(storage, matcher)
    monkeypatch.setattr(main, "read_plan", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "validate_capacity", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "list_attendees", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "read_event", AsyncMock(return_value=EventConfig()))
    monkeypatch.setattr(main, "read_auto_state", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "save_token", AsyncMock())
    monkeypatch.setattr(main, "read_check_in", AsyncMock(return_value=None))
    monkeypatch.setattr(main, "list_check_ins", AsyncMock(return_value={}))
    monkeypatch.setattr(main, "save_check_in", AsyncMock())
    monkeypatch.setattr(main, "update_attendee", AsyncMock())
    monkeypatch.setattr(main, "list_announcements", AsyncMock(return_value=[]))
    # Runs the code under test saved are found again unless a test says otherwise.
    monkeypatch.setattr(main, "read_run", AsyncMock(return_value=Mock()))
    monkeypatch.setattr(main, "save_announcement", AsyncMock())
    monkeypatch.setattr(main, "delete_draft_runs", AsyncMock(return_value=2))
    monkeypatch.delenv("HOST_TOKEN", raising=False)
    for name in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_REASONING_EFFORT",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    main.app.dependency_overrides[main.get_services] = lambda: services
    try:
        yield TestClient(main.app), services
    finally:
        main.app.dependency_overrides.clear()
