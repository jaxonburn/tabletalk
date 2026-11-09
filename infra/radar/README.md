# TableTalk on Radar's hackathon cluster

This adapts the [Radar starter](https://github.com/skyhook-io/cnh-starter) for our
existing monorepo. Docker Compose remains the hot-reloading local development
path; no Minikube or Colima Kubernetes setup is needed.

| Setting                       | Value                                     |
| ----------------------------- | ----------------------------------------- |
| Radar organization            | Cloud Native Hackathon                    |
| Cluster name / MCP ID         | `tabletalk` / `lTCjZBaR2Ss`               |
| Namespace and Argo CD project | `team-tabletalk`                          |
| Public demo                   | https://tabletalk.cnh.radarhq.io          |
| GitOps source                 | `main`, `infra/radar/deploy` in this repo |

Always pass the cluster ID on Radar MCP calls. Other teams share the organization;
don't query or modify their clusters. Don't upgrade the platform's Radar agent.

## What runs here

```text
Public HTTPS URL --> Service app:80 --> nginx / built React assets
                                            |
                                            +-- /api --> API --> matcher
                                                         |         |
                                                         +--> Elastic Cloud

API + matcher -- OTLP --> Collector --> Dash0
Platform Prometheus -- namespace-scoped federation --^
```

Elasticsearch is hosted, not a pod/PVC in this cluster. Both Elastic Cloud Hosted
and Serverless are supported: Serverless manages its own shards/replicas, and
readiness searches our indices rather than calling the unsupported cluster-health
API. The API has one worker, one replica and `Recreate` rollouts to prevent
overlapping writers. Don't add autoscaling without distributed coordination.

Service `app` on port 80 is required by the platform's HTTPS routing. No Ingress,
LoadBalancer, or NodePort is needed. The starter's HTTPRoute is retained in Git
so Argo CD does not prune it; the actual public routing is provided by the
platform outside the virtual cluster. The frontend is a built, unprivileged
nginx image, not Vite.

**This is an intentionally public, unauthenticated hackathon demo. Use fictional
profiles only.** Anyone with the URL can enroll, view profiles, seed, and generate
groups, which closes enrollment. Do not collect real attendee data or expose
additional internal endpoints. Public access was explicitly chosen for this demo.

## One-time setup

1. Accept the Radar invitation, connect Radar MCP, and select **Cloud Native
   Hackathon**. Check the `tabletalk` namespace health and capture the initial
   Checks view for judging.
2. Save the team's kubeconfig outside the repo, for example `~/cnh-kubeconfig`.
   In Radar: cluster → Resources → Secrets → `default/kubeconfig` → reveal/copy
   the `config` value. Never paste it into chat or commit it. The platform rotates
   these credentials; refresh the file if access expires. Restrict its permissions
   with `chmod 600 ~/cnh-kubeconfig`.
3. Keep the existing root `.env` with:

   ```dotenv
   ELASTICSEARCH_URL=https://your-elasticsearch-endpoint:443
   ELASTICSEARCH_API_KEY=your-encoded-elasticsearch-api-key
   DASH0_ENDPOINT=https://your-dash0-otlp-http-ingress
   DASH0_AUTH_TOKEN=your-ingestion-token
   DASH0_DATASET=default
   ```

   Use an Elasticsearch data API key, not an Elastic Cloud management key.
   In Serverless, use a project API key with permission to manage our indices
   and read/write their documents. In Hosted, grant cluster `monitor` for startup's
   deployment detection and index `manage`, `read`, and `write` on
   `tabletalk-attendees-v1` and `tabletalk-plans-v1`. Never add keys to browser
   variables.

4. Upload keys safely from the repo root:

   ```bash
   uv run python scripts/radar_secrets.py --kubeconfig ~/cnh-kubeconfig
   ```

   This verifies the kubeconfig targets `https://tabletalk.vc.cnh.radarhq.io`,
   then sends credentials to Kubernetes over stdin without printing them or
   writing a generated Secret manifest. `tabletalk-elasticsearch` is available
   only to API/matcher; `tabletalk-dash0` only to the Collector. Secrets are not
   stored in Git or GitHub Actions. Kubernetes Secrets are not a substitute for
   encrypted storage and access control.

5. Publish this feature through a reviewed PR into `main`. The CI deploy job
   needs GitHub Actions write permission for repository contents and packages.
   If branch protection blocks the generated deployment commit, configure an
   approved bot exception or change the workflow to a reviewed deployment PR;
   don't disable protection silently.
6. After the first image publishing steps finish, make **both** GHCR packages
   `tabletalk-web` and `tabletalk-python` **Public** in GitHub package settings.
   A public repo alone does not make newly created packages public.
7. Bootstrap GitOps once by applying `argocd/application.yaml` through Radar MCP
   (`apply_resource`, cluster `lTCjZBaR2Ss`) or:

   ```bash
   kubectl --kubeconfig ~/cnh-kubeconfig apply -f infra/radar/argocd/application.yaml
   ```

   Do this after these files reach `main` and before CI's deployment wait expires.
   If the first CI run times out during manual setup, rerun it once packages are
   public and the Application exists. Do not deploy the initial `pending` image
   tags by hand; CI replaces them with the tested source commit SHA.

## Every merge to main

PRs run checks without publishing or deploying. A push to `main` must pass all
frontend, backend, Compose, manifest, and release-image checks before deployment:

1. CI builds the production frontend and shared Python image from the exact
   source SHA, and publishes immutable SHA tags to GHCR.
2. A bot updates only the two image tags in `deploy/kustomization.yaml`. Its
   `deploy: ... [skip ci]` commit does not trigger a build loop. Stale runs don't
   overwrite a newer `main` revision.
3. Argo CD sees the Git change and reconciles the team namespace automatically.
4. CI waits for `/version.json` and both backend revisions in `/api/health` to
   match the source SHA, and for `/api/conference` to succeed. A timeout fails
   CI; use Radar for the reason.

GitOps does not need a long-lived cluster token in GitHub Actions. The setup
kubeconfig can expire without breaking deployments. Main branch protection should
require the PR validation jobs, not the main-only deploy job.

To confirm a deployment, use Radar's Application/workload views, then open
`https://tabletalk.cnh.radarhq.io/version.json` and the app. Add fictional demo
attendees and exercise matching when ready to close enrollment. Don't run the
destructive smoke scenario against live data you intend to keep.

## Dash0: live cluster, not your laptop

The in-cluster Collector exports application traces, logs, and metrics to the
existing Dash0 dataset. It labels telemetry with `k8s.cluster.name=tabletalk` and
`deployment.environment.name=radar`, enriches pod/deployment metadata, and
federates **only `team-tabletalk`** from the platform's existing Prometheus.
This adds container CPU/memory, pod readiness/restarts, requests/limits, and
deployment availability without a privileged DaemonSet.

In Dash0, filter by the cluster/environment above. Look for `tabletalk-api`,
`tabletalk-matcher`, `container_cpu_usage_seconds_total`,
`container_memory_working_set_bytes`, and `kube_pod_container_status_restarts_total`.
Counters such as CPU time need a rate; memory is a gauge. Prometheus metrics
retain their original names; this is not a full Dash0 Operator installation or
host-node monitoring.

The platform forbids privileged pods, hostPath, hostPID, and hostNetwork.
Don't install node collectors that require them. The Collector has only
namespace-scoped pod/ReplicaSet read permissions. Existing SDK instrumentation
and OTLP logs are retained; there is no duplicate auto-instrumentation or pod-log
tailer. Keep one Collector replica to avoid double-scraping.

To stop laptop telemetry while using this live demo, leave `compose.dash0.yaml`
out of the root `.env`'s `COMPOSE_FILE`, stop the old Compose selection first,
then restart it if needed. Local debug telemetry still works. Don't replace the
in-cluster OTLP endpoint with the public Dash0 endpoint in API/matcher.

After rotating credentials, rerun the Secret upload and restart consumers:

```bash
kubectl --kubeconfig ~/cnh-kubeconfig -n team-tabletalk rollout restart deployment/api deployment/matcher deployment/otel-collector
```

## Troubleshooting and data

- **ImagePullBackOff:** verify both GHCR packages are public and tags exist.
- **CreateContainerConfigError:** upload both Secrets before starting workloads.
- **API/matcher not ready:** verify the Elasticsearch key and inspect pod logs;
  startup must create/update both mappings and readiness must search both indices.
- **Argo CD comparison error:** verify the public repo, `main`, and deployment path.
- **Collector cannot scrape:** check `monitoring/prometheus-server` and the
  Collector logs; the platform's Prometheus should already contain the namespace.
- **No Dash0 data:** check the OTLP/HTTP ingress, ingestion token permissions,
  dataset, Collector export errors, and cluster/environment filters.

Everything fits comfortably within the provided request/limit budgets and uses
no application PVCs. Scaling or adding tools still counts against team quota.
Use Radar's Timeline, Events, Logs, and Checks to explain real deployment issues.
An AI investigation is optional and may consume organization allowance/overage;
get approval before starting one, and don't manufacture failures for judging.

Removing the Application/workloads does **not** delete Elastic Cloud data.
Local Compose volumes and cloud indices are independent. Reset/delete cloud
indices only as an explicit, deliberate operation; do not delete the whole team
namespace, platform services, or the cluster as a shortcut.
