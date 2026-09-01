#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fetch_scout_snapshot as scout


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOTS_DIR = ROOT / "data" / "snapshots"
DEFAULT_USER_AGENT = "P2Exchange-POE2ScoutPairSnapshot/1.0 (github-actions)"
HUB_IDS = {"divine", "exalted", "chaos"}


def normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def positive(value: Any) -> float | None:
    number = scout.numeric(value)
    return number if number is not None and number > 0 else None


def snapshot_folder(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).strftime("%d%m%y_%H")


def parse_time(value: str | None) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def iso(timestamp: datetime) -> str:
    return timestamp.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_documents(snapshot_dir: Path) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for path in sorted(snapshot_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("lines"), list):
            documents[path.stem] = payload
    if "Currency" not in documents:
        raise ValueError(f"{snapshot_dir} has no Currency.json snapshot")
    return documents


def aliases(document: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in document.get("lines") or []:
        if isinstance(line, dict):
            item_id = str(line.get("id") or "").strip().lower()
            if item_id:
                result[normalized(item_id)] = item_id
    for item in document.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or "").strip().lower()
        if not item_id:
            continue
        for value in (item_id, item.get("detailsId"), item.get("name")):
            if normalized(value):
                result.setdefault(normalized(value), item_id)
    for hub in HUB_IDS:
        result[normalized(hub)] = hub
    return result


def category_for(item: dict[str, Any], documents: dict[str, dict[str, Any]]) -> str | None:
    api_id = normalized(item.get("ApiId"))
    category_id = normalized(item.get("CategoryApiId"))
    text = normalized(item.get("Text"))
    for category, document in documents.items():
        lookup = aliases(document)
        if api_id in lookup or text in lookup:
            return category
    for category in documents:
        if normalized(category) == category_id:
            return category
    return None


def resolved_id(item: dict[str, Any], document: dict[str, Any]) -> str | None:
    api_id = str(item.get("ApiId") or "").strip().lower()
    if api_id in HUB_IDS:
        return api_id
    lookup = aliases(document)
    return lookup.get(normalized(api_id)) or lookup.get(normalized(item.get("Text")))


def directed_pair(source: dict[str, Any], target: dict[str, Any], source_data: dict[str, Any],
                  target_data: dict[str, Any], document: dict[str, Any], observed_at: str) -> dict | None:
    source_id, target_id = resolved_id(source, document), resolved_id(target, document)
    source_price = positive(source_data.get("RelativePrice"))
    target_price = positive(target_data.get("RelativePrice"))
    target_stock = positive(target_data.get("HighestStock"))
    target_volume = positive(target_data.get("VolumeTraded"))
    if not source_id or not target_id or source_id == target_id or not source_price or not target_price:
        return None
    rate = source_price / target_price
    if not target_stock or not target_volume or rate <= 0:
        return None
    return {
        "from": source_id,
        "to": target_id,
        "rate": round(rate, 12),
        "available_from": round(target_stock / rate, 8),
        "available_to": float(target_stock),
        "volume": float(positive(source_data.get("VolumeTraded")) or 0),
        "trade_count": 0,
        "observed_at": observed_at,
        "source": "poe2scout-snapshot-pairs",
        "observed": True,
        "independent": True,
    }


def compact_pairs(raw_pairs: Any, documents: dict[str, dict[str, Any]], observed_at: str) -> dict[str, list[dict]]:
    result = {category: [] for category in documents}
    seen: dict[str, set[tuple[str, str]]] = {category: set() for category in documents}
    for pair in raw_pairs if isinstance(raw_pairs, list) else []:
        if not isinstance(pair, dict):
            continue
        one, two = pair.get("CurrencyOne"), pair.get("CurrencyTwo")
        one_data, two_data = pair.get("CurrencyOneData"), pair.get("CurrencyTwoData")
        if not all(isinstance(value, dict) for value in (one, two, one_data, two_data)):
            continue
        one_api, two_api = str(one.get("ApiId") or "").lower(), str(two.get("ApiId") or "").lower()
        if one_api not in HUB_IDS and two_api not in HUB_IDS:
            continue
        if one_api in HUB_IDS and two_api in HUB_IDS:
            category = "Currency"
        else:
            category = category_for(two if one_api in HUB_IDS else one, documents)
        if not category:
            continue
        document = documents[category]
        for record in (
            directed_pair(one, two, one_data, two_data, document, observed_at),
            directed_pair(two, one, two_data, one_data, document, observed_at),
        ):
            if not record:
                continue
            key = (record["from"], record["to"])
            if key not in seen[category]:
                seen[category].add(key)
                result[category].append(record)
    return result


def write_pairs(snapshot_dir: Path, raw_pairs: Any, observed_at: str) -> dict[str, int]:
    documents = load_documents(snapshot_dir)
    by_category = compact_pairs(raw_pairs, documents, observed_at)
    counts: dict[str, int] = {}
    for category, document in documents.items():
        document["pairs"] = by_category[category]
        path = snapshot_dir / f"{category}.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        counts[category] = len(by_category[category])
    manifest_path = snapshot_dir / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest["pair_books"] = {
        "source": "poe2scout-snapshot-pairs",
        "observed_at": observed_at,
        "categories": counts,
        "total": sum(counts.values()),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add compact POE2 Scout pair books to an hourly snapshot.")
    parser.add_argument("--snapshots-dir", type=Path, default=DEFAULT_SNAPSHOTS_DIR)
    parser.add_argument("--snapshot-time", default=os.getenv("PAIR_SNAPSHOT_TIME", ""))
    parser.add_argument("--league", default=os.getenv("SCOUT_LEAGUE", "current"))
    parser.add_argument("--api-base", default=os.getenv("SCOUT_API_BASE", scout.DEFAULT_API_BASE))
    parser.add_argument("--realm", default=os.getenv("SCOUT_REALM", scout.DEFAULT_REALM))
    parser.add_argument("--user-agent", default=os.getenv("SCOUT_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("SCOUT_TIMEOUT", "30")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("SCOUT_RETRIES", "4")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    timestamp = parse_time(args.snapshot_time)
    client = scout.ScoutClient(args.api_base, args.user_agent, args.timeout, args.retries)
    leagues = client.get_json(args.realm, "Leagues")
    league = scout.select_league(leagues, args.league)
    league_name = scout.league_value(league)
    raw_pairs = client.get_json(args.realm, "Leagues", league_name, "SnapshotPairs")
    target = args.snapshots_dir / snapshot_folder(timestamp)
    counts = write_pairs(target, raw_pairs, iso(timestamp))
    print(f"[OK] wrote {sum(counts.values())} compact directed pair books to {target}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
