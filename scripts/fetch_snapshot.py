#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SNAP = DATA / "snapshots"
REWARDS = DATA / "rewards.json"

NINJA_BASE = os.getenv("NINJA_API_BASE", "https://poe.ninja/poe2/api/economy").rstrip("/")
LEAGUE = os.getenv("SCOUT_LEAGUE", os.getenv("NINJA_LEAGUE", "Runes of Aldur")).strip()
UA = os.getenv(
    "NINJA_USER_AGENT",
    "POE2-Expedition-Radar-CurrencySnapshot/3.0 (github.com/tantran21501/P2Exchange)",
)
TIMEOUT = int(os.getenv("NINJA_TIMEOUT", "30"))
RETRIES = int(os.getenv("NINJA_RETRIES", "4"))
RETENTION = int(os.getenv("SNAPSHOT_RETENTION_DAYS", "30"))

CATEGORY_BY_TYPE = {
    "currency": "Currency",
    "runes": "Runes",
    "alloys": "Runes",
    "gems": "UncutGems",
}


def norm(value: str) -> str:
    value = value.replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def get_json(path: str, params: dict[str, str]) -> dict:
    url = f"{NINJA_BASE}/{path.lstrip('/')}?{urlencode(params)}"
    last = None
    for attempt in range(RETRIES):
        try:
            req = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": UA,
                    "Referer": "https://poe.ninja/",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Cache-Control": "no-cache",
                },
                method="GET",
            )
            with urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if isinstance(exc, HTTPError):
                print(f"[WARN] poe.ninja HTTP {exc.code}: {url}", file=sys.stderr)
            else:
                print(f"[WARN] poe.ninja request failed: {url}: {exc}", file=sys.stderr)
            if attempt + 1 < RETRIES:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"GET failed: {url}: {last}")


def load_rewards() -> list[dict]:
    data = json.loads(REWARDS.read_text(encoding="utf-8"))
    rewards = data.get("rewards", [])
    if not rewards:
        raise RuntimeError("data/rewards.json contains no rewards")
    return rewards


def line_name(line: dict, items: dict) -> str:
    item_id = line.get("id")
    meta = items.get(str(item_id), {}) if isinstance(items, dict) else {}
    return (
        line.get("name")
        or line.get("text")
        or meta.get("name")
        or meta.get("text")
        or ""
    )


def build_index(payload: dict) -> dict[str, dict]:
    items = payload.get("core", {}).get("items", {})
    result = {}
    for line in payload.get("lines", []):
        if not isinstance(line, dict):
            continue
        name = line_name(line, items)
        if not name:
            continue
        row = dict(line)
        row["name"] = name
        result[norm(name)] = row
    return result


def primary_value(row: dict) -> float | None:
    value = row.get("primaryValue")
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def fetch_category(category: str) -> dict:
    print(f"[INFO] Fetching poe.ninja category: {category}")
    return get_json(
        "exchange/current/overview",
        {"league": LEAGUE, "type": category},
    )


def price_row_to_bases(row: dict, primary_name: str, anchors: dict[str, float]) -> tuple[float | None, float | None]:
    p = primary_value(row)
    if p is None:
        return None, None
    # All values in a category are quoted in the same primary reference currency.
    # Convert through the observed Exalted/Divine anchor lines, so this does not
    # assume whether poe.ninja currently uses Exalted or Divine as primary.
    ex_primary = anchors.get("exalted")
    div_primary = anchors.get("divine")
    price_exalted = p / ex_primary if ex_primary else None
    price_divine = p / div_primary if div_primary else None
    return price_exalted, price_divine


def prune() -> None:
    if RETENTION <= 0 or not SNAP.exists():
        return
    cutoff = time.time() - RETENTION * 86400
    for path in SNAP.rglob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    rewards = load_rewards()
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    historical_rel = now.strftime("%Y-%m-%d/%H.json")

    needed_categories = sorted({CATEGORY_BY_TYPE[r.get("type", "")] for r in rewards if r.get("type") in CATEGORY_BY_TYPE})
    payloads = {category: fetch_category(category) for category in needed_categories}
    indexes = {category: build_index(payload) for category, payload in payloads.items()}

    # Currency category is the canonical anchor source for Exalted/Divine.
    currency_index = indexes.get("Currency", {})
    anchors = {}
    for key, aliases in {
        "exalted": ["Exalted Orb", "Exalted"],
        "divine": ["Divine Orb", "Divine"],
    }.items():
        for alias in aliases:
            row = currency_index.get(norm(alias))
            if row:
                value = primary_value(row)
                if value:
                    anchors[key] = value
                    break

    if "exalted" not in anchors or "divine" not in anchors:
        raise RuntimeError(
            f"Could not find Exalted/Divine anchors in poe.ninja Currency category. "
            f"Found anchors={anchors} primary={payloads.get('Currency', {}).get('core', {}).get('primary')}"
        )

    primary_name = payloads["Currency"].get("core", {}).get("primary")
    print(f"[INFO] League={LEAGUE} primary={primary_name} ExaltedPrimary={anchors['exalted']} DivinePrimary={anchors['divine']}")

    results = []
    failed = []
    seen = set()

    for reward in rewards:
        name = reward.get("name", "").strip()
        rtype = reward.get("type", "")
        if not name or not rtype or name in seen:
            continue
        seen.add(name)
        category = CATEGORY_BY_TYPE.get(rtype)
        if not category:
            failed.append({"name": name, "type": rtype, "error": "unsupported reward type"})
            continue

        row = indexes[category].get(norm(name))
        # Small compatibility aliases for labels that can differ between PoE2DB and poe.ninja.
        if row is None and rtype == "gems":
            for suffix in (" (Level 20)", " (Level 19)", " (Level 18)"):
                row = indexes[category].get(norm(name + suffix))
                if row:
                    break
        if row is None:
            failed.append({"name": name, "type": rtype, "category": category, "error": "not found in poe.ninja exchange overview"})
            print(f"[MISS] {name} [{category}]")
            continue

        ex, div = price_row_to_bases(row, primary_name or "", anchors)
        results.append({
            "name": name,
            "type": rtype,
            "category": category,
            "matched_name": row.get("name"),
            "price_exalted": ex,
            "price_divine": div,
            "listing_volume": row.get("volumePrimaryValue"),
            "max_volume_currency": row.get("maxVolumeCurrency"),
            "max_volume_rate": row.get("maxVolumeRate"),
        })

    # Keep the reward order from rewards.json for deterministic client ordering.
    order = {r.get("name"): i for i, r in enumerate(rewards)}
    results.sort(key=lambda r: order.get(r["name"], 10**9))

    out = {
        "schema_version": 5,
        "source": "poe.ninja",
        "generated_at": stamp,
        "league": LEAGUE,
        "reference": {
            "primary": primary_name,
            "exalted_primary_value": anchors["exalted"],
            "divine_primary_value": anchors["divine"],
            "exalted_per_divine": anchors["divine"] / anchors["exalted"],
        },
        "categories_fetched": needed_categories,
        "rewards": results,
        "stats": {
            "requested": len(seen),
            "priced": len(results),
            "failed": len(failed),
        },
    }
    if failed:
        out["failed"] = failed

    DATA.mkdir(parents=True, exist_ok=True)
    SNAP.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n"
    (DATA / "current.json").write_text(payload, encoding="utf-8")
    hp = SNAP / historical_rel
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(payload, encoding="utf-8")
    (DATA / "meta.json").write_text(
        json.dumps({
            "schema_version": 5,
            "source": "poe.ninja",
            "generated_at": stamp,
            "league": LEAGUE,
            "current_file": "data/current.json",
            "historical_file": f"data/snapshots/{historical_rel}",
            "reward_count": len(results),
            "failed_count": len(failed),
            "exalted_per_divine": anchors["divine"] / anchors["exalted"],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prune()
    print(f"[OK] {len(results)}/{len(seen)} rewards priced; {len(failed)} misses; Exalted/Divine={anchors['divine']/anchors['exalted']:.6f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise
