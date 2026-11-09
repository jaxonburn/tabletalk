# Review TableTalk changes in context

This is a React/TypeScript + Python/FastAPI monorepo for assigning tech-conference
attendees to lunch groups. Read `agent.md` for boundaries and commands.

- Preserve Elasticsearch as the core data and matching layer.
- Enrollment and matching must respect conference and lunch-slot boundaries.
- Never assign someone twice in one lunch plan or silently overwrite a plan.
- Keep balanced group capacity and clear handling of fewer than two attendees.
- Validate names, topics, goals, and free text on the server.
- Keep personal data and enrollment answers out of logs, spans, and metric labels.
- Surface upstream errors explicitly in API responses and frontend alerts.
- Keep source hot reload functional for both services and shared Python code.
- Call out changes that break the documented single-worker mutation assumption.
- Add focused tests for actual behavior changes and run the repository CI checks.
