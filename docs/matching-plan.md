# Matching plan (`scott/matching`)

Status: steps 0–8 are implemented on `scott/matching` (see §8 and §11).
Everything else is still a proposal.

## 1. The workflow we are building

```text
Host setup ──► Enrollment open ──► Cutoff (e.g. 11:40) ──► Match run ──► Review ──► Publish
 tables,        attendees answer      enrollment locks       seats +       host       attendees see
 seats,         Learn / Share /       server-side            narratives    checks     table # and
 cutoff         Connect, then                                              quality    conversation
                "Submit me for lunch"                                                 starters
```

1. **Host setup:** the host creates numbered tables, sets seats per table and the
   cutoff time, and turns on auto-match.
2. **Enrollment:** attendees enter their name and role, then answer short **Learn**,
   **Share** and **Connect** prompts and submit. Their entry is then locked.
3. **Cutoff:** the server closes enrollment at the cutoff time. The browser clock
   is never trusted.
4. **Match run:** happens automatically at cutoff, or when the host clicks
   "Produce matches". The run assigns every attendee to exactly one numbered table,
   then writes a table summary plus personal conversation starters for each person.
5. **Host review:** a per-table and per-person view showing reasons, starters and
   quality metrics. The host can re-run before publishing.
6. **Publish:** each attendee sees their table number, tablemates, a 1–2 sentence
   "what you can talk about" summary and a few starters.

## 2. The core question: Elasticsearch or an LLM?

**Recommendation: a hybrid with a strict split of responsibilities. Don't let an
LLM choose seats.**

| Job | Best tool | Why |
| --- | --- | --- |
| Understand free text ("patching vulns faster with AI" ≈ "AppSec automation") | Elasticsearch now (BM25 + English analyzer); vectors later | Fast, deterministic, cheap, demo-friendly, already in the stack. Vectors fix the synonym gap. |
| Score how good each **pair** of people is | Elasticsearch scores → pairwise affinity matrix | ≈100 attendees means ≈100 queries or 4,950 pairs. That's trivial, and results are reproducible. |
| Choose **seats** (everyone seated once, capacities, no stranded people) | Plain Python optimizer | This is a constrained combinatorial problem. LLMs drop people, duplicate people, ignore capacities, and give different answers each run. |
| Explain matches and write conversation starters | **LLM** (Claude or OpenAI) | This is where LLMs are clearly better: natural, specific, human-sounding text. |
| Optional: tidy intake into topic tags | LLM at enrollment (later) | Improves Elasticsearch recall without moving matching out of Elasticsearch. |

Why not have the LLM do the matching too?

- **Validity:** you'd still need a deterministic validator plus a repair step. At
  that point the optimizer is doing the real work anyway.
- **Scale and latency:** 100 people × 3 answers fills a long prompt, and output
  quality drops as the prompt grows. A single slow call at 11:40 blocks lunch.
- **Repeatability:** hosts need to be able to explain and re-run results. A fixed
  seed makes our optimizer give the same result every time.
- **Project direction:** `agent.md` says to keep Elasticsearch central to
  retrieval rather than replacing matching with an external LLM call.

The LLM still makes the result *feel* smart, because every reason and starter
comes from it, grounded in evidence the deterministic pipeline found.

## 3. Matching pipeline (matcher service)

```text
snapshot ─► affinity matrix ─► table sizing ─► optimize ─► validate ─► narrate ─► validate ─► save run
  (ES)          (ES)            (pure)        (pure)      (pure)       (LLM or     (pure)       (ES)
                                                                       template)
```

### 3.1 Snapshot
At cutoff, read every enrolled attendee for the event (the existing conference
and slot filters). Store the attendee IDs and the host's table config in the
run, so the run can be reproduced later.

### 3.2 Pairwise affinity, `A[i][j]` in `[0, 1]`
For each attendee, run one Elasticsearch query restricted to the snapshot
(`size = N`), then normalize the scores within that query:

| Signal | Query | Weight (start) |
| --- | --- | --- |
| **Learn ↔ Share** (i's *Learn* vs j's *Share*, and the reverse) | `match` on `goal_answers.Share…` with i's Learn text | high (3) |
| **Connect ↔ Connect** shared discussion topics | `match` between Connect answers | medium (2) |
| Interest overlap | `terms` on `interests.exact` + `match` on `interests` | medium (2) |
| Goal complementarity (Learn↔Share, Connect↔Connect) | `terms` on `goals` | low (1) |
| Role diversity | Python bonus for different roles | small (0.5) |

Make the matrix symmetric with `(s_ij + s_ji) / 2`. Weights live in one config
object, so we can tune them against the evaluation harness (§6).

The later upgrade adds a `dense_vector` field holding embeddings computed at
enrollment. Elasticsearch then adds kNN similarity to the same matrix
("hybrid retrieval"). Nothing else in the pipeline changes.

### 3.3 Table sizing (remainders handled before optimizing)
Input: the host's numbered tables `[{number, seats}]` and `N` attendees.

1. If `N > Σ seats`, that can't happen: enrollment stops at capacity.
2. Use the **fewest tables that seat everyone**, preferring the largest tables
   (ties by table number). Then **water-fill**: seat people one at a time at
   the emptiest table that still has a free chair. This keeps table sizes within
   one person of each other wherever the seat counts allow.
3. **No table of one.** Because the chosen tables are already the fewest that
   fit, dropping one can never help. If a table would fall below
   `min_table_size` (default 2), the layout is rejected with a clear
   `SeatingError` that the API returns as a 409.
4. If `N == 1`, nobody can be matched. The run status becomes `needs_host`, and
   the person is shown on the host page as unseated with a clear prompt, not
   silently dropped.

This is a pure function and easy to cover with tests (tables of different
sizes, `N = 0 … capacity`).

### 3.4 Optimize
Goal: maximize the total affinity between people at the same table, plus
**λ × each person's best tablemate score**. The second part is a fairness
term, so nobody gets stuck at a table with no real connection.

1. **Greedy start:** seed tables with the hardest-to-match people first (lowest
   best-affinity). Then add whoever raises the table's score most.
2. **Local search:** swap two people between tables whenever it improves the
   score, until nothing improves. Table sizes are fixed by step 3.3, so only
   swaps are needed.
3. **Seeded restarts:** swapping alone gets stuck. On the live 14-person fixture,
   a single start reached a score of 22.2, while the best seating scored 24.3.
   So the search also runs from `clamp(2000 // N, 8, 96)` seeded random seatings
   and keeps the best. The same snapshot and seed always give the same plan.
4. **Measured cost:** about 0.25 s live for 14 people. For 100 people, about
   2–4 s on a laptop, which is fine for a run that happens once at cutoff. No
   solver library is needed.

### 3.5 Validate the seating
Every snapshot attendee appears exactly once. Every table number exists, its
size equals the planned size and doesn't exceed its seats. A failure is a bug:
fail the run loudly and never publish a partial plan.

### 3.6 Narrate (LLM, with a guaranteed fallback)
- **One LLM call per table**, run in parallel with a concurrency limit and a
  timeout (around 20 seconds).
- **Input:** table number, and for each member their ID, first name, role,
  interests and Learn/Share/Connect answers. Also include the **evidence**: the
  strongest pairwise links the optimizer found (who can help whom, shared
  topics).
- **Output:** structured JSON enforced by the provider (Claude tool use, or
  OpenAI `json_schema`) and then **validated by Pydantic**:
  - The member IDs must be exactly this table's IDs, each once.
  - Text has length limits.
  - Starters must mention only people at this table.
- If the output is invalid, retry once. If it's still invalid, or there's no
  API key, or the call times out, fall back to the **deterministic template**
  (`narratives.py`: it quotes the longest phrases one person offers and another
  asks about, as written, and never invents details). The run never fails because of the LLM; it
  just records `narrative_source: "llm" | "template"` per table.

### 3.7 Save
Each run is stored as its own document in `tabletalk-runs-v1` (ID = run ID).
Saving the same run again is safe and gives the same result. Publishing writes
the chosen run ID to the event with a create-only write, so only one run per
event is ever published.

## 4. Prompts (drafts, versioned in code)

Prompts live in `services/matcher/src/tabletalk_matcher/prompts/` with a
`PROMPT_VERSION` constant. Each run records the version so results can be
compared across prompt changes.

### 4.1 Table narrative prompt
```text
SYSTEM
You write warm, specific lunch-table introductions for a tech conference.
Use ONLY the facts provided. Never invent employers, projects, or personal details.
Refer to people by first name. Keep a friendly, practical tone; no hype.
Return data via the provided tool/schema only.

USER
Table {number}. Members (JSON): {members}
Evidence from the matcher (JSON): {links}
Write:
- table_summary: 1–2 sentences on what this table has in common or can trade.
- conversation_starters: 3 open questions the whole table could discuss.
- For EACH member: summary (1–2 sentences, second person, "You and Priya both…")
  and 2 personal starters that reference a specific tablemate.
```

Output schema (Pydantic → JSON schema):
```python
class MemberNarrative(BaseModel):
    attendee_id: str
    summary: Annotated[str, Field(max_length=300)]
    starters: Annotated[
        list[Annotated[str, Field(max_length=200)]], Field(min_length=2, max_length=3)
    ]


class TableNarrative(BaseModel):
    table_summary: Annotated[str, Field(max_length=300)]
    conversation_starters: Annotated[list[str], Field(min_length=3, max_length=3)]
    members: list[MemberNarrative]
```

### 4.2 Mock-attendee (persona) generator prompt
```text
SYSTEM
You generate realistic, fictional conference attendees for testing a lunch-matching app.
People must be plausible and varied (seniority, company size, industry, region), with
specific first-person answers like a real person would type on a phone: 1–2 sentences,
concrete technologies, occasional informal tone. No real people or real companies' secrets.

USER
Create {count} attendees. Random seed (for variety): {seed}.
Distribute them across these hidden themes, roughly evenly, with ~20% cross-theme people:
{themes}   # e.g. "Kubernetes at e-commerce scale", "AI-assisted vuln patching", ...
Each attendee selects 1–3 goals from [Learn, Share something I've learned, Meet connections]
and answers ONLY the selected goals. Include a hidden `theme` label per attendee.
```
The output reuses the `Enrollment` schema plus a hidden `theme` field that is
used only for evaluation. Every generated person passes the same server-side
validation as a real enrollment.

## 5. Contracts and data changes

| Area | Change |
| --- | --- |
| **Event config** (new `tabletalk-events-v1`) | `tables: [{number, seats}]`, `enrollment_closes_at` (ISO date with time zone), `auto_match`, `auto_publish`, `min_table_size`, `status: setup \| open \| closed \| matched \| published`, `published_run_id`. Capacity = Σ seats. Replaces the hard-coded `GROUP_SIZE` / `MAX_ATTENDEES`. |
| **Intake** | The canonical inputs become `goal_answers` for Learn, Share and Connect (already on `Enrollment`). `can_help` / `want_to_learn` are phased out of the UI and kept only as a fallback for older data. Fix the goal-coercion bug first (see §8). |
| **Attendee identity** | Enrollment returns a random `attendee_token`, kept in the browser's `localStorage`. `GET /api/me/assignment` uses it. Demo-grade, no accounts. |
| **Host access** | A `HOST_TOKEN` environment variable is required for `/api/host/*`. Demo-grade; much better than the current open organizer buttons. |
| **Run** (new `tabletalk-runs-v1`) | `run_id`, `seed`, `prompt_version`, `weights`, config snapshot, `tables: [{number, member_ids, table_summary, starters, narrative_source}]`, `members: {id: {table, summary, starters, reasons}}`, `metrics`, `status`. |

### API surface
| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| `GET/PUT` | `/api/host/event` | host | Tables, seats, cutoff, auto settings |
| `POST` | `/api/host/runs` | host | Produce matches now (creates a draft run) |
| `GET` | `/api/host/runs`, `/api/host/runs/{id}` | host | Results, per-table and per-person detail, metrics |
| `POST` | `/api/host/runs/{id}/publish` | host | Make a run visible to attendees |
| `POST` | `/api/host/demo/attendees` | host | Load the fixture set or generate N personas with an LLM (`seed`, `themes`) |
| `POST` | `/api/enrollments` | attendee | Submit me for lunch (rejected after cutoff or at capacity) |
| `GET` | `/api/me` | attendee | Status and, after publish, table, tablemates, summary, starters |
| `PUT` | `/api/me` | attendee | Edit answers while sign-ups are open |
| `POST` | `/api/me/check-in` | attendee | "I'm seated" at a published table |
| `POST` | `/runs` (internal matcher) | API only | Run the pipeline in §3 |

### Cutoff and scheduling
An `asyncio` task in the API waits until `enrollment_closes_at` and then:

1. closes enrollment (under the existing mutation lock);
2. calls the matcher;
3. auto-publishes if the host turned that on.

On startup, if the cutoff has already passed and nothing was published, it
catches up by running these steps immediately. This relies on the documented
single-API-worker assumption, so mention it in PRs.

## 6. Test data and quality evaluation

1. **Static fixture:** `packages/shared/src/tabletalk_shared/fixtures/three-tables.json`
   (loaded with `load_fixture()`), 14 hand-written
   people across 3 clear themes, plus 2 "bridge" people. Used by unit tests and
   the smoke test. Needs no API key.
2. **LLM persona generator:** `scripts/generate_personas.py --count 40 --seed 7
   --themes …`. It saves results to `fixtures/attendees/generated/*.json`, so a
   good dataset can be replayed and committed. It's also exposed through
   `POST /api/host/demo/attendees` for live demos.
3. **Offline random generator:** builds combinations from topic, role and answer
   templates, so CI and offline demos can create unique data without an LLM.
4. **Quality metrics**, computed for every run and shown to the host:
   - Seated: N / N, with 0 unseated (must always hold)
   - Table sizes vs plan
   - Learn coverage: % of people whose Learn answer has a matching Share answer
     at their table
   - Average table affinity, and the weakest person's best-tablemate score
   - Uplift vs a seeded **random baseline** run on the same data
   - Theme purity / adjusted Rand index against the planted `theme` labels
     (generated data only)
5. **Optional "LLM-as-judge":** score each table 1–5 for conversation potential.
   Shown to hosts as a second opinion, never used to change seats.

## 7. Frontend

- **Attendee:**
  - The enrollment form gets three answer boxes (Learn / Share / Connect) with
    example hints from `GOAL_PROMPTS`, and a "Submit me for lunch" button.
  - After submitting, a locked confirmation shows a countdown to the cutoff.
  - After publish: table number, tablemates (first name and role), "What you can
    talk about", and starters.
- **Host (`/host`):**
  - Event setup: table list with seats, total capacity and cutoff.
  - Live enrollment count.
  - Demo data tools: load the fixture or generate N personas.
  - **Produce matches** and **Publish** buttons.
  - Results: metrics bar, one card per table (members, table summary, starters,
    narrative source), and a searchable list of every person with their table,
    answers, reasons and starters.

## 8. Build order (each step can be demoed)

| # | Milestone | Depends on | Can run in parallel |
| --- | --- | --- | --- |
| 0 ✅ | Fix goal coercion so only stored data is migrated; add the static 3-table fixture | none | yes |
| 1 ✅ | Pure core: `plan_table_sizes`, affinity-matrix builder (Elasticsearch), optimizer, validator, metrics, template narratives, plus tests | 0 | Core of the feature |
| 2 ✅ | Event config, cutoff enforcement, runs index, host endpoints, `HOST_TOKEN` | 0 | yes, alongside 1 |
| 3 ✅ | Host page: setup, produce matches, results by table and person, metrics | 1, 2 | |
| 4 ✅ | LLM provider abstraction (`none` / `anthropic` / `openai`), table narrative prompt, schema validation, fallback | 1 | yes, alongside 3 |
| 5 ✅ | Persona generator (script + host endpoint), offline random generator, evaluation report vs random baseline | 0 | yes, alongside 1–4 |
| 6 ✅ | Attendee token, `/api/me/assignment`, new enrollment UI, publish flow | 2, 4 | |
| 7 ✅ | Cutoff scheduler with auto-match and auto-publish, plus catch-up on startup | 2 | |
| 8 ✅ | Stretch: embeddings and hybrid kNN in Elasticsearch, LLM tag enrichment at enrollment, LLM-as-judge | 1 | |

**Demo milestone after steps 0–3:** load the fixture, produce matches, and
see the tables on the host page with template reasons. No API key needed.
Each later step makes that demo richer.

## 9. Ground rules carried over from `agent.md`

- API keys exist only in server environment variables, never in the browser.
  `.env.example` gets `LLM_PROVIDER`, `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` and
  `LLM_MODEL`.
- Never log or attach to telemetry spans any prompts, answers or names. Telemetry
  records counts, durations, token usage, fallback counts and the prompt version.
- Attendee text is sent to a third-party LLM, so the enrollment form must say so.
  Use fictional data for the demo.
- The internal matcher stays unpublished. Seating stays deterministic for a given
  `(snapshot, config, seed, weights)`.

## 10. Assumptions to confirm

- Minimum table size is **2**. Should it be 3?
- Use as few tables as possible, largest first (ties by table number). The
  alternative is to always spread people across every table.
- Each attendee answers **at least one** of Learn / Share / Connect, not
  necessarily all three.
- Hosts can re-run and compare drafts before publishing. After publishing,
  seats are final.
- Tablemates see each other's first name and role, but not their full answers.
- LLM provider for narratives: _TBD_ (Claude or OpenAI). The rest of the code
  doesn't depend on which one we choose.

## 11. Progress log

**Steps 0–1 (done)**
- Goal validation:
  - New enrollments must have 1–3 current goals.
  - Only stored profiles migrate legacy goals.
  - This fixes two tests on `main` that were already failing, plus a mypy error.
- The 14-person fixture with hidden themes, and its loader.
- `sizing.py`, `affinity.py` (Elasticsearch query plus a lexical stand-in),
  `optimizer.py`, `narratives.py` and `matching.py` (validation and metrics).
- The matcher `/match` endpoint now uses the Elasticsearch affinity pipeline.
- `LunchGroup` and `MatchPlan` gained optional fields: `table_number`,
  `summary`, `starters`, `member_notes`, `narrative_source`, `seed` and
  `metrics`. Plans saved before this change still load.
- The web table cards show the summary and starters, and your personal note
  on your own table.

**Verified against live Elasticsearch** (fixture, default 4-seat tables):
- **Grouping:** the platform, security and data-ml themes are all regrouped.
- **Fit:** average pair fit is 0.63, against 0.25 for random seating.
- **Weakest person:** their best tablemate scores 0.68, against 0.06 for random.
- **Smoke test:** `scripts/smoke.py` passes.

**Known limits after steps 0–1**
- With the default 4-seat tables, 14 people sit at 4/4/3/3 and one table mixes
  themes. Host setup (step 2) fixes this.
- Learn coverage is 64–93% depending on layout. It uses strict phrase overlap,
  so synonyms are missed until embeddings are added (step 8).

**Steps 2–3 (done)**
- **Event setup:** `EventConfig` (numbered tables, seats, an aware-datetime cutoff,
  minimum table size) is stored in `tabletalk-events-v1`.
  - Capacity is the total seats, capped at 100.
  - Enrollment returns 409 after the cutoff, after publishing, or when full.
  - `/api/conference` reports `status`, the cutoff and capacity.
- **Runs:** the matcher's `/match` was replaced by a compute-only `POST /runs`.
  The API stores drafts as `MatchRun` documents in `tabletalk-runs-v1` (random
  seed unless one is given).
- **Publishing:** this is a create-only write of the run's plan to
  `tabletalk-plans-v1`.
  - Publishing the same run again is idempotent; a different run gets a 409.
  - Publishing is refused if anyone enrolled or left since the run, so nobody
    is left without a seat.
  - It is also refused if the table setup changed after the run, so nobody is
    sent to a table that no longer exists. (This came from code review.)
- **Host token:** `HOST_TOKEN` (optional) guards every host endpoint, including
  the older `/api/demo/seed` and `/api/matches`. Attendee endpoints never need it.
- **Host page (`/host`):**
  - status bar;
  - table editor with a quick "N tables × S seats" layout, cutoff and minimum
    size;
  - sample data loader;
  - Produce matches, a run list, and a review view with a metrics bar (each
    metric next to its random-seating value), table cards (summary, starters,
    the reasons behind each pairing), and a searchable list of every person
    (table, personal note, starters, answers);
  - Publish, disabled with an explanation when someone would be left unseated.
- **Attendee page:** the organizer buttons moved to the host page. It shows the
  cutoff and a "closed, groups coming" state.
- **Verified live:**
  - The extended `scripts/smoke.py` passes (draft → publish → conflict →
    setup lock).
  - Walked through the browser flow end to end with a 3×5 layout. All three
    themes were regrouped exactly (5/5/4), with average fit 59% vs 26% random
    and Learn coverage 93%.

**Steps 4–5 (done)**
- **LLM client** (`tabletalk_shared/llm.py`):
  - Uses raw `httpx`, so there is no new SDK or lockfile change.
  - Claude: forced tool use (`tool_choice`) with the schema as `input_schema`.
  - OpenAI-compatible APIs: `response_format: json_schema` in strict mode, with
    an optional `LLM_BASE_URL`.
  - `LLM_MODEL` is required rather than defaulted, because model IDs go stale.
    A misconfiguration disables AI with a reason that the host view shows.
  - Telemetry records only request outcomes and token counts.
- **AI table notes** (`tabletalk_matcher/llm_notes.py`): implements §3.6.
  - The output is checked against the exact people at each table.
  - Notes may not name anyone from another table. Names are matched as
    written, because a live test showed that case-insensitive matching rejected
    valid notes when an outsider's name was also an ordinary word.
  - Over-long text is clipped.
  - Invalid output is retried once, then that table keeps its template notes.
    An overall 45 s deadline applies.
  - Runs accept `use_llm`. The seating is identical with and without AI.
- **Personas** (`tabletalk_shared/personas.py`):
  - The offline generator is seeded and draws from six themes × five topics,
    each topic with a learn question and a share statement, plus ~20% bridges.
  - The LLM generator works in batches of 8. Personas that fail enrollment
    validation are dropped, and duplicate names are removed.
  - `scripts/generate_personas.py` writes fixture files in the same format as
    the bundled sample.
  - The host can choose "Generate N attendees" (offline or AI) with an optional
    seed. Repeating a seed adds nobody new.
- **Evaluation:** `tabletalk_matcher/evaluation.py` (theme purity and adjusted
  Rand index) and `scripts/evaluate_matching.py` (markdown report, `--check` in
  CI).
  - On 5 seeds × 40 generated people, the measures compared with random
    seating were:

    | Measure | TableTalk | Random |
    | --- | --- | --- |
    | Average fit | 61% | 23% |
    | Weakest person's best match | 36% | 11% |
    | Theme ARI | 0.67 | 0.02 |

  - Learn coverage was 58%.
  - The bundled sample reaches ARI 1.0.
- **Verified live:** no real API key was available. The stack ran with
  `LLM_PROVIDER=openai` pointed, via `LLM_BASE_URL`, at a local mock
  OpenAI-compatible server. This confirmed:
  - the environment wiring in Compose;
  - the matcher's outbound calls and schema;
  - AI persona generation;
  - 6 of 6 tables with AI notes, and identical seating with AI off;
  - the host page toggles and "notes by …" labels;
  - the smoke test passing.
- **Fixed after code review:**
  - A run is labelled AI-written only if some table really shows AI text.
  - `/api/matches` never calls the LLM while holding the mutation lock.
  - A non-JSON reply from the provider raises `LLMError`, so it gets a retry
    or a clean 502.
  - AI personas get unique IDs per call, so regenerating with the same seed
    adds new people.
- **Still to check with a real provider:** a real model's output quality and
  how often it falls back to templates.

**Steps 6–7 (done)**
- **Attendee identity:**
  - Enrollment returns a random `attendee_token`. Only its SHA-256 hash is
    stored, in `tabletalk-tokens-v1`, and the token link is written before the
    attendee.
  - `GET /api/me` returns the person's status and, after publishing, their
    table: tablemates as first name, role, and interests only, plus their note,
    starters, and the table summary.
  - Shipped as `/api/me` rather than `/api/me/assignment`.
- **Onboarding:**
  - Name, role, and topics.
  - Learn / Share / Connect chips, each with its own prompt. The prompts come
    from the API (`goal_prompts`), with the example hints as placeholders.
  - A "today's focus" choice when more than one goal is picked.
  - "Submit me for lunch".
  - `can_help` / `want_to_learn` are now optional. An enrollment needs either an
    answer for each goal or both of those fields.
- **Attendee page:**
  - The token is kept in `localStorage` behind a guarded store; if storage is
    blocked, the page works for the session.
  - Shows "You're in for lunch" with your answers and the cutoff.
  - Checks back every 20 s while waiting.
  - Then shows "You're at Table N" with tablemates, your summary, your starters,
    and the table summary, plus "Forget this device".
- **Cutoff watcher:**
  - `EventConfig.auto_match` / `auto_publish`. Auto-match requires a cutoff,
    and auto-publish requires auto-match.
  - `run_due_cutoff` runs once per cutoff and records an `AutoMatchState`, so
    a restart or catch-up never runs twice and a new cutoff is eligible again.
  - It never runs after publishing, and records failures (e.g. fewer than two
    attendees) for the host to see.
  - `cutoff_watcher` polls every `AUTO_MATCH_POLL_SECONDS` and survives storage
    errors.
  - The host page has the two toggles, a status line, and a 20 s refresh. The
    demo-data buttons are disabled once enrollment closes.
- **Verified live:**
  1. The extended smoke test passes (tokens, `/api/me` before and after
     publishing).
  2. Full lunch flow:
     - the host set 3×5 tables with a cutoff about 90 s out, plus auto-match and
       auto-publish;
     - I enrolled through the new UI;
     - the watcher matched and published 1.7 s after the cutoff;
     - a late enrollment got 409;
     - my open page switched to "You're at Table 2" on its own, with the
       platform-themed tablemates.
  3. Restarting the API did not re-run the watcher.
  4. Catch-up: with the API stopped across the cutoff in draft-only mode, it
     made exactly one draft about 1 s after restarting, and enrollment was
     closed but not published.

- **Fixed after code review:**
  - The watcher waits for any enrollment that started before the cutoff to
    finish before matching. Otherwise a last-second sign-up could make the
    auto-run fail permanently.
  - The attendee page only applies the newest refresh for the current token,
    so an overlapping refresh can't hide your enrollment, which risked a
    duplicate sign-up, or bring back a forgotten person. Covered by a
    regression test that fails without the fix.

**Step 8 (done)**
- **Model choice:** benchmarked live on the real notes pipeline, with all
  candidates passing validation.

  | Model | 3 parallel tables | Notes |
  | --- | --- | --- |
  | gpt-4.1-mini | 5.5 s | |
  | gpt-5-mini (minimal) | 8.5 s | |
  | gpt-5.4-mini (low) | **3.9 s** | most natural, specific text |
  | gpt-5.4-nano (low) | 5.2 s | |

  Chosen: `gpt-5.4-mini` with `LLM_REASONING_EFFORT=low`, at about $0.09 per
  25-table run at list price, plus `text-embedding-3-small` at 512 dimensions.
  `LLM_REASONING_EFFORT` is passed as `reasoning_effort` on OpenAI requests.
- **Enrichment instead of tagging at enrollment:** vectors and tags are
  backfilled at match time, cached on attendee documents, and keyed by model.
  Enrollment never waits on a third party, and seeded or generated attendees are
  covered too. Provider failures fall back to BM25.
- **Hybrid scoring:**
  - Each attendee gets one kNN search: my learn vector against their share
    vector (×2), my share against their learn (×2), and topic against topic
    (×1).
  - BM25 rows are max-scaled and kNN rows are min-max scaled, then blended
    with `semantic=0.75`.
  - Topic tags also join the BM25 query.
- **Live bug found and fixed:** Elasticsearch 9.2 omits dense vectors from
  `_source` by default. Cached runs therefore silently skipped kNN while still
  being labelled `vectors`. Vectors are now read with `exclude_vectors: false`,
  and runs are only labelled `vectors` when some attendee actually has them.
  Regression tests cover both.
- **Experiments** (real embeddings, real LLM-written personas):

  | Dataset | Nearest match same theme: keyword → hybrid 0.75 | Theme ARI |
  | --- | --- | --- |
  | Sample, original wording | 83% → 92% | 1.00 both |
  | Sample, mixed paraphrased wording | 83% → 92% | 1.00 both |
  | 39 LLM personas | 79% → 86% | 0.90 → 0.87 |

  - Blend 0.5 was worse on the LLM personas (76%), and pure semantic was
    worse than hybrid there (79%), so 0.75 was chosen.
  - LLM-written personas use concentrated vocabulary per theme, so keyword
    matching is already strong.
- **AI judge:**
  - Rubric v2 makes broad overlap score 2 and requires specific learn↔share
    pairings for a 4–5.
  - On a fixed plan it is repeatable within ±0.06, but across sessions it
    varies by about ±0.3.
  - Keyword and hybrid scored within that noise (≈4.3–4.7), and both beat
    random seating (≈3.6–4.2).
  - Treat it as a coarse sanity check and a source of per-table rationale
    (e.g. "Tomás has no clear specific match"), not a fine-grained metric.
- **Persona quality fix:** the first LLM batch put self-introductions in the
  name field ("I'm Priya, platform engineer"). The prompt now asks for the full
  name and job title only (`personas-v2`), and `looks_like_name` rejects anything
  else. `fixtures/llm-personas-40.json` holds 39 clean, replayable people.
- **Evaluation harness:**
  - `--semantic`, `--compare` (keyword vs hybrid) and `--judge`.
  - A new nearest-match metric.
  - CI still runs the offline `--check`, which needs no keys.
- **Verified live with real OpenAI:**
  - The smoke test passes with real AI drafts.
  - 24 AI personas generated in 7.8 s.
  - First semantic run: 14 s, including enrichment and AI notes for 5/5 tables.
  - Cached semantic run without notes: 1.1 s, with 0 new embedding calls, 24/24
    attendees with vectors, and 2/5 tables differing from keyword-only.
  - The judge runs in 6–8 s.
  - No vectors appear in `/api/conference` or run payloads.
  - The host page shows the scoring mode, per-table judge scores, and
    rationales.
- **Fixed after code review:** keyword-only runs, including the `/api/matches`
  shortcut, ignored the toggle and used topic tags saved by earlier semantic runs.
  Tags are now cleared whenever enrichment didn't run. A regression test checks the
  actual Elasticsearch queries and fails without the fix.
- **Spend:** benchmarking, experiments and verification used roughly $2 of the
  $50 budget, estimated from call counts at list price.

## New attendee UI wired to the backend

The teammates' onboarding UI (`apps/web/src/onboarding/`) is now the attendee app
at `/`. The original page moved to `/classic`.

- **One set of goals.** The UI uses the API's Learn / Share / Connect goals.
  `GoalPrompt` gained `label` and `short`, and the cards show the server's copy.
- **New contract fields.** `Enrollment.company` (optional), `EventConfig.lunch_time`
  and `location` (host-editable), and `LunchGroup.name`, `tagline` (two lines) and
  `topics`. `MyAssignment` adds those plus seats, lunch time and location.
- **Table identity.** Templates name each table after what it shares ("The
  Kubernetes table") and pick a tagline from the goal mix. Names are distinct
  within a plan. With AI on, `table-notes-v2` also writes `table_name` and
  `tagline`; weak, duplicate, or out-of-table values fall back to the template.
- **Edit and check-in.** `PUT /api/me` while open; `POST /api/me/check-in` after
  publish, stored as an event document so no new index or privilege is needed.
  The host sees seated counts per table and a marker per person.
- **Seat diagram.** It shows up to four tablemates (a "+N" seat beyond that) and
  the open seats left at the table, hiding what doesn't apply.
- **Verified.** Locally, through the browser: enroll → edit → reload/resume → host
  cutoff and venue → AI draft → publish → reveal → check-in → conversation card →
  host sees the check-in. The smoke test covers edits and check-in. On Elastic
  Cloud (Serverless): the 12 seed profiles were uploaded, and one draft run (not
  published) used `bm25+vectors+tags` with AI notes.
