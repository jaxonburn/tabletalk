import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ApiError,
  goalOptions,
  request,
  type ConferenceState,
  type EnrolledAttendee,
  type Enrollment,
  type Goal,
  type MyStatus,
} from "./api";
import "./styles.css";

export const ATTENDEE_TOKEN_KEY = "tabletalk-attendee-token";

// Storage can be missing or blocked (privacy modes); the page then works for this session.
function tokenStore(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}
const readToken = () => tokenStore()?.getItem(ATTENDEE_TOKEN_KEY) ?? "";
const storeToken = (token: string) =>
  tokenStore()?.setItem(ATTENDEE_TOKEN_KEY, token);
const clearToken = () => tokenStore()?.removeItem(ATTENDEE_TOKEN_KEY);
const POLL_MS = 20_000;
const emptyForm = { name: "", role: "", interests: "" };
const fallbackPrompts: Record<
  Goal,
  { question: string; example_hint: string }
> = {
  Learn: {
    question: "What do you want to learn?",
    example_hint: "A specific question you'd love answered.",
  },
  "Share something I've learned": {
    question: "What could you share?",
    example_hint: "Something you've built, fixed, or figured out.",
  },
  "Meet connections": {
    question: "What would you enjoy talking about?",
    example_hint: "Topics you'd happily discuss with new people.",
  },
};
const goalLabels: Record<Goal, string> = {
  Learn: "Learn",
  "Share something I've learned": "Share",
  "Meet connections": "Connect",
};
function formatTime(iso: string) {
  return new Date(iso).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function App() {
  const [conference, setConference] = useState<ConferenceState | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [answers, setAnswers] = useState<Partial<Record<Goal, string>>>({});
  const [focus, setFocus] = useState<Goal | null>(null);
  const [token, setTokenState] = useState(readToken);
  // Refreshes can overlap (polling, actions, token changes). Only the newest one,
  // for the current token, may update the page.
  const tokenRef = useRef(token);
  const refreshGeneration = useRef(0);
  const setToken = useCallback((next: string) => {
    tokenRef.current = next;
    setTokenState(next);
  }, []);
  const [me, setMe] = useState<MyStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const forget = useCallback(() => {
    clearToken();
    setToken("");
    setMe(null);
  }, [setToken]);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    const [state, mine] = await Promise.all([
      request<ConferenceState>("/api/conference"),
      token
        ? request<MyStatus>("/api/me", undefined, {
            attendeeToken: token,
          }).catch((cause: unknown) => {
            // A token from another stack or a reset demo: start fresh.
            if (cause instanceof ApiError && cause.status === 401) return null;
            throw cause;
          })
        : Promise.resolve(null),
    ]);
    if (generation !== refreshGeneration.current || token !== tokenRef.current)
      return;
    setConference(state);
    setMe(mine);
    if (token && mine === null) forget();
  }, [token, forget]);

  useEffect(() => {
    let cancelled = false;
    refresh().catch((cause: unknown) => {
      if (!cancelled)
        setError(
          cause instanceof Error
            ? cause.message
            : "Could not load the conference.",
        );
    });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // While someone is waiting for their table, check back so it appears on its own.
  const waiting = Boolean(me && me.status !== "published");
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(() => {
      void refresh().catch(() => undefined);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [waiting, refresh]);

  async function mutate(action: () => Promise<string>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setNotice(await action());
      await refresh();
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

  function toggleGoal(goal: Goal) {
    const next = goals.includes(goal)
      ? goals.filter((value) => value !== goal)
      : [...goals, goal];
    setGoals(next);
    if (!focus || !next.includes(focus)) setFocus(next[0] ?? null);
  }

  function enroll(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!conference) return;
    if (goals.length === 0) {
      setError("Please select at least one lunch goal.");
      return;
    }
    const missing = goals.filter((goal) => !answers[goal]?.trim());
    if (missing.length) {
      setError(
        `Please answer: ${missing.map((goal) => goalLabels[goal]).join(", ")}.`,
      );
      return;
    }
    const enrollment: Enrollment = {
      name: form.name,
      role: form.role,
      interests: form.interests
        .split(",")
        .map((topic) => topic.trim())
        .filter(Boolean),
      lunch_slot: conference.lunch_slot,
      goals,
      focus_goal: focus && goals.includes(focus) ? focus : goals[0],
      goal_answers: Object.fromEntries(
        goals.map((goal) => [goal, answers[goal]?.trim() ?? ""]),
      ),
    };
    void mutate(async () => {
      const attendee = await request<EnrolledAttendee>(
        "/api/enrollments",
        enrollment,
      );
      storeToken(attendee.attendee_token);
      setToken(attendee.attendee_token);
      setForm(emptyForm);
      setGoals([]);
      setAnswers({});
      setFocus(null);
      return "You're in for lunch! Your table will appear here when the host publishes the groups.";
    });
  }

  const published = Boolean(conference?.published);
  const closed =
    published ||
    (conference?.status !== undefined && conference.status !== "open");
  const closesAt = conference?.enrollment_closes_at
    ? formatTime(conference.enrollment_closes_at)
    : null;
  const assignment = me?.assignment ?? null;
  const prompts = conference?.goal_prompts ?? {};

  return (
    <div className="app-shell">
      <header className="site-header">
        <a className="wordmark" href="/" aria-label="TableTalk home">
          <span className="logo-mark" aria-hidden="true">
            tt
          </span>
          TableTalk<span className="wordmark-dot">.</span>
        </a>
        <span className="event-label">
          {conference?.name ?? "Conference connections"}
        </span>
      </header>

      <main>
        <section className="hero">
          <div className="hero-copy">
            <p className="eyebrow">
              <span className="status-dot" /> Good conversations start here
            </p>
            <h1>
              Your next great connection is <span>a lunch away.</span>
            </h1>
            <p className="hero-description">
              Skip the awkward introductions. Tell us what makes you curious,
              and we&apos;ll find a small table of people worth meeting.
            </p>
            <div className="hero-meta">
              <span>{conference?.lunch_label ?? "Conference lunch"}</span>
              <span>Up to {conference?.group_size ?? 4} people per table</span>
              <span>Shared interests. New perspectives.</span>
            </div>
            <a className="onboarding-link" href="/user-onboarding">
              Try the new attendee onboarding
              <span aria-hidden="true">→</span>
            </a>
          </div>
          <div className="table-illustration" aria-hidden="true">
            <span className="orbit-person person-one">You</span>
            <span className="orbit-person person-two">Hello!</span>
            <div className="lunch-table">
              <span>
                Good food.
                <br />
                Better company.
              </span>
            </div>
            <span className="orbit-person person-three">Let&apos;s talk</span>
            <span className="orbit-person person-four">Nice to meet you</span>
            <span className="illustration-caption">
              A little less networking. A lot more connecting.
            </span>
          </div>
        </section>

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
        {!conference && (
          <div className="loading-state">
            {error ? (
              <button
                onClick={() => void mutate(async () => "Conference refreshed.")}
                disabled={busy}
              >
                Reconnect
              </button>
            ) : (
              <p role="status">Setting the table...</p>
            )}
          </div>
        )}

        {conference && (
          <>
            <div className="workspace">
              <section className="enrollment-card">
                <p className="eyebrow">01 / Pull up a chair</p>
                {me && assignment ? (
                  <section aria-labelledby="your-table-heading">
                    <h2 id="your-table-heading">
                      {assignment.table_number != null
                        ? `You're at Table ${assignment.table_number}.`
                        : "Your table is ready."}
                    </h2>
                    <p className="section-description">{assignment.summary}</p>
                    <p className="eyebrow">Your tablemates</p>
                    <ul className="member-list">
                      {assignment.tablemates.map((mate, index) => (
                        <li key={`${mate.first_name}-${index}`}>
                          <span className="avatar" aria-hidden="true">
                            {mate.first_name.slice(0, 2)}
                          </span>
                          <div>
                            <strong>{mate.first_name}</strong>
                            <span className="muted">
                              {mate.role} · {mate.interests.join(", ")}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ul>
                    <p className="eyebrow">Conversation starters for you</p>
                    <ul className="starters your-starters">
                      {assignment.starters.map((starter) => (
                        <li key={starter}>{starter}</li>
                      ))}
                    </ul>
                    {assignment.table_summary &&
                      assignment.table_summary !== assignment.summary && (
                        <>
                          <p className="eyebrow">The whole table</p>
                          <p className="section-description">
                            {assignment.table_summary}
                          </p>
                        </>
                      )}
                    <button className="text-button" onClick={forget}>
                      Not {me.attendee.name.split(" ")[0]}? Forget this device
                    </button>
                  </section>
                ) : me ? (
                  <section aria-labelledby="waiting-heading">
                    <h2 id="waiting-heading">
                      You&apos;re in for lunch, {me.attendee.name.split(" ")[0]}
                      .
                    </h2>
                    <p className="section-description">
                      {me.status === "open"
                        ? closesAt
                          ? `You're locked in. Enrollment closes at ${closesAt}, then your table appears here automatically.`
                          : "You're locked in. Your table appears here as soon as the host publishes the groups."
                        : "Enrollment has closed. The host is setting the tables, and yours will appear here in a moment."}
                    </p>
                    <dl className="answers">
                      {me.attendee.goals.map((goal) => (
                        <div key={goal}>
                          <dt>
                            {goalLabels[goal]}
                            {me.attendee.focus_goal === goal ? " (focus)" : ""}
                          </dt>
                          <dd>{me.attendee.goal_answers?.[goal] ?? "—"}</dd>
                        </div>
                      ))}
                    </dl>
                    <button className="text-button" onClick={forget}>
                      Not {me.attendee.name.split(" ")[0]}? Forget this device
                    </button>
                  </section>
                ) : (
                  <>
                    <h2>
                      {published
                        ? "The tables are ready."
                        : closed
                          ? "Enrollment is closed."
                          : "A little about you."}
                    </h2>
                    <p className="section-description">
                      {published
                        ? "The groups are ready. Your table and conversation starters are available on the device you enrolled with."
                        : closed
                          ? "The host is setting the tables."
                          : closesAt
                            ? `Not a resume. Just a few things that make a conversation worth having. Enrollment closes at ${closesAt}.`
                            : "Not a resume. Just a few things that make a conversation worth having."}
                    </p>
                  </>
                )}
                {!closed && !me && (
                  <form onSubmit={enroll}>
                    <fieldset disabled={busy}>
                      <div className="form-row">
                        <label>
                          Your name
                          <input
                            name="name"
                            autoComplete="name"
                            required
                            maxLength={80}
                            value={form.name}
                            onChange={(e) =>
                              setForm({ ...form, name: e.target.value })
                            }
                            placeholder="Alex Chen"
                          />
                        </label>
                        <label>
                          What do you do?
                          <input
                            name="role"
                            required
                            maxLength={80}
                            value={form.role}
                            onChange={(e) =>
                              setForm({ ...form, role: e.target.value })
                            }
                            placeholder="Platform engineer, curious human..."
                          />
                        </label>
                      </div>
                      <label>
                        What could you talk about for hours?
                        <input
                          name="interests"
                          required
                          maxLength={406}
                          value={form.interests}
                          onChange={(e) =>
                            setForm({ ...form, interests: e.target.value })
                          }
                          placeholder="Kubernetes, open source, accessibility"
                        />
                        <span className="field-hint">
                          Up to 8 topics, separated by commas.
                        </span>
                      </label>
                      <div className="goal-field">
                        <span id="goals-label" className="field-label">
                          What brings you to the lunch table?{" "}
                          <span className="field-hint">
                            Select one or more.
                          </span>
                        </span>
                        <div
                          className="goal-options"
                          role="group"
                          aria-labelledby="goals-label"
                        >
                          {goalOptions.map(({ value, label }) => (
                            <button
                              key={value}
                              type="button"
                              className="goal-chip"
                              aria-pressed={goals.includes(value)}
                              onClick={() => toggleGoal(value)}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>
                      {goalOptions
                        .filter(({ value }) => goals.includes(value))
                        .map(({ value }) => {
                          const prompt =
                            prompts[value] ?? fallbackPrompts[value];
                          return (
                            <label key={value}>
                              {goalLabels[value]}: {prompt.question}
                              <textarea
                                name={`answer-${goalLabels[value].toLowerCase()}`}
                                required
                                maxLength={500}
                                rows={2}
                                value={answers[value] ?? ""}
                                onChange={(e) =>
                                  setAnswers({
                                    ...answers,
                                    [value]: e.target.value,
                                  })
                                }
                                placeholder={prompt.example_hint}
                              />
                            </label>
                          );
                        })}
                      {goals.length > 1 && (
                        <div className="goal-field">
                          <span id="focus-label" className="field-label">
                            Today&apos;s focus{" "}
                            <span className="field-hint">
                              We&apos;ll weigh this one most.
                            </span>
                          </span>
                          <div
                            className="goal-options"
                            role="radiogroup"
                            aria-labelledby="focus-label"
                          >
                            {goals.map((goal) => (
                              <label key={goal} className="checkbox-row">
                                <input
                                  type="radio"
                                  name="focus"
                                  checked={focus === goal}
                                  onChange={() => setFocus(goal)}
                                />
                                {goalLabels[goal]}
                              </label>
                            ))}
                          </div>
                        </div>
                      )}
                      <button className="primary-button" type="submit">
                        {busy ? "Saving your seat..." : "Submit me for lunch"}
                        <span aria-hidden="true">&rarr;</span>
                      </button>
                    </fieldset>
                    <p className="privacy-note">
                      Local demo: profiles and groups are visible to everyone
                      using this instance. If the host turns on AI notes, your
                      answers are sent to an AI provider to write conversation
                      starters. Use fictional details.
                    </p>
                  </form>
                )}
              </section>

              <aside className="organizer-card">
                <p className="eyebrow">02 / Set the table</p>
                <h2>
                  A good mix.
                  <br />
                  Not a random mix.
                </h2>
                <p>
                  Elasticsearch finds shared interests and complementary skills.
                  We turn those connections into small lunch groups.
                </p>
                <div className="stats">
                  <div>
                    <strong>{conference.attendee_count}</strong>
                    <span>people on the list</span>
                  </div>
                  <div>
                    <strong>{published ? "Ready" : "Pending"}</strong>
                    <span>group status</span>
                  </div>
                </div>
                <div className="organizer-actions">
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(async () => "Conference refreshed.")
                    }
                  >
                    Refresh the guest list
                  </button>
                  <a className="text-button host-link" href="/host">
                    Hosting? Open the host view &rarr;
                  </a>
                </div>
                <p className="privacy-note">
                  {closesAt && !published
                    ? `Enrollment closes at ${closesAt}. `
                    : ""}
                  The host makes the groups; publishing them closes enrollment
                  for this lunch.
                </p>
              </aside>
            </div>
          </>
        )}
      </main>
      <footer>
        <span>Built for the moments between the talks.</span>
        <span>Powered by Elasticsearch &middot; Made for people.</span>
      </footer>
    </div>
  );
}
