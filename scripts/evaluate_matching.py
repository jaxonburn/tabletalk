"""Evaluate matching quality against random seating on people with known themes.

Uses the matcher's lexical stand-in for Elasticsearch BM25, so it runs anywhere.
With --semantic it adds real embeddings (EMBEDDING_MODEL + OPENAI_API_KEY) using the
same blend as production; with --judge it asks the LLM judge to score both seatings.

Examples:
    uv run python scripts/evaluate_matching.py
    uv run python scripts/evaluate_matching.py --count 60 --seeds 10 --seats 6
    uv run python scripts/evaluate_matching.py --file personas.json --compare
    uv run python scripts/evaluate_matching.py --file personas.json --semantic --judge
    uv run python scripts/evaluate_matching.py --check   # non-zero exit on regression
"""

import argparse
import asyncio
from statistics import mean

import httpx
from tabletalk_matcher.evaluation import Evaluation, embed_people, evaluate, match_people
from tabletalk_matcher.judge import judge_plan
from tabletalk_shared.fixtures import FixtureAttendee, load_fixture, load_fixture_file
from tabletalk_shared.llm import EmbeddingConfig, LLMConfig, create_embedder, create_llm_client
from tabletalk_shared.models import TableSpec
from tabletalk_shared.personas import THEMES_BY_KEY, generate_offline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=40, help="Generated attendees per seed.")
    parser.add_argument("--seeds", type=int, default=5, help="How many seeds to evaluate.")
    parser.add_argument("--seats", type=int, default=5, help="Seats per table.")
    parser.add_argument("--themes", default="", help="Comma-separated theme keys.")
    parser.add_argument("--file", help="Evaluate a saved fixture file instead of generating.")
    parser.add_argument(
        "--sample", action="store_true", help="Evaluate the bundled 14-person fixture."
    )
    parser.add_argument("--check", action="store_true", help="Fail if matching regresses.")
    parser.add_argument("--semantic", action="store_true", help="Add embeddings (hybrid).")
    parser.add_argument(
        "--compare", action="store_true", help="Report keyword-only and hybrid side by side."
    )
    parser.add_argument("--judge", action="store_true", help="LLM judge: matched vs random.")
    return parser.parse_args()


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def ari(value: float) -> str:
    return f"{value:.2f}"


def print_report(rows: list[Evaluation]) -> None:
    print(
        "| seed | people | tables | avg fit (random) | weakest (random) "
        "| learn coverage | theme purity (random) | theme ARI (random) | nearest match same theme |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        print(
            f"| {row.seed} | {row.attendees} | {'/'.join(map(str, row.table_sizes))} "
            f"| {percent(row.average_fit)} ({percent(row.baseline_average_fit)}) "
            f"| {percent(row.weakest_match)} ({percent(row.baseline_weakest_match)}) "
            f"| {percent(row.learn_coverage)} "
            f"| {percent(row.theme_purity)} ({percent(row.random_theme_purity)}) "
            f"| {ari(row.theme_ari)} ({ari(row.random_theme_ari)}) "
            f"| {percent(row.neighbor_accuracy)} |"
        )
    coverage = [row.learn_coverage for row in rows if row.learn_coverage is not None]
    print(
        f"\nMean: avg fit {percent(mean(r.average_fit for r in rows))} vs "
        f"{percent(mean(r.baseline_average_fit for r in rows))} random · weakest "
        f"{percent(mean(r.weakest_match for r in rows))} vs "
        f"{percent(mean(r.baseline_weakest_match for r in rows))} · learn coverage "
        f"{percent(mean(coverage) if coverage else None)} · theme ARI "
        f"{ari(mean(r.theme_ari for r in rows))} vs {ari(mean(r.random_theme_ari for r in rows))}"
        f" · nearest match same theme {percent(mean(r.neighbor_accuracy for r in rows))}"
    )


def datasets(args: argparse.Namespace) -> list[tuple[int, list[FixtureAttendee], list[TableSpec]]]:
    if args.file or args.sample:
        fixture = load_fixture_file(args.file) if args.file else load_fixture()
        return [(seed, list(fixture.attendees), list(fixture.tables)) for seed in range(args.seeds)]
    themes = [key.strip() for key in args.themes.split(",") if key.strip()] or None
    for key in themes or []:
        if key not in THEMES_BY_KEY:
            raise SystemExit(f"Unknown theme {key!r}. Options: {', '.join(THEMES_BY_KEY)}")
    table_count = -(-args.count // args.seats)
    tables = [TableSpec(number=n, seats=args.seats) for n in range(1, table_count + 1)]
    return [
        (seed, generate_offline(args.count, seed, themes), tables) for seed in range(args.seeds)
    ]


async def run(args: argparse.Namespace) -> list[Evaluation]:
    data = datasets(args)
    semantic = args.semantic or args.compare
    async with httpx.AsyncClient() as http:
        embedder = create_embedder(EmbeddingConfig.from_env(), http) if semantic else None
        if semantic and embedder is None:
            raise SystemExit("Set EMBEDDING_MODEL and OPENAI_API_KEY for --semantic/--compare.")
        cache: dict[str, dict[str, list[float]]] = {}
        rows: list[Evaluation] = []
        for label, use_vectors in (
            [("keyword", False), ("hybrid", True)] if args.compare else [("", semantic)]
        ):
            if label:
                print(f"\n### {label}")
            rows = []
            for seed, people, tables in data:
                vectors = None
                if use_vectors and embedder is not None:
                    missing = [p.attendee for p in people if p.attendee.id not in cache]
                    if missing:
                        cache |= await embed_people(missing, embedder)
                    vectors = cache
                rows.append(evaluate(people, tables, seed, vectors))
            print_report(rows)
            if args.judge:
                await judge_report(data, cache if use_vectors else None, http)
    return rows


async def judge_report(
    data: list[tuple[int, list[FixtureAttendee], list[TableSpec]]],
    vectors: dict[str, dict[str, list[float]]] | None,
    http: httpx.AsyncClient,
) -> None:
    client = create_llm_client(LLMConfig.from_env(), http)
    if client is None:
        raise SystemExit("Set LLM_PROVIDER, LLM_MODEL, and a key for --judge.")
    scores, baselines = [], []
    for seed, people, tables in data:
        plan = match_people([p.attendee for p in people], tables, seed, vectors)
        judgement = await judge_plan(plan, client, seed=seed)
        scores.append(judgement.average or 0)
        baselines.append(judgement.baseline_average or 0)
    print(
        f"LLM judge ({client.model}): matched {mean(scores):.2f}/5 vs "
        f"random {mean(baselines):.2f}/5"
    )


def main() -> int:
    args = parse_args()
    rows = asyncio.run(run(args))

    if args.check:
        failures = []
        if mean(r.average_fit for r in rows) < 1.5 * mean(r.baseline_average_fit for r in rows):
            failures.append("average fit is not at least 1.5x random seating")
        if mean(r.weakest_match for r in rows) <= mean(r.baseline_weakest_match for r in rows):
            failures.append("the weakest match is no better than random seating")
        if mean(r.theme_ari for r in rows) < mean(r.random_theme_ari for r in rows) + 0.2:
            failures.append("tables do not regroup themes clearly better than random")
        for failure in failures:
            print(f"CHECK FAILED: {failure}")
        if failures:
            return 1
        print("CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
