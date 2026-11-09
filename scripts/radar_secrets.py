"""Upload local credentials to TableTalk's cluster without logging their values."""

import argparse
import base64
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from dotenv import dotenv_values

NAMESPACE = "team-tabletalk"
CLUSTER_HOST = "tabletalk.vc.cnh.radarhq.io"
# Every key in these groups must be set.
REQUIRED_GROUPS = {
    "tabletalk-elasticsearch": ("ELASTICSEARCH_URL", "ELASTICSEARCH_API_KEY"),
    "tabletalk-dash0": ("DASH0_ENDPOINT", "DASH0_AUTH_TOKEN", "DASH0_DATASET"),
}
# Uploaded only with the keys that are set; pods mount them as optional.
# tabletalk-llm: API and matcher. tabletalk-host: API only.
OPTIONAL_GROUPS = {
    "tabletalk-llm": ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"),
    "tabletalk-host": ("HOST_TOKEN",),
}


def secret_resources(
    values: Mapping[str, str | None],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build Secret objects from .env values. Returns (resources, warnings).

    Raises ValueError for missing required keys or non-HTTPS endpoints.
    """
    missing = [key for keys in REQUIRED_GROUPS.values() for key in keys if not values.get(key)]
    if missing:
        raise ValueError(f"Missing required .env values: {', '.join(missing)}")
    for key in ("ELASTICSEARCH_URL", "DASH0_ENDPOINT"):
        if urlsplit(values[key] or "").scheme != "https":
            raise ValueError(f"{key} must use HTTPS.")

    groups = dict(REQUIRED_GROUPS)
    warnings = []
    for name, keys in OPTIONAL_GROUPS.items():
        present = tuple(key for key in keys if values.get(key))
        if present:
            groups[name] = present
        else:
            warnings.append(f"{name}: no values in .env; skipped.")
    if "tabletalk-host" not in groups:
        warnings.append("HOST_TOKEN is not set, so the hosted host view will be open to anyone.")

    resources = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "type": "Opaque",
            "data": {key: base64.b64encode((values[key] or "").encode()).decode() for key in keys},
        }
        for name, keys in groups.items()
    ]
    return resources, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument(
        "--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env"
    )
    args = parser.parse_args()
    kubeconfig = args.kubeconfig.expanduser()
    env_file = args.env_file.expanduser()
    if not kubeconfig.is_file() or not env_file.is_file():
        parser.error("Both the kubeconfig and .env file must exist.")

    kubectl = ["kubectl", "--kubeconfig", str(kubeconfig)]
    context = subprocess.run(
        [*kubectl, "config", "view", "--minify", "--output=json"],
        capture_output=True,
        text=True,
        check=True,
    )
    server = json.loads(context.stdout)["clusters"][0]["cluster"]["server"]
    endpoint = urlsplit(server)
    if endpoint.scheme != "https" or endpoint.hostname != CLUSTER_HOST:
        parser.error(f"Refusing to upload secrets: kubeconfig must target https://{CLUSTER_HOST}.")

    try:
        resources, warnings = secret_resources(dotenv_values(env_file))
    except ValueError as error:
        parser.error(str(error))
    result = subprocess.run(
        [*kubectl, "--namespace", NAMESPACE, "apply", "--server-side", "--filename=-"],
        input=json.dumps({"apiVersion": "v1", "kind": "List", "items": resources}),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(
            "Secret upload failed. Check kubeconfig expiry and permissions with "
            f"`kubectl --kubeconfig {kubeconfig} auth can-i patch secrets -n {NAMESPACE}`. "
            "Command output was withheld to avoid exposing credential values."
        )
    names = ", ".join(resource["metadata"]["name"] for resource in resources)
    print(f"Configured {names} in {NAMESPACE}.")
    for warning in warnings:
        print(f"Note: {warning}")


if __name__ == "__main__":
    main()
