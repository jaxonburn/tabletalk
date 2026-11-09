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

No queues, Kubernetes, relational database, LLM, or vector model are required.

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

Before any real/public deployment: authentication, organizer authorization,
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
