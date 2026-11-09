import { useEffect, useState, type FormEvent } from "react";
import {
  goalOptions,
  request,
  type Attendee,
  type ConferenceState,
  type Enrollment,
  type Goal,
  type LunchGroup,
} from "./api";
import "./styles.css";

const emptyForm = {
  name: "",
  role: "",
  interests: "",
  can_help: "",
  want_to_learn: "",
};
function TableCard({
  group,
  attendeeId,
}: {
  group: LunchGroup;
  attendeeId: string | null;
}) {
  const isYourTable = group.members.some((member) => member.id === attendeeId);
  return (
    <article className={`table-card ${isYourTable ? "your-table" : ""}`}>
      <div className="card-heading">
        <h3>{group.id.replace("table-", "Table ")}</h3>
        <span className="badge">
          {isYourTable ? "Your table" : `${group.members.length} people`}
        </span>
      </div>
      <ul className="member-list">
        {group.members.map((member) => (
          <li key={member.id}>
            <span className="avatar" aria-hidden="true">
              {member.name
                .split(" ")
                .map((part) => part[0])
                .slice(0, 2)
                .join("")}
            </span>
            <div>
              <strong>{member.name}</strong>
              <span className="muted">{member.role}</span>
            </div>
          </li>
        ))}
      </ul>
      <div className="match-reasons">
        <p className="eyebrow">Why this table works</p>
        {group.reasons.length ? (
          group.reasons
            .slice(0, 3)
            .map((reason) => (
              <p key={reason.attendee_ids.join(":")}>{reason.text}</p>
            ))
        ) : (
          <p>
            Everyone shares this lunch slot. No direct topic overlap was found,
            so this is a chance to discover a different perspective.
          </p>
        )}
      </div>
    </article>
  );
}

export default function App() {
  const [conference, setConference] = useState<ConferenceState | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [attendeeId, setAttendeeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    request<ConferenceState>("/api/conference")
      .then((state) => {
        if (!controller.signal.aborted) setConference(state);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted)
          setError(
            cause instanceof Error
              ? cause.message
              : "Could not load the conference.",
          );
      });
    return () => controller.abort();
  }, []);

  async function mutate(action: () => Promise<string>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const message = await action();
      setNotice(message);
      setConference(await request<ConferenceState>("/api/conference"));
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

  function enroll(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!conference) return;
    if (goals.length === 0) {
      setError("Please select at least one lunch goal.");
      return;
    }
    const enrollment: Enrollment = {
      ...form,
      interests: form.interests
        .split(",")
        .map((topic) => topic.trim())
        .filter(Boolean),
      lunch_slot: conference.lunch_slot,
      goals,
    };
    void mutate(async () => {
      const attendee = await request<Attendee>("/api/enrollments", enrollment);
      setAttendeeId(attendee.id);
      setForm(emptyForm);
      setGoals([]);
      return "You're on the list! Your table will appear when the organizer makes the groups.";
    });
  }

  const closed = Boolean(conference?.plan);
  const enrolled = Boolean(
    attendeeId &&
    conference?.attendees.some((attendee) => attendee.id === attendeeId),
  );

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
                <h2>
                  {closed
                    ? "The tables are ready."
                    : enrolled
                      ? "You're on the list."
                      : "A little about you."}
                </h2>
                <p className="section-description">
                  {closed
                    ? "Enrollment is closed for this lunch. Explore the groups and their connections below."
                    : enrolled
                      ? "The organizer will assign your lunch group. You can refresh to check for your table."
                      : "Not a resume. Just a few things that make a conversation worth having."}
                </p>
                {!closed && !enrolled && (
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
                              onClick={() =>
                                setGoals(
                                  goals.includes(value)
                                    ? goals.filter((goal) => goal !== value)
                                    : [...goals, value],
                                )
                              }
                            >
                              {label}
                            </button>
                          ))}
                        </div>
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
                      <label>
                        What you can offer
                        <textarea
                          name="can_help"
                          required
                          maxLength={500}
                          rows={2}
                          value={form.can_help}
                          onChange={(e) =>
                            setForm({ ...form, can_help: e.target.value })
                          }
                          placeholder="Making sense of distributed tracing..."
                        />
                      </label>
                      <label>
                        What you&apos;re looking for
                        <textarea
                          name="want_to_learn"
                          required
                          maxLength={500}
                          rows={2}
                          value={form.want_to_learn}
                          onChange={(e) =>
                            setForm({ ...form, want_to_learn: e.target.value })
                          }
                          placeholder="How other teams build developer platforms..."
                        />
                      </label>
                      <button className="primary-button" type="submit">
                        {busy ? "Saving your seat..." : "Find my lunch people"}
                        <span aria-hidden="true">&rarr;</span>
                      </button>
                    </fieldset>
                    <p className="privacy-note">
                      Local demo: profiles and groups are visible to everyone
                      using this instance. Use fictional details.
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
                    <strong>{conference.attendees.length}</strong>
                    <span>people on the list</span>
                  </div>
                  <div>
                    <strong>{conference.plan?.groups.length ?? "—"}</strong>
                    <span>tables ready</span>
                  </div>
                </div>
                <div className="organizer-actions">
                  <button
                    disabled={busy || closed}
                    onClick={() =>
                      void mutate(async () => {
                        const result = await request<{ created: number }>(
                          "/api/demo/seed",
                          {},
                        );
                        return result.created
                          ? `Added ${result.created} fictional attendees.`
                          : "Demo attendees are already on the list.";
                      })
                    }
                  >
                    Add demo attendees
                  </button>
                  <button
                    className="dark-button"
                    disabled={busy || closed || conference.attendees.length < 2}
                    onClick={() =>
                      void mutate(async () => {
                        await request("/api/matches", {});
                        return "The tables are ready. Time to meet your people!";
                      })
                    }
                  >
                    {busy
                      ? "Working..."
                      : closed
                        ? "Groups assigned"
                        : "Make lunch groups"}
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() =>
                      void mutate(async () => "Conference refreshed.")
                    }
                  >
                    Refresh the guest list
                  </button>
                </div>
                <p className="privacy-note">
                  Organizer controls are open in this local demo. Making groups
                  closes enrollment for this lunch.
                </p>
              </aside>
            </div>

            {conference.plan && (
              <section
                className="groups-section"
                aria-labelledby="groups-heading"
              >
                <p className="eyebrow">03 / Meet your people</p>
                <h2 id="groups-heading">Different paths. One table.</h2>
                <p className="section-description">
                  Real connections, with a reason behind each match.
                </p>
                <div className="groups-grid">
                  {conference.plan.groups.map((group) => (
                    <TableCard
                      key={group.id}
                      group={group}
                      attendeeId={attendeeId}
                    />
                  ))}
                </div>
              </section>
            )}
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
