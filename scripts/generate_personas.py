"""Generate fictional attendees as a replayable fixture file.

Examples:
    uv run python scripts/generate_personas.py --count 40 --seed 7
    LLM_PROVIDER=anthropic LLM_MODEL=... ANTHROPIC_API_KEY=... \\
        uv run python scripts/generate_personas.py --source llm --count 24 --out personas.json
"""

import argparse
import asyncio
import sys

import httpx
from tabletalk_shared.llm import LLMConfig, create_llm_client
from tabletalk_shared.personas import (
    THEMES_BY_KEY,
    dumps_fixture,
    fixture_document,
    generate_offline,
    generate_with_llm,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--source", choices=["offline", "llm"], default="offline")
    parser.add_argument(
        "--themes",
        default="",
        help=f"Comma-separated theme keys (default: all). Options: {', '.join(THEMES_BY_KEY)}",
    )
    parser.add_argument("--seats", type=int, default=5, help="Seats per suggested table.")
    parser.add_argument("--out", default="-", help="Output path, or - for stdout.")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    themes = [key.strip() for key in args.themes.split(",") if key.strip()] or None
    unknown = [key for key in themes or [] if key not in THEMES_BY_KEY]
    if unknown:
        print(f"Unknown themes: {', '.join(unknown)}", file=sys.stderr)
        return 2
    if args.source == "offline":
        people = generate_offline(args.count, args.seed, themes)
        description = f"Offline personas (seed {args.seed})."
    else:
        config = LLMConfig.from_env()
        async with httpx.AsyncClient() as http:
            client = create_llm_client(config, http)
            if client is None:
                print(
                    f"LLM is not configured: {config.problem or 'LLM_PROVIDER=none'}",
                    file=sys.stderr,
                )
                return 2
            people = await generate_with_llm(client, args.count, args.seed, themes)
        description = f"LLM personas from {config.provider}:{config.model} (seed {args.seed})."
    document = dumps_fixture(fixture_document(people, description, args.seats))
    if args.out == "-":
        sys.stdout.write(document)
    else:
        with open(args.out, "w") as handle:
            handle.write(document)
        print(f"Wrote {len(people)} attendees to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
