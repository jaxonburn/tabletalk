import base64
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_script() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "radar_secrets.py"
    spec = importlib.util.spec_from_file_location("radar_secrets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REQUIRED = {
    "ELASTICSEARCH_URL": "https://es.example.com:443",
    "ELASTICSEARCH_API_KEY": "es-key",
    "DASH0_ENDPOINT": "https://dash0.example.com",
    "DASH0_AUTH_TOKEN": "dash0-token",
    "DASH0_DATASET": "default",
}


def secrets_by_name(values: dict[str, str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    resources, warnings = load_script().secret_resources(values)
    decoded = {
        r["metadata"]["name"]: {k: base64.b64decode(v).decode() for k, v in r["data"].items()}
        for r in resources
    }
    return decoded, warnings


def test_optional_ai_and_host_secrets_are_separate_and_skip_empty_keys() -> None:
    secrets, warnings = secrets_by_name(
        REQUIRED | {"OPENAI_API_KEY": "sk-test", "ANTHROPIC_API_KEY": "", "HOST_TOKEN": "h"}
    )
    assert secrets["tabletalk-llm"] == {"OPENAI_API_KEY": "sk-test"}
    assert secrets["tabletalk-host"] == {"HOST_TOKEN": "h"}
    assert "OPENAI_API_KEY" not in secrets["tabletalk-elasticsearch"]
    assert warnings == []


def test_missing_optional_secrets_are_skipped_with_a_host_warning() -> None:
    secrets, warnings = secrets_by_name(dict(REQUIRED))
    assert set(secrets) == {"tabletalk-elasticsearch", "tabletalk-dash0"}
    assert any("HOST_TOKEN" in warning for warning in warnings)


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"ELASTICSEARCH_API_KEY": ""}, "Missing required"),
        ({"ELASTICSEARCH_URL": "http://es.example.com"}, "HTTPS"),
    ],
)
def test_required_secrets_are_still_enforced(changes: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_script().secret_resources(REQUIRED | changes)
