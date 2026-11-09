"""Measure matching quality on fictional people whose hidden themes are known."""

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from math import comb

from tabletalk_shared.fixtures import FixtureAttendee
from tabletalk_shared.llm import Embedder
from tabletalk_shared.models import Attendee, MatchPlan, TableSpec

from tabletalk_matcher.affinity import (
    Affinity,
    combine_directed,
    learn_text,
    lexical_directed_scores,
    semantic_directed_scores,
    share_text,
)
from tabletalk_matcher.enrichment import topic_text
from tabletalk_matcher.matching import build_plan

Vectors = dict[str, dict[str, list[float]]]

UNLABELED = {"bridge", "unknown"}


def adjusted_rand_index(truth: Sequence[str], predicted: Sequence[str]) -> float:
    """1.0 means the tables reproduce the themes exactly; about 0.0 means random."""
    if len(truth) != len(predicted):
        raise ValueError("Both labelings must cover the same people.")
    total = len(truth)
    if total < 2:
        return 1.0
    pairs = Counter(zip(truth, predicted, strict=True))
    index = sum(comb(count, 2) for count in pairs.values())
    truth_sum = sum(comb(count, 2) for count in Counter(truth).values())
    predicted_sum = sum(comb(count, 2) for count in Counter(predicted).values())
    expected = truth_sum * predicted_sum / comb(total, 2)
    maximum = (truth_sum + predicted_sum) / 2
    if maximum == expected:
        return 1.0
    return (index - expected) / (maximum - expected)


def theme_purity(seating: Sequence[Sequence[str]], themes: dict[str, str]) -> float:
    """Average share of each table held by its most common theme (bridges excluded)."""
    scores = []
    for table in seating:
        labels = [themes[person] for person in table if themes[person] not in UNLABELED]
        if labels:
            scores.append(Counter(labels).most_common(1)[0][1] / len(labels))
    return sum(scores) / len(scores) if scores else 0.0


def _labels(
    seating: Sequence[Sequence[str]], themes: dict[str, str]
) -> tuple[list[str], list[str]]:
    truth, predicted = [], []
    for number, table in enumerate(seating):
        for person in table:
            if themes[person] not in UNLABELED:
                truth.append(themes[person])
                predicted.append(str(number))
    return truth, predicted


@dataclass(frozen=True)
class Evaluation:
    seed: int
    attendees: int
    table_sizes: list[int]
    average_fit: float
    baseline_average_fit: float
    weakest_match: float
    baseline_weakest_match: float
    learn_coverage: float | None
    theme_purity: float
    random_theme_purity: float
    theme_ari: float
    random_theme_ari: float
    # Share of people whose single best match shares their theme (bridges excluded).
    neighbor_accuracy: float


async def embed_people(attendees: list[Attendee], embedder: Embedder) -> Vectors:
    """The same learn/share/topic vectors the matcher stores in Elasticsearch."""
    slots, texts = [], []
    for attendee in attendees:
        for field, text in (
            ("learn_vector", learn_text(attendee)),
            ("share_vector", share_text(attendee)),
            ("topic_vector", topic_text(attendee)),
        ):
            if text:
                slots.append((attendee.id, field))
                texts.append(text[:2000])
    vectors: Vectors = {}
    for (attendee_id, field), vector in zip(slots, await embedder.embed(texts), strict=True):
        vectors.setdefault(attendee_id, {})[field] = vector
    return vectors


def match_people(
    attendees: list[Attendee], tables: list[TableSpec], seed: int, vectors: Vectors | None
) -> MatchPlan:
    """The production pipeline with the lexical stand-in for BM25 and a Python kNN twin."""
    semantic = semantic_directed_scores(attendees, vectors) if vectors else None
    directed = combine_directed(lexical_directed_scores(attendees), semantic)
    return build_plan(attendees, tables, Affinity.from_directed(attendees, directed), seed=seed)


def evaluate(
    people: list[FixtureAttendee],
    tables: list[TableSpec],
    seed: int = 0,
    vectors: Vectors | None = None,
) -> Evaluation:
    """Seat people with the real matcher and compare with random seating.

    Pass ``vectors`` to score with BM25-style keywords plus embeddings (hybrid).
    """
    attendees = [person.attendee for person in people]
    themes = {person.attendee.id: person.theme for person in people}
    semantic = semantic_directed_scores(attendees, vectors) if vectors else None
    affinity = Affinity.from_directed(
        attendees, combine_directed(lexical_directed_scores(attendees), semantic)
    )
    plan = build_plan(attendees, tables, affinity, seed=seed)
    metrics = plan.metrics
    assert metrics is not None
    seating = [[member.id for member in group.members] for group in plan.groups]

    shuffled = [attendee.id for attendee in attendees]
    random.Random(seed).shuffle(shuffled)
    random_seating, start = [], 0
    for size in metrics.table_sizes:
        random_seating.append(shuffled[start : start + size])
        start += size

    return Evaluation(
        seed=seed,
        attendees=len(attendees),
        table_sizes=metrics.table_sizes,
        average_fit=metrics.average_pair_affinity,
        baseline_average_fit=metrics.baseline_average_pair_affinity,
        weakest_match=metrics.weakest_best_tablemate,
        baseline_weakest_match=metrics.baseline_weakest_best_tablemate,
        learn_coverage=metrics.learn_coverage,
        theme_purity=round(theme_purity(seating, themes), 4),
        random_theme_purity=round(theme_purity(random_seating, themes), 4),
        theme_ari=round(adjusted_rand_index(*_labels(seating, themes)), 4),
        random_theme_ari=round(adjusted_rand_index(*_labels(random_seating, themes)), 4),
        neighbor_accuracy=round(neighbor_accuracy(attendees, themes, affinity), 4),
    )


def neighbor_accuracy(
    attendees: list[Attendee], themes: dict[str, str], affinity: Affinity
) -> float:
    labeled = [a for a in attendees if themes[a.id] not in UNLABELED]
    if not labeled:
        return 0.0
    hits = 0
    for attendee in labeled:
        best = max(
            (other for other in attendees if other.id != attendee.id),
            key=lambda other: (affinity(attendee.id, other.id), other.id),
        )
        hits += themes[best.id] == themes[attendee.id]
    return hits / len(labeled)
