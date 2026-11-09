import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import {
  ApiError,
  request,
  type Attendee,
  type EventConfig,
  type HostState,
  type LunchGroup,
  type MatchMetrics,
  type DemoResult,
  type Judgement,
  type MatchRun,
  type TableVerdict,
  type TableSpec,
} from "./api";
import "./styles.css";

const TOKEN_KEY = "tabletalk-host-token";
const LAYOUT_INVALID =
  "Check the setup: unique table numbers, 2–20 seats per table, and a minimum table size that fits.";
const ANSWER_LABELS = [
  ["Learn", "Learn"],
  ["Share something I've learned", "Share"],
  ["Meet connections", "Connect"],
] as const;

function percent(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function toLocalInput(iso: string | null) {
  if (!iso) return "";
  const date = new Date(iso);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function evenLayout(count: number, seats: number): TableSpec[] {
  return Array.from({ length: count }, (_, index) => ({
    number: index + 1,
    seats,
  }));
}

function MetricsBar({ metrics }: { metrics: MatchMetrics }) {
  const items = [
    {
      label: "Seated",
      value: `${metrics.seated_count} / ${metrics.attendee_count}`,
      hint: "Everyone has a seat",
    },
    {
      label: "Table sizes",
      value: metrics.table_sizes.join(" · "),
      hint: `${metrics.table_sizes.length} tables`,
    },
    {
      label: "Average fit",
      value: percent(metrics.average_pair_affinity),
      hint: `${percent(metrics.baseline_average_pair_affinity)} if seated at random`,
    },
    {
      label: "Weakest match",
      value: percent(metrics.weakest_best_tablemate),
      hint: `Best tablemate for the hardest-to-match person (${percent(metrics.baseline_weakest_best_tablemate)} at random)`,
    },
    {
      label: "Learn coverage",
      value:
        metrics.learn_coverage == null ? "—" : percent(metrics.learn_coverage),
      hint: "People whose Learn answer someone at their table can help with",
    },
  ];
  return (
    <dl className="metrics-bar" aria-label="Match quality">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
          <span>{item.hint}</span>
        </div>
      ))}
    </dl>
  );
}

function HostTableCard({
  group,
  verdict,
  checkIns,
}: {
  group: LunchGroup;
  verdict?: TableVerdict;
  checkIns?: Record<string, string>;
}) {
  const label =
    group.table_number != null
      ? `Table ${group.table_number}`
      : group.id.replace("table-", "Table ");
  const seated = checkIns
    ? group.members.filter((member) => checkIns[member.id]).length
    : null;
  return (
    <article className="table-card">
      <div className="card-heading">
        <h3>
          {label}
          {group.name ? ` · ${group.name}` : ""}
        </h3>
        <span className="badge">
          {group.members.length} people
          {seated != null ? ` · ${seated} seated` : ""}
          {group.narrative_source === "llm" ? " · AI notes" : ""}
          {verdict ? ` · judge ${verdict.score}/5` : ""}
        </span>
      </div>
      {group.tagline && group.tagline.length > 0 && (
        <p className="muted">{group.tagline.join(" ")}</p>
      )}
      {verdict && <p className="judge-note">AI judge: {verdict.rationale}</p>}
      <ul className="member-list">
        {group.members.map((member) => (
          <li key={member.id}>
            <div>
              <strong>
                {member.name}
                {checkIns?.[member.id] ? " ✓ seated" : ""}
              </strong>
              <span className="muted">
                {[member.role, member.company].filter(Boolean).join(" · ")} ·{" "}
                {member.interests.join(", ")}
              </span>
            </div>
          </li>
        ))}
      </ul>
      {group.topics && group.topics.length > 0 && (
        <p className="tags">
          {group.topics.map((topic) => (
            <span key={topic} className="tag">
              {topic}
            </span>
          ))}
        </p>
      )}
      <div className="match-reasons">
        <p className="eyebrow">What this table can talk about</p>
        <p>{group.summary || "No summary for this table."}</p>
        {group.starters && group.starters.length > 0 && (
          <>
            <p className="eyebrow">Conversation starters</p>
            <ul className="starters">
              {group.starters.map((starter) => (
                <li key={starter}>{starter}</li>
              ))}
            </ul>
          </>
        )}
        {group.reasons.length > 0 && (
          <details className="reasons-details">
            <summary>
              Why these people ({group.reasons.length} connections)
            </summary>
            {group.reasons.map((reason) => (
              <p key={reason.attendee_ids.join(":")}>{reason.text}</p>
            ))}
          </details>
        )}
      </div>
    </article>
  );
}

function PeopleList({ groups }: { groups: LunchGroup[] }) {
  const [query, setQuery] = useState("");
  const rows = useMemo(
    () =>
      groups.flatMap((group) =>
        group.members.map((member) => ({
          member,
          table: group.table_number ?? group.id.replace("table-", ""),
          note: group.member_notes?.find(
            (note) => note.attendee_id === member.id,
          ),
        })),
      ),
    [groups],
  );
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? rows.filter(({ member, table }) =>
        [member.name, member.role, `table ${table}`, ...member.interests]
          .join(" ")
          .toLowerCase()
          .includes(needle),
      )
    : rows;
  return (
    <section className="host-card" aria-labelledby="people-heading">
      <div className="card-heading">
        <h2 id="people-heading">Every person</h2>
        <span className="badge">{rows.length} seated</span>
      </div>
      <label className="search-field">
        Search people, roles, topics, or tables
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="kubernetes, table 2, Priya..."
        />
      </label>
      <ul className="people-list">
        {visible.map(({ member, table, note }) => (
          <li key={member.id}>
            <div className="person-heading">
              <strong>{member.name}</strong>
              <span className="muted">{member.role}</span>
              <span className="badge">Table {table}</span>
            </div>
            {member.topic_tags && member.topic_tags.length > 0 && (
              <p className="tags">
                {member.topic_tags.map((tag) => (
                  <span key={tag} className="tag">
                    {tag}
                  </span>
                ))}
              </p>
            )}
            {note && (
              <>
                <p>{note.summary}</p>
                <ul className="starters">
                  {note.starters.map((starter) => (
                    <li key={starter}>{starter}</li>
                  ))}
                </ul>
              </>
            )}
            <details>
              <summary>Their answers</summary>
              <AnswerList member={member} />
            </details>
          </li>
        ))}
        {visible.length === 0 && <li className="muted">No one matches.</li>}
      </ul>
    </section>
  );
}

function AnswerList({ member }: { member: Attendee }) {
  return (
    <dl className="answers">
      <dt>Interests</dt>
      <dd>{member.interests.join(", ")}</dd>
      {ANSWER_LABELS.map(([goal, label]) =>
        member.goal_answers?.[goal] ? (
          <div key={goal}>
            <dt>
              {label}
              {member.focus_goal === goal ? " (focus)" : ""}
            </dt>
            <dd>{member.goal_answers[goal]}</dd>
          </div>
        ) : null,
      )}
      <dt>Can offer</dt>
      <dd>{member.can_help}</dd>
      <dt>Looking for</dt>
      <dd>{member.want_to_learn}</dd>
    </dl>
  );
}

function SetupCard({
  event,
  locked,
  busy,
  onSave,
}: {
  event: EventConfig;
  locked: boolean;
  busy: boolean;
  onSave: (event: EventConfig) => void;
}) {
  const [tables, setTables] = useState(event.tables);
  const [cutoff, setCutoff] = useState(
    toLocalInput(event.enrollment_closes_at),
  );
  const [minSize, setMinSize] = useState(event.min_table_size);
  const [autoMatch, setAutoMatch] = useState(event.auto_match ?? false);
  const [autoPublish, setAutoPublish] = useState(event.auto_publish ?? false);
  const [lunchTime, setLunchTime] = useState(event.lunch_time ?? "");
  const [location, setLocation] = useState(event.location ?? "");
  const [quickCount, setQuickCount] = useState(3);
  const [quickSeats, setQuickSeats] = useState(5);
  const seats = tables.reduce((total, table) => total + table.seats, 0);

  function updateTable(index: number, change: Partial<TableSpec>) {
    setTables(
      tables.map((table, position) =>
        position === index ? { ...table, ...change } : table,
      ),
    );
  }

  function save(formEvent: FormEvent<HTMLFormElement>) {
    formEvent.preventDefault();
    onSave({
      tables,
      enrollment_closes_at: cutoff ? new Date(cutoff).toISOString() : null,
      min_table_size: minSize,
      auto_match: autoMatch,
      auto_publish: autoMatch && autoPublish,
      lunch_time: lunchTime,
      location,
    });
  }

  return (
    <section className="host-card" aria-labelledby="setup-heading">
      <p className="eyebrow">01 / Set up the room</p>
      <h2 id="setup-heading">Tables and cutoff</h2>
      <form onSubmit={save}>
        <fieldset disabled={busy || locked}>
          <div className="quick-layout">
            <label>
              Tables
              <input
                type="number"
                min={1}
                max={50}
                value={quickCount}
                onChange={(e) => setQuickCount(Number(e.target.value))}
              />
            </label>
            <label>
              Seats each
              <input
                type="number"
                min={2}
                max={20}
                value={quickSeats}
                onChange={(e) => setQuickSeats(Number(e.target.value))}
              />
            </label>
            <button
              type="button"
              onClick={() => setTables(evenLayout(quickCount, quickSeats))}
            >
              Use this layout
            </button>
          </div>
          <table className="tables-editor">
            <thead>
              <tr>
                <th scope="col">Table #</th>
                <th scope="col">Seats</th>
                <th scope="col">
                  <span className="visually-hidden">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {tables.map((table, index) => (
                <tr key={index}>
                  <td>
                    <input
                      type="number"
                      min={1}
                      aria-label={`Table ${index + 1} number`}
                      value={table.number}
                      onChange={(e) =>
                        updateTable(index, { number: Number(e.target.value) })
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      min={2}
                      max={20}
                      aria-label={`Table ${table.number} seats`}
                      value={table.seats}
                      onChange={(e) =>
                        updateTable(index, { seats: Number(e.target.value) })
                      }
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="text-button"
                      aria-label={`Remove table ${table.number}`}
                      disabled={tables.length === 1}
                      onClick={() =>
                        setTables(
                          tables.filter((_, position) => position !== index),
                        )
                      }
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            className="text-button"
            onClick={() =>
              setTables([
                ...tables,
                {
                  number:
                    Math.max(0, ...tables.map((table) => table.number)) + 1,
                  seats: tables.at(-1)?.seats ?? 4,
                },
              ])
            }
          >
            + Add a table
          </button>
          <p className="field-hint">
            {tables.length} tables · {seats} seats
            {seats > 100 ? " (the demo caps enrollment at 100)" : ""}
          </p>
          <div className="form-row">
            <label>
              Enrollment closes at
              <input
                type="datetime-local"
                value={cutoff}
                onChange={(e) => setCutoff(e.target.value)}
              />
            </label>
            <label>
              Smallest table allowed
              <input
                type="number"
                min={2}
                max={6}
                value={minSize}
                onChange={(e) => setMinSize(Number(e.target.value))}
              />
            </label>
          </div>
          <div className="form-row">
            <label>
              Lunch time (shown to attendees)
              <input
                maxLength={80}
                value={lunchTime}
                onChange={(e) => setLunchTime(e.target.value)}
                placeholder="12:30–1:15"
              />
            </label>
            <label>
              Location
              <input
                maxLength={80}
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="Main hall"
              />
            </label>
          </div>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={autoMatch}
              onChange={(e) => setAutoMatch(e.target.checked)}
            />
            Match automatically at the cutoff
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={autoMatch && autoPublish}
              disabled={!autoMatch}
              onChange={(e) => setAutoPublish(e.target.checked)}
            />
            Publish the automatic run right away
          </label>
          <p className="field-hint">
            {autoMatch
              ? autoPublish
                ? "At the cutoff, TableTalk matches everyone and attendees see their tables immediately."
                : "At the cutoff, TableTalk makes a draft for you to review and publish."
              : "You'll press Produce matches yourself."}
          </p>
          <button className="primary-button" type="submit">
            Save setup <span aria-hidden="true">&rarr;</span>
          </button>
        </fieldset>
      </form>
      {locked && (
        <p className="privacy-note">
          Groups are published, so the setup is locked.
        </p>
      )}
    </section>
  );
}

export default function HostApp() {
  const [token, setToken] = useState(
    () => sessionStorage.getItem(TOKEN_KEY) ?? "",
  );
  const [tokenInput, setTokenInput] = useState("");
  const [needsToken, setNeedsToken] = useState(false);
  const [state, setState] = useState<HostState | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [run, setRun] = useState<MatchRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [generateCount, setGenerateCount] = useState(24);
  const [generateSeed, setGenerateSeed] = useState("");
  const [generateWithAi, setGenerateWithAi] = useState(false);
  const [notesWithAi, setNotesWithAi] = useState(true);
  const [semantic, setSemantic] = useState(true);

  const load = useCallback(async () => {
    try {
      const next = await request<HostState>("/api/host/state", undefined, {
        hostToken: token,
      });
      setNeedsToken(false);
      setState(next);
      setSelectedRunId((current) =>
        current && next.runs.some((summary) => summary.id === current)
          ? current
          : (next.published_run_id ?? next.runs[0]?.id ?? null),
      );
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setNeedsToken(true);
        return;
      }
      setError(
        cause instanceof Error ? cause.message : "Could not load host data.",
      );
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  // Keep counts, cutoff status, and automatic runs fresh while the host watches.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, 20_000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    if (!selectedRunId) {
      setRun(null);
      return;
    }
    let cancelled = false;
    request<MatchRun>(`/api/host/runs/${selectedRunId}`, undefined, {
      hostToken: token,
    })
      .then((loaded) => {
        if (!cancelled) setRun(loaded);
      })
      .catch((cause: unknown) => {
        if (!cancelled)
          setError(
            cause instanceof Error ? cause.message : "Could not load that run.",
          );
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRunId, token]);

  async function act(action: () => Promise<string>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await action());
      await load();
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : "Something went wrong. Please try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  function submitToken(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    sessionStorage.setItem(TOKEN_KEY, tokenInput);
    setToken(tokenInput);
  }

  const published = state?.status === "published";
  const enrollmentOpen = state?.status === "open";
  const autoForCutoff =
    state?.auto &&
    state.event.enrollment_closes_at &&
    new Date(state.auto.cutoff).getTime() ===
      new Date(state.event.enrollment_closes_at).getTime()
      ? state.auto
      : null;
  const aiReady = Boolean(state?.llm?.enabled);
  const semanticReady = Boolean(state?.llm?.embedding_model || aiReady);
  const semanticLabel = [
    state?.llm?.embedding_model ? "embeddings" : null,
    aiReady ? "topic tags" : null,
  ]
    .filter(Boolean)
    .join(" + ");
  const verdicts = new Map(
    (run?.judgement?.tables ?? []).map((verdict) => [
      verdict.table_number,
      verdict,
    ]),
  );
  const seatedIds = new Set(
    run?.plan.groups.flatMap((group) => group.members.map((m) => m.id)) ?? [],
  );
  const unseated =
    state && run
      ? state.attendees.filter((attendee) => !seatedIds.has(attendee.id)).length
      : 0;
  const outdatedLayout = Boolean(
    state &&
    run &&
    (JSON.stringify(run.tables) !== JSON.stringify(state.event.tables) ||
      run.min_table_size !== state.event.min_table_size),
  );

  return (
    <div className="app-shell host-shell">
      <header className="site-header">
        <a className="wordmark" href="/" aria-label="TableTalk attendee view">
          <span className="logo-mark" aria-hidden="true">
            tt
          </span>
          TableTalk<span className="wordmark-dot">.</span>
        </a>
        <span className="event-label">Host view</span>
      </header>

      <main>
        {error && (
          <div role="alert" className="message error">
            {error}
          </div>
        )}
        {notice && (
          <div role="status" className="message success">
            {notice}
          </div>
        )}

        {needsToken && (
          <section className="host-card token-card">
            <h2>Host sign-in</h2>
            <form onSubmit={submitToken}>
              <label>
                Host token
                <input
                  type="password"
                  autoComplete="current-password"
                  value={tokenInput}
                  onChange={(event) => setTokenInput(event.target.value)}
                />
              </label>
              <button className="primary-button" type="submit">
                Continue <span aria-hidden="true">&rarr;</span>
              </button>
            </form>
          </section>
        )}

        {!state && !needsToken && !error && (
          <p role="status" className="loading-state">
            Loading the host view...
          </p>
        )}

        {state && (
          <>
            <section className="host-summary" aria-label="Lunch status">
              <div>
                <strong className={`status-pill status-${state.status}`}>
                  {state.status === "open"
                    ? "Enrollment open"
                    : state.status === "closed"
                      ? "Enrollment closed"
                      : "Groups published"}
                </strong>
              </div>
              <div>
                <strong>
                  {state.attendees.length} / {state.capacity}
                </strong>
                <span>people enrolled</span>
              </div>
              <div>
                <strong>{state.event.tables.length}</strong>
                <span>tables · {state.seat_count} seats</span>
              </div>
              <div>
                <strong>
                  {state.event.enrollment_closes_at
                    ? formatDateTime(state.event.enrollment_closes_at)
                    : "No cutoff"}
                </strong>
                <span>
                  enrollment cutoff
                  {state.event.auto_match
                    ? state.event.auto_publish
                      ? " · auto match + publish"
                      : " · auto match"
                    : ""}
                </span>
              </div>
            </section>
            {autoForCutoff && (
              <div
                role="status"
                className={`message ${autoForCutoff.outcome === "failed" ? "error" : "success"}`}
              >
                {autoForCutoff.message} ({formatDateTime(autoForCutoff.at)})
              </div>
            )}

            <div className="host-grid">
              <SetupCard
                key={JSON.stringify(state.event)}
                event={state.event}
                locked={published}
                busy={busy}
                onSave={(event) =>
                  void act(async () => {
                    await request<EventConfig>("/api/host/event", event, {
                      method: "PUT",
                      hostToken: token,
                      invalidMessage: LAYOUT_INVALID,
                    });
                    return "Setup saved.";
                  })
                }
              />

              <section className="host-card" aria-labelledby="match-heading">
                <p className="eyebrow">02 / Make the matches</p>
                <h2 id="match-heading">Produce and publish</h2>
                <div className="organizer-actions">
                  <button
                    disabled={busy || !enrollmentOpen}
                    onClick={() =>
                      void act(async () => {
                        const result = await request<{ created: number }>(
                          "/api/host/demo/attendees",
                          { source: "fixture" },
                          { hostToken: token },
                        );
                        return result.created
                          ? `Added ${result.created} sample attendees across three themes.`
                          : "The sample attendees are already enrolled.";
                      })
                    }
                  >
                    Load 14 sample attendees
                  </button>
                  <div className="generate-row">
                    <label>
                      People to generate
                      <input
                        type="number"
                        min={1}
                        max={100}
                        value={generateCount}
                        onChange={(e) =>
                          setGenerateCount(Number(e.target.value))
                        }
                      />
                    </label>
                    <label>
                      Seed (optional)
                      <input
                        type="number"
                        min={0}
                        placeholder="random"
                        value={generateSeed}
                        onChange={(e) => setGenerateSeed(e.target.value)}
                      />
                    </label>
                  </div>
                  {aiReady && (
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={generateWithAi}
                        onChange={(e) => setGenerateWithAi(e.target.checked)}
                      />
                      Write personas with AI ({state.llm?.provider})
                    </label>
                  )}
                  <button
                    disabled={busy || !enrollmentOpen}
                    onClick={() =>
                      void act(async () => {
                        const result = await request<DemoResult>(
                          "/api/host/demo/attendees",
                          {
                            source: "generated",
                            count: generateCount,
                            ...(generateSeed === ""
                              ? {}
                              : { seed: Number(generateSeed) }),
                            use_llm: aiReady && generateWithAi,
                          },
                          {
                            hostToken: token,
                            invalidMessage:
                              "Generate between 1 and 100 people with a non-negative seed.",
                          },
                        );
                        const by =
                          result.generator === "offline"
                            ? "the offline generator"
                            : result.generator.replace("llm:", "AI via ");
                        return result.created
                          ? `Generated ${result.created} fictional attendees with ${by}.`
                          : "Those generated attendees are already enrolled.";
                      })
                    }
                  >
                    Generate {generateCount} attendees
                  </button>
                  {semanticReady && (
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={semantic}
                        onChange={(e) => setSemantic(e.target.checked)}
                      />
                      Semantic matching ({semanticLabel})
                    </label>
                  )}
                  {aiReady && (
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={notesWithAi}
                        onChange={(e) => setNotesWithAi(e.target.checked)}
                      />
                      Write table notes with AI ({state.llm?.model})
                    </label>
                  )}
                  <button
                    className="dark-button"
                    disabled={busy || published || state.attendees.length < 2}
                    onClick={() =>
                      void act(async () => {
                        const created = await request<MatchRun>(
                          "/api/host/runs",
                          {
                            use_llm: aiReady && notesWithAi,
                            use_semantic: semanticReady && semantic,
                          },
                          { hostToken: token },
                        );
                        setSelectedRunId(created.id);
                        const aiTables = created.plan.groups.filter(
                          (group) => group.narrative_source === "llm",
                        ).length;
                        const notes = !(aiReady && notesWithAi)
                          ? ""
                          : aiTables === created.plan.groups.length
                            ? " AI wrote notes for every table."
                            : ` AI wrote notes for ${aiTables} of ${created.plan.groups.length} tables; the rest use templates.`;
                        return `Draft ready: ${created.plan.groups.length} tables for ${created.plan.attendee_count} people.${notes} Review it, then publish.`;
                      })
                    }
                  >
                    {busy ? "Working..." : "Produce matches"}
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => void act(async () => "Refreshed.")}
                  >
                    Refresh
                  </button>
                </div>
                <p className="privacy-note">
                  Each run is a draft with its own seed. Compare runs, then
                  publish one. Publishing closes enrollment.
                </p>
                {state.llm && !state.llm.enabled && (
                  <p className="privacy-note">
                    AI is configured but unavailable: {state.llm.problem} Notes
                    and personas use templates.
                  </p>
                )}
                {state.runs.length > 0 && (
                  <ul className="runs-list" aria-label="Match runs">
                    {state.runs.map((summary) => (
                      <li key={summary.id}>
                        <button
                          type="button"
                          aria-pressed={summary.id === selectedRunId}
                          onClick={() => setSelectedRunId(summary.id)}
                        >
                          <span>
                            {formatDateTime(summary.created_at)} · seed{" "}
                            {summary.seed}
                          </span>
                          <span className="muted">
                            {summary.attendee_count} people ·{" "}
                            {summary.table_count} tables
                            {summary.metrics
                              ? ` · fit ${percent(summary.metrics.average_pair_affinity)}`
                              : ""}
                            {summary.narrative_provider
                              ? " · AI notes"
                              : " · template notes"}
                            {summary.retrieval ? ` · ${summary.retrieval}` : ""}
                            {summary.judge_average != null
                              ? ` · judge ${summary.judge_average.toFixed(1)}/5`
                              : ""}
                          </span>
                          {summary.published && (
                            <span className="badge">Published</span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            {run && (
              <section className="groups-section" aria-labelledby="run-heading">
                <div className="run-heading">
                  <div>
                    <p className="eyebrow">03 / Review</p>
                    <h2 id="run-heading">
                      {run.id === state.published_run_id
                        ? "Published groups"
                        : "Draft groups"}
                    </h2>
                    <p className="section-description">
                      Run {run.id} · seed {run.seed} ·{" "}
                      {formatDateTime(run.created_at)} ·{" "}
                      {run.plan.narrative_provider
                        ? `notes by ${run.plan.narrative_provider}`
                        : "template notes"}
                      {run.plan.retrieval
                        ? ` · scored with ${run.plan.retrieval}`
                        : ""}
                    </p>
                  </div>
                  {!published && (
                    <button
                      className="dark-button"
                      disabled={busy || unseated > 0 || outdatedLayout}
                      onClick={() =>
                        void act(async () => {
                          await request(
                            `/api/host/runs/${run.id}/publish`,
                            {},
                            { hostToken: token },
                          );
                          return "Published! Attendees can now see their tables.";
                        })
                      }
                    >
                      Publish this run
                    </button>
                  )}
                </div>
                {unseated > 0 && (
                  <div role="alert" className="message error">
                    {unseated} enrolled{" "}
                    {unseated === 1 ? "person is" : "people are"} not in this
                    run. Produce a new run so everyone gets a seat.
                  </div>
                )}
                {outdatedLayout && !published && (
                  <div role="alert" className="message error">
                    The table setup changed after this run. Produce a new run to
                    publish.
                  </div>
                )}
                {run.plan.metrics && <MetricsBar metrics={run.plan.metrics} />}
                {aiReady && (
                  <div className="judge-row">
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          const verdict = await request<Judgement>(
                            `/api/host/runs/${run.id}/judge`,
                            {},
                            { hostToken: token },
                          );
                          setRun({ ...run, judgement: verdict });
                          return "The AI judge scored every table.";
                        })
                      }
                    >
                      {run.judgement
                        ? "Ask the AI judge again"
                        : "Ask the AI judge"}
                    </button>
                    {run.judgement && (
                      <p>
                        <strong>
                          AI judge: {run.judgement.average?.toFixed(1)}/5
                        </strong>{" "}
                        for these tables
                        {run.judgement.baseline_average != null
                          ? ` vs ${run.judgement.baseline_average.toFixed(1)}/5 for the same people seated at random`
                          : ""}
                        . A second opinion on conversation potential; it never
                        changes seats, and scores vary by about ±0.3 between
                        runs.
                      </p>
                    )}
                  </div>
                )}
                <div className="groups-grid">
                  {run.plan.groups.map((group) => (
                    <HostTableCard
                      key={group.id}
                      group={group}
                      verdict={verdicts.get(group.table_number ?? null)}
                      checkIns={
                        run.id === state.published_run_id
                          ? (state.check_ins ?? {})
                          : undefined
                      }
                    />
                  ))}
                </div>
                <PeopleList groups={run.plan.groups} />
              </section>
            )}
          </>
        )}
      </main>
      <footer>
        <span>Host tools for TableTalk.</span>
        <a href="/">Back to the attendee view</a>
      </footer>
    </div>
  );
}
