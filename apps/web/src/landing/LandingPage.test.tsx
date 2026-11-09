import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ATTENDEE_PATH, ORGANIZER_PATH, pageForPath } from "../routes";
import LandingPage from "./LandingPage";

afterEach(cleanup);

describe("landing page", () => {
  it("leads to the attendee and organizer workflows", () => {
    render(<LandingPage />);
    expect(screen.getByRole("heading", { name: "TableTalk" })).toBeTruthy();
    expect(
      screen
        .getByRole("link", { name: /Attendee Workflow/ })
        .getAttribute("href"),
    ).toBe(ATTENDEE_PATH);
    expect(
      screen
        .getByRole("link", { name: /Organizer Workflow/ })
        .getAttribute("href"),
    ).toBe(ORGANIZER_PATH);
  });

  it("sets the page title while it's shown", () => {
    document.title = "Before";
    render(<LandingPage />);
    expect(document.title).toBe("TableTalk — Better conversations start here");
    cleanup();
    expect(document.title).toBe("Before");
  });
});

describe("routes", () => {
  it.each([
    ["/", "landing"],
    ["", "landing"],
    ["/somewhere-else", "landing"],
    ["/user-onboarding", "attendee"],
    ["/user-onboarding/", "attendee"],
    ["/organizer-workflow", "organizer"],
    ["/event-organizer", "organizer"],
    ["/host", "organizer"],
    ["/host/review", "host"],
    ["/classic", "classic"],
  ])("%s shows the %s page", (path, page) => {
    expect(pageForPath(path)).toBe(page);
  });
});
