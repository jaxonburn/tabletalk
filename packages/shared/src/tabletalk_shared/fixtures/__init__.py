"""Fictional attendee datasets for tests, demos, and matching evaluation."""

import json
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from tabletalk_shared.models import Attendee, Enrollment, TableSpec


class FixtureAttendee(BaseModel):
    """An attendee plus a hidden theme label used only to evaluate matching quality."""

    theme: str
    attendee: Attendee


class Fixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: str
    tables: list[TableSpec]
    attendees: list[FixtureAttendee]


def load_fixture(name: str = "three-tables") -> Fixture:
    """Load a bundled fixture by name."""
    return parse_fixture(resources.files(__package__).joinpath(f"{name}.json").read_text())


def load_fixture_file(path: str | Path) -> Fixture:
    """Load a fixture saved by scripts/generate_personas.py (same format as bundled ones)."""
    return parse_fixture(Path(path).read_text())


def parse_fixture(text: str) -> Fixture:
    raw = json.loads(text)
    attendees = []
    for original in raw["attendees"]:
        entry = dict(original)
        theme = entry.pop("theme")
        attendee_id = entry.pop("id")
        # Validate as a new enrollment so fixtures obey the same rules as real people.
        enrollment = Enrollment.model_validate(entry)
        attendees.append(
            FixtureAttendee(
                theme=theme, attendee=Attendee(id=attendee_id, **enrollment.model_dump())
            )
        )
    return Fixture(description=raw["description"], tables=raw["tables"], attendees=attendees)
