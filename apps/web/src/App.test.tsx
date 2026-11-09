import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { ATTENDEE_TOKEN_KEY } from "./App";
import { request, type ConferenceState } from "./api";

const conference: ConferenceState = {
  id: "demo",
  name: "Test conference",
  lunch_slot: "day-1-lunch",
  lunch_label: "Day 1 lunch",
  group_size: 4,
  attendees: [],
  attendee_count: 0,
  plan: null,
  published: false,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
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

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("localStorage", storage);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const prompts = {
  Learn: { question: "What do you want to learn?", example_hint: "e.g. K8s" },
  "Share something I've learned": {
    question: "What could you share?",
    example_hint: "e.g. tracing",
  },
  "Meet connections": {
    question: "What would you enjoy discussing?",
    example_hint: "e.g. platforms",
  },
};
const openConference: ConferenceState = {
  ...conference,
  goal_prompts: prompts,
};

function fillBasics() {
  fireEvent.change(screen.getByLabelText("Your name"), {
    target: { value: "Alex Chen" },
  });
  fireEvent.change(screen.getByLabelText("What do you do?"), {
    target: { value: "Engineer" },
  });
  fireEvent.change(screen.getByLabelText(/What could you talk/), {
    target: { value: "Kubernetes, Python" },
  });
}

const enrolledAttendee = {
  id: "me-1",
  conference_id: "demo",
  name: "Alex Chen",
  role: "Engineer",
  interests: ["kubernetes"],
  goals: ["Learn" as const],
  focus_goal: "Learn" as const,
  goal_answers: { Learn: "How do you autoscale?" },
  lunch_slot: "day-1-lunch",
};

describe("enrollment", () => {
  it("asks one question per selected goal and submits the onboarding answers", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input) =>
      String(input) === "/api/me"
        ? jsonResponse({
            attendee: enrolledAttendee,
            status: "open",
            enrollment_closes_at: null,
            assignment: null,
          })
        : jsonResponse(openConference),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("A little about you.");
    fillBasics();
    fireEvent.click(screen.getByRole("button", { name: "I want to learn" }));
    const connections = screen.getByRole("button", {
      name: "I just want to make connections",
    });
    fireEvent.click(connections);
    expect(connections.getAttribute("aria-pressed")).toBe("true");
    fireEvent.change(
      screen.getByLabelText(/Learn: What do you want to learn/),
      {
        target: { value: "How do you autoscale?" },
      },
    );
    fireEvent.change(
      screen.getByLabelText(/Connect: What would you enjoy discussing/),
      { target: { value: "Platform teams" } },
    );
    fireEvent.click(screen.getByRole("radio", { name: "Connect" }));
    fetchMock.mockImplementationOnce(async () =>
      jsonResponse(
        { ...enrolledAttendee, attendee_token: "secret-token" },
        201,
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Submit me for lunch/ }),
    );

    await screen.findByText(/You're in for lunch, Alex/);
    const enrollmentRequest = fetchMock.mock.calls.find(
      ([path]) => path === "/api/enrollments",
    )?.[1];
    expect(JSON.parse(String(enrollmentRequest?.body))).toEqual({
      name: "Alex Chen",
      role: "Engineer",
      interests: ["Kubernetes", "Python"],
      lunch_slot: "day-1-lunch",
      goals: ["Learn", "Meet connections"],
      focus_goal: "Meet connections",
      goal_answers: {
        Learn: "How do you autoscale?",
        "Meet connections": "Platform teams",
      },
    });
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBe("secret-token");
    const meCall = fetchMock.mock.calls.find(([path]) => path === "/api/me");
    expect(
      (meCall?.[1]?.headers as Record<string, string>)["X-Attendee-Token"],
    ).toBe("secret-token");
  });

  it("requires a goal and an answer for each selected goal", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(openConference));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("A little about you.");
    fillBasics();
    fireEvent.click(
      screen.getByRole("button", { name: /Submit me for lunch/ }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Please select at least one lunch goal.",
    );
    fireEvent.click(screen.getByRole("button", { name: "I want to learn" }));
    fireEvent.submit(
      screen
        .getByRole("button", { name: /Submit me for lunch/ })
        .closest("form")!,
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Please answer: Learn.",
    );
    expect(
      fetchMock.mock.calls.some(([path]) => path === "/api/enrollments"),
    ).toBe(false);
  });

  it("shows a returning attendee their table after publishing", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(async (input) =>
        String(input) === "/api/me"
          ? jsonResponse({
              attendee: enrolledAttendee,
              status: "published",
              enrollment_closes_at: null,
              assignment: {
                table_number: 7,
                tablemates: [
                  { first_name: "Sam", role: "SRE", interests: ["tracing"] },
                ],
                summary: "Sam can help you explore autoscaling.",
                starters: ["Ask Sam how they approached autoscaling."],
                table_summary: "Common ground in kubernetes.",
                table_starters: [],
                narrative_source: "template",
              },
            })
          : jsonResponse(openConference),
      ),
    );
    render(<App />);
    expect(await screen.findByText("You're at Table 7.")).toBeTruthy();
    expect(
      screen.getByText("Sam can help you explore autoscaling."),
    ).toBeTruthy();
    expect(
      screen.getByText("Ask Sam how they approached autoscaling."),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Your name")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Forget this device/ }));
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBeNull();
  });

  it("never brings back a forgotten attendee from an in-flight refresh", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "saved-token");
    const seated = {
      attendee: enrolledAttendee,
      status: "published",
      enrollment_closes_at: null,
      assignment: {
        table_number: 7,
        tablemates: [],
        summary: "Your table.",
        starters: [],
        table_summary: "",
        table_starters: [],
        narrative_source: "template",
      },
    };
    let release: (response: Response) => void = () => undefined;
    let meCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockImplementation(async (input) => {
        if (String(input) !== "/api/me") return jsonResponse(openConference);
        meCalls += 1;
        if (meCalls === 1) return jsonResponse(seated);
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }),
    );
    render(<App />);
    await screen.findByText("You're at Table 7.");
    fireEvent.click(
      screen.getByRole("button", { name: "Refresh the guest list" }),
    );
    await waitFor(() => expect(meCalls).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: /Forget this device/ }));
    expect(await screen.findByLabelText("Your name")).toBeTruthy();
    release(jsonResponse(seated));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByText("You're at Table 7.")).toBeNull();
    expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBeNull();
  });

  it("forgets a token the server no longer recognizes", async () => {
    storage.setItem(ATTENDEE_TOKEN_KEY, "stale-token");
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockImplementation(async (input) =>
          String(input) === "/api/me"
            ? jsonResponse({ detail: "Unknown" }, 401)
            : jsonResponse(openConference),
        ),
    );
    render(<App />);
    expect(await screen.findByText("A little about you.")).toBeTruthy();
    await waitFor(() => expect(storage.getItem(ATTENDEE_TOKEN_KEY)).toBeNull());
  });

  it("shows loading failures and allows reconnecting", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ detail: "Storage unavailable" }, 503));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Storage unavailable",
    );
    fetchMock.mockResolvedValue(jsonResponse(conference));
    fireEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    await screen.findByText("A little about you.");
  });
});

describe("API errors", () => {
  it("handles non-JSON proxy failures with a readable error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Bad gateway", { status: 502 })),
    );
    await expect(request("/api/conference")).rejects.toThrow(
      "The request failed (502)",
    );
  });
});

describe("public conference privacy", () => {
  it("shows aggregate counts without publishing attendees' tables", async () => {
    const published: ConferenceState = {
      ...conference,
      attendee_count: 2,
      published: true,
      status: "published",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => jsonResponse(published)),
    );
    render(<App />);
    expect(await screen.findByText("The tables are ready.")).toBeTruthy();
    expect(screen.getByText("2", { selector: "strong" })).toBeTruthy();
    expect(screen.queryByText("Table 7")).toBeNull();
    expect(screen.queryByText("Priya Nair")).toBeNull();
  });
});
