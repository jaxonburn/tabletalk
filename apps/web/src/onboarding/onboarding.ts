/**
 * Behavior for the attendee app (/, also /user-onboarding), ported from the
 * TableTalk attendee prototype (tabletalk-protchibit.html) and wired to the API.
 *
 * Step 1: name, title, and company. Step 2: interests. Step 3: pick one or more
 * lunch goals, star one as the focus, and answer a question for each selected
 * goal. Saving enrolls you (POST /api/enrollments) or updates your answers
 * (PUT /api/me). Then a waiting room that checks /api/me until the host
 * publishes tables, your table reveal (with everyone at the table), check-in,
 * and conversation starters.
 */

import {
  ApiError,
  request,
  type ConferenceState,
  type EnrolledAttendee,
  type Enrollment,
  type Goal,
  type GoalPrompt,
  type MyAssignment,
  type MyStatus,
} from "../api";
import { clearToken, readToken, storeToken } from "../attendeeToken";

export interface GoalDefinition {
  id: Goal;
  /** Used in element IDs, since goal values contain spaces and apostrophes. */
  slug: string;
  label: string;
  short: string;
  question: string;
  hint: string;
}

// Fallbacks match services/api/src/tabletalk_api/seed.py. The API's copy wins.
export const GOALS: readonly GoalDefinition[] = [
  {
    id: "Learn",
    slug: "learn",
    label: "Learn something",
    short: "Questions & fresh ideas",
    question: "What questions do you have about the topics you want to learn?",
    hint: "For example: How do you tune Kubernetes autoscaling for a service with unpredictable traffic?",
  },
  {
    id: "Share something I've learned",
    slug: "share",
    label: "Share my expertise",
    short: "Put your experience to use",
    question: "What have you learned that you would like to share?",
    hint: "For example: I've tuned Kubernetes autoscaling for a service with unpredictable traffic.",
  },
  {
    id: "Meet connections",
    slug: "connect",
    label: "Meet new people",
    short: "Good company, no pitch",
    question: "What topics would you be happy to discuss with new people?",
    hint: "For example: I'd enjoy comparing platform engineering lessons and hearing how other teams work.",
  },
];

export const INTERESTS = [
  "Kubernetes",
  "Platform engineering",
  "Observability",
  "Security",
  "AI & ML",
  "Open source",
  "Developer experience",
  "Data & search",
];
const MAX_INTERESTS = 8;
const MAX_ANSWER = 500;
export const POLL_MS = 15_000;
/** After the reveal, check less often, just for host updates. */
export const INBOX_POLL_MS = 60_000;
const INBOX_LIMIT = 3;
const FALLBACK_QUESTIONS = [
  "What’s one idea from today’s talks you want to try back at work?",
  "What tool or practice would you recommend to everyone at this table?",
  "What problem are you hoping someone here has already solved?",
];

type Screen =
  | "about"
  | "topics"
  | "goals"
  | "waiting"
  | "matching"
  | "assigned"
  | "confirmed";

const SCREENS: Screen[] = [
  "about",
  "topics",
  "goals",
  "waiting",
  "matching",
  "assigned",
  "confirmed",
];
const STEPS: [Screen, string][] = [
  ["about", "About you"],
  ["topics", "Your interests"],
  ["goals", "Your goals"],
];
const ONBOARDING: Screen[] = ["about", "topics", "goals"];
// The logo goes back one page.
const BACK: Partial<Record<Screen, Screen>> = {
  topics: "about",
  goals: "topics",
  waiting: "goals",
  assigned: "waiting",
  confirmed: "assigned",
};

// Star node data comes from the Lucide icon library, embedded for portability.
const STAR_PATH =
  "M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z";

interface GoalView {
  card: HTMLDivElement;
  choice: HTMLInputElement;
  star: HTMLButtonElement;
  body: HTMLDivElement;
  badge: HTMLSpanElement;
  input: HTMLTextAreaElement;
  error: HTMLParagraphElement;
  label: HTMLElement;
  short: HTMLElement;
  question: HTMLElement;
  hint: HTMLElement;
}

/** Server goal copy, falling back to the built-in text for anything missing. */
export function goalDefinitions(
  prompts: Partial<Record<Goal, GoalPrompt>> = {},
): GoalDefinition[] {
  return GOALS.map((g) => {
    const p = prompts[g.id];
    return {
      ...g,
      label: p?.label || g.label,
      short: p?.short || g.short,
      question: p?.question || g.question,
      hint: p?.example_hint ? `For example: ${p.example_hint}` : g.hint,
    };
  });
}

/** "11:40 AM" split for the waiting room's big clock. */
export function clockParts(iso: string): [string, string] {
  const parts = new Intl.DateTimeFormat([], {
    hour: "numeric",
    minute: "2-digit",
  }).formatToParts(new Date(iso));
  const period = parts.find((p) => p.type === "dayPeriod")?.value ?? "";
  const time = parts
    .filter((p) => p.type !== "dayPeriod")
    .map((p) => p.value)
    .join("")
    .trim();
  return [time, period];
}

function initials(name: string) {
  return (name.trim()[0] ?? "?").toUpperCase();
}

function errorText(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
}

/**
 * Wires up the onboarding markup inside `root` (the #tabletalk-auto element).
 * Returns a function that removes the page's listeners and timers.
 */
export function initOnboarding(root: HTMLElement): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  let matchTimer: number | undefined;
  let pollTimer: number | undefined;

  function q<T extends HTMLElement = HTMLElement>(id: string): T {
    const node = root.querySelector<T>(`#ta-${id}`);
    if (!node) throw new Error(`Missing #ta-${id} in the onboarding markup.`);
    return node;
  }
  const input = (id: string) => q<HTMLInputElement>(id);
  const button = (id: string) => q<HTMLButtonElement>(id);

  function on<E extends Event = Event>(
    target: EventTarget,
    type: string,
    listener: (event: E) => void,
  ) {
    target.addEventListener(type, listener as EventListener, { signal });
  }

  const reduced = () =>
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function animate(
    node: Element,
    keyframes: Keyframe[],
    options: KeyframeAnimationOptions,
  ): Animation | null {
    if (reduced() || typeof node.animate !== "function") return null;
    return node.animate(keyframes, options);
  }

  let goals = goalDefinitions();
  const state = {
    selectedGoals: new Set<Goal>(),
    explicitFocus: null as Goal | null,
    answers: {} as Partial<Record<Goal, string>>,
    interests: new Set<string>(),
  };
  let conference: ConferenceState | null = null;
  let me: MyStatus | null = null;
  let token = readToken();
  let current: Screen = "about";
  let questions: string[] = [];
  let questionIndex = 0;
  let questionRevealed = false;
  let busy = false;

  function el<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    attrs: Record<string, string> = {},
    text?: string,
  ): HTMLElementTagNameMap[K] {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else e.setAttribute(k, v);
    }
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function selectedGoals() {
    return goals.filter((g) => state.selectedGoals.has(g.id));
  }

  function focusGoal(): Goal | null {
    const selected = selectedGoals();
    if (selected.some((g) => g.id === state.explicitFocus)) {
      return state.explicitFocus;
    }
    return selected.length ? selected[0].id : null;
  }

  function identity() {
    return {
      first: input("first-name").value.trim(),
      last: input("last-name").value.trim(),
      title: input("title").value.trim(),
      company: input("company").value.trim(),
    };
  }

  /** Answers can change only while sign-ups are open. */
  function editable() {
    const status = me?.status ?? conference?.status ?? "open";
    return status === "open";
  }

  function backFrom(name: Screen): Screen | undefined {
    const prev = BACK[name];
    if (prev && ONBOARDING.includes(prev) && me && !editable())
      return undefined;
    return prev;
  }

  function show(name: Screen, { focus = true, back = false } = {}) {
    current = name;
    setNav(name);
    updateBrand(name);
    SCREENS.forEach((s) => {
      q(s).hidden = s !== name;
    });
    const progress = q("progress");
    const step = STEPS.findIndex(([s]) => s === name);
    progress.hidden = step < 0;
    if (step >= 0) {
      [...progress.children].forEach((e, i) => {
        e.classList.toggle("ta-current", i === step);
        e.classList.toggle("ta-complete", i < step);
      });
      progress.setAttribute("aria-valuenow", String(step + 1));
      progress.setAttribute(
        "aria-valuetext",
        `Step ${step + 1} of 3: ${STEPS[step][1]}`,
      );
    }
    const section = q(name);
    animate(
      section,
      [
        { opacity: 0, transform: `translateX(${back ? -9 : 9}px)` },
        { opacity: 1, transform: "translateX(0)" },
      ],
      { duration: 220, easing: "ease-out" },
    );
    // The template ran in an embed that grew with its content. As a full page,
    // start each screen at the top instead of where the last one was scrolled.
    if (window.scrollY > 0) window.scrollTo({ top: 0 });
    if (focus) {
      const heading = section.querySelector("h2");
      if (heading) {
        heading.setAttribute("tabindex", "-1");
        heading.focus();
      }
    }
    renderInbox();
    schedulePoll();
  }

  function pulse(node: Element) {
    animate(node, [{ transform: "scale(.97)" }, { transform: "scale(1)" }], {
      duration: 170,
      easing: "ease-out",
    });
  }

  function fieldError(inputId: string, errorId: string, message: string) {
    const field = q(inputId);
    const error = q(errorId);
    field.setAttribute("aria-invalid", String(Boolean(message)));
    error.textContent = message;
    error.hidden = !message;
  }

  // Step 2: interests

  function interestError(message: string) {
    q("interest-error").textContent = message;
    q("interest-error").hidden = !message;
  }

  function selectInterest(checkbox: HTMLInputElement, value: string) {
    if (state.interests.size >= MAX_INTERESTS) {
      checkbox.checked = false;
      interestError(`You can pick up to ${MAX_INTERESTS} interests.`);
      return false;
    }
    state.interests.add(value);
    interestError("");
    return true;
  }

  function addInterest(label: string, { select = false } = {}) {
    const value = label.trim();
    if (!value) return;
    const existing = [...q("interests").querySelectorAll("input")].find(
      (i) => i.value.toLowerCase() === value.toLowerCase(),
    );
    if (existing) {
      if (select && !existing.checked) {
        existing.checked = selectInterest(existing, existing.value);
      }
      return;
    }
    const wrap = el("label", {
      class: "ta-interest-choice cursor-interaction",
    });
    const checkbox = el("input", {
      type: "checkbox",
      value,
      "aria-label": value,
    });
    const span = el("span", {}, value);
    wrap.append(checkbox, span);
    q("interests").append(wrap);
    if (select) checkbox.checked = selectInterest(checkbox, value);
    on(checkbox, "change", () => {
      if (checkbox.checked) selectInterest(checkbox, value);
      else state.interests.delete(value);
      pulse(span);
    });
  }
  INTERESTS.forEach((i) => addInterest(i));

  function customInterest() {
    const field = input("custom-interest");
    const value = field.value.trim();
    if (!value) {
      field.focus();
      return;
    }
    addInterest(value, { select: true });
    field.value = "";
    q("interest-status").textContent = `${value} added to your interests.`;
    field.focus();
  }
  on(q("add-interest"), "click", customInterest);
  on<KeyboardEvent>(q("custom-interest"), "keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      customInterest();
    }
  });

  function interestsValid() {
    if (state.interests.size) return true;
    interestError("Pick at least one interest so we can find common ground.");
    return false;
  }

  // Step 3: goals

  function starIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    for (const [k, v] of Object.entries({
      xmlns: ns,
      width: "24",
      height: "24",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.8",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    })) {
      svg.setAttribute(k, v);
    }
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", STAR_PATH);
    svg.append(path);
    return svg;
  }

  const goalViews = new Map<Goal, GoalView>();

  function chooseGoal(id: Goal, selected: boolean) {
    if (selected) {
      state.selectedGoals.add(id);
      if (!state.explicitFocus) state.explicitFocus = id;
    } else {
      state.selectedGoals.delete(id);
      if (state.explicitFocus === id) {
        state.explicitFocus = state.selectedGoals.values().next().value ?? null;
      }
    }
    renderGoals({ animateId: selected ? id : null });
  }

  goals.forEach((g) => {
    const card = el("div", {
      class: "ta-goal-item",
      id: `ta-goal-item-${g.slug}`,
    });
    const header = el("div", { class: "ta-goal-item-header" });
    const label = el("label", { class: "ta-goal-select cursor-interaction" });
    const choice = el("input", {
      type: "checkbox",
      value: g.id,
      id: `ta-choice-${g.slug}`,
      "aria-controls": `ta-prompt-${g.slug}`,
    });
    const text = el("span");
    const title = el("b", {}, g.label);
    const short = el("small", {}, g.short);
    text.append(title, short);
    label.append(choice, text);
    const star = el("button", {
      type: "button",
      id: `ta-star-${g.slug}`,
      class: "ta-focus-star cursor-interaction",
      "aria-pressed": "false",
      "aria-label": `Select ${g.label} and make it your focus`,
    });
    star.append(starIcon());
    header.append(label, star);

    const body = el("div", {
      class: "ta-goal-prompt",
      id: `ta-prompt-${g.slug}`,
    });
    const badge = el("span", { class: "ta-focus-indicator" }, "YOUR FOCUS");
    const question = el(
      "label",
      { class: "ta-label", for: `ta-answer-${g.slug}` },
      g.question,
    );
    const hint = el(
      "p",
      { class: "ta-field-hint", id: `ta-hint-${g.slug}` },
      g.hint,
    );
    const answer = el("textarea", {
      id: `ta-answer-${g.slug}`,
      rows: "3",
      maxlength: String(MAX_ANSWER),
      "aria-describedby": `ta-hint-${g.slug} ta-answer-error-${g.slug}`,
    });
    const error = el("p", {
      class: "ta-field-error",
      id: `ta-answer-error-${g.slug}`,
    });
    body.hidden = true;
    badge.hidden = true;
    error.hidden = true;
    answer.disabled = true;
    body.append(badge, question, hint, answer, error);
    card.append(header, body);
    q("goal-choices").append(card);
    goalViews.set(g.id, {
      card,
      choice,
      star,
      body,
      badge,
      input: answer,
      error,
      label: title,
      short,
      question,
      hint,
    });

    on(choice, "change", () => {
      chooseGoal(g.id, choice.checked);
      pulse(header);
    });
    on(star, "click", () => {
      const wasSelected = state.selectedGoals.has(g.id);
      state.selectedGoals.add(g.id);
      state.explicitFocus = g.id;
      renderGoals({ animateId: wasSelected ? null : g.id });
      pulse(star);
    });
    on(answer, "input", () => {
      state.answers[g.id] = answer.value;
      if (answer.value.trim()) {
        answer.setAttribute("aria-invalid", "false");
        error.hidden = true;
      }
    });
  });

  /** Swap in the server's goal wording without rebuilding the cards. */
  function applyGoalCopy() {
    goals.forEach((g) => {
      const v = goalViews.get(g.id);
      if (!v) return;
      v.label.textContent = g.label;
      v.short.textContent = g.short;
      v.question.textContent = g.question;
      v.hint.textContent = g.hint;
    });
    renderGoals();
  }

  // Step 1: about you

  const REQUIRED: [string, string, string][] = [
    ["first-name", "first-error", "Please add your first name."],
    ["last-name", "last-error", "Please add your last name."],
    [
      "title",
      "title-error",
      "Please add your title, so tablemates know what you do.",
    ],
  ];

  function aboutValid() {
    let firstInvalid: HTMLInputElement | null = null;
    for (const [id, errorId, message] of REQUIRED) {
      const missing = !input(id).value.trim();
      fieldError(id, errorId, missing ? message : "");
      if (missing && !firstInvalid) firstInvalid = input(id);
    }
    if (firstInvalid) {
      q("about-error").textContent =
        "Please complete the highlighted fields before continuing.";
      firstInvalid.focus();
      return false;
    }
    q("about-error").textContent = "";
    return true;
  }
  REQUIRED.forEach(([id, errorId]) =>
    on(q(id), "input", () => {
      if (input(id).value.trim()) fieldError(id, errorId, "");
    }),
  );

  function renderGoals({ animateId = null as Goal | null } = {}) {
    const selected = selectedGoals();
    const focus = focusGoal();
    goals.forEach((g) => {
      const v = goalViews.get(g.id);
      if (!v) return;
      const isOn = state.selectedGoals.has(g.id);
      const isFocus = isOn && focus === g.id;
      const wasHidden = v.body.hidden;
      v.choice.checked = isOn;
      v.choice.setAttribute("aria-expanded", String(isOn));
      v.card.classList.toggle("ta-is-selected", isOn);
      v.card.classList.toggle("ta-is-focus", isFocus);
      v.body.hidden = !isOn;
      v.input.disabled = !isOn;
      v.input.required = isOn;
      v.badge.hidden = !isFocus;
      v.star.setAttribute("aria-pressed", String(isFocus));
      v.star.setAttribute(
        "aria-label",
        isFocus
          ? `${g.label} is your focus goal`
          : isOn
            ? `Make ${g.label} your focus`
            : `Select ${g.label} and make it your focus`,
      );
      const saved = state.answers[g.id] ?? "";
      if (v.input.value !== saved) v.input.value = saved;
      if (!isOn) {
        v.error.hidden = true;
        v.input.setAttribute("aria-invalid", "false");
      }
      if (isOn && wasHidden && animateId === g.id) {
        animate(
          v.body,
          [
            { opacity: 0, transform: "translateY(-7px)" },
            { opacity: 1, transform: "translateY(0)" },
          ],
          { duration: 230, easing: "ease-out" },
        );
      }
    });
    const focusLabel = goals.find((g) => g.id === focus)?.label;
    q("goal-count").textContent = selected.length
      ? `${selected.length} goal${selected.length === 1 ? "" : "s"} selected · Focus: ${focusLabel}`
      : "Pick at least one goal to get started.";
    if (selected.length) q("goal-error").hidden = true;
    q("goals-error").textContent = "";
  }

  function aboutNext() {
    if (!aboutValid()) return;
    show("topics");
  }
  on(q("topics-next"), "click", () => {
    if (!interestsValid()) return;
    renderGoals();
    show("goals");
  });
  on(q("topics-back"), "click", () => show("about", { back: true }));
  on(q("about-form"), "submit", (e) => {
    e.preventDefault();
    aboutNext();
  });
  on(q("continue"), "click", aboutNext);
  ["first-name", "last-name", "title", "company"].forEach((id) =>
    on<KeyboardEvent>(q(id), "keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        aboutNext();
      }
    }),
  );
  on(q("back"), "click", () => show("topics", { back: true }));

  // Always build answers from the selected goals, never from the full draft
  // cache: answers to goals that were deselected stay out of the submission.
  function buildEnrollment(): Enrollment {
    const person = identity();
    const selected = selectedGoals();
    return {
      name: [person.first, person.last].join(" ").trim(),
      role: person.title,
      company: person.company,
      interests: [...state.interests],
      goals: selected.map((g) => g.id),
      focus_goal: focusGoal(),
      goal_answers: Object.fromEntries(
        selected.map((g) => [g.id, (state.answers[g.id] ?? "").trim()]),
      ),
      lunch_slot: conference?.lunch_slot ?? "day-1-lunch",
    };
  }

  function goalsValid() {
    let firstInvalid: HTMLElement | null = null;
    if (!state.selectedGoals.size) {
      q("goal-error").textContent = "Choose at least one lunch goal.";
      q("goal-error").hidden = false;
      firstInvalid = q(`choice-${goals[0].slug}`);
    }
    for (const g of selectedGoals()) {
      const answer = q<HTMLTextAreaElement>(`answer-${g.slug}`);
      state.answers[g.id] = answer.value;
      if (!answer.value.trim()) {
        answer.setAttribute("aria-invalid", "true");
        q(`answer-error-${g.slug}`).textContent =
          "A few words will help us find the right people.";
        q(`answer-error-${g.slug}`).hidden = false;
        if (!firstInvalid) firstInvalid = answer;
      }
    }
    if (firstInvalid) {
      q("goals-error").textContent =
        "Please choose your goals and complete the questions before saving.";
      firstInvalid.focus();
      return false;
    }
    return true;
  }

  function submitError(message: string) {
    q("submit-error").textContent = message;
    q("submit-error").hidden = !message;
  }

  function setBusy(id: string, value: boolean) {
    busy = value;
    button(id).setAttribute("aria-busy", String(value));
  }

  async function enroll(enrollment: Enrollment): Promise<MyStatus> {
    if (token) {
      try {
        return await request<MyStatus>("/api/me", enrollment, {
          method: "PUT",
          attendeeToken: token,
        });
      } catch (error) {
        // A forgotten or reset enrollment: sign up again instead.
        if (!(error instanceof ApiError && error.status === 401)) throw error;
        forgetToken();
      }
    }
    const created = await request<EnrolledAttendee>(
      "/api/enrollments",
      enrollment,
    );
    token = created.attendee_token;
    storeToken(token);
    return request<MyStatus>("/api/me", undefined, { attendeeToken: token });
  }

  /** Sign-ups closed mid-edit: show the saved enrollment's waiting room or table. */
  async function returnToStatus() {
    let status: MyStatus | null;
    try {
      status = await refresh();
    } catch {
      return;
    }
    if (!status || signal.aborted) return;
    fillForm(status);
    applyEventDetails();
    if (status.assignment) {
      renderAssigned(status.assignment);
      show("assigned");
    } else {
      renderWaiting();
      show("waiting");
    }
  }

  async function goalsNext() {
    if (busy || !goalsValid()) return;
    submitError("");
    setBusy("submit", true);
    try {
      const status = await enroll(buildEnrollment());
      if (signal.aborted) return;
      me = status;
      renderWaiting();
      show("waiting");
    } catch (error) {
      if (signal.aborted) return;
      submitError(errorText(error));
      if (error instanceof ApiError && error.status === 409) {
        if (token) await returnToStatus();
        else void loadConference();
      }
    } finally {
      setBusy("submit", false);
    }
  }
  on(q("goals-form"), "submit", (e) => {
    e.preventDefault();
    void goalsNext();
  });
  on(q("submit"), "click", () => void goalsNext());

  function editAnswers() {
    if (!editable()) return;
    window.clearTimeout(matchTimer);
    show("about", { back: true });
  }
  on(q("edit"), "click", editAnswers);
  on(q("wait-edit"), "click", editAnswers);

  // Event details

  function when() {
    const lunch = me?.assignment;
    return [
      lunch?.lunch_time || conference?.lunch_time,
      lunch?.location || conference?.location,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  function applyEventDetails() {
    const name = conference?.name ?? "TableTalk";
    const hours = me?.assignment?.lunch_time || conference?.lunch_time || "";
    q("event").textContent = `${name} / lunch`.toUpperCase();
    q("event-time-name").textContent = `${name} / lunch`.toUpperCase();
    q("event-time-hours").textContent = hours;
    q("event-time-hours").hidden = !hours;
    const closed = !token && conference && conference.status !== "open";
    q("closed-notice").hidden = !closed;
    q("closed-notice").textContent = closed
      ? conference?.status === "published"
        ? "Tables for this lunch are already set, so sign-ups are closed. See you at the next one!"
        : "Sign-ups for this lunch have closed. The host is setting the tables now."
      : "";
    button("continue").disabled = Boolean(closed);
    button("submit").disabled = Boolean(closed);
    q("submit-label").textContent = me ? "Save my changes" : "Save my spot";
    q("forget").hidden = !token;
  }

  async function loadConference() {
    try {
      conference = await request<ConferenceState>("/api/conference");
    } catch {
      return; // Built-in goal copy still works, and enrollment shows its own errors.
    }
    if (signal.aborted) return;
    goals = goalDefinitions(conference.goal_prompts);
    applyGoalCopy();
    applyEventDetails();
    if (current === "waiting") renderWaiting();
  }

  // Waiting room

  function setClock(time: string, period = "") {
    const clock = q("reveal-time");
    clock.replaceChildren(document.createTextNode(time));
    if (period) clock.append(el("span", {}, ` ${period}`));
  }

  function renderWaiting() {
    applyEventDetails();
    const assignment = me?.assignment;
    q("wait-initial").hidden = Boolean(assignment);
    q("wait-ready").hidden = !assignment;
    q("demo-control").hidden = Boolean(assignment);
    q("wait-edit").hidden = !editable();
    button("reveal-table").disabled = false;
    if (assignment) {
      const first = me?.attendee.name.split(" ")[0] ?? "";
      q("ready-message").textContent =
        `The wait is over${first ? `, ${first}` : ""}. We found your table.`;
      return;
    }
    const cutoff = me?.enrollment_closes_at ?? conference?.enrollment_closes_at;
    if (!editable()) {
      q("time-label").textContent = "SIGN-UPS ARE CLOSED";
      setClock("Any", "minute");
      q("wait-when").textContent =
        "Your host is setting the tables now. This page updates on its own.";
    } else if (cutoff) {
      const [time, period] = clockParts(cutoff);
      q("time-label").textContent = "TABLE MATCHES OPEN AT";
      setClock(time, period);
      q("wait-when").textContent =
        `We will match you with a table after ${time} ${period}. Stay tuned!`.replace(
          "  ",
          " ",
        );
    } else {
      q("time-label").textContent = "TABLE MATCHES OPEN";
      setClock("Soon");
      q("wait-when").textContent =
        "We will match you with a table once sign-ups close. Stay tuned!";
    }
  }

  function forgetToken() {
    clearToken();
    token = "";
    me = null;
  }

  /** Fetch my enrollment. Returns null (and forgets the token) if it's gone. */
  async function refresh(): Promise<MyStatus | null> {
    if (!token) return null;
    try {
      const status = await request<MyStatus>("/api/me", undefined, {
        attendeeToken: token,
      });
      if (signal.aborted) return null;
      me = status;
      renderInbox();
      return status;
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 401 &&
        !signal.aborted
      ) {
        forgetToken();
        resetForm();
        applyEventDetails();
        show("about", { back: true });
        q("closed-notice").textContent =
          "We couldn't find your enrollment on this device. Please sign up again.";
        q("closed-notice").hidden = false;
      }
      throw error;
    }
  }

  async function checkNow(manual = false) {
    if (manual) q("check-status").textContent = "CHECKING…";
    const hadTable = Boolean(me?.assignment);
    try {
      await refresh();
    } catch {
      if (manual && !signal.aborted)
        q("check-status").textContent = "COULDN’T CHECK · TRY AGAIN";
      return;
    }
    if (signal.aborted || current !== "waiting") return;
    renderWaiting();
    schedulePoll();
    if (me?.assignment) {
      if (!hadTable) q("reveal-table").focus();
    } else {
      const time = new Intl.DateTimeFormat([], {
        hour: "numeric",
        minute: "2-digit",
      }).format(new Date());
      q("check-status").textContent = `NOT YET · CHECKED ${time}`.toUpperCase();
    }
  }
  on(q("check-now"), "click", () => void checkNow(true));

  function schedulePoll() {
    window.clearInterval(pollTimer);
    pollTimer = undefined;
    if (!token || !me) return;
    if (current === "waiting" && !me.assignment) {
      pollTimer = window.setInterval(() => void checkNow(), POLL_MS);
    } else if (!ONBOARDING.includes(current)) {
      pollTimer = window.setInterval(
        () => void refresh().catch(() => undefined),
        INBOX_POLL_MS,
      );
    }
  }

  /** The host's latest updates, newest first. */
  function renderInbox() {
    const inbox = q("inbox");
    const items = (me?.announcements ?? []).slice(0, INBOX_LIMIT);
    inbox.hidden = !items.length || ONBOARDING.includes(current);
    inbox.replaceChildren(
      ...items.map((a) => {
        const item = el("article", { class: "ta-inbox-item" });
        const when = new Intl.DateTimeFormat([], {
          hour: "numeric",
          minute: "2-digit",
        }).format(new Date(a.sent_at));
        item.append(
          el("b", {}, a.title),
          el("p", {}, a.body),
          el("small", {}, `FROM YOUR HOST · ${when}`.toUpperCase()),
        );
        return item;
      }),
    );
  }
  on(document, "visibilitychange", () => {
    if (
      document.visibilityState === "visible" &&
      current === "waiting" &&
      !me?.assignment
    ) {
      void checkNow();
    }
  });

  // Your table

  function setText(parent: ParentNode, selector: string, text: string) {
    const node = parent.querySelector(selector);
    if (node) node.textContent = text;
  }

  function tableNumber(a: MyAssignment) {
    return a.table_number == null
      ? "—"
      : String(a.table_number).padStart(2, "0");
  }

  function renderAssigned(a: MyAssignment) {
    const number = tableNumber(a);
    q("number").textContent = number;
    q("seated-number").textContent = number;
    q("done-number").textContent = number;
    q("vibe").textContent = (a.table_name || `Table ${number}`).toUpperCase();
    q("table-when").textContent = when();
    q("table-when").hidden = !when();
    q("roster-foot").textContent = when();
    q("roster-foot").hidden = !when();

    const mates = a.tablemates;
    const slots = [
      ...root.querySelectorAll<HTMLElement>(
        ".ta-seat:not(.ta-you):not(.ta-open)",
      ),
    ];
    slots.forEach((seat, i) => {
      const overflow = i === slots.length - 1 && mates.length > slots.length;
      const mate = mates[i];
      seat.hidden = !mate;
      if (overflow) {
        setText(seat, ".ta-avatar", `+${mates.length - i}`);
        setText(seat, "small", `${mates.length - i} more`);
      } else if (mate) {
        setText(seat, ".ta-avatar", initials(mate.first_name));
        setText(seat, "small", mate.first_name);
      }
    });
    const open = a.seats ? Math.max(0, a.seats - mates.length - 1) : 0;
    q("open-seat").hidden = open === 0;
    q("open-text").textContent =
      `${open} open seat${open === 1 ? "" : "s"} · saved for a walk-in`;
    const names = mates.map((m) => m.first_name).join(", ");
    q("seat-description").setAttribute(
      "aria-label",
      `Your table ${number}${names ? `, with ${names}` : ""}, and your seat${
        open ? `, plus ${open} open seat${open === 1 ? "" : "s"}` : ""
      }.${when() ? ` ${when()}.` : ""}`,
    );

    q("reason").textContent = a.summary;
    q("people").replaceChildren(
      ...mates.map((mate) => {
        const s = el("span");
        const about = [mate.role, mate.company].filter(Boolean).join(" · ");
        s.append(
          el("b", {}, mate.first_name),
          document.createTextNode(` / ${about}`),
        );
        return s;
      }),
    );
    const topics = a.topics?.length
      ? a.topics
      : [...new Set(mates.flatMap((m) => m.interests))].slice(0, 3);
    q("tags").replaceChildren(...topics.map((tag) => el("span", {}, tag)));
    q("table-summary").textContent = a.table_summary;
    q("table-summary-tile").hidden =
      !a.table_summary || a.table_summary === a.summary;
    q("edit").hidden = !editable();

    questions = [...new Set([...a.starters, ...a.table_starters])];
    if (!questions.length) questions = FALLBACK_QUESTIONS;
    questionIndex = 0;
    resetQuestion();
    renderSeated();
  }

  function revealTable() {
    if (!me?.assignment) return;
    renderAssigned(me.assignment);
    show("assigned");
  }

  // "See everyone": the whole table in a dialog

  const roster = q<HTMLDialogElement>("roster");

  function openSeats(a: MyAssignment) {
    return a.seats ? Math.max(0, a.seats - a.tablemates.length - 1) : 0;
  }

  function rosterRow(
    className: string,
    avatar: string | Node,
    name: string,
    line: string,
  ) {
    const row = el("li", {
      class: className ? `ta-roster-row ${className}` : "ta-roster-row",
    });
    const badge = el("span", {
      class: "ta-roster-avatar",
      "aria-hidden": "true",
    });
    const text = el("span", { class: "ta-roster-text" });
    if (typeof avatar === "string") badge.textContent = avatar;
    else badge.append(avatar);
    text.append(el("b", {}, name), el("small", {}, line));
    row.append(badge, text);
    return row;
  }

  function renderRoster(status: MyStatus, a: MyAssignment) {
    const number = tableNumber(a);
    const open = openSeats(a);
    const people = a.tablemates.length + 1;
    q("roster-eyebrow").textContent = a.table_name
      ? `TABLE ${number} · ${a.table_name.toUpperCase()}`
      : `TABLE ${number}`;
    q("roster-sub").textContent =
      `${people} ${people === 1 ? "person" : "people"}${
        open ? ` · ${open} open seat${open === 1 ? "" : "s"}` : ""
      }`;
    const { attendee } = status;
    const chair = root.querySelector(".ta-you .ta-avatar svg");
    const rows = [
      rosterRow(
        "ta-roster-you",
        chair ? chair.cloneNode(true) : "",
        attendee.name,
        [
          `You${status.checked_in_at ? " · checked in" : ""}`,
          attendee.role,
          attendee.company,
        ]
          .filter(Boolean)
          .join(" · "),
      ),
      ...a.tablemates.map((mate) =>
        rosterRow(
          "",
          initials(mate.first_name),
          mate.first_name,
          [mate.role, mate.company].filter(Boolean).join(" · "),
        ),
      ),
    ];
    if (open) {
      rows.push(
        rosterRow(
          "ta-roster-open",
          "+",
          open === 1 ? "Open seat" : `${open} open seats`,
          open === 1 ? "Saved for a walk-in" : "Saved for walk-ins",
        ),
      );
    }
    q("roster-list").replaceChildren(...rows);
  }

  function closeRoster() {
    if (typeof roster.close === "function") {
      roster.close();
    } else {
      roster.removeAttribute("open");
      q("see-table").focus();
    }
  }

  on(q("see-table"), "click", () => {
    if (!me?.assignment) return;
    renderRoster(me, me.assignment);
    if (typeof roster.showModal === "function") roster.showModal();
    else roster.setAttribute("open", "");
  });
  on(q("roster-close"), "click", closeRoster);
  // A click on the dialog itself (not its content) is a click on the backdrop.
  on<MouseEvent>(roster, "click", (e) => {
    if (e.target === roster) closeRoster();
  });
  on(roster, "close", () => q("see-table").focus());

  on(q("reveal-table"), "click", () => {
    if (!me?.assignment) return;
    button("reveal-table").disabled = true;
    show("matching");
    window.clearTimeout(matchTimer);
    matchTimer = window.setTimeout(revealTable, reduced() ? 0 : 800);
  });

  // Check-in, ticket, and icebreakers

  function confetti() {
    if (reduced()) return;
    const colors = ["var(--blue)", "#FFFFFF", "var(--blue-text)", "#9DC0FF"];
    for (let i = 0; i < 48; i++) {
      const p = el("span");
      p.style.background = colors[i % colors.length];
      q("confetti").append(p);
      const f = ((i * 37) % 101) / 100;
      const side = i % 2 ? 1 : -1;
      const dx = side * (25 + f * 180);
      const rise = -45 - f * 100;
      const fall = 330 + f * 240;
      const a = animate(
        p,
        [
          { transform: "translate(0,0) rotate(0deg)", opacity: 1 },
          {
            transform: `translate(${dx * 0.6}px,${rise}px) rotate(${i * 19}deg)`,
            opacity: 1,
            offset: 0.22,
          },
          {
            transform: `translate(${dx}px,${fall}px) rotate(${400 + i * 25}deg)`,
            opacity: 0,
          },
        ],
        {
          duration: 1250 + (i % 9) * 65,
          delay: (i % 5) * 25,
          easing: "cubic-bezier(.15,.65,.5,1)",
          fill: "forwards",
        },
      );
      if (a) a.onfinish = () => p.remove();
      else p.remove();
    }
  }

  function openTicket() {
    const attendee = me?.attendee;
    const a = me?.assignment;
    if (!attendee || !a) return;
    const [first, ...rest] = attendee.name.split(" ");
    const last = rest.length ? rest[rest.length - 1] : "";
    q("ticket-number").textContent = tableNumber(a);
    q("confirmed-name").textContent = first;
    q("ticket-name").textContent = [first, ...rest].join(" ");
    q("ticket-initials").textContent =
      `${first?.[0] ?? ""}${last[0] ?? ""}`.toUpperCase() || "?";
    const lines: [string, string | undefined, string][] = [
      ["ticket-title", attendee.role, "No role added"],
      ["ticket-company", attendee.company, "No company added"],
    ];
    for (const [id, value, empty] of lines) {
      q(id).textContent = value || empty;
      q(id).classList.toggle("ta-empty", !value);
    }
    q("when").textContent = when();
    q("summary-goal").textContent = `Your goals: ${goals
      .filter((g) => attendee.goals.includes(g.id))
      .map((g) => g.label)
      .join(", ")}`;
    q("summary-offer").textContent = `Your focus: ${
      goals.find((g) => g.id === attendee.focus_goal)?.label ?? ""
    }`;
    show("confirmed");
  }

  function you() {
    return root.querySelector<HTMLElement>(".ta-you");
  }

  function renderSeated() {
    const seated = Boolean(me?.checked_in_at);
    q("seated").hidden = seated;
    q("seated-done").hidden = !seated;
    const seat = you();
    if (seat) {
      seat.classList.toggle("ta-is-seated", seated);
      setText(seat, "small", seated ? "You · seated" : "You");
    }
    q("seat-status").textContent = seated
      ? "You’re here"
      : "Your seat is waiting";
  }

  on(q("seated"), "click", async () => {
    if (busy || !me?.assignment || !token) return;
    setBusy("seated", true);
    try {
      const status = await request<MyStatus>(
        "/api/me/check-in",
        {},
        {
          attendeeToken: token,
        },
      );
      if (signal.aborted) return;
      me = status;
      renderSeated();
      openTicket();
      confetti();
    } catch (error) {
      if (!signal.aborted) q("seat-status").textContent = errorText(error);
    } finally {
      setBusy("seated", false);
    }
  });
  on(q("seated-done"), "click", () => {
    if (me?.checked_in_at) openTicket();
  });

  // The conversation card: flip it over, then deal another

  let flipping = false;

  function resetQuestion() {
    questionRevealed = false;
    q("question").classList.remove("ta-revealed");
    q("question-text").textContent = "Break the ice.";
    q("question-hint").textContent =
      "Tap to turn over your table’s conversation card ↗";
    q("question-actions").hidden = true;
  }

  function fillQuestion() {
    if (!questions.length) return;
    q("question-text").textContent = questions[questionIndex];
    q("question-hint").textContent =
      "Go around the table. Start wherever you like.";
  }

  function shine() {
    const streak = q("question").querySelector(".ta-q-shine");
    if (!streak) return;
    animate(
      streak,
      [
        { transform: "translateX(-120%)", opacity: 0 },
        { opacity: 1, offset: 0.2 },
        { transform: "translateX(120%)", opacity: 0 },
      ],
      { duration: 900, easing: "ease-out" },
    );
  }

  function burst() {
    const colors = ["#FFFFFF", "#9DC0FF", "#5B95F7"];
    for (let i = 0; i < 16; i++) {
      const s = el("span", i % 2 ? { class: "ta-q-dot" } : {});
      s.style.background = colors[i % 3];
      q("q-burst").append(s);
      const angle = (i / 16) * Math.PI * 2 + ((i * 37) % 10) / 25;
      const distance = 120 + ((i * 53) % 70);
      const dx = Math.cos(angle) * distance * 1.25;
      const dy = Math.sin(angle) * distance * 0.75;
      const a = animate(
        s,
        [
          { transform: "translate(0,0) scale(.3) rotate(45deg)", opacity: 0 },
          { opacity: 1, offset: 0.12 },
          {
            transform: `translate(${dx}px,${dy}px) scale(1) rotate(${225 + i * 40}deg)`,
            opacity: 0,
          },
        ],
        {
          duration: 720 + (i % 4) * 110,
          easing: "cubic-bezier(.15,.7,.3,1)",
          fill: "forwards",
        },
      );
      if (a) a.onfinish = () => s.remove();
      else s.remove();
    }
  }

  function reveal() {
    if (!questions.length) return;
    questionRevealed = true;
    q("question-actions").hidden = false;
    const card = q("question");
    card.classList.add("ta-revealed");
    const half = animate(
      card,
      [
        {
          transform: "perspective(1000px) rotateY(0deg) scale(1)",
          filter: "brightness(1)",
        },
        {
          transform:
            "perspective(1000px) rotateY(90deg) scale(1.06) translateY(-6px)",
          filter: "brightness(1.6)",
        },
      ],
      { duration: 230, easing: "cubic-bezier(.5,0,.9,.5)" },
    );
    if (!half) {
      fillQuestion();
      return;
    }
    flipping = true;
    half.onfinish = () => {
      if (signal.aborted) return;
      fillQuestion();
      const back = card.animate(
        [
          {
            transform:
              "perspective(1000px) rotateY(-90deg) scale(1.06) translateY(-6px)",
            filter: "brightness(1.6)",
          },
          {
            transform: "perspective(1000px) rotateY(12deg) scale(1.02)",
            filter: "brightness(1.1)",
            offset: 0.6,
          },
          {
            transform: "perspective(1000px) rotateY(-4deg) scale(1)",
            filter: "brightness(1)",
            offset: 0.82,
          },
          {
            transform: "perspective(1000px) rotateY(0deg) scale(1)",
            filter: "brightness(1)",
          },
        ],
        { duration: 640, easing: "cubic-bezier(.2,.7,.2,1)" },
      );
      card.animate(
        [
          {
            boxShadow:
              "0 0 0 0 rgba(157,192,255,.9),0 18px 44px rgba(30,110,243,.45)",
          },
          {
            boxShadow:
              "0 0 0 16px rgba(157,192,255,0),0 26px 80px rgba(30,110,243,.9)",
            offset: 0.45,
          },
          {
            boxShadow:
              "0 0 0 0 rgba(157,192,255,0),0 18px 44px rgba(30,110,243,.45)",
          },
        ],
        { duration: 1000, easing: "ease-out" },
      );
      burst();
      shine();
      back.onfinish = () => {
        flipping = false;
      };
    };
  }

  function deal() {
    if (flipping || !questions.length) return;
    questionIndex = (questionIndex + 1) % questions.length;
    const card = q("question");
    const out = animate(
      card,
      [
        { transform: "translate(0,0) rotate(0deg)", opacity: 1 },
        { transform: "translate(70%,-14px) rotate(14deg)", opacity: 0 },
      ],
      { duration: 280, easing: "cubic-bezier(.5,0,.9,.4)", fill: "forwards" },
    );
    if (!out) {
      fillQuestion();
      return;
    }
    flipping = true;
    out.onfinish = () => {
      if (signal.aborted) return;
      fillQuestion();
      const dealIn = card.animate(
        [
          {
            transform: "translate(-40px,40px) rotate(-8deg) scale(.9)",
            opacity: 0,
          },
          {
            transform: "translate(0,-6px) rotate(1.5deg) scale(1.01)",
            opacity: 1,
            offset: 0.7,
          },
          { transform: "translate(0,0) rotate(0deg) scale(1)", opacity: 1 },
        ],
        { duration: 480, easing: "cubic-bezier(.2,.7,.2,1)" },
      );
      out.cancel();
      shine();
      dealIn.onfinish = () => {
        flipping = false;
      };
    };
  }

  on(q("question"), "click", () => {
    if (!questionRevealed && !flipping) reveal();
  });
  on(q("another"), "click", deal);

  // Header: logo, then the event name (onboarding) or event and lunch hours

  function updateBrand(name: Screen) {
    const can = Boolean(backFrom(name));
    q("brand").setAttribute("aria-disabled", String(!can));
    q("brand").setAttribute(
      "aria-label",
      can ? "TableTalk. Go back one page" : "TableTalk",
    );
  }

  function goBack() {
    const prev = backFrom(current);
    if (!prev) return;
    if (prev === "waiting") renderWaiting();
    show(prev, { back: true });
  }
  on(q("brand"), "click", goBack);

  function setNav(name: Screen) {
    const onboarding = ONBOARDING.includes(name);
    q("event").hidden = !onboarding;
    q("event-time").hidden = onboarding;
  }

  function resetForm() {
    ["first-name", "last-name", "title", "company", "custom-interest"].forEach(
      (id) => {
        input(id).value = "";
        input(id).removeAttribute("aria-invalid");
      },
    );
    [
      "first-error",
      "last-error",
      "title-error",
      "interest-error",
      "submit-error",
    ].forEach((id) => {
      q(id).hidden = true;
    });
    [...q("interests").children].forEach((choice) => {
      const box = choice.querySelector("input");
      if (!box) return;
      if (!INTERESTS.includes(box.value)) choice.remove();
      else box.checked = false;
    });
    state.interests.clear();
    state.selectedGoals.clear();
    state.explicitFocus = null;
    state.answers = {};
    goalViews.forEach((v) => {
      v.input.value = "";
      v.error.hidden = true;
      v.input.setAttribute("aria-invalid", "false");
    });
    q("goal-error").hidden = true;
    renderGoals();
    questions = [];
    questionIndex = 0;
    resetQuestion();
  }

  /** Put a saved enrollment back into the form, so editing starts from it. */
  function fillForm(status: MyStatus) {
    resetForm();
    const { attendee } = status;
    const [first, ...rest] = attendee.name.split(" ");
    input("first-name").value = first ?? "";
    input("last-name").value = rest.join(" ");
    input("title").value = attendee.role;
    input("company").value = attendee.company ?? "";
    attendee.interests.forEach((i) => addInterest(i, { select: true }));
    attendee.goals.forEach((g) => state.selectedGoals.add(g));
    state.explicitFocus = attendee.focus_goal ?? null;
    state.answers = { ...(attendee.goal_answers ?? {}) };
    renderGoals();
  }

  // Footer: "Forget me on this device" (shown once this device is signed up)

  on(q("forget"), "click", () => {
    window.clearTimeout(matchTimer);
    if (roster.open) closeRoster();
    forgetToken();
    resetForm();
    applyEventDetails();
    show("about", { back: true });
  });

  // Start: show the form right away, then load the event and any saved enrollment.

  show("about", { focus: false });
  applyEventDetails();
  void (async () => {
    await loadConference();
    if (!token || signal.aborted) return;
    let status: MyStatus | null = null;
    try {
      status = await refresh();
    } catch {
      return;
    }
    if (!status || signal.aborted) return;
    fillForm(status);
    applyEventDetails();
    if (status.assignment) {
      renderAssigned(status.assignment);
      show("assigned", { focus: false });
    } else {
      renderWaiting();
      show("waiting", { focus: false });
    }
  })();

  return () => {
    controller.abort();
    window.clearTimeout(matchTimer);
    window.clearInterval(pollTimer);
  };
}
