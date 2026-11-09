import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { request, type ConferenceState } from "./api";

const conference: ConferenceState = {
  id: "demo",
  name: "Test conference",
  lunch_slot: "day-1-lunch",
  lunch_label: "Day 1 lunch",
  group_size: 4,
  attendees: [],
  plan: null,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fillEnrollmentForm() {
  fireEvent.change(screen.getByLabelText("Your name"), {
    target: { value: "Alex" },
  });
  fireEvent.change(screen.getByLabelText("What do you do?"), {
    target: { value: "Engineer" },
  });
  fireEvent.change(screen.getByLabelText(/What could you talk/), {
    target: { value: "Kubernetes, Python" },
  });
  fireEvent.change(screen.getByLabelText("What you can offer"), {
    target: { value: "Python APIs" },
  });
  fireEvent.change(screen.getByLabelText("What you're looking for"), {
    target: { value: "Kubernetes" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("enrollment", () => {
  it("allows selecting and deselecting multiple lunch goals and submits them", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(conference));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("A little about you.");
    fireEvent.click(screen.getByRole("button", { name: "I want to learn" }));
    fireEvent.click(
      screen.getByRole("button", {
        name: "I want to share something I've learned",
      }),
    );
    const connections = screen.getByRole("button", {
      name: "I just want to make connections",
    });
    fireEvent.click(connections);
    expect(connections.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(connections);
    expect(connections.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(connections);
    expect(screen.queryByRole("button", { name: "Hire" })).toBeNull();
    fillEnrollmentForm();
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "new-attendee" }, 201));
    fireEvent.click(
      screen.getByRole("button", { name: /Find my lunch people/ }),
    );

    await screen.findByText(/You're on the list!/);
    const enrollmentRequest = fetchMock.mock.calls.find(
      ([path]) => path === "/api/enrollments",
    )?.[1];
    expect(enrollmentRequest?.method).toBe("POST");
    const enrollmentBody: unknown = JSON.parse(String(enrollmentRequest?.body));
    expect(enrollmentBody).toEqual({
      name: "Alex",
      role: "Engineer",
      interests: ["Kubernetes", "Python"],
      goals: ["Learn", "Share something I've learned", "Meet connections"],
      can_help: "Python APIs",
      want_to_learn: "Kubernetes",
      lunch_slot: "day-1-lunch",
    });
  });

  it("requires at least one lunch goal before enrolling", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(conference));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("A little about you.");
    fillEnrollmentForm();
    fireEvent.click(
      screen.getByRole("button", { name: /Find my lunch people/ }),
    );
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Please select at least one lunch goal.",
    );
    expect(
      fetchMock.mock.calls.some(([path]) => path === "/api/enrollments"),
    ).toBe(false);
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
