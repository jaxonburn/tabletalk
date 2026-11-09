"""Decide how many people sit at each table before anyone is seated."""

from dataclasses import dataclass

from tabletalk_shared.models import TableSpec


class SeatingError(ValueError):
    """The attendees cannot be seated with this table layout."""


@dataclass(frozen=True)
class TablePlan:
    number: int
    seats: int
    size: int


def plan_table_sizes(
    tables: list[TableSpec], attendee_count: int, min_table_size: int = 2
) -> list[TablePlan]:
    """Use the fewest tables that fit everyone, balanced as evenly as seats allow.

    Larger tables are preferred (ties by table number) so fewer tables are needed.
    Raises SeatingError rather than plan a table below ``min_table_size``.
    """
    if attendee_count < min_table_size:
        raise SeatingError(f"At least {min_table_size} attendees are needed to make a lunch table.")
    numbers = [table.number for table in tables]
    if len(numbers) != len(set(numbers)):
        raise SeatingError("Table numbers must be unique.")
    capacity = sum(table.seats for table in tables)
    if attendee_count > capacity:
        raise SeatingError(f"{attendee_count} attendees exceed the {capacity} available seats.")

    by_size = sorted(tables, key=lambda table: (-table.seats, table.number))
    chosen: list[TableSpec] = []
    seated = 0
    for table in by_size:
        if seated >= attendee_count:
            break
        chosen.append(table)
        seated += table.seats

    sizes = _water_fill([table.seats for table in chosen], attendee_count)
    # The chosen tables are the fewest that fit everyone, so dropping one can't help.
    if min(sizes) < min_table_size:
        raise SeatingError(
            f"These tables can't seat {attendee_count} people with at least "
            f"{min_table_size} per table (minimum table size)."
        )

    plans = [
        TablePlan(number=table.number, seats=table.seats, size=size)
        for table, size in zip(chosen, sizes, strict=True)
    ]
    return sorted(plans, key=lambda plan: plan.number)


def _water_fill(seats: list[int], people: int) -> list[int]:
    """Seat people one at a time at the emptiest table that still has a free chair."""
    sizes = [0] * len(seats)
    for _ in range(people):
        open_tables = [index for index in range(len(seats)) if sizes[index] < seats[index]]
        index = min(open_tables, key=lambda candidate: (sizes[candidate], candidate))
        sizes[index] += 1
    return sizes
