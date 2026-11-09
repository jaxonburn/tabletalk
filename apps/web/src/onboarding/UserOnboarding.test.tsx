import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UserOnboarding from "./UserOnboarding";

const learnQuestion =
  "What questions do you have about the topics you want to learn?";
const shareQuestion = "What would you enjoy helping someone with?";

function type(label: RegExp | string, value: string) {
  fireEvent.input(screen.getByLabelText(label), { target: { value } });
}

function click(name: RegExp | string, role = "button") {
  fireEvent.click(screen.getByRole(role, { name }));
}

function heading(name: RegExp) {
  return screen.getByRole("heading", { name });
}

function completeAboutYou() {
  type(/First name/, "Ada");
  type(/Last name/, "Lovelace");
  type(/Title/, "Engineer");
  type(/Company/, "Analytical Engines");
  click(/Next: your interests/);
}

beforeEach(() => {
  // Reduced motion skips the template's animations and the matching delay.
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches: true } as MediaQueryList),
  );
  render(<UserOnboarding />);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("user onboarding", () => {
  it("asks for a name before moving on to interests", () => {
    expect(heading(/First, a proper/)).toBeTruthy();
    click(/Next: your interests/);
    expect(screen.getByText("Please add your first name.")).toBeTruthy();
    expect(screen.getByText("Please add your last name.")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /What gets you/ })).toBeNull();

    completeAboutYou();
    expect(document.activeElement).toBe(heading(/What gets you/));
  });

  it("submits only the selected goals and their answers", () => {
    completeAboutYou();
    click("Startups", "checkbox");
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
    const learnAnswer = screen.getByLabelText(
      learnQuestion,
    ) as HTMLTextAreaElement;
    expect(learnAnswer.disabled).toBe(true);
    expect(learnAnswer.value).toBe("How do teams find their first customers?");
    click(/Save my spot/);

    expect(heading(/Good company/)).toBeTruthy();
    expect(window.tabletalkPrototype?.getLastSubmission()).toEqual({
      name: "Ada Lovelace",
      role: "Engineer",
      company: "Analytical Engines",
      interests: ["Startups", "Climate"],
      selected_goal_ids: ["share"],
      focus_goal_id: "share",
      goal_answers: { share: "Sales and early growth." },
    });
  });

  it("reveals a sample table, checks you in, and deals a question", async () => {
    completeAboutYou();
    click(/Next: your goals/);
    click("Select Share my expertise and make it your focus");
    type(shareQuestion, "Outbound sales.");
    click(/Save my spot/);

    click(/Simulate the 12:15 match/);
    expect(
      screen.getByText("The wait is over, Ada. We found your table."),
    ).toBeTruthy();
    click(/Meet my table/);

    expect(
      await screen.findByRole("heading", { name: /Found for you/ }),
    ).toBeTruthy();
    expect(screen.getByText(/Maya is building her first sales/)).toBeTruthy();
    click(/I’m seated at Table 08/);

    expect(heading(/You’re in/).textContent).toMatch(/You’re in,\s*Ada\./);
    expect(screen.getByText("Engineer · Analytical Engines")).toBeTruthy();
    expect(window.tabletalkPrototype?.getCheckIn()?.table).toBe("08");
    click(/Break the ice/);
    expect(
      screen.getByText(
        "What’s a tiny frustration you’d happily build an entire company to fix?",
      ),
    ).toBeTruthy();
  });

  it("keeps the edit screen open when leaving during a pending match", () => {
    vi.useFakeTimers();
    completeAboutYou();
    click(/Next: your goals/);
    click("Select Share my expertise and make it your focus");
    type(shareQuestion, "Outbound sales.");
    click(/Save my spot/);
    click(/Simulate the 12:15 match/);
    click(/Meet my table/);

    click("Menu");
    click(/Edit my details/);
    vi.runOnlyPendingTimers();

    expect(heading(/First, a proper/)).toBeTruthy();
  });

  it("removes the development hook when the page unmounts", () => {
    expect(window.tabletalkPrototype).toBeDefined();
    cleanup();
    expect(window.tabletalkPrototype).toBeUndefined();
  });
});
