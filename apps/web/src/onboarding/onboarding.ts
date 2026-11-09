/**
 * Behavior for the attendee onboarding page (/user-onboarding), ported from the
 * TableTalk attendee prototype (tabletalk-tikatika.html).
 *
 * Step 1: name, title, and company. Step 2: interests. Step 3: pick one or more
 * lunch goals, star one as the focus, and answer a question for each selected
 * goal. Then a waiting room, a sample match, check-in, and icebreakers.
 *
 * Everything runs in the browser. Goals, tables, and matching are sample data,
 * and nothing is sent to the conference API yet.
 */

export type GoalId =
  "learn" | "meet" | "collaborate" | "opportunities" | "share";

export interface GoalDefinition {
  id: GoalId;
  label: string;
  short: string;
  question: string;
  hint: string;
}

// Demo configuration from the template. When this page is connected to the
// API, replace these IDs, labels, questions, and example hints with the
// server's goal definitions (see services/api/src/tabletalk_api/seed.py).
export const GOALS: readonly GoalDefinition[] = [
  {
    id: "learn",
    label: "Learn something",
    short: "Questions & fresh ideas",
    question: "What questions do you have about the topics you want to learn?",
    hint: "For example: How do teams find their first ten customers?",
  },
  {
    id: "meet",
    label: "Meet new people",
    short: "Good company, no pitch",
    question: "Who would you love to meet over lunch?",
    hint: "For example: Founders, designers, or people working in AI.",
  },
  {
    id: "collaborate",
    label: "Find collaborators",
    short: "Build something together",
    question: "What are you working on, and who could help you build it?",
    hint: "For example: A developer to test an idea with, or a cofounder with sales experience.",
  },
  {
    id: "opportunities",
    label: "Explore opportunities",
    short: "Your next chapter",
    question: "What kind of opportunity are you looking for?",
    hint: "For example: A product role at an early-stage company.",
  },
  {
    id: "share",
    label: "Share my expertise",
    short: "Put your experience to use",
    question: "What would you enjoy helping someone with?",
    hint: "For example: Reviewing a portfolio or finding early customers.",
  },
];

const INTERESTS = [
  "AI & tech",
  "Design",
  "Startups",
  "Sales & growth",
  "Careers",
  "Community",
];

interface SampleTable {
  number: string;
  vibe: string;
  theme: [string, string];
  names: string[];
  initials: string[];
  people: [string, string][];
  tags: string[];
  questions: string[];
}

const TABLES = {
  builders: {
    number: "08",
    vibe: "THE BUILDERS’ TABLE",
    theme: ["Small bets.", "Big ideas."],
    names: ["Maya", "Jordan", "Ari", "Sam"],
    initials: ["MK", "JL", "AP", "SN"],
    people: [
      ["Maya", "Founder, building her first sales motion"],
      ["Jordan", "Product designer, open to collaborations"],
    ],
    tags: ["Side projects", "Product", "Early customers"],
    questions: [
      "What’s a tiny frustration you’d happily build an entire company to fix?",
      "What’s something you shipped before you felt ready?",
      "What’s one skill at this table you’d borrow for a week?",
    ],
  },
  career: {
    number: "12",
    vibe: "THE NEXT-CHAPTER TABLE",
    theme: ["New faces.", "Next moves."],
    names: ["Nina", "Chris", "Alex", "Drew"],
    initials: ["NP", "CL", "AT", "DW"],
    people: [
      ["Nina", "Engineering lead, growing her team"],
      ["Alex", "Developer, exploring new roles"],
    ],
    tags: ["Career pivots", "Hiring", "Small teams"],
    questions: [
      "What’s a job you’d try for a month if experience didn’t matter?",
      "What’s something you’re good at that your résumé completely misses?",
      "What’s the best career advice you decided to ignore?",
    ],
  },
  open: {
    number: "03",
    vibe: "THE HAPPY-DETOUR TABLE",
    theme: ["No pitches.", "Good stories."],
    names: ["Leo", "Jess", "Kai", "Robin"],
    initials: ["LC", "JS", "KN", "RH"],
    people: [
      ["Leo", "Architect, curious about technology"],
      ["Jess", "Community builder, side-project enthusiast"],
    ],
    tags: ["Creative work", "Side quests", "Fresh perspectives"],
    questions: [
      "What’s a rabbit hole you fell into recently that deserves more attention?",
      "What’s a surprisingly strong opinion you have about something very unimportant?",
      "What’s something you made just because you wanted it to exist?",
    ],
  },
} satisfies Record<string, SampleTable>;

/** Example submission shape only: align these field names with the API. */
export interface OnboardingSubmission {
  name: string;
  role: string;
  company: string;
  interests: string[];
  selected_goal_ids: GoalId[];
  focus_goal_id: GoalId | null;
  goal_answers: Partial<Record<GoalId, string>>;
}

interface PrototypeHook {
  goalDefinitions: GoalDefinition[];
  buildSubmission: () => OnboardingSubmission;
  getLastSubmission: () => OnboardingSubmission | null;
  getCheckIn: () => { table: string; seatedAt: string } | null;
}

declare global {
  interface Window {
    /** Development hook for checking the prototype's submission, not an API. */
    tabletalkPrototype?: Readonly<PrototypeHook>;
  }
}

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
}

/**
 * Wires up the onboarding markup inside `root` (the #tabletalk-auto element).
 * Returns a function that removes the page's listeners and timers.
 */
export function initOnboarding(root: HTMLElement): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  let matchTimer: number | undefined;

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

  const state = {
    selectedGoals: new Set<GoalId>(),
    explicitFocus: null as GoalId | null,
    answers: {} as Partial<Record<GoalId, string>>,
    interests: new Set<string>(),
    lastSubmission: null as OnboardingSubmission | null,
  };
  let current: Screen = "about";
  let assigned: SampleTable | null = null;
  let questionIndex = 0;
  let questionRevealed = false;
  let seatedAt: string | null = null;

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
    return GOALS.filter((g) => state.selectedGoals.has(g.id));
  }

  function focusGoal(): GoalId | null {
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
  }

  function pulse(node: Element) {
    animate(node, [{ transform: "scale(.97)" }, { transform: "scale(1)" }], {
      duration: 170,
      easing: "ease-out",
    });
  }

  // Step 2: interests

  function addInterest(label: string, { select = false } = {}) {
    const value = label.trim();
    if (!value) return;
    const existing = [...q("interests").querySelectorAll("input")].find(
      (i) => i.value.toLowerCase() === value.toLowerCase(),
    );
    if (existing) {
      if (select) {
        existing.checked = true;
        state.interests.add(existing.value);
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
    checkbox.checked = select;
    if (select) state.interests.add(value);
    on(checkbox, "change", () => {
      if (checkbox.checked) state.interests.add(value);
      else state.interests.delete(value);
      pulse(span);
    });
    wrap.append(checkbox, span);
    q("interests").append(wrap);
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

  const goalViews = new Map<GoalId, GoalView>();

  function chooseGoal(id: GoalId, selected: boolean) {
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

  GOALS.forEach((g) => {
    const card = el("div", {
      class: "ta-goal-item",
      id: `ta-goal-item-${g.id}`,
    });
    const header = el("div", { class: "ta-goal-item-header" });
    const label = el("label", { class: "ta-goal-select cursor-interaction" });
    const choice = el("input", {
      type: "checkbox",
      value: g.id,
      id: `ta-choice-${g.id}`,
      "aria-controls": `ta-prompt-${g.id}`,
    });
    const text = el("span");
    text.append(el("b", {}, g.label), el("small", {}, g.short));
    label.append(choice, text);
    const star = el("button", {
      type: "button",
      id: `ta-star-${g.id}`,
      class: "ta-focus-star cursor-interaction",
      "aria-pressed": "false",
      "aria-label": `Select ${g.label} and make it your focus`,
    });
    star.append(starIcon());
    header.append(label, star);

    const body = el("div", {
      class: "ta-goal-prompt",
      id: `ta-prompt-${g.id}`,
    });
    const badge = el("span", { class: "ta-focus-indicator" }, "YOUR FOCUS");
    const question = el(
      "label",
      { class: "ta-label", for: `ta-answer-${g.id}` },
      g.question,
    );
    const hint = el(
      "p",
      { class: "ta-field-hint", id: `ta-hint-${g.id}` },
      g.hint,
    );
    const answer = el("textarea", {
      id: `ta-answer-${g.id}`,
      rows: "3",
      maxlength: "600",
      "aria-describedby": `ta-hint-${g.id} ta-answer-error-${g.id}`,
    });
    const error = el("p", {
      class: "ta-field-error",
      id: `ta-answer-error-${g.id}`,
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

  function fieldError(inputId: string, errorId: string, message: string) {
    const field = q(inputId);
    const error = q(errorId);
    field.setAttribute("aria-invalid", String(Boolean(message)));
    error.textContent = message;
    error.hidden = !message;
  }

  // Step 1: about you

  const REQUIRED_NAMES: [string, string, string][] = [
    ["first-name", "first-error", "first name"],
    ["last-name", "last-error", "last name"],
  ];

  function aboutValid() {
    let firstInvalid: HTMLInputElement | null = null;
    for (const [id, errorId, label] of REQUIRED_NAMES) {
      const missing = !input(id).value.trim();
      fieldError(id, errorId, missing ? `Please add your ${label}.` : "");
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
  REQUIRED_NAMES.forEach(([id, errorId]) =>
    on(q(id), "input", () => {
      if (input(id).value.trim()) fieldError(id, errorId, "");
    }),
  );

  function renderGoals({ animateId = null as GoalId | null } = {}) {
    const selected = selectedGoals();
    const focus = focusGoal();
    GOALS.forEach((g) => {
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
    const focusLabel = GOALS.find((g) => g.id === focus)?.label;
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
  function buildSubmission(): OnboardingSubmission {
    const person = identity();
    const selected = selectedGoals();
    return {
      name: [person.first, person.last].join(" ").trim(),
      role: person.title,
      company: person.company,
      interests: [...state.interests],
      selected_goal_ids: selected.map((g) => g.id),
      focus_goal_id: focusGoal(),
      goal_answers: Object.fromEntries(
        selected.map((g) => [g.id, (state.answers[g.id] ?? "").trim()]),
      ),
    };
  }

  function goalsValid() {
    let firstInvalid: HTMLElement | null = null;
    if (!state.selectedGoals.size) {
      q("goal-error").textContent = "Choose at least one lunch goal.";
      q("goal-error").hidden = false;
      firstInvalid = q(`choice-${GOALS[0].id}`);
    }
    for (const g of selectedGoals()) {
      const answer = q<HTMLTextAreaElement>(`answer-${g.id}`);
      state.answers[g.id] = answer.value;
      if (!answer.value.trim()) {
        answer.setAttribute("aria-invalid", "true");
        q(`answer-error-${g.id}`).textContent =
          "A few words will help us find the right people.";
        q(`answer-error-${g.id}`).hidden = false;
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

  // Waiting room and sample match

  function resetWaitingRoom() {
    q("wait-initial").hidden = false;
    q("wait-ready").hidden = true;
    q("demo-control").hidden = false;
    button("reveal-table").disabled = false;
  }

  function waiting() {
    resetWaitingRoom();
    show("waiting");
  }

  function goalsNext() {
    if (!goalsValid()) return;
    state.lastSubmission = buildSubmission();
    waiting();
  }
  on(q("goals-form"), "submit", (e) => {
    e.preventDefault();
    goalsNext();
  });
  on(q("submit"), "click", goalsNext);
  on(q("edit"), "click", () => show("about", { back: true }));
  on(q("wait-edit"), "click", () => show("about", { back: true }));

  function setText(parent: ParentNode, selector: string, text: string) {
    const node = parent.querySelector(selector);
    if (node) node.textContent = text;
  }

  function pair() {
    const submission = state.lastSubmission;
    if (!submission) return;
    const focus = submission.focus_goal_id;
    const offer = submission.goal_answers.share ?? "";
    let t: SampleTable;
    let reason: string;
    if (focus === "meet") {
      t = TABLES.open;
      reason =
        "You’re here to meet new people. This table mixes creative backgrounds with a shared curiosity about making things.";
    } else if (focus === "opportunities") {
      t = TABLES.career;
      reason =
        "You’re exploring what’s next. Nina is growing her team, and Alex is making a similar move.";
    } else if (focus === "share") {
      t = TABLES.builders;
      reason = /sales|growth|outbound|customer/i.test(offer)
        ? "Maya is building her first sales motion. Your experience can help, alongside different perspectives on product and design."
        : "Maya and Ari are building early-stage projects. This table brings together people who can share experience and learn from each other.";
    } else {
      t = TABLES.builders;
      reason =
        focus === "learn"
          ? "You’re here to learn. Jordan brings product experience, while Maya and Ari bring the perspective of building from scratch."
          : "You’re looking for collaborators. Jordan is open to projects, and Maya and Ari are building early-stage products.";
    }
    assigned = t;
    q("number").textContent = t.number;
    q("seated-number").textContent = t.number;
    q("done-number").textContent = t.number;
    resetSeated();
    q("vibe").textContent = t.vibe;
    q("theme").replaceChildren(
      document.createTextNode(t.theme[0]),
      document.createElement("br"),
      document.createTextNode(t.theme[1]),
    );
    q("reason").textContent = reason;
    q("seat-description").setAttribute(
      "aria-label",
      `Your assigned table ${t.number}, with ${t.names.join(", ")}, your seat, and one open seat. 12:30 to 1:15, Main hall`,
    );
    root
      .querySelectorAll(".ta-seat:not(.ta-you):not(.ta-open)")
      .forEach((seat, i) => {
        setText(seat, ".ta-avatar", t.initials[i]);
        setText(seat, "small", t.names[i]);
      });
    q("people").replaceChildren(
      ...t.people.map(([name, bio]) => {
        const s = el("span");
        s.append(el("b", {}, name), document.createTextNode(` / ${bio}`));
        return s;
      }),
    );
    q("tags").replaceChildren(...t.tags.map((tag) => el("span", {}, tag)));
    q("summary-goal").textContent = `Your goals: ${GOALS.filter((g) =>
      submission.selected_goal_ids.includes(g.id),
    )
      .map((g) => g.label)
      .join(", ")}`;
    q("summary-offer").textContent = `Your focus: ${
      GOALS.find((g) => g.id === focus)?.label ?? ""
    }`;
    show("assigned");
  }

  on(q("simulate"), "click", () => {
    q("ready-message").textContent =
      `The wait is over, ${identity().first}. We found your table.`;
    q("wait-initial").hidden = true;
    q("wait-ready").hidden = false;
    q("demo-control").hidden = true;
    q("reveal-table").focus();
  });
  on(q("reveal-table"), "click", () => {
    button("reveal-table").disabled = true;
    show("matching");
    window.clearTimeout(matchTimer);
    matchTimer = window.setTimeout(pair, reduced() ? 0 : 800);
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
    if (!assigned) return;
    const person = identity();
    q("ticket-number").textContent = assigned.number;
    q("confirmed-name").textContent = person.first;
    q("ticket-name").textContent = `${person.first} ${person.last}`;
    q("ticket-role").textContent = [person.title, person.company]
      .filter(Boolean)
      .join(" · ");
    q("ticket-role").hidden = !person.title && !person.company;
    show("confirmed");
  }

  function you() {
    return root.querySelector<HTMLElement>(".ta-you");
  }

  function resetSeated() {
    seatedAt = null;
    q("seated").hidden = false;
    q("seated-done").hidden = true;
    const seat = you();
    if (seat) {
      seat.classList.remove("ta-is-seated");
      setText(seat, "small", "You");
    }
    q("seat-status").textContent = "Your seat is waiting";
  }

  on(q("seated"), "click", () => {
    if (seatedAt || !assigned) return;
    seatedAt = new Date().toISOString();
    const seat = you();
    if (seat) {
      seat.classList.add("ta-is-seated");
      setText(seat, "small", "You · seated");
    }
    q("seat-status").textContent = "You’re here";
    q("seated").hidden = true;
    q("seated-done").hidden = false;
    openTicket();
    confetti();
  });
  on(q("seated-done"), "click", () => {
    if (seatedAt && assigned) openTicket();
  });

  function reveal() {
    if (!assigned) return;
    questionRevealed = true;
    q("question-text").textContent = assigned.questions[questionIndex];
    q("question-hint").textContent =
      "Go around the table. Start wherever you like.";
    q("question-actions").hidden = false;
    animate(
      q("question"),
      [{ transform: "rotateY(-9deg)" }, { transform: "rotateY(0deg)" }],
      { duration: 280 },
    );
  }
  on(q("question"), "click", () => {
    if (!questionRevealed) reveal();
  });
  on(q("another"), "click", () => {
    if (!assigned) return;
    questionIndex = (questionIndex + 1) % assigned.questions.length;
    reveal();
  });

  const hook: Readonly<PrototypeHook> = Object.freeze({
    goalDefinitions: GOALS.map((g) => ({ ...g })),
    buildSubmission,
    getLastSubmission: () =>
      state.lastSubmission
        ? (JSON.parse(
            JSON.stringify(state.lastSubmission),
          ) as OnboardingSubmission)
        : null,
    getCheckIn: () =>
      seatedAt && assigned ? { table: assigned.number, seatedAt } : null,
  });
  window.tabletalkPrototype = hook;

  // Header: logo, event label, and menu

  function updateBrand(name: Screen) {
    const can = Boolean(BACK[name]);
    q("brand").setAttribute("aria-disabled", String(!can));
    q("brand").setAttribute(
      "aria-label",
      can ? "TableTalk. Go back one page" : "TableTalk",
    );
  }

  function goBack() {
    const prev = BACK[current];
    if (!prev) return;
    closeNav();
    if (prev === "waiting") resetWaitingRoom();
    show(prev, { back: true });
  }
  on(q("brand"), "click", goBack);

  function setNav(name: Screen) {
    const onboarding = ONBOARDING.includes(name);
    q("event").hidden = !onboarding;
    q("nav").hidden = onboarding;
    closeNav();
  }

  function openNav() {
    q("nav-menu").hidden = false;
    q("nav-button").setAttribute("aria-expanded", "true");
    q("nav-button").setAttribute("aria-label", "Close menu");
    q("nav-edit").focus();
  }

  function closeNav(returnFocus = false) {
    if (q("nav-menu").hidden) return;
    q("nav-menu").hidden = true;
    q("nav-button").setAttribute("aria-expanded", "false");
    q("nav-button").setAttribute("aria-label", "Menu");
    if (returnFocus) q("nav-button").focus();
  }

  on(q("nav-button"), "click", () => {
    if (q("nav-menu").hidden) openNav();
    else closeNav(true);
  });
  on(q("nav-edit"), "click", () => {
    closeNav();
    resetSeated();
    show("about", { back: true });
  });

  function resetDemo() {
    ["first-name", "last-name", "title", "company", "custom-interest"].forEach(
      (id) => {
        input(id).value = "";
        input(id).removeAttribute("aria-invalid");
      },
    );
    ["first-error", "last-error"].forEach((id) => {
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
    state.lastSubmission = null;
    goalViews.forEach((v) => {
      v.input.value = "";
      v.error.hidden = true;
      v.input.setAttribute("aria-invalid", "false");
    });
    q("goal-error").hidden = true;
    renderGoals();
    assigned = null;
    questionIndex = 0;
    questionRevealed = false;
    resetSeated();
    q("question-text").textContent = "Break the ice.";
    q("question-hint").textContent =
      "Tap to turn over your table’s conversation card ↗";
    q("question-actions").hidden = true;
    show("about", { back: true });
  }
  on(q("nav-restart"), "click", () => {
    closeNav();
    resetDemo();
  });

  on<KeyboardEvent>(root, "keydown", (e) => {
    if (e.key === "Escape" && !q("nav-menu").hidden) {
      e.preventDefault();
      closeNav(true);
    }
  });
  on(document, "click", (e) => {
    if (!q("nav").contains(e.target as Node)) closeNav();
  });

  show("about", { focus: false });

  return () => {
    controller.abort();
    window.clearTimeout(matchTimer);
    if (window.tabletalkPrototype === hook) delete window.tabletalkPrototype;
  };
}
