"""Upload local credentials to TableTalk's cluster without logging their values."""

import argparse
import base64
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

NAMESPACE = "team-tabletalk"
CLUSTER_HOST = "tabletalk.vc.cnh.radarhq.io"


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

    values = dotenv_values(env_file)
    key_groups = {
        "tabletalk-elasticsearch": ("ELASTICSEARCH_URL", "ELASTICSEARCH_API_KEY"),
        "tabletalk-dash0": ("DASH0_ENDPOINT", "DASH0_AUTH_TOKEN", "DASH0_DATASET"),
    }
    missing = [key for keys in key_groups.values() for key in keys if not values.get(key)]
    if missing:
        parser.error(f"Missing required .env values: {', '.join(missing)}")
    for key in ("ELASTICSEARCH_URL", "DASH0_ENDPOINT"):
        if urlsplit(values[key] or "").scheme != "https":
            parser.error(f"{key} must use HTTPS.")

    resources = [
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "type": "Opaque",
            "data": {key: base64.b64encode((values[key] or "").encode()).decode() for key in keys},
        }
        for name, keys in key_groups.items()
    ]
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
    print(f"Configured tabletalk-elasticsearch and tabletalk-dash0 in {NAMESPACE}.")


if __name__ == "__main__":
    main()
