#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fetch_poeninja_snapshot as pn


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "snapshots"
DEFAULT_USER_AGENT = "P2Exchange-POENinjaFullCategorySnapshot/1.0 (local script)"
SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_CATEGORIES = (
    "Currency",
    "Fragments",
    "Abyss",
    "UncutGems",
    "LineageSupportGems",
    "Essences",
    "SoulCores",
    "Idols",
    "Runes",
    "Ritual",
    "Expedition",
    "Delirium",
    "Breach",
    "Verisium",
)


def requested_league(args: argparse.Namespace) -> str | None:
    return (
        str(args.league or "").strip()
        or str(args.league_name or "").strip()
        or None
    )


def parse_snapshot_time(raw: str | None) -> datetime:
    value = str(raw or "").strip()
    if not value:
        return datetime.now(timezone.utc)

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid --snapshot-time {raw!r}; expected an ISO timestamp") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def generated_at(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_folder_name(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%d%m%y_%H")


def category_file_name(category: str) -> str:
    value = str(category or "").strip()
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"Unsafe poe.ninja category name: {category!r}")
    return f"{value}.json"


def validate_overview(category: str, overview: dict[str, Any]) -> None:
    if not isinstance(overview, dict):
        raise pn.PoeNinjaApiError(f"Unexpected overview payload for {category!r}: {overview!r}")
    if not isinstance(overview.get("lines"), list):
        raise pn.PoeNinjaApiError(f"Overview payload for {category!r} has no lines array")


def category_counts(overview: dict[str, Any]) -> dict[str, int]:
    items = overview.get("items")
    lines = overview.get("lines")
    return {
        "items": len(items) if isinstance(items, list) else 0,
        "lines": len(lines) if isinstance(lines, list) else 0,
    }


def build_manifest(
    league: dict[str, Any],
    timestamp: datetime,
    overviews_by_category: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source": "poeninja",
        "generated_at": generated_at(timestamp),
        "snapshot_folder": snapshot_folder_name(timestamp),
        "league": {
            "value": pn.league_value(league),
            "name": pn.league_label(league),
        },
        "categories_fetched": list(overviews_by_category),
        "category_counts": {
            category: category_counts(overview)
            for category, overview in overviews_by_category.items()
        },
    }


def fetch_category_overviews(
    client: pn.PoeNinjaClient,
    league: str,
    categories: list[str],
) -> dict[str, dict[str, Any]]:
    overviews_by_category: dict[str, dict[str, Any]] = {}
    for category in categories:
        overview = pn.fetch_overview(client, league, category)
        validate_overview(category, overview)
        overviews_by_category[category] = overview
        counts = category_counts(overview)
        print(
            f"[INFO] {category}: {counts['items']} items, {counts['lines']} lines",
            file=sys.stderr,
        )
    return overviews_by_category


def write_snapshot_folder(
    output_dir: Path,
    timestamp: datetime,
    league: dict[str, Any],
    overviews_by_category: dict[str, dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    for category, overview in overviews_by_category.items():
        validate_overview(category, overview)

    snapshot_dir = output_dir / snapshot_folder_name(timestamp)
    for category, overview in overviews_by_category.items():
        pn.write_json(snapshot_dir / category_file_name(category), overview)

    manifest = build_manifest(league, timestamp, overviews_by_category)
    pn.write_json(snapshot_dir / "_manifest.json", manifest)
    return snapshot_dir, manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch raw poe.ninja PoE2 exchange category snapshots."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--api-base", default=os.getenv("NINJA_API_BASE", pn.DEFAULT_API_BASE))
    parser.add_argument("--league", default=os.getenv("NINJA_LEAGUE", ""))
    parser.add_argument("--league-name", default=os.getenv("NINJA_LEAGUE_NAME", ""))
    parser.add_argument(
        "--categories",
        default=os.getenv("NINJA_CATEGORIES", ",".join(DEFAULT_CATEGORIES)),
        help="Comma separated poe.ninja exchange categories.",
    )
    parser.add_argument(
        "--snapshot-time",
        default=os.getenv("NINJA_SNAPSHOT_TIME", ""),
        help="ISO timestamp used for deterministic UTC snapshot folder names.",
    )
    parser.add_argument("--user-agent", default=os.getenv("NINJA_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("NINJA_TIMEOUT", "30")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("NINJA_RETRIES", "4")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = parse_snapshot_time(args.snapshot_time)
    categories = pn.parse_categories(args.categories)
    client = pn.PoeNinjaClient(args.api_base, args.user_agent, args.timeout, args.retries)

    leagues = pn.fetch_leagues(client)
    league = pn.select_league(leagues, requested_league(args))
    selected_league = pn.league_value(league)
    print(f"[INFO] League: {selected_league}", file=sys.stderr)
    print(
        f"[INFO] Snapshot folder: {snapshot_folder_name(timestamp)} UTC",
        file=sys.stderr,
    )

    overviews_by_category = fetch_category_overviews(client, selected_league, categories)
    snapshot_dir, manifest = write_snapshot_folder(
        args.output_dir,
        timestamp,
        league,
        overviews_by_category,
    )
    print(
        "[OK] "
        f"wrote {len(manifest['categories_fetched'])} category snapshots to {snapshot_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
