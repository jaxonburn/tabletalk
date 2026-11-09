# TableTalk: contributor and agent handoff

## Purpose and direction

TableTalk helps tech-conference attendees meet during lunch. Attendees enroll
with their name, role, interests, what they can offer, and what they're looking
for. They select one or more lunch goals: `Learn`, `Share something I've learned`,
and `Meet connections`. An organizer creates one assigned lunch group per attendee.
Future versions may let attendees choose among suggested connections.

This is a Cloud Native Hackathon project. Prioritize a working, understandable
demo over infrastructure breadth. Elasticsearch and Gitar are the primary sponsor
integrations; Dash0 is the observability destination.

## Architecture

- `apps/web`: React, TypeScript, Vite. Same-origin `/api` requests are proxied to
  the conference API by Vite in development. The browser never accesses
  Elasticsearch or the matching service directly.
- `services/api`: public FastAPI API. Owns enrollment, fictional demo seeding,
  conference state, and organizer-triggered matching. Calls the matching service
  over HTTPX. An in-process lock serializes mutations in the single API worker.
- `services/matcher`: internal FastAPI API, not published to the host. Retrieves
  candidates from Elasticsearch and builds balanced groups of up to four.
- `packages/shared`: Pydantic contracts, index mappings/storage helpers, shared
  error handling, and OpenTelemetry initialization. Both services depend on this
  uv workspace package.
- Elasticsearch is the core data layer. `tabletalk-attendees-v1` contains
  searchable profiles. `tabletalk-plans-v1` stores a whole completed lunch plan
  under a stable conference/slot ID. Create-only writes make plan publication
  idempotent and prevent overwriting a winning concurrent plan.
- `infra/otel`: OTLP/HTTP Collector configurations. Local debug exporter by
  default; optional Compose overlay forwards traces, metrics, and logs to Dash0.
- `compose.elasticsearch-cloud.yaml`: optional authenticated Elastic Cloud
  connection; removes the local Elasticsearch service without changing hot reload.
- `infra/radar`: deployment on Radar's provided `tabletalk` cluster, namespace
  `team-tabletalk`. Production nginx frontend, single-writer API, matcher,
  namespace-scoped Collector, and external Elasticsearch.

No queues, Kubernetes, relational database, LLM, or vector model are required for
local development. Kubernetes is the separate hosted hackathon deployment.

## Current matching behavior

Conference and lunch slot are mandatory Elasticsearch filters. Candidate queries
exclude assigned attendees and restrict results to the enrollment snapshot.
Ranking boosts exact and analyzed topic overlap, complementary goals, and
offer/need text through BM25. Ties resolve by stable attendee ID.
Learning and sharing are complementary goals; meeting connections matches others
who want to make connections. Seed profiles include single and multiple selections.
Seed profiles also include a `focus_goal` and a `goal_answers` entry for every selected
goal, with prompt questions and example hints defined in `services/api/src/tabletalk_api/seed.py`.
These fields are optional for the existing frontend; the matcher still uses the
original goals and offer/need fields rather than scoring the new answers or focus.

The greedy allocator starts from the lowest remaining ID and grows each table
using relevance against its current members. Group sizes are balanced to avoid
singleton tables (for example, five people become groups of three and two).
This is explainable, not globally optimal.

Explanations cite exact interests, aligned goals, and significant word overlap.
They are deliberately conservative and are not an Elasticsearch score breakdown
or an LLM-generated compatibility claim.

The `can_help` enrollment field represents "what I can offer"; `want_to_learn`
represents the broader "what I'm looking for." Keep frontend/API names consistent
if you rename these contracts.

## Entry points

- `GET /health`: dependency readiness; excluded from HTTP tracing.
- `GET /api/health`: public readiness alias reporting API/matcher release versions
  for CI's hosted deployment verification.
- `GET /api/conference`: conference details, enrolled attendees, completed plan.
- `POST /api/enrollments`: validated enrollment, rejected after assignment.
- `POST /api/demo/seed`: add missing fictional profiles; safe to repeat.
- `POST /api/matches`: call the matcher and return the persisted plan.
- Internal `POST /match`: generate or return the existing plan.

## Running and checking

```bash
docker compose up --build -d
# UI: http://localhost:5173; API docs: http://localhost:8000/docs

npm ci
uv sync --locked --all-packages
npm run lint && npm run format:check && npm test && npm run build
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Frontend and Python source edits reload in Docker, including shared Python code.
Dependency changes require updating the appropriate lockfile and rebuilding.
CI mirrors these checks. Backend tests isolate Elasticsearch calls; use the live
Compose stack to verify actual relevance, persistence, and trace propagation.

The root `.env` selects Compose files with `COMPOSE_FILE`. Local Elasticsearch is
the default (`compose.yaml`). For Elastic Cloud, select
`compose.yaml:compose.elasticsearch-cloud.yaml` and set `ELASTICSEARCH_URL` to
the HTTPS Elasticsearch endpoint and `ELASTICSEARCH_API_KEY` to its encoded key.
Append `:compose.dash0.yaml` for Dash0 export. Explicit `-f` flags override this
selection. Stop the old mode with `docker compose down` before switching; never
remove its volume just to change modes. Local and hosted data remain separate.
Both backends share one authenticated client factory; API keys require HTTPS.
Elastic Serverless is detected at startup; never set its shard/replica settings
or use the cluster-health API there. Readiness searches both application indices.

## Live deployment and observability

See `infra/radar/README.md` for bootstrap and safe Secret upload. Radar MCP calls
must target only cluster `lTCjZBaR2Ss` (`tabletalk`), not other teams or the
platform's `cnh` cluster. Never upgrade the platform Radar agent or assume host
access is allowed. The team URL routes to Service `app:80`.

Passing CI on `main` publishes SHA-tagged web/Python images and commits only
generated Kustomize image tags. Argo CD deploys `infra/radar/deploy`; CI waits for
the expected frontend/API/matcher versions and backend readiness. PRs do not deploy.
Do not commit/push from an assistant session; the user publishes reviewed work.
Do not apply placeholder `pending` image tags to the live cluster.

Use the existing `.env` keys only through `scripts/radar_secrets.py` with an
explicit, verified team kubeconfig. Never commit Secrets/kubeconfigs, bake keys
into images, or send them through chat. The live demo is intentionally public
and unauthenticated for fictional profiles only; real attendee use remains out
of scope. Keep API at one replica/worker with `Recreate` rollout strategy.

The live Collector adds cluster/environment/pod metadata and federates scoped
workload metrics from existing Prometheus to Dash0. No Dash0 node agent,
privileged pods, duplicate auto-instrumentation, or pod-log tailer. Filter Dash0
by `k8s.cluster.name=tabletalk` and `deployment.environment.name=radar` to separate
it from local Compose telemetry. AI investigations require cost/allowance consent.

## Feature branch workflow

Start every new feature from the latest `main`, on a separate feature branch.
With a clean working tree, run:

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/short-description
```

Keep feature work off `main`. Open a pull request targeting `main` so Gitar can
review the changes and CI can run before merging. Before starting another feature,
repeat this workflow to pick up the latest merged changes.

## Rules for extending the project

- Keep Elasticsearch central to retrieval and persistence. Add vectors/hybrid
  search here rather than replacing matching with an external LLM call.
- Keep orchestration in the API and matching logic in the matching service.
- Validate enrollment inputs server-side; keep conference/slot filters and
  duplicate-assignment prevention intact.
- Surface dependency failures with actionable errors, not empty successful plans.
- Do not log names, enrollment text, HTTP bodies, or full Elasticsearch queries.
  Avoid high-cardinality personal identifiers in metric labels.
- Keep credentials out of source, browser bundles, and telemetry.
- Add focused behavior tests when changing enrollment or matching.
- Preserve the single-worker assumption until distributed coordination exists.
  The internal matcher must not be exposed as an independent public mutation API.

## Known demo boundaries

One conference, one lunch slot, at most 100 attendees, no authentication, and
public organizer controls/profile data on the local instance. Enrollment closes
when groups are published. Seed profiles are fictional. The browser remembers
the current enrollment only for its page session; refreshing removes the
personal table highlight, not the saved enrollment or group.

Before real-attendee or production deployment: authentication, organizer authorization,
attendee consent and visibility controls, deletion/retention, Elasticsearch
security, TLS, distributed enrollment/matching coordination, and deployment
configuration. Don't treat the Vite development server as a production frontend.

## Useful next milestones

1. Improve enrollment UX with role/topic chips and multi-step mobile onboarding.
2. Add hybrid keyword/vector retrieval and evaluate match quality on fixtures.
3. Add attendee identity and personal assignment views.
4. Support multiple lunch slots, edits, and deliberate reassignment policies.
5. Add Dash0 browser RUM, a match-latency dashboard, and a meaningful SLO.
6. Install Gitar on GitHub and ship through reviewed PRs throughout the hackathon.

No repository remote, account switching, commits, or pushes are performed by the
local scaffold.
