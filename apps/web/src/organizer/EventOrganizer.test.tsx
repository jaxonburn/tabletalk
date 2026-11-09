import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EventOrganizer from "./EventOrganizer";
import { buildSeatingPlan, qrMatrix } from "./organizer";

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

beforeEach(() => {
  // Reduced motion skips the template's animations and the matching delay.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: true } as MediaQueryList),
  );
  render(<EventOrganizer />);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("event organizer", () => {
  it("opens on the sample event's overview", () => {
    expect(
      screen.getByRole("heading", { name: "Utah Builders Summit" }),
    ).toBeTruthy();
    expect(byId("breadcrumb").textContent).toBe(
      "UTAH BUILDERS SUMMIT / OVERVIEW",
    );
    expect(byId("ready-summary").textContent).toBe(
      "30 ready to match · 4 still completing their profile",
    );
    expect(byId("plan-tables").textContent).toBe("6 tables · 36 seats");
    expect(byId("qr").querySelector("svg")).toBeTruthy();
  });

  it("edits the room and checks it before saving", () => {
    click("Tables");
    expect(
      screen.getByRole("heading", { name: "Tables and cutoff." }),
    ).toBeTruthy();
    expect(byId("room-save-state").textContent).toBe("Saved setup");

    type("Tables", "3");
    type("Seats each", "4");
    click("Use this layout");
    expect(byId("room-total").textContent).toBe("3 tables · 12 seats");
    expect(byId("room-capacity-note").textContent).toBe(
      "12 seats for 30 ready attendees. Add 18 seats before matching.",
    );
    expect(byId("room-save-state").textContent).toBe("Unsaved changes");

    fireEvent.input(byId("room-number-1"), { target: { value: "1" } });
    click(/Save setup/);
    expect(byId("room-error").textContent).toBe(
      "Each table needs a unique number. Table 1 is listed twice.",
    );
    expect(byId("room-number-1").getAttribute("aria-invalid")).toBe("true");
  });

  it("creates a seating plan, publishes it, and records an update", async () => {
    click("Matching");
    click("Run now");
    click(/Create seating plan/);

    expect(await screen.findByText("READY TO REVIEW")).toBeTruthy();
    expect(byId("plan-grid").querySelectorAll("details")).toHaveLength(6);
    expect(byId("plan-summary").textContent).toBe(
      "30 attendees · 6 active tables · 4 incomplete profiles excluded",
    );

    click(/Publish table assignments/);
    expect(byId("top-status").textContent).toBe("Tables published");
    expect(byId("publish").textContent).toBe("Table assignments published ✓");

    // Published tables are locked.
    click("Tables");
    expect(byId("room-lock").hidden).toBe(false);
    expect((byId("room-controls") as HTMLFieldSetElement).disabled).toBe(true);

    click("Updates");
    fireEvent.change(screen.getByLabelText("Start with a message"), {
      target: { value: "ready" },
    });
    fireEvent.change(screen.getByLabelText("Who should receive it?"), {
      target: { value: "matched" },
    });
    expect(byId("preview-title").textContent).toBe("Your table is ready.");
    expect(byId("preview-audience").textContent).toBe("30 RECIPIENTS");
    click(/Review update/);
    expect(byId("review-detail").textContent).toBe(
      "30 attendees · in-app inbox · Send now",
    );
    click("Send update");
    expect(byId("history-list").textContent).toBe(
      "Your table is ready.Simulated send · 30 recipients",
    );
  });

  it("creates a new event and sends you to its Tables setup", () => {
    click("＋ Create event");
    expect(
      screen.getByRole("heading", { name: "Create your event." }),
    ).toBeTruthy();
    click("Create event →");
    expect(byId("event-error").textContent).toBe(
      "Add the event name, date, lunch times, and location.",
    );

    type("Event name", "Design Days");
    type("Where should people go?", "Atrium");
    click("Create event →");

    expect(
      screen.getByRole("heading", { name: "Tables and cutoff." }),
    ).toBeTruthy();
    expect(byId("breadcrumb").textContent).toBe("DESIGN DAYS / TABLES");
    expect(byId("room-save-state").textContent).toBe("Setup needed");
    expect((byId("event-select") as HTMLSelectElement).value).toBe("evt-2");
    // The cutoff defaults to 15 minutes before lunch starts.
    expect((byId("cutoff") as HTMLInputElement).value).toBe("2026-11-20T12:15");
  });

  it("removes the development hook when the page unmounts", () => {
    expect(window.tabletalkAdminDemo).toBeDefined();
    cleanup();
    expect(window.tabletalkAdminDemo).toBeUndefined();
  });
});

describe("organizer helpers", () => {
  it("fills every table to the minimum, then spreads the rest", () => {
    const layout = [
      { number: 1, seats: 4 },
      { number: 2, seats: 6 },
      { number: 3, seats: 4 },
    ];
    const result = buildSeatingPlan(layout, 9, 2);
    expect("plan" in result && result.plan.map((t) => t.people.length)).toEqual(
      [3, 3, 3],
    );
    expect(buildSeatingPlan(layout, 15, 2)).toEqual({
      error:
        "There are 15 ready attendees but only 14 seats. Add seats in Tables before matching.",
    });
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
