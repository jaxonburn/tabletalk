"""Deterministic explanations and conversation starters built from real overlaps."""

from collections import Counter
from itertools import combinations

from tabletalk_shared.models import Attendee, MatchReason, MemberNote

from tabletalk_matcher.affinity import (
    COMPLEMENTARY_GOALS,
    LEARN,
    SHARE,
    connect_text,
    learn_text,
    share_text,
    shared_phrases,
)

MAX_STARTER_LENGTH = 200
GENERIC_TABLE_STARTERS = [
    "What's one idea from today's talks you want to try back at work?",
    "What tool or practice would you recommend to everyone at this table?",
    "What problem are you hoping someone here has already solved?",
]


def first_name(attendee: Attendee) -> str:
    return attendee.name.split()[0]


def human_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def teaching_overlap(mentor: Attendee, learner: Attendee) -> list[str]:
    """Phrases the mentor offers that the learner is asking about, longest first."""
    return shared_phrases(share_text(mentor), learn_text(learner))


def explain_pair(left: Attendee, right: Attendee) -> MatchReason | None:
    reasons = []
    shared = sorted(set(left.interests) & set(right.interests))
    if shared:
        reasons.append(f"Shared interests: {', '.join(shared)}.")
    aligned_goals = [
        _goal_sentence(goal, left, right)
        for goal in left.goals
        if COMPLEMENTARY_GOALS[goal] in right.goals
    ]
    if aligned_goals:
        reasons.append(" ".join(dict.fromkeys(aligned_goals)))
    for mentor, learner in ((left, right), (right, left)):
        if overlap := teaching_overlap(mentor, learner):
            reasons.append(f"{mentor.name} can help {learner.name} explore {human_list(overlap)}.")
    if connect_overlap := shared_phrases(connect_text(left), connect_text(right)):
        reasons.append(f"Both want to talk about {human_list(connect_overlap)}.")
    if not reasons:
        return None
    return MatchReason(attendee_ids=(left.id, right.id), text=" ".join(reasons))


def table_reasons(members: list[Attendee]) -> list[MatchReason]:
    return [
        reason
        for left, right in combinations(members, 2)
        if (reason := explain_pair(left, right)) is not None
    ]


def shared_topics(members: list[Attendee], limit: int = 3) -> list[str]:
    counts = Counter(topic for member in members for topic in set(member.interests))
    common = [(topic, count) for topic, count in counts.items() if count >= 2]
    return [topic for topic, _ in sorted(common, key=lambda item: (-item[1], item[0]))[:limit]]


def table_summary(members: list[Attendee]) -> str:
    sentences = []
    if topics := shared_topics(members):
        sentences.append(f"This table has common ground in {human_list(topics)}.")
    links = [
        (mentor, learner, overlap)
        for mentor in members
        for learner in members
        if mentor is not learner and (overlap := teaching_overlap(mentor, learner))
    ]
    if links:
        mentor, learner, overlap = max(links, key=lambda link: (_strength(link[2]), link[0].id))
        sentences.append(
            f"{first_name(mentor)} can help {first_name(learner)} with {human_list(overlap)}."
        )
    if not sentences:
        sentences.append("Different backgrounds at one table: a good mix for fresh perspectives.")
    return " ".join(sentences)


def table_starters(members: list[Attendee]) -> list[str]:
    starters: list[str] = []
    for member in members:
        question = member.goal_answers.get(LEARN, "").strip()
        has_mentor = any(
            teaching_overlap(other, member) for other in members if other is not member
        )
        if question.endswith("?") and has_mentor:
            starters.append(_clip(f"{first_name(member)} asks: {question}"))
    for topic in shared_topics(members):
        starters.append(f"What's the hardest {topic} problem you've worked on this year?")
    for generic in GENERIC_TABLE_STARTERS:
        starters.append(generic)
    return list(dict.fromkeys(starters))[:3]


def member_note(member: Attendee, members: list[Attendee]) -> MemberNote:
    others = [other for other in members if other is not member]
    summary: list[str] = []
    starters: list[str] = []

    mentors = [(other, overlap) for other in others if (overlap := teaching_overlap(other, member))]
    if mentors:
        mentor, overlap = max(mentors, key=lambda item: (_strength(item[1]), item[0].id))
        summary.append(f"{first_name(mentor)} can help you explore {human_list(overlap)}.")
        starters.append(f"Ask {first_name(mentor)} how they approached {overlap[0]}.")

    learners = [
        (other, overlap) for other in others if (overlap := teaching_overlap(member, other))
    ]
    if learners:
        learner, overlap = max(learners, key=lambda item: (_strength(item[1]), item[0].id))
        summary.append(f"You can help {first_name(learner)} with {human_list(overlap)}.")
        starters.append(
            f"Ask {first_name(learner)} what's behind their question about {overlap[0]}."
        )

    shared = {
        topic: [first_name(other) for other in others if topic in other.interests]
        for topic in member.interests
    }
    if best := max(
        ((topic, names) for topic, names in shared.items() if names),
        key=lambda item: len(item[1]),
        default=None,
    ):
        topic, names = best
        if len(summary) < 2:
            together = "both" if len(names) == 1 else "all"
            summary.append(f"You and {human_list(names)} are {together} into {topic}.")
        starters.append(f"Compare notes with {names[0]} on {topic}.")

    if not summary:
        intros = human_list([f"{first_name(other)} ({other.role})" for other in others])
        summary.append(f"You'll meet {intros}: a chance to hear a different perspective.")
    for other in others:
        starters.append(f"Ask {first_name(other)} what they're most excited about today.")

    return MemberNote(
        attendee_id=member.id,
        summary=" ".join(summary[:2]),
        starters=[_clip(starter) for starter in dict.fromkeys(starters)][:3],
    )


def _clip(text: str) -> str:
    if len(text) <= MAX_STARTER_LENGTH:
        return text
    return text[: MAX_STARTER_LENGTH - 1].rsplit(" ", 1)[0] + "…"


def _goal_sentence(goal: str, left: Attendee, right: Attendee) -> str:
    if goal == LEARN:
        return f"{left.name} wants to learn and {right.name} wants to share."
    if goal == SHARE:
        return f"{left.name} wants to share and {right.name} wants to learn."
    return "Both want to make new connections."


def _strength(phrases: list[str]) -> int:
    return sum(len(phrase.split()) for phrase in phrases)
