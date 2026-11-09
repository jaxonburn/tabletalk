from datetime import datetime
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

CONFERENCE_ID = "cloud-native-hackathon"
LUNCH_SLOT = "day-1-lunch"
GROUP_SIZE = 4
MAX_ATTENDEES = 100

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
OptionalShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
OptionalAnswer = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Goal = Literal["Learn", "Share something I've learned", "Meet connections"]
# Goals stored by earlier versions of the enrollment form. Retired goals were all
# networking-oriented, so they migrate to "Meet connections" when profiles are read.
LEGACY_GOALS: dict[str, Goal] = {
    "Meet peers": "Meet connections",
    "Find a cofounder": "Meet connections",
    "Hire": "Meet connections",
    "Get hired": "Meet connections",
    "Sell / partner": "Meet connections",
    "Invest": "Meet connections",
}


class Enrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ShortText
    role: ShortText
    interests: list[Topic] = Field(min_length=1, max_length=8)
    goals: list[Goal] = Field(min_length=1, max_length=3)
    focus_goal: Goal | None = None
    goal_answers: dict[Goal, Answer] = Field(default_factory=dict)
    # Short "what I can offer" / "what I'm looking for" topics. The Learn/Share/Connect
    # onboarding answers each selected goal instead, so these are optional there.
    company: OptionalShortText = ""
    can_help: OptionalAnswer = ""
    want_to_learn: OptionalAnswer = ""
    lunch_slot: str = LUNCH_SLOT

    @field_validator("interests")
    @classmethod
    def normalize_interests(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.lower() for value in values))

    @field_validator("goals")
    @classmethod
    def normalize_goals(cls, values: list[Goal]) -> list[Goal]:
        return list(dict.fromkeys(values))

    @field_validator("lunch_slot")
    @classmethod
    def validate_slot(cls, value: str) -> str:
        if value != LUNCH_SLOT:
            raise ValueError("Choose the available lunch slot.")
        return value

    @model_validator(mode="after")
    def validate_goal_answers(self) -> Self:
        if self.focus_goal is None:
            if self.goal_answers:
                raise ValueError("Choose a focus goal when providing goal answers.")
            if not (self.can_help and self.want_to_learn):
                raise ValueError(
                    "Answer each lunch goal, or say what you can offer and what you're looking for."
                )
            return self
        if self.focus_goal not in self.goals:
            raise ValueError("The focus goal must be one of your selected goals.")
        if set(self.goal_answers) != set(self.goals):
            raise ValueError("Provide an answer for each selected goal, and only selected goals.")
        return self


class Attendee(Enrollment):
    """A stored profile. Unlike new enrollments, stored profiles may predate goals."""

    id: str
    conference_id: str = CONFERENCE_ID
    goals: list[Goal] = Field(default_factory=list, max_length=3)
    # Canonical topics an LLM extracted from the answers (step 8). Never client-supplied.
    topic_tags: list[str] = Field(default_factory=list)

    @field_validator("goals", mode="before")
    @classmethod
    def migrate_legacy_goals(cls, values: object) -> object:
        if values is None:
            return []
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            return list(dict.fromkeys(LEGACY_GOALS.get(value, value) for value in values))
        return values


class MatchReason(BaseModel):
    attendee_ids: tuple[str, str]
    text: str


class TableSpec(BaseModel):
    """A numbered physical table and how many chairs it has."""

    number: int = Field(ge=1)
    seats: int = Field(ge=2, le=20)


# Used until a host saves their own table layout.
DEFAULT_TABLES = [
    TableSpec(number=number, seats=GROUP_SIZE)
    for number in range(1, -(-MAX_ATTENDEES // GROUP_SIZE) + 1)
]

NarrativeSource = Literal["template", "llm"]


class MemberNote(BaseModel):
    """What one attendee sees about their table."""

    attendee_id: str
    summary: str
    starters: list[str]


class LunchGroup(BaseModel):
    id: str
    table_number: int | None = None
    members: list[Attendee]
    reasons: list[MatchReason]
    summary: str = ""
    starters: list[str] = Field(default_factory=list)
    member_notes: list[MemberNote] = Field(default_factory=list)
    narrative_source: NarrativeSource = "template"
    # A short, friendly table name ("The Kubernetes Table") and a two-line tagline.
    name: str = ""
    tagline: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class MatchMetrics(BaseModel):
    """How well a plan worked. Affinity values are in [0, 1]."""

    attendee_count: int
    seated_count: int
    table_sizes: list[int]
    average_pair_affinity: float
    weakest_best_tablemate: float
    learn_coverage: float | None
    baseline_average_pair_affinity: float
    baseline_weakest_best_tablemate: float


class MatchPlan(BaseModel):
    conference_id: str = CONFERENCE_ID
    lunch_slot: str = LUNCH_SLOT
    groups: list[LunchGroup]
    generated_at: str
    attendee_count: int
    seed: int | None = None
    metrics: MatchMetrics | None = None
    run_id: str | None = None
    # "provider:model" when an LLM wrote any table notes; None means templates only.
    narrative_provider: str | None = None
    prompt_version: str | None = None
    # How pair fit was scored: "bm25", "bm25+vectors", "bm25+tags", or "bm25+vectors+tags".
    retrieval: str | None = None


class EventConfig(BaseModel):
    """The host's lunch setup: numbered tables, seats, and the enrollment cutoff."""

    model_config = ConfigDict(extra="forbid")

    tables: list[TableSpec] = Field(
        default_factory=lambda: [table.model_copy() for table in DEFAULT_TABLES],
        min_length=1,
        max_length=50,
    )
    enrollment_closes_at: AwareDatetime | None = None
    min_table_size: int = Field(default=2, ge=2, le=6)
    # Step 7: run matching on its own at the cutoff, and optionally publish the result.
    auto_match: bool = False
    auto_publish: bool = False
    # Shown to attendees with their table.
    lunch_time: OptionalShortText = "12:30–1:15"
    location: OptionalShortText = "Main hall"
    # Event details from the organizer console. Times are local to `timezone`.
    name: ShortText = "Cloud Native Hackathon"
    date: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    timezone: Annotated[str, StringConstraints(max_length=64)] = "America/Denver"
    lunch_start: Annotated[str, StringConstraints(pattern=r"^\d{2}:\d{2}$")] = "12:30"
    lunch_end: Annotated[str, StringConstraints(pattern=r"^\d{2}:\d{2}$")] = "13:15"

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("Choose a valid time zone.") from error
        return value

    @model_validator(mode="after")
    def lunch_ends_after_it_starts(self) -> Self:
        if self.lunch_end <= self.lunch_start:
            raise ValueError("Lunch must end after it starts.")
        return self

    @field_validator("tables")
    @classmethod
    def unique_table_numbers(cls, tables: list[TableSpec]) -> list[TableSpec]:
        numbers = [table.number for table in tables]
        if len(numbers) != len(set(numbers)):
            raise ValueError("Each table needs a unique number.")
        return sorted(tables, key=lambda table: table.number)

    @model_validator(mode="after")
    def tables_fit_minimum(self) -> Self:
        if max(table.seats for table in self.tables) < self.min_table_size:
            raise ValueError("At least one table must seat the minimum table size.")
        return self

    @model_validator(mode="after")
    def automation_needs_a_cutoff(self) -> Self:
        if self.auto_match and self.enrollment_closes_at is None:
            raise ValueError("Set an enrollment cutoff to match automatically.")
        if self.auto_publish and not self.auto_match:
            raise ValueError("Turn on automatic matching to publish automatically.")
        return self

    @property
    def seat_count(self) -> int:
        return sum(table.seats for table in self.tables)

    @property
    def capacity(self) -> int:
        return min(self.seat_count, MAX_ATTENDEES)


EnrollmentStatus = Literal["open", "closed", "published"]


class RunRequest(BaseModel):
    """What the API asks the matcher to compute. The matcher reads the snapshot itself."""

    tables: list[TableSpec]
    min_table_size: int = 2
    seed: int = 0
    use_llm: bool = True
    # Embeddings + LLM topic tags, when configured. Off means BM25 only.
    use_semantic: bool = True


class TableVerdict(BaseModel):
    table_number: int | None
    score: int = Field(ge=1, le=5)
    rationale: str


class Judgement(BaseModel):
    """An LLM's second opinion on a plan. It never changes seats."""

    model: str
    prompt_version: str
    tables: list[TableVerdict]
    average: float | None
    # The same people seated at random, judged the same way, for calibration.
    baseline_average: float | None
    judged_at: datetime


class JudgeRequest(BaseModel):
    plan: MatchPlan
    seed: int = 0


class MatchRun(BaseModel):
    """A draft plan the host can review, compare, and publish."""

    id: str
    created_at: datetime
    seed: int
    tables: list[TableSpec]
    min_table_size: int
    plan: MatchPlan
    judgement: Judgement | None = None


class RunSummary(BaseModel):
    id: str
    created_at: datetime
    seed: int
    attendee_count: int
    table_count: int
    metrics: MatchMetrics | None
    published: bool
    narrative_provider: str | None = None
    retrieval: str | None = None
    judge_average: float | None = None
    judge_baseline_average: float | None = None


class LLMStatus(BaseModel):
    """Which AI provider is configured. Never includes credentials."""

    provider: str
    model: str
    enabled: bool
    problem: str | None = None
    embedding_model: str | None = None


class AutoMatchState(BaseModel):
    """What the cutoff watcher did for a given cutoff, so a restart never repeats it."""

    cutoff: datetime
    outcome: Literal["done", "failed"]
    run_id: str | None = None
    published: bool = False
    message: str
    at: datetime


AnnouncementAudience = Literal["all", "matched"]


class AnnouncementRequest(BaseModel):
    """An update for attendees' in-app inbox, sent now or at `send_at`."""

    model_config = ConfigDict(extra="forbid")

    title: ShortText
    body: Answer
    audience: AnnouncementAudience = "all"
    send_at: AwareDatetime | None = None


class Announcement(AnnouncementRequest):
    id: str
    created_at: datetime
    recipients: int = 0


class AttendeeAnnouncement(BaseModel):
    """What attendees see in their inbox."""

    id: str
    title: str
    body: str
    sent_at: datetime


class HostState(BaseModel):
    event: EventConfig
    status: EnrollmentStatus
    capacity: int
    seat_count: int
    attendees: list[Attendee]
    runs: list[RunSummary]
    published_run_id: str | None
    server_time: datetime
    llm: LLMStatus | None = None
    auto: AutoMatchState | None = None
    # Attendee ID -> when they tapped "I'm seated" at their published table.
    check_ins: dict[str, datetime] = Field(default_factory=dict)
    announcements: list[Announcement] = Field(default_factory=list)


class GoalPrompt(BaseModel):
    """How a goal is presented during onboarding. The API is the source of truth."""

    label: str
    short: str
    question: str
    example_hint: str


class EnrolledAttendee(Attendee):
    """Returned once at enrollment. The token is the attendee's only key to their table."""

    attendee_token: str


class Tablemate(BaseModel):
    """What attendees see about each other: no full answers."""

    first_name: str
    role: str
    company: str = ""
    interests: list[str]


class MyAssignment(BaseModel):
    table_number: int | None
    tablemates: list[Tablemate]
    summary: str
    starters: list[str]
    table_summary: str
    table_starters: list[str]
    narrative_source: NarrativeSource
    table_name: str = ""
    tagline: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    seats: int | None = None
    lunch_time: str = ""
    location: str = ""


class MyStatus(BaseModel):
    attendee: Attendee
    status: EnrollmentStatus
    enrollment_closes_at: datetime | None
    assignment: MyAssignment | None
    checked_in_at: datetime | None = None
    announcements: list[AttendeeAnnouncement] = Field(default_factory=list)


class ConferenceState(BaseModel):
    id: str = CONFERENCE_ID
    name: str = "Cloud Native Hackathon"
    lunch_slot: str = LUNCH_SLOT
    lunch_label: str = "Day 1 lunch"
    group_size: int = GROUP_SIZE
    attendees: list[Attendee]
    attendee_count: int
    plan: MatchPlan | None
    published: bool
    status: EnrollmentStatus = "open"
    enrollment_closes_at: datetime | None = None
    capacity: int = MAX_ATTENDEES
    goal_prompts: dict[str, GoalPrompt] = Field(default_factory=dict)
    lunch_time: str = ""
    location: str = ""
