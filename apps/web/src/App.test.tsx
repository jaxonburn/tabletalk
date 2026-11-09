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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("enrollment", () => {
  it("limits goal selection to three and submits the enrollment contract", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockImplementation(async () => jsonResponse(conference));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("A little about you.");
    fireEvent.click(screen.getByRole("button", { name: "Hire" }));
    fireEvent.click(screen.getByRole("button", { name: "Learn" }));
    fireEvent.click(screen.getByRole("button", { name: "Invest" }));
    expect(
      screen
        .getByRole("button", { name: "Meet peers" })
        .hasAttribute("disabled"),
    ).toBe(true);

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
      goals: ["Hire", "Learn", "Invest"],
      can_help: "Python APIs",
      want_to_learn: "Kubernetes",
      lunch_slot: "day-1-lunch",
    });
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
