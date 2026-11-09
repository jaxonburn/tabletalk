from tabletalk_shared.models import Attendee

SEED_PROFILES = [
    (
        "Alex Chen",
        "Platform engineer",
        ["kubernetes", "platform engineering"],
        "Kubernetes deployments and platform tooling",
        "Observability and distributed tracing",
    ),
    (
        "Sam Rivera",
        "SRE",
        ["kubernetes", "observability"],
        "Observability and distributed tracing",
        "Kubernetes deployments",
    ),
    (
        "Jordan Patel",
        "Backend engineer",
        ["kubernetes", "python"],
        "Python APIs and service design",
        "Kubernetes platform tooling",
    ),
    (
        "Taylor Brooks",
        "Developer advocate",
        ["kubernetes", "community"],
        "Kubernetes onboarding and community building",
        "Python service design",
    ),
    (
        "Morgan Lee",
        "Search engineer",
        ["elasticsearch", "search"],
        "Elasticsearch relevance and keyword search",
        "Embeddings and retrieval evaluation",
    ),
    (
        "Casey Kim",
        "ML engineer",
        ["elasticsearch", "machine learning"],
        "Embeddings and retrieval evaluation",
        "Elasticsearch relevance",
    ),
    (
        "Riley Davis",
        "Data engineer",
        ["elasticsearch", "data pipelines"],
        "Data pipelines and indexing",
        "Keyword search and retrieval evaluation",
    ),
    (
        "Jamie Wilson",
        "Backend engineer",
        ["elasticsearch", "python"],
        "Python APIs and Elasticsearch indexing",
        "Data pipelines",
    ),
    (
        "Avery Thompson",
        "Frontend engineer",
        ["react", "accessibility"],
        "React components and accessible forms",
        "Frontend performance",
    ),
    (
        "Quinn Martinez",
        "Web performance engineer",
        ["react", "web performance"],
        "Frontend performance and browser metrics",
        "Accessible forms",
    ),
    (
        "Drew Robinson",
        "Product designer",
        ["react", "design systems"],
        "Design systems and accessible forms",
        "React components",
    ),
    (
        "Skyler Adams",
        "Full-stack engineer",
        ["react", "typescript"],
        "React components and TypeScript",
        "Design systems and browser metrics",
    ),
]


def seed_attendees() -> list[Attendee]:
    return [
        Attendee(
            id=f"seed-{index:02}",
            name=name,
            role=role,
            interests=interests,
            goals=["Learn", "Meet peers"],
            can_help=can_help,
            want_to_learn=want_to_learn,
        )
        for index, (name, role, interests, can_help, want_to_learn) in enumerate(
            SEED_PROFILES, start=1
        )
    ]
