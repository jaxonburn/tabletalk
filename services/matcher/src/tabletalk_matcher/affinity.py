"""Score how well every pair of attendees fits, using Elasticsearch relevance."""

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from tabletalk_shared.models import Attendee, Goal
from tabletalk_shared.storage import conference_filter

LEARN: Goal = "Learn"
SHARE: Goal = "Share something I've learned"
CONNECT: Goal = "Meet connections"

COMPLEMENTARY_GOALS: dict[Goal, Goal] = {LEARN: SHARE, SHARE: LEARN, CONNECT: CONNECT}

_STOP_WORDS_TEXT = """
    a about across actually after all also am an and any are as at be been before beyond
    build but by can could did do does doing don every for from get getting go going had
    has have how i i'd i'm i've if in into is it its just keep keen like love make makes
    more most much my new not of on one or other our out over people really so some
    still such than that the their them there these they things this those through to
    too up us use used using very want was way we we've what when where which while who
    why will with without would you your happy enjoy talk talking chat compare notes
    learn learned share sharing help teams team work working time times day days week weeks
    hour hours fresh internal actually production good best question questions problem
    problems approach thing stuff lot lots
"""
STOP_WORDS = frozenset(_STOP_WORDS_TEXT.split())
# Fine inside a phrase ("developer experience") but too generic to show on their own.
GENERIC_SINGLE_WORDS = frozenset({"developer", "engineer", "engineering", "software", "service"})


@dataclass(frozen=True)
class AffinityWeights:
    """Relative importance of each signal. Tune against fixtures, not by feel."""

    learn_share: float = 3.0
    connect: float = 2.0
    exact_interest: float = 2.0
    interest_text: float = 1.0
    goals: float = 1.0
    tags: float = 2.0
    role_diversity: float = 0.05
    # Semantic (embedding) scoring, blended with BM25: final = (1 - semantic) * bm25 + ...
    # 0.75 gave the best nearest-match accuracy on both the sample and LLM-written personas
    # (see docs/matching-plan.md, step 8).
    semantic: float = 0.75
    semantic_learn_share: float = 2.0
    semantic_topic: float = 1.0


DEFAULT_WEIGHTS = AffinityWeights()


def learn_text(attendee: Attendee) -> str:
    # Concise topic fields come first so they win ties when picking display phrases.
    return _join(attendee.want_to_learn, attendee.goal_answers.get(LEARN))


def share_text(attendee: Attendee) -> str:
    return _join(attendee.can_help, attendee.goal_answers.get(SHARE))


def connect_text(attendee: Attendee) -> str:
    return _join(attendee.goal_answers.get(CONNECT))


def _join(*parts: str | None) -> str:
    return "\n".join(part for part in parts if part)


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'+#.-]*[A-Za-z0-9+#]|[A-Za-z0-9]")
SEGMENT_PATTERN = re.compile(r"(?<=[.?!;:])\s+|\n+")


@dataclass(frozen=True)
class Token:
    key: str
    surface: str
    stop: bool


def _normalize(word: str) -> str:
    word = word.lower()
    # A tiny plural fold keeps "pipelines"/"pipeline" and "vulnerabilities"/"vulnerability"
    # together.
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokenize(text: str) -> list[list[Token]]:
    """Split text into sentence-like segments of normalized tokens."""
    segments = []
    for segment in SEGMENT_PATTERN.split(text):
        tokens = []
        for surface in TOKEN_PATTERN.findall(segment):
            lowered = surface.lower()
            stop = lowered in STOP_WORDS or len(lowered) < 2
            tokens.append(Token(key=_normalize(surface), surface=surface, stop=stop))
        if tokens:
            segments.append(tokens)
    return segments


def significant_words(value: str) -> set[str]:
    return {token.key for segment in tokenize(value) for token in segment if not token.stop}


def shared_phrases(source: str, other: str, limit: int = 2, max_words: int = 3) -> list[str]:
    """Longest phrases from ``source`` that also appear in ``other``, as written in source."""
    other_grams = set()
    for segment in tokenize(other):
        segment_keys = [token.key for token in segment]
        for size in range(1, max_words + 1):
            for start in range(len(segment_keys) - size + 1):
                other_grams.add(tuple(segment_keys[start : start + size]))

    candidates = []
    for segment_index, segment in enumerate(tokenize(source)):
        for size in range(max_words, 0, -1):
            for start in range(len(segment) - size + 1):
                gram = segment[start : start + size]
                if any(token.stop for token in gram):
                    continue
                if size == 1 and not _topic_like(gram[0], at_sentence_start=start == 0):
                    continue
                if tuple(token.key for token in gram) in other_grams:
                    candidates.append((size, segment_index, start, gram))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1], candidate[2]))

    phrases: list[str] = []
    covered: set[str] = set()
    for _, _, start, gram in candidates:
        keys = {token.key for token in gram}
        if keys & covered:
            continue
        covered |= keys
        phrases.append(
            " ".join(
                _display(token.surface, at_sentence_start=start + offset == 0)
                for offset, token in enumerate(gram)
            )
        )
        if len(phrases) == limit:
            break
    return phrases


def _topic_like(token: Token, *, at_sentence_start: bool) -> bool:
    # Lone words like "golden" or "fresh" mean different things in different answers.
    # Keep long words, acronyms/brands (AI, SBOM, BM25, KServe), and capitalized
    # names in mid-sentence (Kafka, Python).
    surface = token.surface
    if token.key in GENERIC_SINGLE_WORDS:
        return False
    return (
        len(token.key) >= 7
        or any(character.isdigit() for character in surface)
        or any(character.isupper() for character in surface[1:])
        or (surface[:1].isupper() and not at_sentence_start)
    )


def _display(surface: str, *, at_sentence_start: bool) -> str:
    # Lowercase a sentence's opening capital, but keep names, acronyms, and brand casing.
    if at_sentence_start and surface[:1].isupper() and surface[1:].islower():
        return surface.lower()
    return surface


# Elasticsearch field names for the per-goal answers.
def _answer_field(goal: Goal) -> str:
    return f"goal_answers.{goal}"


def affinity_query(
    attendee: Attendee, snapshot_ids: Iterable[str], weights: AffinityWeights = DEFAULT_WEIGHTS
) -> dict[str, Any]:
    """Rank everyone else in the snapshot by how well they fit this attendee."""
    should: list[dict[str, Any]] = []

    def match(field: str, text: str, boost: float) -> None:
        if text:
            should.append({"match": {field: {"query": text, "boost": boost}}})

    wants, offers, connects = learn_text(attendee), share_text(attendee), connect_text(attendee)
    # They can teach what I want to learn.
    match(_answer_field(SHARE), wants, weights.learn_share)
    match("can_help", wants, weights.learn_share)
    # I can teach what they want to learn.
    match(_answer_field(LEARN), offers, weights.learn_share)
    match("want_to_learn", offers, weights.learn_share)
    # We would enjoy the same open-ended conversation.
    match(_answer_field(CONNECT), connects, weights.connect)
    match("interests", " ".join(attendee.interests), weights.interest_text)
    should.append(
        {"terms": {"interests.exact": attendee.interests, "boost": weights.exact_interest}}
    )
    if attendee.topic_tags:
        should.append({"terms": {"topic_tags": attendee.topic_tags, "boost": weights.tags}})
    if attendee.goals:
        should.append(
            {
                "terms": {
                    "goals": sorted({COMPLEMENTARY_GOALS[goal] for goal in attendee.goals}),
                    "boost": weights.goals,
                }
            }
        )
    return {
        "bool": {
            "filter": [*conference_filter(), {"terms": {"id": sorted(snapshot_ids)}}],
            "must_not": [{"term": {"id": attendee.id}}],
            "should": should,
            "minimum_should_match": 0,
        }
    }


def knn_query(
    attendee_id: str,
    vectors: Mapping[str, list[float]],
    snapshot_ids: Iterable[str],
    weights: AffinityWeights = DEFAULT_WEIGHTS,
) -> list[dict[str, Any]]:
    """Elasticsearch kNN clauses: my question vs their experience, and the reverse."""
    ids = sorted(snapshot_ids)
    pairs = [
        ("learn_vector", "share_vector", weights.semantic_learn_share),
        ("share_vector", "learn_vector", weights.semantic_learn_share),
        ("topic_vector", "topic_vector", weights.semantic_topic),
    ]
    return [
        {
            "field": their_field,
            "query_vector": vectors[my_field],
            "k": len(ids),
            "num_candidates": max(len(ids), 10),
            "boost": boost,
            "filter": {
                "bool": {
                    "filter": [*conference_filter(), {"terms": {"id": ids}}],
                    "must_not": [{"term": {"id": attendee_id}}],
                }
            },
        }
        for my_field, their_field, boost in pairs
        if vectors.get(my_field)
    ]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm if norm else 0.0


def semantic_directed_scores(
    attendees: list[Attendee],
    vectors: Mapping[str, Mapping[str, list[float]]],
    weights: AffinityWeights = DEFAULT_WEIGHTS,
) -> dict[str, dict[str, float]]:
    """Python twin of the kNN query (Elasticsearch cosine score = (1 + cos) / 2)."""
    pairs = [
        ("learn_vector", "share_vector", weights.semantic_learn_share),
        ("share_vector", "learn_vector", weights.semantic_learn_share),
        ("topic_vector", "topic_vector", weights.semantic_topic),
    ]
    scores: dict[str, dict[str, float]] = {}
    for me in attendees:
        mine = vectors.get(me.id, {})
        row: dict[str, float] = {}
        for other in attendees:
            if other.id == me.id:
                continue
            theirs = vectors.get(other.id, {})
            row[other.id] = sum(
                boost * (1 + _cosine(mine[my_field], theirs[their_field])) / 2
                for my_field, their_field, boost in pairs
                if mine.get(my_field) and theirs.get(their_field)
            )
        scores[me.id] = row
    return scores


def combine_directed(
    bm25: Mapping[str, Mapping[str, float]],
    semantic: Mapping[str, Mapping[str, float]] | None,
    weights: AffinityWeights = DEFAULT_WEIGHTS,
) -> dict[str, dict[str, float]]:
    """Blend keyword and semantic rows, each normalized per attendee to [0, 1].

    BM25 is scaled by the row's best score. Cosine scores share a high baseline, so the
    semantic row is min-max scaled, keeping only how much better one candidate is.
    """
    combined: dict[str, dict[str, float]] = {}
    for attendee_id, row in bm25.items():
        best = max(row.values(), default=0.0)
        keyword = {other: (score / best if best > 0 else 0.0) for other, score in row.items()}
        semantic_row = (semantic or {}).get(attendee_id) or {}
        if not semantic_row:
            combined[attendee_id] = keyword
            continue
        low, high = min(semantic_row.values()), max(semantic_row.values())
        spread = high - low
        blended = {}
        for other in set(keyword) | set(semantic_row):
            meaning = (semantic_row.get(other, low) - low) / spread if spread > 0 else 0.0
            blended[other] = (1 - weights.semantic) * keyword.get(other, 0.0) + (
                weights.semantic * meaning
            )
        combined[attendee_id] = blended
    return combined


class Affinity:
    """Symmetric pairwise fit scores in [0, 1] between attendees in one snapshot."""

    def __init__(self, scores: Mapping[frozenset[str], float]) -> None:
        self._scores = dict(scores)

    def __call__(self, left: str, right: str) -> float:
        if left == right:
            return 0.0
        return self._scores.get(frozenset((left, right)), 0.0)

    @classmethod
    def from_directed(
        cls,
        attendees: list[Attendee],
        directed: Mapping[str, Mapping[str, float]],
        weights: AffinityWeights = DEFAULT_WEIGHTS,
    ) -> "Affinity":
        """Combine raw per-attendee relevance scores into one symmetric matrix.

        Each attendee's scores are scaled by their own best score, so a prolific writer
        does not dominate. The two directions are then averaged.
        """
        normalized: dict[str, dict[str, float]] = {}
        for attendee_id, row in directed.items():
            best = max(row.values(), default=0.0)
            normalized[attendee_id] = {
                other: (score / best if best > 0 else 0.0) for other, score in row.items()
            }
        scores: dict[frozenset[str], float] = {}
        for left, right in combinations(attendees, 2):
            forward = normalized.get(left.id, {}).get(right.id, 0.0)
            backward = normalized.get(right.id, {}).get(left.id, 0.0)
            score = (forward + backward) / 2
            if left.role.lower() != right.role.lower():
                score += weights.role_diversity
            scores[frozenset((left.id, right.id))] = min(1.0, score)
        return cls(scores)


def lexical_directed_scores(
    attendees: list[Attendee], weights: AffinityWeights = DEFAULT_WEIGHTS
) -> dict[str, dict[str, float]]:
    """A dependency-free stand-in for Elasticsearch, used by tests and offline evaluation."""
    words = {
        attendee.id: {
            "learn": significant_words(learn_text(attendee)),
            "share": significant_words(share_text(attendee)),
            "connect": significant_words(connect_text(attendee)),
            "interests": significant_words(" ".join(attendee.interests)),
        }
        for attendee in attendees
    }
    scores: dict[str, dict[str, float]] = {}
    for me in attendees:
        mine = words[me.id]
        row: dict[str, float] = {}
        for other in attendees:
            if other.id == me.id:
                continue
            theirs = words[other.id]
            complementary = {COMPLEMENTARY_GOALS[goal] for goal in me.goals}
            row[other.id] = (
                weights.tags * len(set(me.topic_tags) & set(other.topic_tags))
                + weights.learn_share * len(mine["learn"] & theirs["share"])
                + weights.learn_share * len(mine["share"] & theirs["learn"])
                + weights.connect * len(mine["connect"] & theirs["connect"])
                + weights.exact_interest * len(set(me.interests) & set(other.interests))
                + weights.interest_text * len(mine["interests"] & theirs["interests"])
                + weights.goals * len(complementary & set(other.goals))
            )
        scores[me.id] = row
    return scores
