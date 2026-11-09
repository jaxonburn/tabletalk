import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  Announcement,
  Attendee,
  EventConfig,
  HostState,
  MatchRun,
} from "../api";
import EventOrganizer from "./EventOrganizer";
import { localToUtc, qrMatrix, utcToLocal } from "./organizer";

function click(name: RegExp | string) {
  fireEvent.click(screen.getByRole("button", { name }));
}

function type(label: RegExp | string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

function byId(id: string) {
  const node = document.getElementById(`ad-${id}`);
  if (!node) throw new Error(`Missing #ad-${id}`);
  return node;
}

function json(body: unknown, code = 200) {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: { "content-type": "application/json" },
  });
}

function person(i: number): Attendee {
  return {
    id: `p${i}`,
    conference_id: "c",
    name: `Person ${i}`,
    role: "Engineer",
    interests: ["kubernetes"],
    goals: ["Learn"],
    lunch_slot: "day-1-lunch",
  };
}

/** An in-memory host API, enough for the console's flows. */
class FakeHost {
  event: EventConfig = {
    tables: [
      { number: 1, seats: 4 },
      { number: 2, seats: 4 },
    ],
    enrollment_closes_at: "2026-11-20T19:15:00Z",
    min_table_size: 2,
    auto_match: false,
    auto_publish: false,
    lunch_time: "12:30 PM–1:15 PM",
    location: "Hall B",
    name: "KubeCon Lunch",
    date: "2026-11-20",
    timezone: "America/Denver",
    lunch_start: "12:30",
    lunch_end: "13:15",
  };
  attendees: Attendee[] = [1, 2, 3, 4, 5].map(person);
  runs: MatchRun[] = [];
  published: string | null = null;
  announcements: Announcement[] = [];
  token = "";
  calls: { key: string; body?: unknown; headers?: HeadersInit }[] = [];

  state(): HostState {
    return {
      event: this.event,
      status: this.published ? "published" : "open",
      capacity: 8,
      seat_count: 8,
      attendees: this.attendees,
      runs: this.runs.map((r) => ({
        id: r.id,
        created_at: r.created_at,
        seed: r.seed,
        attendee_count: r.plan.attendee_count,
        table_count: r.plan.groups.length,
        metrics: null,
        published: r.id === this.published,
      })),
      published_run_id: this.published,
      server_time: "2026-11-20T18:00:00Z",
      announcements: this.announcements,
    };
  }

  handle(path: string, init?: RequestInit): Response {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    const key = `${method} ${path}`;
    this.calls.push({ key, body, headers: init?.headers });
    const headers = (init?.headers ?? {}) as Record<string, string>;
    if (this.token && headers["X-Host-Token"] !== this.token) {
      return json({ detail: "Host token required." }, 401);
    }
    if (key === "GET /api/host/state") return json(this.state());
    if (key === "PUT /api/host/event") {
      this.event = body as EventConfig;
      return json(this.event);
    }
    if (key === "POST /api/host/runs") {
      const id = `run-${this.runs.length + 1}`;
      const run: MatchRun = {
        id,
        created_at: "2026-11-20T18:30:00Z",
        seed: 1,
        tables: this.event.tables,
        min_table_size: this.event.min_table_size,
        plan: {
          run_id: id,
          attendee_count: this.attendees.length,
          generated_at: "2026-11-20T18:30:00Z",
          groups: [
            {
              id: "table-1",
              table_number: 1,
              name: "The Kubernetes table",
              members: this.attendees.slice(0, 3),
              reasons: [],
            },
            {
              id: "table-2",
              table_number: 2,
              name: "The Observability table",
              members: this.attendees.slice(3),
              reasons: [],
            },
          ],
        },
      };
      this.runs.unshift(run);
      return json(run, 201);
    }
    const runMatch = path.match(/^\/api\/host\/runs\/([^/]+)(\/publish)?$/);
    if (runMatch) {
      const run = this.runs.find((r) => r.id === runMatch[1]);
      if (!run) return json({ detail: "Not found" }, 404);
      if (method === "POST") {
        this.published = run.id;
        return json(run.plan);
      }
      return json(run);
    }
    if (key === "DELETE /api/host/runs") {
      const before = this.runs.length;
      this.runs = this.runs.filter((r) => r.id === this.published);
      return json({ deleted: before - this.runs.length });
    }
    if (key === "POST /api/host/announcements") {
      const sent: Announcement = {
        ...body,
        id: `a${this.announcements.length + 1}`,
        created_at: "2026-11-20T18:40:00Z",
        recipients: this.attendees.length,
      };
      this.announcements.unshift(sent);
      return json(sent, 201);
    }
    if (key === "POST /api/host/demo/attendees") {
      this.attendees = [...this.attendees, person(6), person(7)];
      return json({ created: 2, generator: body.source });
    }
    return json({ detail: `No route for ${key}` }, 404);
  }
}

let server: FakeHost;

async function mount() {
  render(<EventOrganizer />);
  await waitFor(() =>
    expect(byId("overview-title").textContent).toBe("KubeCon Lunch"),
  );
}

beforeEach(() => {
  // Reduced motion skips the template's animations.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: true } as MediaQueryList),
  );
  sessionStorage.clear();
  server = new FakeHost();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) =>
      server.handle(path, init),
    ),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("organizer console", () => {
  it("opens on the server's event", async () => {
    await mount();
    expect(byId("breadcrumb").textContent).toBe("KUBECON LUNCH / OVERVIEW");
    expect(byId("registered").textContent).toBe("5");
    expect(byId("plan-tables").textContent).toBe("2 tables · 8 seats");
    expect(byId("create").hidden).toBe(true);
    expect((byId("signup-url") as HTMLInputElement).value).toBe(
      `${window.location.origin}/`,
    );
    expect(byId("qr").querySelector("svg")).toBeTruthy();
  });

  it("saves the room with the cutoff converted from the event's time zone", async () => {
    await mount();
    click("Tables");
    expect(byId("room-save-state").textContent).toBe("Saved setup");
    // 12:15 in Denver is 19:15 UTC in November.
    expect((byId("cutoff") as HTMLInputElement).value).toBe("2026-11-20T12:15");
    type("Tables", "3");
    type("Seats each", "4");
    click("Use this layout");
    fireEvent.input(byId("cutoff"), { target: { value: "2026-11-20T12:00" } });
    click(/Save setup/);
    await waitFor(() =>
      expect(byId("toast").textContent).toMatch(/Room setup saved/),
    );
    expect(server.event.tables).toHaveLength(3);
    expect(server.event.enrollment_closes_at).toBe("2026-11-20T19:00:00.000Z");
    expect(server.event.name).toBe("KubeCon Lunch");
  });

  it("checks the room before saving", async () => {
    await mount();
    click("Tables");
    fireEvent.input(byId("room-number-1"), { target: { value: "1" } });
    click(/Save setup/);
    expect(byId("room-error").textContent).toBe(
      "Each table needs a unique number. Table 1 is listed twice.",
    );
    expect(server.calls.some((c) => c.key === "PUT /api/host/event")).toBe(
      false,
    );
  });

  it("runs matching, publishes, and sends an update", async () => {
    await mount();
    click("Matching");
    click("Run now");
    click(/Create seating plan/);
    expect(await screen.findByText("READY TO REVIEW")).toBeTruthy();
    expect(byId("plan-grid").querySelectorAll("details")).toHaveLength(2);
    expect(byId("plan-grid").textContent).toContain("The Kubernetes table");
    expect(byId("plan-summary").textContent).toBe(
      "5 attendees · 2 active tables",
    );

    click(/Publish table assignments/);
    await waitFor(() =>
      expect(byId("top-status").textContent).toBe("Tables published"),
    );
    expect(server.published).toBe("run-1");

    click("Tables");
    expect((byId("room-controls") as HTMLFieldSetElement).disabled).toBe(true);

    click("Updates");
    fireEvent.change(screen.getByLabelText("Start with a message"), {
      target: { value: "ready" },
    });
    fireEvent.change(screen.getByLabelText("Who should receive it?"), {
      target: { value: "matched" },
    });
    expect(byId("preview-audience").textContent).toBe("5 RECIPIENTS");
    click(/Review update/);
    click("Send update");
    await waitFor(() =>
      expect(byId("history-list").textContent).toBe(
        "Your table is ready.Delivered to the inbox · 5 recipients",
      ),
    );
    const sent = server.calls.find(
      (c) => c.key === "POST /api/host/announcements",
    );
    expect(sent?.body).toMatchObject({ audience: "matched", send_at: null });
  });

  it("discards a draft so the room can change", async () => {
    await mount();
    click("Matching");
    click("Run now");
    click(/Create seating plan/);
    await screen.findByText("READY TO REVIEW");
    click("Tables");
    expect((byId("room-controls") as HTMLFieldSetElement).disabled).toBe(true);
    click(/Discard/);
    await waitFor(() =>
      expect((byId("room-controls") as HTMLFieldSetElement).disabled).toBe(
        false,
      ),
    );
    expect(server.runs).toHaveLength(0);
  });

  it("edits the event details", async () => {
    await mount();
    click("Edit event");
    type("Event name", "Platform Day");
    click("Save changes →");
    await waitFor(() =>
      expect(byId("overview-title").textContent).toBe("Platform Day"),
    );
    expect(server.event.lunch_time).toBe("12:30 PM–1:15 PM");
  });

  it("keeps the cutoff when only the name changes, even with no saved date", async () => {
    server.event = { ...server.event, date: null };
    await mount();
    expect(byId("plan-date").textContent).toBe("Nov 20, 2026");
    click("Edit event");
    type("Event name", "Renamed");
    click("Save changes →");
    await waitFor(() => expect(server.event.name).toBe("Renamed"));
    expect(server.event.enrollment_closes_at).toBe("2026-11-20T19:15:00Z");
  });

  it("refuses event changes that would move the cutoff into the past", async () => {
    await mount();
    click("Edit event");
    type("Event date", "2026-01-15");
    click("Save changes →");
    expect(byId("event-error").textContent).toMatch(/already passed/);
    expect(server.calls.some((c) => c.key === "PUT /api/host/event")).toBe(
      false,
    );
  });

  it("adds demo attendees from the chosen source", async () => {
    await mount();
    fireEvent.change(byId("sample-source"), {
      target: { value: "generated" },
    });
    click("Add demo attendees");
    await waitFor(() => expect(byId("registered").textContent).toBe("7"));
    const call = server.calls.find(
      (c) => c.key === "POST /api/host/demo/attendees",
    );
    expect(call?.body).toEqual({ source: "generated", count: 24 });
  });

  it("asks for the host token when the server requires one", async () => {
    server.token = "lunch-host";
    render(<EventOrganizer />);
    await waitFor(() => expect(byId("token-form").hidden).toBe(false));
    type(/host token/, "lunch-host");
    fireEvent.submit(byId("token-form"));
    await waitFor(() =>
      expect(byId("overview-title").textContent).toBe("KubeCon Lunch"),
    );
    expect(sessionStorage.getItem("tabletalk-host-token")).toBe("lunch-host");
  });

  it("removes the development hook when the page unmounts", async () => {
    await mount();
    expect(window.tabletalkAdminDemo).toBeDefined();
    cleanup();
    expect(window.tabletalkAdminDemo).toBeUndefined();
  });
});

describe("organizer helpers", () => {
  it("converts event-local times to UTC and back, across time zones", () => {
    expect(localToUtc("2026-11-20T12:15", "America/Denver")).toBe(
      "2026-11-20T19:15:00.000Z",
    );
    expect(localToUtc("2026-07-01T12:00", "America/New_York")).toBe(
      "2026-07-01T16:00:00.000Z",
    );
    expect(utcToLocal("2026-11-20T19:15:00Z", "America/Denver")).toBe(
      "2026-11-20T12:15",
    );
  });

  it("draws a version 4 QR code with its three finder patterns", () => {
    const m = qrMatrix("https://tabletalk.example/e/evt-summit");
    expect(m).toHaveLength(33);
    for (const [x, y] of [
      [0, 0],
      [26, 0],
      [0, 26],
    ]) {
      expect(m[y][x]).toBe(true);
      expect(m[y + 1][x + 1]).toBe(false);
      expect(m[y + 3][x + 3]).toBe(true);
    }
  });
});
