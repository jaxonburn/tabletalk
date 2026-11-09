# TableTalk

**Your next great connection is a lunch away.**

Conferences are full of interesting people. Finding the right ones between talks
shouldn't feel like another job. TableTalk asks attendees a few quick questions,
then brings them together in small lunch groups with something real to talk about.

We're building this at the [Cloud Native Hackathon](https://www.wemakedevs.org/hackathons/cnh),
with Elasticsearch at the heart of matching, OpenTelemetry for understanding the
app, and Gitar for reviewing the work as we build.

## What's here

- A React enrollment experience: name, role, interests, what you can offer, and what
  you're looking for. Choose one or more lunch goals: learn, share something you've
  learned, or make connections.
- Twelve fictional attendees to get a demo going quickly.
- Elasticsearch-powered matching based on shared topics, complementary goals, and
  the things people can offer and want to learn.
- Small groups of up to four, with explanations grounded in enrollment answers.
- Two Python services with distributed traces, logs, and metrics.
- Docker Compose with hot reload for the frontend **and** both backend services.

## Pull up a chair

You'll need Docker with Compose. Give Docker at least **4 GB of memory**.

```bash
docker compose up --build -d
```

Open **http://localhost:5173**. Enroll yourself using fictional details, click
**Add demo attendees**, then **Make lunch groups**. Your table will be highlighted
for the current page session.

Making groups closes enrollment for that lunch. Group generation and seeding are
idempotent: repeating them doesn't create duplicate groups or seed profiles.
Groups are saved in Elasticsearch and survive container restarts.

| Local URL | What's there |
| --- | --- |
| http://localhost:5173 | The app |
| http://localhost:8000/docs | Conference API documentation |
| http://localhost:8000/health | API, matching service, and Elasticsearch readiness |
| http://localhost:9200 | Development Elasticsearch |

```bash
docker compose logs -f api matcher otel-collector
docker compose down
```

Source changes reload automatically. Rebuild after changing dependency manifests
or lockfiles. Ports can be overridden with `WEB_PORT`, `API_PORT`, and
`ELASTICSEARCH_PORT` in an optional `.env`; see `.env.example`.

To deliberately reset **all local attendees and groups**, stop the stack and
remove its Elasticsearch volume:

```bash
docker compose down --volumes
```

That command deletes this Compose project's saved demo data. Don't use it on a
stack whose data you want to keep.

## How the pieces fit

```text
React + Vite
     |
     v
Conference API -----> Matching service
     |                       |
     +----> Elasticsearch <--+

Python services --> OpenTelemetry Collector --> Dash0 (optional)
```

The **conference API** owns enrollment and organizer actions. The **matching
service** asks Elasticsearch for candidates, fills balanced tables, and saves the
assignment. Elasticsearch is the core data layer for profiles and completed plans;
there's no second database hiding the interesting work.

The first version uses keyword relevance and structured filters, not an LLM.
Conference and lunch slot are hard filters. Shared interests, complementary goals
(learning/sharing or meeting connections), and offer/need text influence ranking. Assignment
is a deterministic greedy heuristic, **not** a globally optimal matching algorithm.
Explanations use actual topic, goal, and word overlap rather than generated claims.

Next, we can add embeddings and hybrid retrieval without moving search out of
Elasticsearch. Future attendees could choose from recommendations rather than
receiving a single group.

## Observability, without setup friction

By default, the Collector prints telemetry summaries locally. No account or API
key is required. Requests propagate trace context from the API to the matching
service and Elasticsearch. Custom spans and counters cover enrollment and group
generation; HTTP instrumentation supplies request duration metrics.

To export to Dash0, copy `.env.example` to `.env`, fill in your **OTLP/HTTP base
endpoint**, auth token, and dataset, then run:

```bash
docker compose -f compose.yaml -f compose.dash0.yaml up --build -d
```

Credentials stay in the Collector, never in the browser. Search query capture is
disabled, and custom telemetry records counts rather than enrollment answers.
Browser real-user monitoring, dashboards, and an SLO are follow-up work—not
implemented yet.

## Working on the repo

```text
apps/web/            React + TypeScript + Vite
services/api/        Public FastAPI enrollment / organizer API
services/matcher/    Internal FastAPI matching API
packages/shared/    Python contracts, Elasticsearch helpers, telemetry
infra/otel/         Local and Dash0 Collector configurations
tests/              Backend behavior tests
.gitar/review/      Project-specific Gitar review guidance
node_modules/       npm workspace dependencies (generated, ignored by Git)
```

Frontend source, configuration, and build output stay under `apps/web/`. npm
workspaces share a root lockfile and hoist dependencies to root `node_modules/`;
Python dependencies live separately in the ignored root `.venv/`.

For host-side development, install Node.js 22 and [uv](https://docs.astral.sh/uv/).
The containers already include their runtimes.

```bash
npm ci
uv sync --locked --all-packages

npm run dev
# In other terminals, with Elasticsearch and the matcher reachable:
uv run uvicorn tabletalk_matcher.main:app --port 8001 --reload
uv run uvicorn tabletalk_api.main:app --port 8000 --reload
```

Prefer Compose for the complete stack. If running Python on the host while the
Compose stack is up, stop the container API first or choose different host ports.

```bash
npm run lint
npm run format:check
npm test
npm run build
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

On a **fresh, disposable** running stack, `uv run python scripts/smoke.py` also
exercises enrollment and grouping against real Elasticsearch. It writes fictional
profiles and closes enrollment, so don't run it against a demo you want to keep
open. CI runs it on its own temporary stack.

See [`agent.md`](agent.md) for a contributor/agent handoff, boundaries, and next
steps. To use Gitar, publish the repository when ready, install its GitHub app,
and develop through reviewed PRs. The repo includes review instructions and CI;
neither activates the app by itself.

## A hackathon demo, not a production deployment

This version has one conference, one lunch slot, and a 100-attendee cap. Organizer
controls and attendee profiles are intentionally public **within this local
instance**. There is no authentication or authorization. Elasticsearch security
is disabled for development, published ports bind to localhost, and internal
service/Collector ports are not published.

Use fictional profiles. Don't expose this stack publicly or collect real attendee
data until authentication, organizer permissions, consent, deletion, and data
retention are implemented. The API intentionally runs a single worker; scaling
requires distributed coordination for enrollment and matching.

Less awkward networking. More good conversations.
