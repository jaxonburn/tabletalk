from tabletalk_shared.models import Attendee, Enrollment, Goal

# Step 1: Name, what you do, interests, and goals.
# Step 2: Choose today's focus goal and answer a prompt for each selected goal.
# Each prompt includes an example hint to encourage specific, useful answers.
GOAL_PROMPTS: dict[Goal, dict[str, str]] = {
    "Learn": {
        "label": "Learn something",
        "short": "Questions & fresh ideas",
        "question": "What questions do you have about the topics you want to learn?",
        "example_hint": (
            "How do you tune Kubernetes autoscaling for a service with unpredictable traffic?"
        ),
    },
    "Share something I've learned": {
        "label": "Share my expertise",
        "short": "Put your experience to use",
        "question": "What have you learned that you would like to share?",
        "example_hint": (
            "I've tuned Kubernetes autoscaling for a service with unpredictable traffic."
        ),
    },
    "Meet connections": {
        "label": "Meet new people",
        "short": "Good company, no pitch",
        "question": "What topics would you be happy to discuss with new people?",
        "example_hint": (
            "I'd enjoy comparing platform engineering lessons and hearing how other teams work."
        ),
    },
}

SEED_PROFILES = [
    Enrollment(
        name="Alex Chen",
        role="Platform engineer",
        interests=["kubernetes", "platform engineering"],
        goals=["Learn"],
        focus_goal="Learn",
        goal_answers={
            "Learn": (
                "How do you use distributed tracing to find the cause of slow requests "
                "between services running on Kubernetes?"
            ),
        },
        can_help="Kubernetes deployments and platform tooling",
        want_to_learn="Observability and distributed tracing",
    ),
    Enrollment(
        name="Sam Rivera",
        role="SRE",
        interests=["kubernetes", "observability"],
        goals=["Share something I've learned"],
        focus_goal="Share something I've learned",
        goal_answers={
            "Share something I've learned": (
                "I've tuned Kubernetes autoscaling for a service with unpredictable traffic "
                "and used distributed tracing to spot latency before adding more replicas."
            ),
        },
        can_help="Observability and distributed tracing",
        want_to_learn="Kubernetes deployments",
    ),
    Enrollment(
        name="Jordan Patel",
        role="Backend engineer",
        interests=["kubernetes", "python"],
        goals=["Learn", "Share something I've learned"],
        focus_goal="Learn",
        goal_answers={
            "Learn": (
                "How do platform teams make Kubernetes deployments safe and approachable "
                "for developers who mostly write Python?"
            ),
            "Share something I've learned": (
                "I've built Python APIs with explicit timeouts and retries so one slow "
                "dependency doesn't stall every request."
            ),
        },
        can_help="Python APIs and service design",
        want_to_learn="Kubernetes platform tooling",
    ),
    Enrollment(
        name="Taylor Brooks",
        role="Developer advocate",
        interests=["kubernetes", "community"],
        goals=["Share something I've learned", "Meet connections"],
        focus_goal="Meet connections",
        goal_answers={
            "Share something I've learned": (
                "I've run Kubernetes onboarding workshops where beginners shipped a "
                "working service before learning all the cluster terminology."
            ),
            "Meet connections": (
                "I'd enjoy discussing welcoming open-source communities, developer "
                "onboarding, and what makes a first conference less intimidating."
            ),
        },
        can_help="Kubernetes onboarding and community building",
        want_to_learn="Python service design",
    ),
    Enrollment(
        name="Morgan Lee",
        role="Search engineer",
        interests=["elasticsearch", "search"],
        goals=["Share something I've learned"],
        focus_goal="Share something I've learned",
        goal_answers={
            "Share something I've learned": (
                "I've improved Elasticsearch relevance by testing BM25 boosts against "
                "a small set of real queries instead of guessing at weights."
            ),
        },
        can_help="Elasticsearch relevance and keyword search",
        want_to_learn="Embeddings and retrieval evaluation",
    ),
    Enrollment(
        name="Casey Kim",
        role="ML engineer",
        interests=["elasticsearch", "machine learning"],
        goals=["Learn", "Share something I've learned"],
        focus_goal="Share something I've learned",
        goal_answers={
            "Learn": (
                "How do you combine keyword relevance and vector similarity in "
                "Elasticsearch without letting one score dominate?"
            ),
            "Share something I've learned": (
                "I've evaluated embedding models on labeled retrieval examples and "
                "found that a smaller model can win on the queries users actually ask."
            ),
        },
        can_help="Embeddings and retrieval evaluation",
        want_to_learn="Elasticsearch relevance",
    ),
    Enrollment(
        name="Riley Davis",
        role="Data engineer",
        interests=["elasticsearch", "data pipelines"],
        goals=["Learn"],
        focus_goal="Learn",
        goal_answers={
            "Learn": (
                "How do you measure whether a change to an Elasticsearch indexing "
                "pipeline improves search quality rather than just indexing speed?"
            ),
        },
        can_help="Data pipelines and indexing",
        want_to_learn="Keyword search and retrieval evaluation",
    ),
    Enrollment(
        name="Jamie Wilson",
        role="Backend engineer",
        interests=["elasticsearch", "python"],
        goals=["Learn", "Meet connections"],
        focus_goal="Meet connections",
        goal_answers={
            "Learn": (
                "How do you keep Elasticsearch indexes up to date when upstream "
                "events arrive late or are delivered more than once?"
            ),
            "Meet connections": (
                "I'd like to compare Python service design, data pipeline mishaps, "
                "and practical ways small teams operate Elasticsearch."
            ),
        },
        can_help="Python APIs and Elasticsearch indexing",
        want_to_learn="Data pipelines",
    ),
    Enrollment(
        name="Avery Thompson",
        role="Frontend engineer",
        interests=["react", "accessibility"],
        goals=["Learn", "Share something I've learned"],
        focus_goal="Learn",
        goal_answers={
            "Learn": (
                "How do you diagnose a React page that feels slow on a phone even "
                "when its API requests finish quickly?"
            ),
            "Share something I've learned": (
                "I've made multi-step React forms work with keyboards and screen "
                "readers by managing focus and announcing validation errors."
            ),
        },
        can_help="React components and accessible forms",
        want_to_learn="Frontend performance",
    ),
    Enrollment(
        name="Quinn Martinez",
        role="Web performance engineer",
        interests=["react", "web performance"],
        goals=["Share something I've learned"],
        focus_goal="Share something I've learned",
        goal_answers={
            "Share something I've learned": (
                "I've used browser interaction metrics and React profiling to "
                "find expensive renders that were hidden by fast API responses."
            ),
        },
        can_help="Frontend performance and browser metrics",
        want_to_learn="Accessible forms",
    ),
    Enrollment(
        name="Drew Robinson",
        role="Product designer",
        interests=["react", "design systems"],
        goals=["Meet connections"],
        focus_goal="Meet connections",
        goal_answers={
            "Meet connections": (
                "I'd be happy discussing accessible design systems, how designers "
                "and React engineers collaborate, and everyone's favorite conference talks."
            ),
        },
        can_help="Design systems and accessible forms",
        want_to_learn="React components",
    ),
    Enrollment(
        name="Skyler Adams",
        role="Full-stack engineer",
        interests=["react", "typescript"],
        goals=["Learn", "Share something I've learned", "Meet connections"],
        focus_goal="Share something I've learned",
        goal_answers={
            "Learn": (
                "How do teams keep a design system consistent across products "
                "without slowing down frontend changes?"
            ),
            "Share something I've learned": (
                "I've used TypeScript discriminated unions to keep React form "
                "states predictable as onboarding flows grow."
            ),
            "Meet connections": (
                "I'd enjoy discussing React architecture, browser metrics, and "
                "the tradeoffs other small teams make when shipping quickly."
            ),
        },
        can_help="React components and TypeScript",
        want_to_learn="Design systems and browser metrics",
    ),
]


def seed_attendees() -> list[Attendee]:
    return [
        Attendee(
            id=f"seed-{index:02}",
            **profile.model_dump(),
        )
        for index, profile in enumerate(SEED_PROFILES, start=1)
    ]
