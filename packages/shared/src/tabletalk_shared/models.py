from typing import Annotated, Literal, Self

from pydantic import (
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
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Goal = Literal["Learn", "Share something I've learned", "Meet connections"]
LEGACY_GOALS = {"Meet peers": "Meet connections"}


class Enrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ShortText
    role: ShortText
    interests: list[Topic] = Field(min_length=1, max_length=8)
    goals: list[Goal] = Field(min_length=1, max_length=3)
    focus_goal: Goal | None = None
    goal_answers: dict[Goal, Answer] = Field(default_factory=dict)
    can_help: Answer
    want_to_learn: Answer
    lunch_slot: str = LUNCH_SLOT

    @field_validator("interests")
    @classmethod
    def normalize_interests(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.lower() for value in values))

    @field_validator("goals", mode="before")
    @classmethod
    def migrate_legacy_goals(cls, values: list[str]) -> list[str]:
        allowed = {"Learn", "Share something I've learned", "Meet connections"}
        mapped = [LEGACY_GOALS.get(value, value) for value in values or []]
        return [value for value in mapped if value in allowed] or ["Meet connections"]

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
            return self
        if self.focus_goal not in self.goals:
            raise ValueError("The focus goal must be one of your selected goals.")
        if set(self.goal_answers) != set(self.goals):
            raise ValueError("Provide an answer for each selected goal, and only selected goals.")
        return self


class Attendee(Enrollment):
    id: str
    conference_id: str = CONFERENCE_ID


class MatchReason(BaseModel):
    attendee_ids: tuple[str, str]
    text: str


class LunchGroup(BaseModel):
    id: str
    members: list[Attendee]
    reasons: list[MatchReason]


class MatchPlan(BaseModel):
    conference_id: str = CONFERENCE_ID
    lunch_slot: str = LUNCH_SLOT
    groups: list[LunchGroup]
    generated_at: str
    attendee_count: int


class ConferenceState(BaseModel):
    id: str = CONFERENCE_ID
    name: str = "Cloud Native Hackathon"
    lunch_slot: str = LUNCH_SLOT
    lunch_label: str = "Day 1 lunch"
    group_size: int = GROUP_SIZE
    attendees: list[Attendee]
    plan: MatchPlan | None
