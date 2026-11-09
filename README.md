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
- A mobile-first attendee onboarding prototype at `/user-onboarding`: about you,
  interests, goals with a starred focus and a question per goal, then a waiting
  room, a sample table, check-in, and icebreakers. It runs on sample data and
  isn't connected to the API yet.
- Twelve fictional attendees to get a demo going quickly.
- Elasticsearch-powered matching based on shared topics, complementary goals, and
  the things people can offer and want to learn.
- Small groups of up to four, with explanations grounded in enrollment answers.
- Two Python services with distributed traces, logs, and metrics.
- Docker Compose with hot reload for the frontend **and** both backend services.

## Pull up a chair

You'll need Docker with Compose **2.24.4 or newer**. Give Docker at least
**4 GB of memory** when using local Elasticsearch.

```bash
docker compose up --build -d
```

Open **http://localhost:5173** and enroll yourself using fictional details:

1. Enter your name, role, and topics.
2. Pick one or more of **Learn**, **Share**, or **Connect**, and answer the short
   prompt for each one.
3. If you picked more than one, choose today's focus.
4. Click **Submit me for lunch**.

You're then locked in. The page remembers you on this device, so it survives
refreshes, and your table appears on its own once groups are published.

Then open the host view at **http://localhost:5173/host**:

1. **Set up the room:** choose numbered tables, seats per table, an enrollment
   cutoff, and the smallest table allowed. Optionally, **match automatically at
   the cutoff** and **publish the automatic run right away**.
2. **Load 14 sample attendees** (three planted themes), then **Produce matches**.
   Each run is a draft with its own seed, so you can compare several.
3. **Review** a run: match-quality metrics compared with random seating, every
   table's summary and starters, and a searchable list of every person with their
   table, notes, and answers.
4. **Publish** one run. Attendees then see their own table number, their
   tablemates (first name, role, and interests), a 1–2 sentence "what you can
   talk about", and personal conversation starters.

With automatic matching on, the API's cutoff watcher does steps 2–4 for you right
after the cutoff: it produces a run and publishes it, or leaves it as a draft for
review. It runs once per cutoff, catches up if the API was down at the cutoff, and
shows its result on the host page. Set the polling interval with
`AUTO_MATCH_POLL_SECONDS` (default 10).

Enrollment closes at the cutoff or when a run is published, whichever comes first.
A run can't be published if someone enrolled after it was produced, so nobody is
left without a seat. Publishing and seeding are idempotent. Everything is saved
in Elasticsearch and survives container restarts.

Host controls are open on a local stack. Set `HOST_TOKEN` in `.env` to require a
token for the host view and host API.

| Local URL | What's there |
| --- | --- |
| http://localhost:5173 | The attendee app |
| http://localhost:5173/host | The host view: setup, runs, review, publish |
| http://localhost:5173/user-onboarding | Attendee onboarding prototype |
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

### Local Elasticsearch or Elastic Cloud

The root `.env` selects which Compose files to use. No `.env` means the default
local Elasticsearch container. Copy `.env.example` if you don't already have one;
don't overwrite an existing file containing credentials.

| `COMPOSE_FILE` in `.env` | Elasticsearch | Telemetry |
| --- | --- | --- |
| `compose.yaml` | Local container | Local summaries |
| `compose.yaml:compose.elasticsearch-cloud.yaml` | Elastic Cloud | Local summaries |
| `compose.yaml:compose.dash0.yaml` | Local container | Dash0 |
| `compose.yaml:compose.elasticsearch-cloud.yaml:compose.dash0.yaml` | Elastic Cloud | Dash0 |

For cloud mode, also set:

```dotenv
ELASTICSEARCH_URL=https://your-elasticsearch-endpoint:443
ELASTICSEARCH_API_KEY=your-encoded-elasticsearch-api-key
```

Use the Elasticsearch endpoint, not Kibana or a Cloud ID. Use an Elasticsearch
API key's encoded value without an `ApiKey` prefix, not an Elastic Cloud
management key. For an Elastic Cloud Hosted deployment, grant cluster `monitor`
and index `manage`, `read`, and `write` on all five application indices
(`tabletalk-attendees-v1`, `-plans-v1`, `-events-v1`, `-runs-v1`, `-tokens-v1`, or
the pattern `tabletalk-*`). TLS verification stays enabled. For the hosted
deployment's AI and host-token secrets, see `infra/radar/README.md`.

Elastic Cloud Serverless is also supported. Use a project API key with index
management and document read/write permissions. Startup detects Serverless and
leaves shard/replica settings to Elastic; readiness searches the application
indices instead of using the unavailable cluster-health API.

The same command starts either mode:

```bash
docker compose up --build -d
```

Cloud mode removes the local Elasticsearch service and its startup dependencies.
Only the API and matcher receive its credentials. Local mode ignores cloud
credentials, even if they remain in `.env`. Hot reload works in both modes.
Explicit `-f` flags override `COMPOSE_FILE`, so include the cloud overlay if using
those flags. The `:` file separator above is for macOS/Linux; Windows uses `;`.

**Switching modes:** run `docker compose down` with the old selection first,
then update `.env` and start again. Don't add `--volumes`: each database keeps
its own attendees and plans, and data is not automatically migrated. In cloud
mode, `down --volumes` does not reset or delete cloud data.

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

Seating uses keyword relevance and structured filters, not an LLM. Conference
and lunch slot are hard filters. Elasticsearch scores how well every
pair of attendees fits, using shared interests, complementary goals
(learning/sharing or meeting connections), and Learn/Share/Connect answers. A
seeded optimizer then seats everyone so tablemates fit well and nobody is
stranded. It is deterministic and heuristic, **not** provably optimal. Each table
gets a summary, conversation starters, and per-person notes that quote real
overlaps rather than generated claims. Plans include quality metrics compared with
random seating. See [`docs/matching-plan.md`](docs/matching-plan.md).

### Optional AI notes and personas

An LLM can rewrite each table's summary, starters, and per-person notes. It
**never** chooses seats: runs with and without AI notes seat everyone identically.
Each table is sent only its own people and the matcher's evidence. The model must
return a strict JSON structure, and the notes are then checked:

- the notes must cover exactly that table's people;
- they must not name anyone from another table;
- over-long text is clipped.

Invalid output gets one retry, then that table keeps its template notes. A
45-second overall deadline means a slow provider can't hold up the run.

The same provider can also write fictional **personas** for testing. Each persona
must pass the same validation as a real enrollment.

To enable it, set these in `.env`, then restart the stack. The host view then
shows AI toggles for notes and personas:

```bash
LLM_PROVIDER=anthropic          # or openai (any OpenAI-compatible API via LLM_BASE_URL)
LLM_MODEL=<model id from your provider>
ANTHROPIC_API_KEY=...           # or OPENAI_API_KEY
```

Keys stay in the Python services. The browser, telemetry, and logs never see
them or the prompt text. With AI enabled, attendee answers are sent to the
provider, and the enrollment form says so. Without a provider, everything uses
templates.

### Test data and measuring match quality

The host view can load the 14-person sample or **generate** any number of
fictional attendees. Generation works offline: a deterministic, seeded generator
draws from six themes with complementary Learn/Share topics, and makes about 20%
of people cross-theme "bridges". It can also use the AI provider. To save a
dataset you can replay, or to measure quality against random seating:

```bash
uv run python scripts/generate_personas.py --count 40 --seed 7 --out personas.json
uv run python scripts/evaluate_matching.py              # 5 seeds × 40 people
uv run python scripts/evaluate_matching.py --file personas.json
uv run python scripts/evaluate_matching.py --check      # CI regression guard
```

The evaluation reports the following, each next to the random-seating value:

- average fit;
- the weakest person's best match;
- Learn coverage;
- how well tables regroup each person's hidden theme (purity and adjusted Rand
  index).

### Semantic matching and the AI judge

Set `EMBEDDING_MODEL` (e.g. `text-embedding-3-small`) with an `OPENAI_API_KEY` to
add **hybrid retrieval** inside Elasticsearch.

- **Vectors:** at match time, anyone without vectors gets three 512-dimension
  `dense_vector` fields (learn, share, topic) stored on their attendee document.
- **Topic tags:** with an LLM configured, attendees also get short canonical
  topic tags, so "vuln triage" and "CVE prioritization" can line up.
- **Caching:** both are computed once and reused across runs.
- **Scoring:** each run blends BM25 keyword relevance with Elasticsearch kNN
  similarity. The kNN step pairs one person's Learn answer with another's Share
  answer, and compares topics.
- **Fallback:** a provider failure falls back to BM25.
- **Labelling:** the host page labels every run (`bm25`, `bm25+vectors+tags`)
  and has a toggle to compare the modes.

**Ask the AI judge** scores each table 1–5 for conversation potential and seats
the same people at random for comparison. It is a coarse second opinion: it never
changes seats, and repeat scores vary by about ±0.3.

Measured on 39 LLM-written personas and the 14-person sample, hybrid improved how
often each person's best match shares their theme (79%→86% and 83%→92%). It left
whole-table theme recovery and the judge's score within noise of keyword-only.
Details are in `docs/matching-plan.md` §11.

Tested defaults: `LLM_MODEL=gpt-5.4-mini`, `LLM_REASONING_EFFORT=low`,
`EMBEDDING_MODEL=text-embedding-3-small`. Approximate cost at list prices:

| Action | Cost |
| --- | --- |
| A 25-table run with AI notes | about $0.09 |
| Judging that run | about $0.05 |
| 40 AI personas | about $0.10 |
| Embeddings | fractions of a cent |

Future attendees could choose from recommendations rather than receiving a single
group.

## Observability, without setup friction

By default, the Collector prints telemetry summaries locally. No account or API
key is required. Requests propagate trace context from the API to the matching
service and Elasticsearch. Custom spans and counters cover enrollment and group
generation; HTTP instrumentation supplies request duration metrics.

To export to Dash0, add `compose.dash0.yaml` to `COMPOSE_FILE` as shown above,
and fill in your **OTLP/HTTP base endpoint**, auth token, and dataset in `.env`.
Then run:

```bash
docker compose up --build -d
```

Credentials stay in the Collector, never in the browser. Search query capture is
disabled, and custom telemetry records counts rather than enrollment answers.
Browser real-user monitoring, dashboards, and an SLO are follow-up work—not
implemented yet.

## Live hackathon deployment

Radar provides our Kubernetes cluster at **https://tabletalk.cnh.radarhq.io**.
The deployment files, safe Secret upload, Dash0 live-workload telemetry, and
one-time Argo CD setup are documented in [`infra/radar/README.md`](infra/radar/README.md).
After setup, passing CI on `main` publishes images and updates GitOps; Argo CD
deploys them and CI checks the live version. PRs validate without deploying.
Compose remains the local development workflow.

The live demo is intentionally open for **fictional profiles only**, with no
authentication. It must not be used for real attendee data.

## Working on the repo

```text
apps/web/            React + TypeScript + Vite
services/api/        Public FastAPI enrollment / organizer API
services/matcher/    Internal FastAPI matching API
packages/shared/    Python contracts, Elasticsearch helpers, telemetry
infra/otel/         Local and Dash0 Collector configurations
infra/radar/        Provided cluster, GitOps, and live Dash0 configuration
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

This version has one conference, one lunch slot, and a 100-attendee cap. Attendee
profiles are intentionally public **within this local instance**. Host controls are
open unless the optional shared `HOST_TOKEN` is set, and there are no user accounts
or per-attendee authorization. Elasticsearch security
is disabled for development, published ports bind to localhost, and internal
service/Collector ports are not published.

Don't expose the local development ports publicly. The hosted demo is deliberately
open for fictional profiles only; don't collect real attendee data until
authentication, organizer permissions, consent, deletion, and data retention are
implemented. The API intentionally runs a single worker; scaling requires
distributed coordination for enrollment and matching.

Less awkward networking. More good conversations.
