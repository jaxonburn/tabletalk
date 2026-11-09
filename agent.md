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
  Elasticsearch or the matching service directly. `src/main.tsx` picks the page
  from the path: `/host` loads the host view (`HostApp.tsx`), `/classic` loads the
  original single-page app (`App.tsx`), and everything else (including
  `/user-onboarding`) loads the attendee app in `src/onboarding/`. Each page is a
  separate chunk with its own stylesheet.
- `src/onboarding/`: the onboarding template's HTML and CSS, kept as close to the
  original as possible, plus `onboarding.ts`, which drives the markup and talks to
  the API: `GET /api/conference` for event details and goal copy, `POST
  /api/enrollments` or `PUT /api/me` to save, `GET /api/me` to resume and to poll
  the waiting room (every 15 s, plus "Check for my table now"), and `POST
  /api/me/check-in`. Goal IDs are the API's goal values; element IDs use slugs
  (`learn`, `share`, `connect`). Both attendee pages share the token in
  `src/attendeeToken.ts`.
- `services/api`: public FastAPI API. Owns enrollment, the cutoff, host event
  setup, fictional demo data, draft runs, and publishing. Calls the matching
  service over HTTPX. An in-process lock serializes mutations in the single API
  worker. Host endpoints require `X-Host-Token` only when `HOST_TOKEN` is set.
- `services/matcher`: internal FastAPI API, not published to the host. `POST /runs`
  computes a plan for the current snapshot and the given tables/seed, without
  saving anything.
- `packages/shared`: Pydantic contracts, index mappings/storage helpers, shared
  error handling, and OpenTelemetry initialization. Both services depend on this
  uv workspace package.
- Elasticsearch is the core data layer. `tabletalk-attendees-v1` contains
  searchable profiles. `tabletalk-events-v1` holds the host's setup (tables,
  seats, cutoff). `tabletalk-runs-v1` holds draft runs. `tabletalk-plans-v1`
  stores the single *published* plan under a stable conference/slot ID; its
  existence closes enrollment. Create-only writes make publishing idempotent
  and prevent overwriting a plan that won a concurrent publish.
- `infra/otel`: OTLP/HTTP Collector configurations. Local debug exporter by
  default; optional Compose overlay forwards traces, metrics, and logs to Dash0.
- `compose.elasticsearch-cloud.yaml`: optional authenticated Elastic Cloud
  connection; removes the local Elasticsearch service without changing hot reload.
- `infra/radar`: deployment on Radar's provided `tabletalk` cluster, namespace
  `team-tabletalk`. Production nginx frontend, single-writer API, matcher,
  namespace-scoped Collector, and external Elasticsearch.

No queues, Kubernetes, relational database, LLM, or vector model are required for
local development. Kubernetes is the separate hosted hackathon deployment. An LLM is
optional, for table notes, test personas, topic tags, and the AI judge.

## Current matching behavior

Conference and lunch slot are mandatory Elasticsearch filters, and every query is
restricted to the attendee snapshot taken when matching starts. The pipeline lives
in `services/matcher` and is described in detail in `docs/matching-plan.md`:

1. **Affinity** (`affinity.py`): one BM25 query per attendee scores everyone else.
   Learn answers match others' Share answers and `can_help`, and the reverse.
   Connect answers match each other, and interests and complementary goals also
   count. Scores are scaled per attendee and averaged in both directions into a
   symmetric `[0, 1]` matrix. Different roles get a small bonus.
   `lexical_directed_scores` is a dependency-free stand-in for tests and evaluation.
2. **Sizing** (`sizing.py`): use the fewest tables that seat everyone, preferring
   larger tables, then balance sizes within seat limits. Every table gets at least
   two people (14 at 5-seat tables → 5/5/4); impossible layouts raise `SeatingError`
   (a 409). Tables come from the host's saved setup (`EventConfig`). Until a
   host saves one, `DEFAULT_TABLES` (25 four-seat tables) is used.
3. **Optimizer** (`optimizer.py`): greedy seating that starts with the hardest
   person to match, then swaps people between tables, restarted from seeded random
   seatings. It maximizes how well tablemates fit, plus a bonus for each person's
   best tablemate so nobody is stranded. The same snapshot and seed always give
   the same plan. This is heuristic, not provably optimal.
4. **Validation** (`matching.py`): every attendee is seated exactly once at a real
   table of the planned size. Otherwise the plan is rejected, never saved.
5. **Narratives** (`narratives.py`): per-pair reasons, a table summary, three table
   starters, and a per-person `MemberNote` (summary + starters). They quote the
   longest phrases one person offers and another asks about, as written, and only
   mention people at the same table. They are the fallback for AI notes.
6. **AI notes** (`llm_notes.py`, optional): one LLM call per table, using the
   versioned prompt `PROMPT_VERSION`, a strict JSON schema, and only that table's
   people plus the evidence. `parse_notes` rejects notes that don't cover exactly
   this table's people or that name someone from another table, and clips long
   text. Failures retry once, then keep the templates. A 45 s deadline applies
   overall. The plan records `narrative_provider` and `prompt_version`.
7. **Metrics**: seated count, table sizes, average pair fit, the weakest person's
   best-tablemate fit, and Learn coverage, each compared with random seating.
8. **Semantic enrichment and hybrid scoring** (step 8, optional): `enrichment.py`
   backfills vectors and LLM `topic_tags` at match time for anyone missing them
   for the current model.
   - **Storage:** vectors are three 512-dimension cosine `dense_vector` fields
     on the attendee document; `embedding_model` / `tags_model` mark them
     current.
   - **Scoring:** `elasticsearch_affinity` adds one kNN search per attendee
     (`knn_query`), and `combine_directed` blends it with BM25 (`semantic`
     weight 0.75). `RunRequest.use_semantic=false` gives pure BM25, and plans
     record `retrieval`.
   - **Elasticsearch 9 gotcha:** dense vectors are excluded from `_source` by
     default (`exclude_source_vectors`). Read them with
     `source={"exclude_vectors": False}`. Profile reads exclude
     `ENRICHMENT_ONLY_FIELDS`, so vectors never reach API payloads.
9. **AI judge** (`judge.py`, matcher `POST /judge`, API
   `POST /api/host/runs/{id}/judge`): scores each table 1–5 with a strict rubric,
   plus a seeded random seating of the same people. The result is stored on the
   run. It never changes seats.

Learning and sharing are complementary goals; meeting connections matches others
who want to make connections. `goal_answers` (Learn / Share / Connect) and the older
`can_help` / `want_to_learn` fields are both used. Seed profiles include a
`focus_goal` and a `goal_answers` entry for every selected goal, with prompt
questions and example hints in `services/api/src/tabletalk_api/seed.py`.
`tabletalk_shared.fixtures.load_fixture()` loads 14 fictional people across three
hidden themes for tests and evaluation. `tabletalk_shared.personas` generates more,
either offline (`generate_offline`: seeded, six themes, ~20% bridges) or with an
LLM (`generate_with_llm`: invalid personas are dropped). `tabletalk_shared.llm` is
the provider-neutral client (`LLM_PROVIDER` none/anthropic/openai, with
`LLM_MODEL` required). `tabletalk_matcher.evaluation` and
`scripts/evaluate_matching.py` score theme purity and ARI against random seating.
CI runs `--check`.

New enrollments must select 1–3 current goals. Only stored profiles migrate legacy
goals (for example `Meet peers` → `Meet connections`).

The `can_help` enrollment field represents "what I can offer"; `want_to_learn`
represents the broader "what I'm looking for." Keep frontend/API names consistent
if you rename these contracts.

## Entry points

- `GET /health`: dependency readiness; excluded from HTTP tracing.
- `GET /api/health`: public readiness alias reporting API/matcher release versions
  for CI's hosted deployment verification.
- `GET /api/conference`: aggregate only (attendee count, `published`), `status`
  (`open | closed | published`), cutoff, capacity, `lunch_time`, `location`, and
  goal prompts (`label`, `short`, `question`, `example_hint`) for the onboarding.
- `POST /api/enrollments`: validated enrollment; rejected after the cutoff, after
  publishing, or when the host's tables are full.
  - The new onboarding sends `goals`, `focus_goal`, and one `goal_answers` entry
    per goal.
  - Older clients may instead send `can_help` + `want_to_learn`.
  - Returns the attendee plus a one-time `attendee_token`. Only
    `sha256(token)` → attendee ID is stored, in `tabletalk-tokens-v1`.
- `GET /api/me` (`X-Attendee-Token`): your enrollment, `status`, and, once
  published, your table.
  - Tablemates are shown as first name, role, company, and interests only.
  - Also includes your note, starters, the table summary, the table's name,
    two-line tagline, topics, seat count, lunch time, location, and
    `checked_in_at`.
  - Learn questions may appear in table starters; other answers never do.
- `PUT /api/me` (`X-Attendee-Token`): replace your answers while sign-ups are open
  (409 after). Keeps your ID and token; replacing the document drops stale
  vectors and tags, so the next run re-enriches you.
- `POST /api/me/check-in` (`X-Attendee-Token`): "I'm seated", only once your table
  is published. Stored once per attendee in `tabletalk-events-v1`
  (`kind: checkin`); repeating it keeps the first time. The host state lists
  `check_ins`.
- Host endpoints (`X-Host-Token` required only when `HOST_TOKEN` is set):
  - `GET /api/host/state`: event setup, status, attendees, run summaries.
  - `PUT /api/host/event`: numbered tables, seats, cutoff, minimum table size,
    and the `lunch_time` / `location` shown to attendees.
    Locked after publishing; can't shrink below current enrollment.
  - `POST /api/host/demo/attendees` (`{"source": "fixture" | "seed"}`) and
    `POST /api/demo/seed`: add missing fictional profiles; safe to repeat.
  - `POST /api/host/runs`: produce a draft run (optional `seed`), stored in
    `tabletalk-runs-v1`.
  - `GET /api/host/runs/{id}`: the full run, with plan and metrics.
  - `POST /api/host/runs/{id}/publish`: create-only write to `tabletalk-plans-v1`.
    Idempotent for the same run. Returns 409 for a different run, if the enrolled
    attendees no longer match the run's snapshot, or if the table setup changed
    after the run.
  - `POST /api/matches`: shortcut that produces a seed-0 run with template notes
    (never AI, because it holds the mutation lock) and publishes it.
- Internal `POST /runs`: compute a plan; never persists.

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
- LLMs may write text, never seats. Every LLM output must pass server-side
  validation, with a deterministic fallback. Version prompt changes
  (`PROMPT_VERSION`, `PERSONA_PROMPT_VERSION`), and never log prompts or responses.
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

One conference, one lunch slot, at most 100 attendees, no user accounts, an
optional shared `HOST_TOKEN` for host controls, and public profile data on the
local instance. Enrollment closes at the host's cutoff or when groups are
published. With `auto_match`, the API's `cutoff_watcher` (one asyncio task in the
single worker) creates a run at the cutoff and, with `auto_publish`, publishes it.
- The outcome is stored per cutoff (`AutoMatchState`), so restarts catch up but
  never repeat it.
- It never holds the mutation lock during matching or AI calls; only the publish
  step takes the lock.
- Running more than one worker would need a distributed lock or leader election
  first.

Seed profiles are fictional. Each browser keeps its attendee token in
`localStorage` (with a "Forget this device" option). Anyone holding the token can
see that person's table.

Before real-attendee or production deployment: authentication, organizer authorization,
attendee consent and visibility controls, deletion/retention, Elasticsearch
security, TLS, distributed enrollment/matching coordination, and deployment
configuration. Don't treat the Vite development server as a production frontend.

## Useful next milestones

1. Improve enrollment UX with role/topic chips and multi-step mobile onboarding.
2. Done: hybrid keyword/vector retrieval, topic tags, and an AI judge (see
   `docs/matching-plan.md`). Next: tune on real enrollments.
3. Done: attendee identity and personal assignment views.
4. Support multiple lunch slots, edits, and deliberate reassignment policies.
5. Add Dash0 browser RUM, a match-latency dashboard, and a meaningful SLO.
6. Install Gitar on GitHub and ship through reviewed PRs throughout the hackathon.

No repository remote, account switching, commits, or pushes are performed by the
local scaffold.
