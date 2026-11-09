export const goalOptions = [
  { value: "Learn", label: "I want to learn" },
  {
    value: "Share something I've learned",
    label: "I want to share something I've learned",
  },
  { value: "Meet connections", label: "I just want to make connections" },
] as const;

export type Goal = (typeof goalOptions)[number]["value"];

export interface Enrollment {
  name: string;
  role: string;
  interests: string[];
  goals: Goal[];
  can_help: string;
  want_to_learn: string;
  lunch_slot: string;
}

export interface Attendee extends Enrollment {
  id: string;
  conference_id: string;
}

export interface LunchGroup {
  id: string;
  members: Attendee[];
  reasons: { attendee_ids: [string, string]; text: string }[];
}

export interface MatchPlan {
  groups: LunchGroup[];
  attendee_count: number;
  generated_at: string;
}

export interface ConferenceState {
  id: string;
  name: string;
  lunch_slot: string;
  lunch_label: string;
  group_size: number;
  attendees: Attendee[];
  plan: MatchPlan | null;
}

export async function request<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok) {
    const data: unknown = response.headers
      .get("content-type")
      ?.includes("application/json")
      ? await response.json()
      : null;
    const detail =
      typeof data === "object" && data !== null && "detail" in data
        ? data.detail
        : null;
    throw new Error(
      typeof detail === "string"
        ? detail
        : response.status === 422
          ? "Please check your answers. Select at least one lunch goal and up to 8 topics (1–50 characters each)."
          : `The request failed (${response.status}). Please try again.`,
    );
  }
  return response.json() as Promise<T>;
}
