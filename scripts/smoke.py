"""Exercise a fresh demo stack. This intentionally creates fictional demo data."""

import os

import httpx


def main() -> None:
    with httpx.Client(
        base_url=os.getenv("TABLETALK_SMOKE_URL", "http://localhost:8000"), timeout=90
    ) as client:
        client.get("/health").raise_for_status()
        initial = client.get("/api/conference")
        initial.raise_for_status()
        assert initial.json()["attendees"] == [], "Smoke checks require a fresh stack."
        assert client.post("/api/matches").status_code == 409
        seeded = client.post("/api/demo/seed")
        seeded.raise_for_status()
        assert seeded.json()["created"] == 12
        assert client.post("/api/demo/seed").json()["created"] == 0
        enrolled = client.post(
            "/api/enrollments",
            json={
                "name": "Demo Guest",
                "role": "Engineer",
                "interests": ["kubernetes", "observability"],
                "goals": ["Learn"],
                "can_help": "Python APIs",
                "want_to_learn": "Distributed tracing",
            },
        )
        enrolled.raise_for_status()
        response = client.post("/api/matches")
        response.raise_for_status()
        plan = response.json()
        members = [member for group in plan["groups"] for member in group["members"]]
        assert plan["attendee_count"] == 13
        assert len({member["id"] for member in members}) == 13
        assert sorted(len(group["members"]) for group in plan["groups"]) == [3, 3, 3, 4]
        assert all(group["reasons"] for group in plan["groups"])
        assert client.post("/api/matches").json() == plan
        assert client.post("/api/enrollments", json=enrolled.json()).status_code == 422
        assert (
            client.post(
                "/api/enrollments",
                json={
                    key: value
                    for key, value in enrolled.json().items()
                    if key not in {"id", "conference_id"}
                },
            ).status_code
            == 409
        )
        assert client.get("/api/conference").json()["plan"] == plan
    print("Live demo passed: enrollment, seeding, group allocation, explanations, and idempotence.")


if __name__ == "__main__":
    main()
