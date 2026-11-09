"""Choose seats that maximize table fit while protecting the hardest-to-match person."""

import random
from collections.abc import Callable, Sequence
from itertools import combinations

PairScore = Callable[[str, str], float]


def table_score(members: Sequence[str], affinity: PairScore, fairness: float) -> float:
    """Sum of pair fits, plus a bonus for each member's best tablemate."""
    pairs = sum(affinity(left, right) for left, right in combinations(members, 2))
    best = sum(
        max((affinity(member, other) for other in members if other != member), default=0.0)
        for member in members
    )
    return pairs + fairness * best


def assign_tables(
    attendee_ids: Sequence[str],
    sizes: Sequence[int],
    affinity: PairScore,
    *,
    seed: int = 0,
    fairness: float = 1.0,
    max_passes: int = 25,
    restarts: int | None = None,
) -> list[list[str]]:
    """Return one member list per planned table size, seating everyone exactly once.

    Swap search can stall in a local optimum, so it runs from the greedy seating and
    from seeded random seatings, keeping the best result. Smaller events get more
    restarts because each one is cheaper.
    """
    if sum(sizes) != len(attendee_ids) or len(set(attendee_ids)) != len(attendee_ids):
        raise ValueError("Table sizes must seat every unique attendee exactly once.")
    ids = sorted(attendee_ids)
    if restarts is None:
        restarts = max(8, min(96, 2000 // max(len(ids), 1)))
    rng = random.Random(seed)
    starts = [_greedy_tables(ids, sizes, affinity)]
    for _ in range(restarts):
        shuffled = ids.copy()
        rng.shuffle(shuffled)
        starts.append(_chunk(shuffled, sizes))

    best: list[list[str]] | None = None
    best_score = float("-inf")
    for tables in starts:
        _improve_by_swaps(tables, affinity, rng, fairness, max_passes)
        score = sum(table_score(table, affinity, fairness) for table in tables)
        if score > best_score + 1e-9:
            best, best_score = tables, score
    assert best is not None
    return [sorted(table) for table in best]


def _chunk(ids: Sequence[str], sizes: Sequence[int]) -> list[list[str]]:
    tables, start = [], 0
    for size in sizes:
        tables.append(list(ids[start : start + size]))
        start += size
    return tables


def _greedy_tables(ids: list[str], sizes: Sequence[int], affinity: PairScore) -> list[list[str]]:
    remaining = set(ids)
    # People whose best possible match is weakest get a table built around them first.
    best_match = {
        person: max((affinity(person, other) for other in ids if other != person), default=0.0)
        for person in ids
    }
    tables: list[list[str]] = []
    for size in sizes:
        anchor = min(remaining, key=lambda person: (best_match[person], person))
        table = [anchor]
        remaining.remove(anchor)
        while len(table) < size:
            pick = min(
                remaining,
                key=lambda person: (-sum(affinity(person, member) for member in table), person),
            )
            table.append(pick)
            remaining.remove(pick)
        tables.append(table)
    return tables


def _improve_by_swaps(
    tables: list[list[str]],
    affinity: PairScore,
    rng: random.Random,
    fairness: float,
    max_passes: int,
) -> None:
    scores = [table_score(table, affinity, fairness) for table in tables]
    for _ in range(max_passes):
        improved = False
        table_pairs = list(combinations(range(len(tables)), 2))
        rng.shuffle(table_pairs)
        for first, second in table_pairs:
            for left_index in range(len(tables[first])):
                for right_index in range(len(tables[second])):
                    left_table, right_table = tables[first], tables[second]
                    candidate_left = left_table.copy()
                    candidate_right = right_table.copy()
                    candidate_left[left_index], candidate_right[right_index] = (
                        right_table[right_index],
                        left_table[left_index],
                    )
                    new_left = table_score(candidate_left, affinity, fairness)
                    new_right = table_score(candidate_right, affinity, fairness)
                    if new_left + new_right > scores[first] + scores[second] + 1e-9:
                        tables[first], tables[second] = candidate_left, candidate_right
                        scores[first], scores[second] = new_left, new_right
                        improved = True
        if not improved:
            return
