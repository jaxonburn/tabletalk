"""Fictional attendee generators for demos and matching evaluation.

Every generated person carries a hidden ``theme`` label that is only used to measure
whether matching regroups similar people. Matching itself never sees the label.
"""

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from tabletalk_shared.fixtures import FixtureAttendee
from tabletalk_shared.llm import (
    NULLABLE_STRING,
    STRING,
    STRING_LIST,
    LLMClient,
    LLMError,
    strict_object,
)
from tabletalk_shared.models import Attendee, Enrollment, Goal

LEARN: Goal = "Learn"
SHARE: Goal = "Share something I've learned"
CONNECT: Goal = "Meet connections"
BRIDGE_RATE = 0.2
PERSONA_PROMPT_VERSION = "personas-v2"


@dataclass(frozen=True)
class Topic:
    """One subject someone can ask about (learn) or speak to (share)."""

    name: str
    learn: str
    share: str


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    roles: list[str]
    interests: list[str]
    topics: list[Topic]
    connect: list[str] = field(default_factory=list)


THEMES = [
    Theme(
        key="platform",
        title="Kubernetes platforms at e-commerce scale",
        roles=["Platform engineer", "SRE", "Staff engineer", "Platform product manager"],
        interests=["kubernetes", "platform engineering", "autoscaling", "developer experience"],
        topics=[
            Topic(
                "Kubernetes autoscaling for traffic spikes",
                "How do you autoscale Kubernetes services for 10x flash-sale spikes without "
                "paying for idle nodes?",
                "I tuned Kubernetes autoscaling on queue depth for a retail site's flash sales.",
            ),
            Topic(
                "golden paths for developers",
                "How do platform teams decide what belongs in a golden path for developers?",
                "I built golden paths for developers on our internal platform with Backstage.",
            ),
            Topic(
                "Kubernetes cost optimization",
                "Where do the biggest Kubernetes cost optimization wins usually hide?",
                "I cut our Kubernetes cost by a third with Karpenter and right-sized requests.",
            ),
            Topic(
                "multi-cluster Kubernetes upgrades",
                "How do you run multi-cluster Kubernetes upgrades without weekend outages?",
                "I've automated multi-cluster Kubernetes upgrades across 40 clusters.",
            ),
            Topic(
                "measuring developer experience",
                "How are teams measuring developer experience beyond DORA metrics?",
                "I run quarterly surveys measuring developer experience for 300 engineers.",
            ),
        ],
        connect=[
            "I'd enjoy swapping stories about running platform teams during peak traffic.",
            "Happy to talk about internal developer platforms and on-call culture.",
        ],
    ),
    Theme(
        key="security",
        title="AI-assisted vulnerability management",
        roles=["Application security lead", "DevSecOps engineer", "Security engineer"],
        interests=["application security", "vulnerability management", "supply chain security"],
        topics=[
            Topic(
                "AI-assisted vulnerability patching",
                "How are teams reducing time to patch vulnerabilities with AI-assisted "
                "vulnerability patching without drowning reviewers?",
                "We cut time to patch critical CVEs to four days with AI-assisted vulnerability "
                "patching and human review.",
            ),
            Topic(
                "CVE triage at scale",
                "How do you handle CVE triage at scale and decide which alerts actually matter?",
                "I built CVE triage at scale using reachability analysis to drop noisy alerts.",
            ),
            Topic(
                "SBOMs and image signing",
                "Is anyone getting real value from SBOMs and image signing in production?",
                "I rolled out SBOMs and image signing with Sigstore for every container we ship.",
            ),
            Topic(
                "secrets scanning in CI",
                "What's working for secrets scanning in CI without blocking every merge?",
                "I set up secrets scanning in CI with pre-commit hooks and automatic rotation.",
            ),
            Topic(
                "threat modeling for AI features",
                "How do you approach threat modeling for AI features like agents and RAG?",
                "I run threat modeling for AI features, including prompt injection reviews.",
            ),
        ],
        connect=[
            "Keen to meet people wrestling with vulnerability backlogs.",
            "I'd enjoy talking about how security and product teams share ownership.",
        ],
    ),
    Theme(
        key="data-ml",
        title="Search, RAG, and LLM evaluation",
        roles=["Machine learning engineer", "Search engineer", "Data engineer"],
        interests=["rag", "elasticsearch", "embeddings", "llm evaluation"],
        topics=[
            Topic(
                "hybrid search with BM25 and vectors",
                "When does hybrid search with BM25 and vectors actually beat tuned keywords?",
                "We shipped hybrid search with BM25 and vectors over two million documents.",
            ),
            Topic(
                "RAG retrieval evaluation",
                "How do you do RAG retrieval evaluation before anything reaches production?",
                "I built RAG retrieval evaluation with golden datasets and LLM-as-judge scoring.",
            ),
            Topic(
                "fresh embeddings in streaming pipelines",
                "How are people keeping fresh embeddings in streaming pipelines?",
                "I keep fresh embeddings in streaming pipelines with Kafka and incremental "
                "re-indexing.",
            ),
            Topic(
                "LLM cost and latency budgets",
                "How do teams set LLM cost and latency budgets for user-facing features?",
                "I cut our LLM cost and latency budgets in half with caching and smaller models.",
            ),
            Topic(
                "search relevance tuning",
                "What's your process for search relevance tuning with real user feedback?",
                "I run search relevance tuning sprints using click data and judgment lists.",
            ),
        ],
        connect=[
            "Happy to chat about data pipelines that feed search and LLM apps.",
            "I'd enjoy comparing notes on shipping AI features responsibly.",
        ],
    ),
    Theme(
        key="observability",
        title="Observability and incident response",
        roles=["SRE", "Observability engineer", "Engineering manager"],
        interests=["observability", "opentelemetry", "incident response", "slos"],
        topics=[
            Topic(
                "OpenTelemetry rollout",
                "How did you get an OpenTelemetry rollout across dozens of teams to stick?",
                "I led an OpenTelemetry rollout across 60 services with auto-instrumentation.",
            ),
            Topic(
                "SLOs that teams respect",
                "How do you set SLOs that teams respect instead of ignore?",
                "I introduced SLOs that teams respect by tying error budgets to release pace.",
            ),
            Topic(
                "observability cost control",
                "How do you keep observability cost control without losing signal?",
                "I cut observability cost control headaches with tail sampling in the collector.",
            ),
            Topic(
                "blameless incident reviews",
                "How do you run blameless incident reviews that lead to real fixes?",
                "I facilitate blameless incident reviews and track every follow-up to closure.",
            ),
            Topic(
                "distributed tracing for slow requests",
                "How do you use distributed tracing for slow requests across services?",
                "I use distributed tracing for slow requests to find the real bottleneck fast.",
            ),
        ],
        connect=[
            "I'd love to trade on-call war stories and alert-fatigue fixes.",
            "Happy to talk about making telemetry useful for product teams.",
        ],
    ),
    Theme(
        key="frontend",
        title="Web performance and accessibility",
        roles=["Frontend engineer", "Design systems engineer", "Full-stack engineer"],
        interests=["react", "web performance", "accessibility", "design systems"],
        topics=[
            Topic(
                "Core Web Vitals improvements",
                "Which Core Web Vitals improvements gave you the biggest real-user wins?",
                "I improved our Core Web Vitals with image priorities and fewer client renders.",
            ),
            Topic(
                "accessible design systems",
                "How do you keep accessible design systems accessible as they grow?",
                "I maintain accessible design systems with automated axe checks in CI.",
            ),
            Topic(
                "React server components",
                "Are React server components worth the migration for an existing app?",
                "We migrated a large app to React server components and halved bundle size.",
            ),
            Topic(
                "frontend observability",
                "What does good frontend observability look like beyond error tracking?",
                "I set up frontend observability with real-user monitoring and session traces.",
            ),
            Topic(
                "screen reader testing",
                "How do teams fit screen reader testing into a normal sprint?",
                "I run screen reader testing sessions with VoiceOver and NVDA every sprint.",
            ),
        ],
        connect=[
            "I'd enjoy talking about craft, accessibility, and shipping fast UIs.",
            "Happy to compare notes on design-engineering collaboration.",
        ],
    ),
    Theme(
        key="oss",
        title="Open source maintainership and community",
        roles=["Open source maintainer", "Developer advocate", "Community manager"],
        interests=["open source", "community", "developer relations", "governance"],
        topics=[
            Topic(
                "growing open source contributors",
                "How do you go about growing open source contributors beyond the first PR?",
                "I grew our open source contributors from 5 to 80 with good-first-issue triage.",
            ),
            Topic(
                "maintainer burnout",
                "How do you avoid maintainer burnout on a popular project?",
                "I wrote our policy on maintainer burnout: rotations and saying no to features.",
            ),
            Topic(
                "CNCF project governance",
                "What does CNCF project governance look like day to day?",
                "I helped our project through CNCF project governance and sandbox onboarding.",
            ),
            Topic(
                "developer docs that people read",
                "How do you write developer docs that people read?",
                "I rebuilt our developer docs that people read around runnable tutorials.",
            ),
            Topic(
                "funding open source",
                "What are realistic options for funding open source work?",
                "I've secured funding open source work through foundations and sponsorships.",
            ),
        ],
        connect=[
            "I'd love to meet other maintainers and swap community-building ideas.",
            "Happy to talk about sustaining open source projects long term.",
        ],
    ),
]
THEMES_BY_KEY = {theme.key: theme for theme in THEMES}

FIRST_NAMES = [
    "Ada",
    "Amara",
    "Ana",
    "Ari",
    "Asha",
    "Ben",
    "Bo",
    "Caleb",
    "Camila",
    "Chen",
    "Chiara",
    "Dani",
    "Dev",
    "Diego",
    "Elif",
    "Emeka",
    "Esra",
    "Farah",
    "Felix",
    "Gabe",
    "Hana",
    "Hugo",
    "Ines",
    "Isaac",
    "Jada",
    "Jonas",
    "Kai",
    "Kenji",
    "Lena",
    "Leo",
    "Lina",
    "Luca",
    "Maya",
    "Mateo",
    "Mina",
    "Nadia",
    "Nia",
    "Noah",
    "Omar",
    "Pia",
    "Quinn",
    "Rafa",
    "Ravi",
    "Rosa",
    "Sami",
    "Sara",
    "Tariq",
    "Tess",
    "Theo",
    "Uma",
    "Vera",
    "Wei",
    "Yara",
    "Yusuf",
    "Zoe",
]
LAST_NAMES = [
    "Abara",
    "Bauer",
    "Castillo",
    "Dubois",
    "Eriksen",
    "Fujita",
    "Garcia",
    "Haddad",
    "Ivanova",
    "Jensen",
    "Kaur",
    "Larsen",
    "Moreau",
    "Nakamura",
    "Okafor",
    "Petrov",
    "Quinn",
    "Rossi",
    "Silva",
    "Tanaka",
    "Usman",
    "Varga",
    "Weber",
    "Xu",
    "Yilmaz",
    "Zhou",
]


def _goals(rng: random.Random) -> list[Goal]:
    count = rng.choices([1, 2, 3], weights=[3, 4, 2])[0]
    return sorted(rng.sample([LEARN, SHARE, CONNECT], count), key=[LEARN, SHARE, CONNECT].index)


def generate_offline(
    count: int, seed: int = 0, themes: list[str] | None = None
) -> list[FixtureAttendee]:
    """Deterministic personas from the theme catalog; no network or API key needed."""
    chosen = [THEMES_BY_KEY[key] for key in themes] if themes else THEMES
    if not chosen:
        raise ValueError("Choose at least one theme.")
    rng = random.Random(seed)
    names = [f"{first} {last}" for first in FIRST_NAMES for last in LAST_NAMES]
    rng.shuffle(names)
    people = []
    for index in range(count):
        theme = chosen[index % len(chosen)]
        bridge = len(chosen) > 1 and rng.random() < BRIDGE_RATE
        other = rng.choice([candidate for candidate in chosen if candidate is not theme])
        learn_from = other if bridge else theme
        learn_topic = rng.choice(learn_from.topics)
        share_topic = rng.choice([topic for topic in theme.topics if topic is not learn_topic])
        interests = rng.sample(theme.interests, 2) + (
            [rng.choice(other.interests)] if bridge else []
        )
        goals = _goals(rng)
        answers: dict[Goal, str] = {}
        if LEARN in goals:
            answers[LEARN] = learn_topic.learn
        if SHARE in goals:
            answers[SHARE] = share_topic.share
        if CONNECT in goals:
            answers[CONNECT] = rng.choice(theme.connect)
        enrollment = Enrollment(
            name=names[index % len(names)],
            role=rng.choice(theme.roles),
            interests=interests,
            goals=goals,
            focus_goal=rng.choice(goals),
            goal_answers=answers,
            can_help=share_topic.name[0].upper() + share_topic.name[1:],
            want_to_learn=learn_topic.name[0].upper() + learn_topic.name[1:],
        )
        people.append(
            FixtureAttendee(
                theme="bridge" if bridge else theme.key,
                attendee=Attendee(id=f"gen-{seed}-{index + 1:03}", **enrollment.model_dump()),
            )
        )
    return people


PERSONA_SYSTEM_PROMPT = """\
You generate realistic, FICTIONAL attendees for testing a lunch-matching app at a
cloud-native tech conference. Vary seniority, company size, industry, and region.
Write answers the way a real person types on a phone: first person, 1-2 sentences,
concrete technologies, occasionally informal. Never use real people's names or real
companies' confidential details."""

PERSONA_USER_PROMPT = """\
Create {count} attendees. Variety seed: {seed}.
Spread them roughly evenly across these hidden themes, and make about 20% of people
"bridges" whose Learn topic comes from a different theme than their Share topic:
{themes}
For each attendee:
- theme: the theme key (or "bridge" for bridge people).
- name: a realistic fictional full name only, e.g. "Priya Natarajan" (no greeting,
  no "I'm", no job title).
- role: the job title only, e.g. "Staff platform engineer" (no company).
- interests: 2-4 short lowercase topics.
- goals: 1-3 of exactly "Learn", "Share something I've learned", "Meet connections".
- focus_goal: one of their goals.
- learn / share / connect: their answer for each selected goal, or null if not selected.
  Learn is a specific question, Share is something they've done, Connect is what they'd
  enjoy discussing.
- can_help and want_to_learn: short topic phrases (under 80 characters).
People in the same theme should be able to help each other: some should ask about
things others have done."""

PERSONA_SCHEMA = strict_object(
    {
        "attendees": {
            "type": "array",
            "items": strict_object(
                {
                    "theme": STRING,
                    "name": STRING,
                    "role": STRING,
                    "interests": STRING_LIST,
                    "goals": STRING_LIST,
                    "focus_goal": STRING,
                    "learn": NULLABLE_STRING,
                    "share": NULLABLE_STRING,
                    "connect": NULLABLE_STRING,
                    "can_help": STRING,
                    "want_to_learn": STRING,
                }
            ),
        }
    }
)


NAME_PATTERN = re.compile(r"^[^\W\d_][\w.'’-]*(?: [^\W\d_][\w.'’-]*){1,3}$")


def looks_like_name(value: object) -> bool:
    """2-4 name-like words; rejects self-introductions such as "I'm Priya, engineer"."""
    return (
        isinstance(value, str)
        and bool(NAME_PATTERN.match(value.strip()))
        and not re.match(r"^i['’]m\b", value.strip(), re.I)
    )


def persona_from_llm(raw: dict[str, Any], attendee_id: str) -> FixtureAttendee | None:
    """Validate one generated persona exactly like a real enrollment; drop it if invalid."""
    if not looks_like_name(raw.get("name")):
        return None
    goals = raw.get("goals") or []
    answers = {
        goal: raw.get(key)
        for goal, key in ((LEARN, "learn"), (SHARE, "share"), (CONNECT, "connect"))
        if goal in goals and isinstance(raw.get(key), str) and raw.get(key, "").strip()
    }
    try:
        enrollment = Enrollment.model_validate(
            {
                "name": raw.get("name"),
                "role": raw.get("role"),
                "interests": raw.get("interests"),
                "goals": goals,
                "focus_goal": raw.get("focus_goal") if answers else None,
                "goal_answers": answers,
                "can_help": raw.get("can_help"),
                "want_to_learn": raw.get("want_to_learn"),
            }
        )
    except ValidationError:
        return None
    theme = raw.get("theme")
    return FixtureAttendee(
        theme=theme if isinstance(theme, str) and theme else "unknown",
        attendee=Attendee(id=attendee_id, **enrollment.model_dump()),
    )


async def generate_with_llm(
    client: LLMClient,
    count: int,
    seed: int = 0,
    themes: list[str] | None = None,
    *,
    batch_size: int = 8,
    concurrency: int = 3,
) -> list[FixtureAttendee]:
    """Ask the LLM for personas in small batches. Invalid people are dropped, not repaired.

    LLM output differs on every call, so IDs are unique per call. Only the offline
    generator uses seed-based IDs, because it reproduces the same people for a seed.
    """
    chosen = [THEMES_BY_KEY[key] for key in themes] if themes else THEMES
    theme_list = "\n".join(f"- {theme.key}: {theme.title}" for theme in chosen)
    limit = asyncio.Semaphore(concurrency)

    async def batch(index: int, size: int) -> list[dict[str, Any]]:
        async with limit:
            data = await client.generate_json(
                system=PERSONA_SYSTEM_PROMPT,
                user=PERSONA_USER_PROMPT.format(
                    count=size, seed=f"{seed}-{index}", themes=theme_list
                ),
                schema_name="attendees",
                schema=PERSONA_SCHEMA,
                max_tokens=600 * size,
                timeout_seconds=120,
            )
        people = data.get("attendees")
        if not isinstance(people, list):
            raise LLMError("The persona response had no attendees list.")
        return [person for person in people if isinstance(person, dict)][:size]

    sizes = [min(batch_size, count - start) for start in range(0, count, batch_size)]
    batches = await asyncio.gather(*(batch(index, size) for index, size in enumerate(sizes)))
    people: list[FixtureAttendee] = []
    seen_names: set[str] = set()
    call_id = uuid4().hex[:8]
    for raw in (person for group in batches for person in group):
        persona = persona_from_llm(raw, f"llm-{call_id}-{len(people) + 1:03}")
        if persona is None or persona.attendee.name.lower() in seen_names:
            continue
        seen_names.add(persona.attendee.name.lower())
        people.append(persona)
    return people


def fixture_document(
    people: list[FixtureAttendee], description: str, seats_per_table: int = 5
) -> dict[str, Any]:
    """The same JSON shape as fixtures/three-tables.json, with a suggested layout."""
    table_count = max(1, -(-len(people) // seats_per_table))
    return {
        "description": description,
        "tables": [
            {"number": number, "seats": seats_per_table} for number in range(1, table_count + 1)
        ],
        "attendees": [
            {
                "id": person.attendee.id,
                "theme": person.theme,
                **person.attendee.model_dump(
                    exclude={"id", "conference_id", "lunch_slot", "topic_tags"}
                ),
            }
            for person in people
        ],
    }


def dumps_fixture(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"
