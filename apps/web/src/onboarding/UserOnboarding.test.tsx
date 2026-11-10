import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ConferenceState,
  EnrollmentStatus,
  MyAssignment,
  MyStatus,
} from "../api";
import { ATTENDEE_TOKEN_KEY } from "../attendeeToken";
import UserOnboarding from "./UserOnboarding";
import { POLL_MS } from "./onboarding";

const learnQuestion = "What do you want to learn at lunch?";
const shareQuestion = "What could you share?";

const conference: ConferenceState = {
  id: "cloud-native-hackathon",
  name: "Cloud Native Hackathon",
  lunch_slot: "day-1-lunch",
  lunch_label: "Day 1 lunch",
  group_size: 6,
  attendees: [],
  attendee_count: 3,
  plan: null,
  published: false,
  status: "open",
  enrollment_closes_at: "2026-11-10T18:40:00Z",
  lunch_time: "12:30–1:15",
  location: "Hall B",
  goal_prompts: {
    Learn: {
      label: "Learn something",
      short: "Questions",
      question: learnQuestion,
      example_hint: "Autoscaling for flash sales",
    },
    "Share something I've learned": {
      label: "Share my expertise",
      short: "Experience",
      question: shareQuestion,
      example_hint: "Tracing at scale",
    },
    "Meet connections": {
      label: "Meet new people",
      short: "People",
      question: "What would you enjoy discussing?",
      example_hint: "Platforms",
    },
  },
};

const attendee = {
  id: "a1",
  conference_id: "cloud-native-hackathon",
  name: "Ada Lovelace",
  role: "Engineer",
  company: "Analytical Engines",
  interests: ["kubernetes", "climate"],
  goals: ["Share something I've learned" as const],
  focus_goal: "Share something I've learned" as const,
  goal_answers: { "Share something I've learned": "Sales and early growth." },
  lunch_slot: "day-1-lunch",
};

const assignment: MyAssignment = {
  table_number: 8,
  tablemates: [
    {
      first_name: "Maya",
      role: "Founder",
      company: "Acme",
      interests: ["sales"],
    },
    { first_name: "Jordan", role: "Designer", interests: ["product"] },
  ],
  summary: "Maya is building her first sales motion. You can help.",
  starters: ["Ask Maya how she finds her first customers."],
  table_summary: "This table shares an interest in early growth.",
  table_starters: ["What did you ship before you felt ready?"],
  narrative_source: "llm",
  table_name: "The Builders' table",
  tagline: ["Small bets.", "Big ideas."],
  topics: ["sales", "product"],
  seats: 5,
  lunch_time: "12:30–1:15",
  location: "Hall B",
};

function status(
  overrides: Partial<MyStatus> = {},
  value: EnrollmentStatus = "open",
): MyStatus {
  return {
    attendee,
    status: value,
    enrollment_closes_at: conference.enrollment_closes_at ?? null,
    assignment: null,
    checked_in_at: null,
    ...overrides,
  };
}

function json(body: unknown, code = 200) {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: { "content-type": "application/json" },
  });
}

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  clear() {
    this.values.clear();
  }
}

type Handler = (init: RequestInit | undefined) => Response;
let routes: Record<string, Handler>;
let calls: { key: string; init?: RequestInit }[];
let storage: MemoryStorage;

function route(method: string, path: string, handler: Handler) {
  routes[`${method} ${path}`] = handler;
}

function sent(key: string) {
  const call = calls.filter((c) => c.key === key).at(-1);
  return call?.init?.body ? JSON.parse(String(call.init.body)) : undefined;
}

function type(label: RegExp | string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

function click(name: RegExp | string, role = "button") {
  fireEvent.click(screen.getByRole(role, { name }));
}

function heading(name: RegExp) {
  return screen.getByRole("heading", { name });
}

function byId(id: string) {
  const node = document.getElementById(`ta-${id}`);
  if (!node) throw new Error(`Missing #ta-${id}`);
  return node;
}

function completeAboutYou() {
  type(/First name/, "Ada");
  type(/Last name/, "Lovelace");
  type(/Title/, "Engineer");
  type(/Company/, "Analytical Engines");
  click(/Next: your interests/);
}

async function mount() {
  render(<UserOnboarding />);
  // The server's goal wording replaces the built-in copy once loaded.
  await screen.findByText("Questions");
}

beforeEach(() => {
  // Reduced motion skips the template's animations and the matching delay.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: true } as MediaQueryList),
  );
  storage = new MemoryStorage();
  vi.stubGlobal("localStorage", storage);
  routes = {};
  calls = [];
  route("GET", "/api/conference", () => json(conference));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${path}`;
      calls.push({ key, init });
      const handler = routes[key];
      return handler
        ? handler(init)
        : json({ detail: `No route for ${key}` }, 404);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("attendee onboarding", () => {
  it("asks for a name, title, and an interest before moving on", async () => {
    await mount();
    expect(heading(/First, a proper/)).toBeTruthy();
    expect(byId("event").textContent).toBe("CLOUD NATIVE HACKATHON / LUNCH");
    // The event and lunch hours badge replaces it after onboarding.
    expect(byId("event-time").hidden).toBe(true);
    click(/Next: your interests/);
    expect(screen.getByText("Please add your first name.")).toBeTruthy();
    expect(screen.getByText(/Please add your title/)).toBeTruthy();

    completeAboutYou();
    expect(document.activeElement).toBe(heading(/What gets you/));
    click(/Next: your goals/);
    expect(screen.getByText(/Pick at least one interest/)).toBeTruthy();
    click("Kubernetes", "checkbox");
    click(/Next: your goals/);
    expect(heading(/Pick your goals/)).toBeTruthy();
  });

  it("enrolls with the server's goals and remembers this device", async () => {
    route("POST", "/api/enrollments", () =>
      json({ ...attendee, attendee_token: "secret-token" }, 201),
    );
    route("GET", "/api/me", () => json(status()));
    await mount();
    completeAboutYou();
    click("Kubernetes", "checkbox");
    type("Add your own", "Climate");
    click("Add");
    click(/Next: your goals/);

    click(/Save my spot/);
    expect(screen.getByText("Choose at least one lunch goal.")).toBeTruthy();

    click(/Learn something/, "checkbox");
    type(learnQuestion, "How do teams find their first customers?");
    click("Select Share my expertise and make it your focus");
    type(shareQuestion, "Sales and early growth.");
    expect(
      screen.getByText("2 goals selected · Focus: Share my expertise"),
    ).toBeTruthy();
    // A deselected goal keeps its draft answer but leaves the submission.
    click(/Learn something/, "checkbox");
    click(/Save my spot/);

    expect(
      await screen.findByRole("heading", { name: /is on its way/ }),
    ).toBeTruthy();
    expect(sent("POST /api/enrollments")).toEqual({
      name: "Ada Lovelace",
      role: "Engineer",
      company: "Analytical Engines",
      interests: ["Kubernetes", "Climate"],
      goals: ["Share something I've learned"],
      focus_goal: "Share something I've learned",
      goal_answers: {
        "Share something I've learned": "Sales and early growth.",
      },
      lunch_slot: "day-1-lunch",
    });
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBe("secret-token");
    expect(screen.getByText("TABLE MATCHES OPEN AT")).toBeTruthy();
  });

  it.each([true, false])(
    "signs up the same way with the wheelchair box checked: %s",
    async (checked) => {
      route("POST", "/api/enrollments", () =>
        json({ ...attendee, attendee_token: "t" }, 201),
      );
      route("GET", "/api/me", () => json(status()));
      await mount();
      const box = screen.getByRole("checkbox", {
        name: "I need a wheelchair-accessible place at my lunch table.",
      }) as HTMLInputElement;
      expect(box.checked).toBe(false);
      // The whole label is the tap target.
      if (checked) {
        fireEvent.click(
          screen.getByText(
            "I need a wheelchair-accessible place at my lunch table.",
          ),
        );
      }
      expect(box.checked).toBe(checked);
      completeAboutYou();
      click("Kubernetes", "checkbox");
      click(/Next: your goals/);
      click(/Meet new people/, "checkbox");
      type("What would you enjoy discussing?", "Platforms");
      click(/Save my spot/);
      expect(
        await screen.findByRole("heading", { name: /is on its way/ }),
      ).toBeTruthy();
      const body = sent("POST /api/enrollments");
      expect(Object.keys(body).sort()).toEqual([
        "company",
        "focus_goal",
        "goal_answers",
        "goals",
        "interests",
        "lunch_slot",
        "name",
        "role",
      ]);
      expect(JSON.stringify(body)).not.toMatch(/wheelchair|access/i);
    },
  );

  it("shows the server's reason when enrollment fails", async () => {
    route("POST", "/api/enrollments", () =>
      json({ detail: "Enrollment for this lunch has closed." }, 409),
    );
    await mount();
    completeAboutYou();
    click("Kubernetes", "checkbox");
    click(/Next: your goals/);
    click(/Meet new people/, "checkbox");
    type("What would you enjoy discussing?", "Platforms");
    click(/Save my spot/);
    expect(
      await screen.findByText("Enrollment for this lunch has closed."),
    ).toBeTruthy();
  });

  it("checks for a table, reveals it, checks you in, and deals questions", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    let published = false;
    let seated = false;
    route("GET", "/api/me", () =>
      json(
        published ? status({ assignment }, "published") : status({}, "closed"),
      ),
    );
    route("POST", "/api/me/check-in", () => {
      seated = true;
      return json(
        status(
          { assignment, checked_in_at: "2026-11-10T19:31:00Z" },
          "published",
        ),
      );
    });
    await mount();
    expect(await screen.findByText("SIGN-UPS ARE CLOSED")).toBeTruthy();
    // Answers can't change once sign-ups close.
    expect(document.getElementById("ta-wait-edit")?.hidden).toBe(true);

    published = true;
    click(/Check for my table now/);
    expect(
      await screen.findByText("The wait is over, Ada. We found your table."),
    ).toBeTruthy();
    click(/Meet my table/);

    expect(
      await screen.findByRole("heading", { name: /Found for you/ }),
    ).toBeTruthy();
    expect(screen.getByText("THE BUILDERS' TABLE")).toBeTruthy();
    expect(screen.getByText(/Maya is building her first sales/)).toBeTruthy();
    expect(screen.getByText("2 open seats · saved for a walk-in")).toBeTruthy();
    expect(screen.getByText(/Founder · Acme/)).toBeTruthy();
    expect(byId("event-time").hidden).toBe(false);
    expect(byId("event-time-name").textContent).toBe(
      "CLOUD NATIVE HACKATHON / LUNCH",
    );
    expect(byId("event-time-hours").textContent).toBe("12:30–1:15");
    click(/I’m seated at Table 08/);

    expect(
      (await screen.findByRole("heading", { name: /You’re in/ })).textContent,
    ).toMatch(/You’re in,\s*Ada\./);
    expect(seated).toBe(true);
    // The place card: initials, then the role and company on their own lines.
    expect(byId("ticket-initials").textContent).toBe("AL");
    expect(byId("ticket-title").textContent).toBe("Engineer");
    expect(byId("ticket-company").textContent).toBe("Analytical Engines");
    expect(document.getElementById("ta-when")?.textContent).toBe(
      "12:30–1:15 · Hall B",
    );
    click(/Break the ice/);
    expect(
      screen.getByText("Ask Maya how she finds her first customers."),
    ).toBeTruthy();
    click(/Deal another question/);
    expect(
      screen.getByText("What did you ship before you felt ready?"),
    ).toBeTruthy();
  });

  it("lists everyone at the table, with the open seats", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    route("GET", "/api/me", () => json(status({ assignment }, "published")));
    await mount();
    await screen.findByRole("heading", { name: /Found for you/ });
    const roster = byId("roster");
    expect(roster.hasAttribute("open")).toBe(false);

    click(/See everyone/);
    expect(roster.hasAttribute("open")).toBe(true);
    expect(byId("roster-eyebrow").textContent).toBe(
      "TABLE 08 · THE BUILDERS' TABLE",
    );
    expect(byId("roster-sub").textContent).toBe("3 people · 2 open seats");
    const rows = [...roster.querySelectorAll("li")].map((li) => [
      // Your row shows the chair icon, so it has no letters.
      li.querySelector(".ta-roster-avatar")?.textContent?.trim(),
      li.querySelector("b")?.textContent,
      li.querySelector("small")?.textContent,
    ]);
    expect(rows).toEqual([
      ["", "Ada Lovelace", "You · Engineer · Analytical Engines"],
      ["M", "Maya", "Founder · Acme"],
      ["J", "Jordan", "Designer"],
      ["+", "2 open seats", "Saved for walk-ins"],
    ]);
    expect(byId("roster-foot").textContent).toBe("12:30–1:15 · Hall B");

    click("Close");
    expect(roster.hasAttribute("open")).toBe(false);
    expect(document.activeElement).toBe(byId("see-table"));
  });

  it("previews fictional matches and check-in without changing live data", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    route("GET", "/api/me", () => json(status()));
    await mount();
    await screen.findByRole("heading", { name: /is on its way/ });
    const checks = calls.filter((c) => c.key === "GET /api/me").length;

    click("Simulate matches");
    await screen.findByRole("heading", { name: /Found for you/ });
    expect(byId("preview-notice").hidden).toBe(false);
    expect(
      screen.getByText(/Demo preview with fictional tablemates/),
    ).toBeTruthy();
    expect(screen.getByText("THE DEMO TABLE")).toBeTruthy();
    expect(byId("tags").textContent).toContain("kubernetes");
    expect(byId("edit").hidden).toBe(true);
    vi.advanceTimersByTime(POLL_MS * 5);
    fireEvent(document, new Event("visibilitychange"));
    expect(calls.filter((c) => c.key === "GET /api/me").length).toBe(checks);

    click(/See everyone/);
    expect(byId("roster-list").textContent).toContain("Ada Lovelace");
    expect(byId("roster-list").textContent).toContain("Alex");
    click("Close");
    click(/I’m seated at Table 01/);
    await screen.findByRole("heading", { name: /You’re in/ });
    click(/Break the ice/);
    expect(
      screen.getByText(
        "What’s one idea from today’s talks you want to try back at work?",
      ),
    ).toBeTruthy();
    expect(calls.every((c) => c.key.startsWith("GET "))).toBe(true);
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBe("saved-token");

    click("Return to live waiting room");
    await waitFor(() =>
      expect(
        calls.filter((c) => c.key === "GET /api/me").length,
      ).toBeGreaterThan(checks),
    );
    expect(heading(/is on its way/)).toBeTruthy();
    expect(byId("preview-notice").hidden).toBe(true);
    expect(byId("wait-edit").hidden).toBe(false);
    expect(
      screen.getByRole("button", { name: "Simulate matches" }),
    ).toBeTruthy();
    const resumedChecks = calls.filter((c) => c.key === "GET /api/me").length;
    vi.advanceTimersByTime(POLL_MS);
    await waitFor(() =>
      expect(
        calls.filter((c) => c.key === "GET /api/me").length,
      ).toBeGreaterThan(resumedChecks),
    );
  });

  it("returns from the demo to a real assignment published in the meantime", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    let published = false;
    route("GET", "/api/me", () =>
      json(published ? status({ assignment }, "published") : status()),
    );
    route("POST", "/api/me/check-in", () =>
      json(
        status(
          { assignment, checked_in_at: "2026-11-10T19:31:00Z" },
          "published",
        ),
      ),
    );
    await mount();
    await screen.findByRole("heading", { name: /is on its way/ });
    click("Simulate matches");
    await screen.findByRole("heading", { name: /Found for you/ });
    published = true;
    click("Return to live waiting room");
    await screen.findByText("The wait is over, Ada. We found your table.");
    expect(
      screen.queryByRole("button", { name: "Simulate matches" }),
    ).toBeNull();
    click(/Meet my table/);
    await screen.findByText("THE BUILDERS' TABLE");
    expect(byId("preview-notice").hidden).toBe(true);
    click(/I’m seated at Table 08/);
    await screen.findByRole("heading", { name: /You’re in/ });
    expect(calls.filter((c) => c.key === "POST /api/me/check-in")).toHaveLength(
      1,
    );
  });

  it("forgets this device from the footer", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    route("GET", "/api/me", () => json(status()));
    await mount();
    await screen.findByRole("heading", { name: /is on its way/ });
    expect(byId("forget").hidden).toBe(false);

    click(/Forget me on this device/i);
    expect(heading(/First, a proper/)).toBeTruthy();
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBeNull();
    expect(
      (screen.getByLabelText(/First name/) as HTMLInputElement).value,
    ).toBe("");
    expect(byId("forget").hidden).toBe(true);
  });

  it("resumes an open enrollment and saves edits with PUT", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    route("GET", "/api/me", () => json(status()));
    route("PUT", "/api/me", () => json(status()));
    await mount();
    expect(
      await screen.findByRole("heading", { name: /is on its way/ }),
    ).toBeTruthy();

    fireEvent.click(document.getElementById("ta-wait-edit")!);
    expect((screen.getByLabelText(/Title/) as HTMLInputElement).value).toBe(
      "Engineer",
    );
    type(/Title/, "Staff engineer");
    click(/Next: your interests/);
    expect(
      (screen.getByRole("checkbox", { name: "Kubernetes" }) as HTMLInputElement)
        .checked,
    ).toBe(true);
    click(/Next: your goals/);
    click(/Save my changes/);

    await waitFor(() => expect(sent("PUT /api/me")).toBeDefined());
    expect(sent("PUT /api/me").role).toBe("Staff engineer");
    expect(sent("PUT /api/me").interests).toEqual(["Kubernetes", "climate"]);
    expect(calls.some((c) => c.key === "POST /api/enrollments")).toBe(false);
  });

  it("stops checking once the table is found", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    let published = false;
    route("GET", "/api/me", () =>
      json(published ? status({ assignment }, "published") : status()),
    );
    await mount();
    await screen.findByRole("heading", { name: /is on its way/ });
    published = true;
    vi.advanceTimersByTime(POLL_MS);
    expect(
      await screen.findByText("The wait is over, Ada. We found your table."),
    ).toBeTruthy();
    const checks = calls.filter((c) => c.key === "GET /api/me").length;
    vi.advanceTimersByTime(POLL_MS * 3);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(calls.filter((c) => c.key === "GET /api/me").length).toBe(checks);
  });

  it("shows the table when tables are published mid-edit", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    let published = false;
    route("GET", "/api/me", () =>
      json(published ? status({ assignment }, "published") : status()),
    );
    route("PUT", "/api/me", () => {
      published = true;
      return json({ detail: "Groups are already assigned." }, 409);
    });
    await mount();
    await screen.findByRole("heading", { name: /is on its way/ });
    fireEvent.click(document.getElementById("ta-wait-edit")!);
    click(/Next: your interests/);
    click(/Next: your goals/);
    click(/Save my changes/);
    expect(
      await screen.findByRole("heading", { name: /Found for you/ }),
    ).toBeTruthy();
  });

  it("shows the host's updates after sign-up", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    route("GET", "/api/me", () =>
      json(
        status({
          announcements: [
            {
              id: "a1",
              title: "Lunch moved",
              body: "We're in Hall C today.",
              sent_at: "2026-11-10T19:00:00Z",
            },
          ],
        }),
      ),
    );
    await mount();
    await screen.findByRole("heading", { name: /is on its way/ });
    expect(document.getElementById("ta-inbox")?.hidden).toBe(false);
    expect(screen.getByText("We're in Hall C today.")).toBeTruthy();
  });

  it("forgets a token the server no longer knows", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "stale-token");
    route("GET", "/api/me", () => json({ detail: "Unknown" }, 401));
    await mount();
    expect(
      await screen.findByText(/couldn't find your enrollment on this device/),
    ).toBeTruthy();
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBeNull();
  });

  it("explains when sign-ups are already closed", async () => {
    route("GET", "/api/conference", () =>
      json({ ...conference, status: "closed" }),
    );
    await mount();
    expect(
      screen.getByText(/Sign-ups for this lunch have closed/),
    ).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: /Next: your interests/,
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});
