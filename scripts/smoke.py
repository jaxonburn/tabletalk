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
        assert initial.json()["attendee_count"] == 0, "Smoke checks require a fresh stack."
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
        token = {"X-Attendee-Token": enrolled.json()["attendee_token"]}
        assert client.get("/api/me").status_code == 401
        waiting = client.get("/api/me", headers=token).json()
        assert waiting["status"] == "open" and waiting["assignment"] is None
        edit = {
            "name": "Demo Guest",
            "role": "Staff engineer",
            "company": "Demo Co",
            "interests": ["kubernetes", "observability"],
            "goals": ["Learn"],
            "focus_goal": "Learn",
            "goal_answers": {"Learn": "How do you trace requests across services?"},
        }
        edited = client.put("/api/me", json=edit, headers=token)
        edited.raise_for_status()
        assert edited.json()["attendee"]["id"] == enrolled.json()["id"]
        assert edited.json()["attendee"]["company"] == "Demo Co"
        assert client.post("/api/me/check-in", headers=token).status_code == 409
        event = client.get("/api/host/state").json()["event"]
        event |= {"name": "Smoke Lunch", "date": "2026-11-20", "timezone": "America/Denver"}
        client.put("/api/host/event", json=event).raise_for_status()
        assert client.get("/api/conference").json()["name"] == "Smoke Lunch"
        throwaway = client.post("/api/host/runs", json={"seed": 9})
        throwaway.raise_for_status()
        assert client.delete("/api/host/runs").json()["deleted"] == 1
        assert client.get("/api/host/state").json()["runs"] == []
        hello = {"title": "Lunch soon", "body": "Tables open at noon."}
        client.post("/api/host/announcements", json=hello).raise_for_status()
        assert client.get("/api/me", headers=token).json()["announcements"][0]["title"] == (
            "Lunch soon"
        )
        draft = client.post("/api/host/runs", json={"seed": 3})
        draft.raise_for_status()
        run = draft.json()
        assert run["plan"]["attendee_count"] == 13
        draft_state = client.get("/api/conference").json()
        assert draft_state["plan"] is None and draft_state["published"] is False
        state = client.get("/api/host/state").json()
        assert [summary["id"] for summary in state["runs"]] == [run["id"]]
        assert state["status"] == "open"
        response = client.post("/api/matches")
        response.raise_for_status()
        plan = response.json()
        members = [member for group in plan["groups"] for member in group["members"]]
        assert plan["attendee_count"] == 13
        assert len({member["id"] for member in members}) == 13
        assert sorted(len(group["members"]) for group in plan["groups"]) == [3, 3, 3, 4]
        assert all(group["reasons"] for group in plan["groups"])
        assert client.post("/api/matches").json() == plan
        seated = client.get("/api/me", headers=token).json()["assignment"]
        my_table = next(
            group
            for group in plan["groups"]
            if any(member["id"] == enrolled.json()["id"] for member in group["members"])
        )
        assert seated["table_number"] == my_table["table_number"]
        assert len(seated["tablemates"]) == len(my_table["members"]) - 1
        assert seated["table_name"] and len(seated["tagline"]) == 2 and seated["seats"]
        assert client.put("/api/me", json=edit, headers=token).status_code == 409
        checked_in = client.post("/api/me/check-in", headers=token)
        checked_in.raise_for_status()
        assert checked_in.json()["checked_in_at"]
        check_ins = client.get("/api/host/state").json()["check_ins"]
        assert enrolled.json()["id"] in check_ins
        assert client.post("/api/enrollments", json=enrolled.json()).status_code == 422
        assert (
            client.post(
                "/api/enrollments",
                json={
                    key: value
                    for key, value in enrolled.json().items()
                    if key not in {"id", "conference_id", "attendee_token", "topic_tags"}
                },
            ).status_code
            == 409
        )
        public = client.get("/api/conference").json()
        assert public["published"] is True and public["status"] == "published"
        assert public["plan"] is None and public["attendees"] == [], "Public data is aggregate."
        assert client.post(f"/api/host/runs/{run['id']}/publish").status_code == 409
        assert client.put("/api/host/event", json={}).status_code == 409
        assert client.get("/api/host/state").json()["status"] == "published"
    print(
        "Live demo passed: enrollment, edits, event details, announcements, discarding "
        "drafts, attendee tokens, seeding, draft runs, "
        "publishing, personal tables, check-in, group allocation, explanations, and idempotence."
    )


if __name__ == "__main__":
    main()
