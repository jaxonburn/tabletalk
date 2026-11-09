import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import HostApp from "./HostApp";
import type { Attendee, HostState, MatchRun } from "./api";

function person(id: string, name: string, interests: string[]): Attendee {
  return {
    id,
    conference_id: "demo",
    name,
    role: "Engineer",
    interests,
    goals: ["Learn"],
    goal_answers: { Learn: `How does ${name} approach ${interests[0]}?` },
    focus_goal: "Learn",
    can_help: "Python",
    want_to_learn: interests[0],
    lunch_slot: "day-1-lunch",
  };
}

const priya = person("a", "Priya Nair", ["kubernetes"]);
const marcus = person("b", "Marcus Webb", ["kubernetes"]);
const aisha = person("c", "Aisha Bello", ["security"]);
const tom = person("d", "Tom Lindqvist", ["security"]);

const run: MatchRun = {
  id: "run-1",
  created_at: "2026-11-09T18:40:00Z",
  seed: 7,
  tables: [
    { number: 1, seats: 2 },
    { number: 2, seats: 2 },
  ],
  min_table_size: 2,
  plan: {
    attendee_count: 4,
    generated_at: "2026-11-09T18:40:00Z",
    run_id: "run-1",
    metrics: {
      attendee_count: 4,
      seated_count: 4,
      table_sizes: [2, 2],
      average_pair_affinity: 0.62,
      weakest_best_tablemate: 0.6,
      learn_coverage: 0.75,
      baseline_average_pair_affinity: 0.25,
      baseline_weakest_best_tablemate: 0.05,
    },
    groups: [
      {
        id: "table-1",
        table_number: 1,
        members: [priya, marcus],
        reasons: [{ attendee_ids: ["a", "b"], text: "Shared interests." }],
        summary: "Common ground in kubernetes.",
        starters: ["Marcus asks: how do you autoscale?"],
        member_notes: [
          {
            attendee_id: "a",
            summary: "You can help Marcus with autoscaling.",
            starters: ["Ask Marcus about flash sales."],
          },
        ],
        narrative_source: "template",
      },
      {
        id: "table-2",
        table_number: 2,
        members: [aisha, tom],
        reasons: [],
        summary: "Common ground in security.",
        starters: [],
        member_notes: [],
        narrative_source: "template",
      },
    ],
  },
};

function hostState(overrides: Partial<HostState> = {}): HostState {
  return {
    event: {
      tables: run.tables,
      enrollment_closes_at: "2026-11-09T18:40:00Z",
      min_table_size: 2,
    },
    status: "open",
    capacity: 4,
    seat_count: 4,
    attendees: [priya, marcus, aisha, tom],
    runs: [
      {
        id: "run-1",
        created_at: run.created_at,
        seed: 7,
        attendee_count: 4,
        table_count: 2,
        metrics: run.plan.metrics ?? null,
        published: false,
      },
    ],
    published_run_id: null,
    server_time: "2026-11-09T18:00:00Z",
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubApi(state: HostState) {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/host/state") return jsonResponse(state);
    if (path === "/api/host/runs/run-1") return jsonResponse(run);
    if (path === "/api/host/runs") return jsonResponse(run, 201);
    return jsonResponse({ detail: `Unexpected ${path}` }, 500);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("host view", () => {
  it("shows the latest run with metrics, tables, and every person", async () => {
    stubApi(hostState());
    render(<HostApp />);
    expect(await screen.findByText("Draft groups")).toBeTruthy();
    const metrics = screen.getByLabelText("Match quality");
    expect(within(metrics).getByText("4 / 4")).toBeTruthy();
    expect(within(metrics).getByText("62%")).toBeTruthy();
    expect(within(metrics).getByText("25% if seated at random")).toBeTruthy();
    expect(screen.getByText("Common ground in kubernetes.")).toBeTruthy();
    expect(
      screen.getByText("You can help Marcus with autoscaling."),
    ).toBeTruthy();

    fireEvent.change(screen.getByLabelText(/Search people/), {
      target: { value: "security" },
    });
    const people = screen.getByRole("heading", { name: "Every person" })
      .parentElement!.parentElement!;
    expect(within(people).queryByText("Priya Nair")).toBeNull();
    expect(within(people).getByText("Aisha Bello")).toBeTruthy();
  });

  it("asks for the host token when the API requires one", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ detail: "Enter the token." }, 401));
    vi.stubGlobal("fetch", fetchMock);
    render(<HostApp />);
    fireEvent.change(await screen.findByLabelText("Host token"), {
      target: { value: "lunch-host" },
    });
    fetchMock.mockImplementation(async () => jsonResponse(hostState()));
    fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([, init]) =>
            (init?.headers as Record<string, string> | undefined)?.[
              "X-Host-Token"
            ] === "lunch-host",
        ),
      ).toBe(true),
    );
    expect(sessionStorage.getItem("tabletalk-host-token")).toBe("lunch-host");
  });

  it("produces a draft run on request", async () => {
    const fetchMock = stubApi(hostState({ runs: [] }));
    render(<HostApp />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Produce matches" }),
    );
    expect(await screen.findByText(/Draft ready: 2 tables/)).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(
        ([path, init]) => path === "/api/host/runs" && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("blocks publishing a run that would leave a new attendee unseated", async () => {
    stubApi(
      hostState({
        attendees: [
          priya,
          marcus,
          aisha,
          tom,
          person("e", "Late Larry", ["go"]),
        ],
      }),
    );
    render(<HostApp />);
    expect(
      await screen.findByText(/1 enrolled person is not in this run/),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Publish this run" })
        .hasAttribute("disabled"),
    ).toBe(true);
  });

  it("blocks publishing a run made for an older table setup", async () => {
    stubApi(
      hostState({
        event: {
          tables: [{ number: 10, seats: 8 }],
          enrollment_closes_at: null,
          min_table_size: 2,
        },
      }),
    );
    render(<HostApp />);
    expect(
      await screen.findByText(/table setup changed after this run/),
    ).toBeTruthy();
    expect(
      screen
        .getByRole("button", { name: "Publish this run" })
        .hasAttribute("disabled"),
    ).toBe(true);
  });

  it("generates attendees offline when no AI provider is configured", async () => {
    const fetchMock = stubApi(hostState({ runs: [] }));
    fetchMock.mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/host/demo/attendees") {
        const body: unknown = JSON.parse(String(init?.body));
        expect(body).toEqual({
          source: "generated",
          count: 24,
          use_llm: false,
        });
        return jsonResponse({ created: 24, generator: "offline" });
      }
      return jsonResponse(hostState({ runs: [] }));
    });
    render(<HostApp />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Generate 24 attendees" }),
    );
    expect(
      await screen.findByText(/Generated 24 fictional attendees/),
    ).toBeTruthy();
    expect(screen.queryByLabelText(/Write table notes with AI/)).toBeNull();
  });

  it("offers AI options when a provider is ready and sends the choice", async () => {
    const ready = hostState({
      runs: [],
      llm: {
        provider: "anthropic",
        model: "claude-x",
        enabled: true,
        problem: null,
      },
    });
    const fetchMock = stubApi(ready);
    render(<HostApp />);
    const notesToggle = await screen.findByLabelText(
      "Write table notes with AI (claude-x)",
    );
    expect((notesToggle as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Produce matches" }));
    await screen.findByText(/Draft ready/);
    const runCall = fetchMock.mock.calls.find(
      ([path, init]) => path === "/api/host/runs" && init?.method === "POST",
    );
    expect(JSON.parse(String(runCall?.[1]?.body))).toEqual({
      use_llm: true,
      use_semantic: true,
    });
  });

  it("explains when AI is configured but unavailable", async () => {
    stubApi(
      hostState({
        llm: {
          provider: "openai",
          model: "",
          enabled: false,
          problem: "Set LLM_MODEL to the provider's model ID.",
        },
      }),
    );
    render(<HostApp />);
    expect(
      await screen.findByText(
        /AI is configured but unavailable: Set LLM_MODEL/,
      ),
    ).toBeTruthy();
  });

  it("saves automatic matching settings and shows what the watcher did", async () => {
    const cutoff = "2026-11-09T18:40:00Z";
    const fetchMock = stubApi(
      hostState({
        event: {
          tables: run.tables,
          enrollment_closes_at: cutoff,
          min_table_size: 2,
          auto_match: true,
          auto_publish: false,
        },
        auto: {
          cutoff,
          outcome: "done",
          run_id: "run-1",
          published: false,
          message:
            "Matched automatically at the cutoff. Review the draft, then publish.",
          at: cutoff,
        },
      }),
    );
    render(<HostApp />);
    expect(
      await screen.findByText(/Matched automatically at the cutoff/),
    ).toBeTruthy();
    const autoPublish = screen.getByLabelText(
      "Publish the automatic run right away",
    );
    fireEvent.click(autoPublish);
    fireEvent.click(screen.getByRole("button", { name: /Save setup/ }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([path]) => path === "/api/host/event"),
      ).toBe(true),
    );
    const saved = fetchMock.mock.calls.find(
      ([path]) => path === "/api/host/event",
    );
    expect(saved?.[1]?.method).toBe("PUT");
    expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({
      auto_match: true,
      auto_publish: true,
    });
  });

  it("asks the AI judge and shows scores next to random seating", async () => {
    const ready = hostState({
      llm: {
        provider: "openai",
        model: "gpt-x",
        enabled: true,
        problem: null,
        embedding_model: "text-embedding-3-small",
      },
    });
    const fetchMock = stubApi(ready);
    fetchMock.mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/host/state") return jsonResponse(ready);
      if (path === "/api/host/runs/run-1") return jsonResponse(run);
      if (path === "/api/host/runs/run-1/judge")
        return jsonResponse({
          model: "openai:gpt-x",
          prompt_version: "v2",
          tables: [
            {
              table_number: 1,
              score: 5,
              rationale: "Priya can answer Marcus.",
            },
          ],
          average: 4.5,
          baseline_average: 2.0,
          judged_at: "2026-11-09T18:40:00Z",
        });
      return jsonResponse({ detail: `Unexpected ${path}` }, 500);
    });
    render(<HostApp />);
    expect(
      await screen.findByLabelText(
        "Semantic matching (embeddings + topic tags)",
      ),
    ).toBeTruthy();
    fireEvent.click(
      await screen.findByRole("button", { name: "Ask the AI judge" }),
    );
    expect(await screen.findByText("AI judge: 4.5/5")).toBeTruthy();
    expect(
      screen.getByText(/2.0\/5 for the same people seated at random/),
    ).toBeTruthy();
    expect(screen.getByText("AI judge: Priya can answer Marcus.")).toBeTruthy();
  });
});
