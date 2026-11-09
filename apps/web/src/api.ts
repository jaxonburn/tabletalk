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
  focus_goal?: Goal | null;
  goal_answers?: Partial<Record<Goal, string>>;
  can_help?: string;
  want_to_learn?: string;
  lunch_slot: string;
}

export interface Attendee extends Enrollment {
  id: string;
  conference_id: string;
  topic_tags?: string[];
}

export interface EnrolledAttendee extends Attendee {
  attendee_token: string;
}

export interface GoalPrompt {
  question: string;
  example_hint: string;
}

export interface Tablemate {
  first_name: string;
  role: string;
  interests: string[];
}

export interface MyAssignment {
  table_number: number | null;
  tablemates: Tablemate[];
  summary: string;
  starters: string[];
  table_summary: string;
  table_starters: string[];
  narrative_source: "template" | "llm";
}

export interface MyStatus {
  attendee: Attendee;
  status: EnrollmentStatus;
  enrollment_closes_at: string | null;
  assignment: MyAssignment | null;
}

export interface MemberNote {
  attendee_id: string;
  summary: string;
  starters: string[];
}

export interface LunchGroup {
  id: string;
  table_number?: number | null;
  members: Attendee[];
  reasons: { attendee_ids: [string, string]; text: string }[];
  summary?: string;
  starters?: string[];
  member_notes?: MemberNote[];
  narrative_source?: "template" | "llm";
}

export interface MatchMetrics {
  attendee_count: number;
  seated_count: number;
  table_sizes: number[];
  average_pair_affinity: number;
  weakest_best_tablemate: number;
  learn_coverage: number | null;
  baseline_average_pair_affinity: number;
  baseline_weakest_best_tablemate: number;
}

export interface MatchPlan {
  groups: LunchGroup[];
  attendee_count: number;
  generated_at: string;
  seed?: number | null;
  metrics?: MatchMetrics | null;
  run_id?: string | null;
  narrative_provider?: string | null;
  prompt_version?: string | null;
  retrieval?: string | null;
}

export type EnrollmentStatus = "open" | "closed" | "published";

export interface ConferenceState {
  id: string;
  name: string;
  lunch_slot: string;
  lunch_label: string;
  group_size: number;
  attendees: Attendee[];
  plan: MatchPlan | null;
  status?: EnrollmentStatus;
  enrollment_closes_at?: string | null;
  capacity?: number;
  goal_prompts?: Partial<Record<Goal, GoalPrompt>>;
}

export interface TableSpec {
  number: number;
  seats: number;
}

export interface EventConfig {
  tables: TableSpec[];
  enrollment_closes_at: string | null;
  min_table_size: number;
  auto_match?: boolean;
  auto_publish?: boolean;
}

export interface AutoMatchState {
  cutoff: string;
  outcome: "done" | "failed";
  run_id: string | null;
  published: boolean;
  message: string;
  at: string;
}

export interface RunSummary {
  id: string;
  created_at: string;
  seed: number;
  attendee_count: number;
  table_count: number;
  metrics: MatchMetrics | null;
  published: boolean;
  narrative_provider?: string | null;
  retrieval?: string | null;
  judge_average?: number | null;
  judge_baseline_average?: number | null;
}

export interface TableVerdict {
  table_number: number | null;
  score: number;
  rationale: string;
}

export interface Judgement {
  model: string;
  prompt_version: string;
  tables: TableVerdict[];
  average: number | null;
  baseline_average: number | null;
  judged_at: string;
}

export interface MatchRun {
  id: string;
  created_at: string;
  seed: number;
  tables: TableSpec[];
  min_table_size: number;
  plan: MatchPlan;
  judgement?: Judgement | null;
}

export interface LLMStatus {
  provider: string;
  model: string;
  enabled: boolean;
  problem: string | null;
  embedding_model?: string | null;
}

export interface DemoResult {
  created: number;
  generator: string;
}

export interface HostState {
  event: EventConfig;
  status: EnrollmentStatus;
  capacity: number;
  seat_count: number;
  attendees: Attendee[];
  runs: RunSummary[];
  published_run_id: string | null;
  server_time: string;
  llm?: LLMStatus | null;
  auto?: AutoMatchState | null;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT";
  hostToken?: string;
  attendeeToken?: string;
  invalidMessage?: string;
}

const ENROLLMENT_INVALID =
  "Please check your answers: pick at least one lunch goal, answer each one (up to 500 characters), and list up to 8 topics (1–50 characters each).";

export async function request<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (options.hostToken) headers["X-Host-Token"] = options.hostToken;
  if (options.attendeeToken)
    headers["X-Attendee-Token"] = options.attendeeToken;
  const response = await fetch(path, {
    method: options.method ?? (body === undefined ? "GET" : "POST"),
    ...(Object.keys(headers).length ? { headers } : {}),
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
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
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : response.status === 422
          ? (options.invalidMessage ?? ENROLLMENT_INVALID)
          : `The request failed (${response.status}). Please try again.`,
      response.status,
    );
  }
  return response.json() as Promise<T>;
}
