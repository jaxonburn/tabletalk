from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

CONFERENCE_ID = "cloud-native-hackathon"
LUNCH_SLOT = "day-1-lunch"
GROUP_SIZE = 4
MAX_ATTENDEES = 100

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Answer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Goal = Literal[
    "Find a cofounder", "Hire", "Get hired", "Learn", "Sell / partner", "Invest", "Meet peers"
]


class Enrollment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ShortText
    role: ShortText
    interests: list[Topic] = Field(min_length=1, max_length=8)
    goals: list[Goal] = Field(default_factory=list, max_length=3)
    can_help: Answer
    want_to_learn: Answer
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
