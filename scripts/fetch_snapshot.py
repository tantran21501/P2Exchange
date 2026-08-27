#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
REWARDS = DATA / "rewards.json"

NINJA_BASE = os.getenv(
    "NINJA_API_BASE", "https://poe.ninja/poe2/api/economy"
).rstrip("/")
NINJA_LEAGUE = os.getenv("NINJA_LEAGUE", "").strip()
NINJA_LEAGUE_NAME = os.getenv("NINJA_LEAGUE_NAME", "").strip()
NINJA_HARDCORE = os.getenv("NINJA_HARDCORE", "false").lower() in {"1", "true", "yes"}
UA = os.getenv(
    "NINJA_USER_AGENT",
    "POE2-Expedition-Radar-CurrencySnapshot/4.0 (github.com/tantran21501/P2Exchange)",
)
TIMEOUT = int(os.getenv("NINJA_TIMEOUT", "30"))
RETRIES = int(os.getenv("NINJA_RETRIES", "4"))
RETENTION = int(os.getenv("SNAPSHOT_RETENTION_DAYS", "30"))

CATEGORY_BY_TYPE = {
    "currency": "Currency",
    "runes": "Runes",
    "alloys": "Runes",
    "gems": "UncutGems",
    "expedition": "Expedition",
    "verisium": "Verisium",
}


def norm(value: object) -> str:
    s = str(value or "").replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def get_json(path: str, params: dict[str, str] | None = None):
    url = f"{NINJA_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urlencode(params)
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
                },
                method="GET",
            )
            with urlopen(req, timeout=TIMEOUT) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            print(f"[WARN] GET failed: {url}: {exc}", file=sys.stderr)
            if attempt + 1 < RETRIES:
                time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"GET failed: {url}: {last}")


def load_rewards() -> list[dict]:
    data = json.loads(REWARDS.read_text(encoding="utf-8"))
    rewards = data.get("rewards", [])
    if not isinstance(rewards, list) or not rewards:
        raise RuntimeError("data/rewards.json contains no rewards")
    return rewards


def discover_league() -> tuple[str, str]:
    """Resolve league ID. Explicit NINJA_LEAGUE wins; otherwise discover by name/current league."""
    if NINJA_LEAGUE:
        leagues = get_json("leagues")
        for league in leagues if isinstance(leagues, list) else []:
            if isinstance(league, dict) and str(league.get("id", "")) == NINJA_LEAGUE:
                return str(league["id"]), str(league.get("name") or league["id"])
        # Allow direct slug/id even if discovery response is temporarily odd.
        return NINJA_LEAGUE, NINJA_LEAGUE_NAME or NINJA_LEAGUE

    leagues = get_json("leagues")
    if not isinstance(leagues, list) or not leagues:
        raise RuntimeError("poe.ninja returned no economy leagues")

    # Explicit human-readable league name.
    if NINJA_LEAGUE_NAME:
        target = norm(NINJA_LEAGUE_NAME)
        for league in leagues:
            if not isinstance(league, dict):
                continue
            if norm(league.get("name")) == target or norm(league.get("id")) == target:
                return str(league["id"]), str(league.get("name") or league["id"])
        raise RuntimeError(f"Could not find poe.ninja league named {NINJA_LEAGUE_NAME!r}")

    # Current temporary league is documented as first entry. For HC, prefer an HC id/name.
    candidates = [x for x in leagues if isinstance(x, dict)]
    if NINJA_HARDCORE:
        for league in candidates:
            lid = str(league.get("id", ""))
            name = str(league.get("name", ""))
            if "hc" in lid.lower() or "hardcore" in name.lower():
                return lid, name
    first = candidates[0]
    return str(first["id"]), str(first.get("name") or first["id"])


def metadata_items(payload: dict) -> dict[str, dict]:
    """Normalize core.items, which may be an array or an object keyed by id."""
    core = payload.get("core") or {}
    raw = core.get("items") or []
    result: dict[str, dict] = {}

    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, dict):
                result[str(key)] = value
                item_id = value.get("id") or value.get("apiId") or value.get("api_id")
                if item_id is not None:
                    result[str(item_id)] = value
    elif isinstance(raw, list):
        for value in raw:
            if not isinstance(value, dict):
                continue
            item_id = value.get("id") or value.get("apiId") or value.get("api_id")
            if item_id is not None:
                result[str(item_id)] = value

    return result


def line_name(line: dict, items: dict[str, dict]) -> str:
    item_id = line.get("id")
    meta = items.get(str(item_id), {})
    return str(
        line.get("name")
        or line.get("text")
        or meta.get("name")
        or meta.get("text")
        or ""
    )


def build_index(payload: dict) -> dict[str, dict]:
    items = metadata_items(payload)
    result: dict[str, dict] = {}
    for line in payload.get("lines", []) or []:
        if not isinstance(line, dict):
            continue
        name = line_name(line, items)
        if not name:
            continue
        row = dict(line)
        row["name"] = name
        result[norm(name)] = row
        # Also index the stable line id; useful for reference currencies.
        if line.get("id"):
            result.setdefault(norm(line["id"]), row)
    return result


def numeric(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def primary_value(row: dict) -> float | None:
    return numeric(row.get("primaryValue"))


def fetch_category(category: str, league_id: str) -> dict:
    print(f"[INFO] Fetching poe.ninja category: {category}")
    return get_json(
        "exchange/current/overview",
        {"league": league_id, "type": category},
    )


def find_anchor(index: dict[str, dict], aliases: list[str]) -> tuple[float | None, str | None]:
    for alias in aliases:
        row = index.get(norm(alias))
        if row:
            value = primary_value(row)
            if value is not None:
                return value, row.get("name")
    return None, None


def derive_reference(payload: dict, currency_index: dict[str, dict]) -> dict:
    """Return a conversion graph without assuming primary is Exalted or Divine.

    primaryValue is quoted in core.primary. Therefore the primaryValue of Exalted
    is 'Exalted in primary units', and the primaryValue of Divine is likewise.
    For any reward quoted in primary units:
        reward_exalted = reward_primary / exalted_primary_value
        reward_divine  = reward_primary / divine_primary_value
    This remains valid regardless of which reference currency is primary.
    """
    core = payload.get("core") or {}
    primary = str(core.get("primary") or "").strip()
    secondary = str(core.get("secondary") or "").strip()

    exalted_value, exalted_name = find_anchor(
        currency_index, ["Exalted Orb", "Exalted", "exalted-orb", "exalted"]
    )
    divine_value, divine_name = find_anchor(
        currency_index, ["Divine Orb", "Divine", "divine-orb", "divine"]
    )

    # Reference currency itself is exactly 1 unit of primary.
    # This is a useful fallback when its line is omitted.
    if norm(primary) in {"divine", "divineorb", "divineorb"}:
        divine_value = 1.0
        divine_name = divine_name or "Divine Orb"
    elif norm(primary) in {"exalted", "exaltedorb"}:
        exalted_value = 1.0
        exalted_name = exalted_name or "Exalted Orb"

    if not exalted_value or not divine_value:
        # core.rates is currency-id -> units of that currency per 1 primary
        # reference currency. Therefore the primaryValue-equivalent of that
        # currency is 1 / rate. Example: primary=divine and rates.exalted=250
        # means 1 Divine = 250 Exalted, so 1 Exalted = 0.004 Divine.
        rates = core.get("rates") or {}
        rate_map = {}
        if isinstance(rates, dict):
            for key, value in rates.items():
                v = numeric(value)
                if v:
                    rate_map[norm(key)] = v

        def rate_for(aliases: list[str]) -> float | None:
            for alias in aliases:
                v = rate_map.get(norm(alias))
                if v:
                    return v
            return None

        primary_norm = norm(primary)
        if not exalted_value:
            if primary_norm in {"exalted", "exaltedorb"}:
                exalted_value = 1.0
            else:
                rate = rate_for(["exalted", "exalted-orb"])
                if rate:
                    exalted_value = 1.0 / rate

        if not divine_value:
            if primary_norm in {"divine", "divineorb"}:
                divine_value = 1.0
            else:
                rate = rate_for(["divine", "divine-orb"])
                if rate:
                    divine_value = 1.0 / rate

    if not exalted_value or not divine_value:
        raise RuntimeError(
            "Could not derive Exalted/Divine reference values from poe.ninja Currency. "
            f"primary={primary!r} secondary={secondary!r} "
            f"anchors={{'exalted':{exalted_value},'divine':{divine_value}}} "
            f"items={len(metadata_items(payload))} lines={len(payload.get('lines', []) or [])}"
        )

    return {
        "primary": primary,
        "secondary": secondary,
        "exalted_primary_value": exalted_value,
        "divine_primary_value": divine_value,
        "exalted_per_divine": divine_value / exalted_value,
        "divine_per_exalted": exalted_value / divine_value,
        "exalted_name": exalted_name,
        "divine_name": divine_name,
        "rate_source": "primaryValue_anchor_or_core.rates",
    }


def reward_price(row: dict, reference: dict) -> tuple[float | None, float | None]:
    p = primary_value(row)
    if p is None:
        return None, None
    return (
        p / reference["exalted_primary_value"],
        p / reference["divine_primary_value"],
    )


def aliases_for_reward(name: str, rtype: str) -> list[str]:
    aliases = [name]
    if rtype == "gems":
        for suffix in (" (Level 20)", " (Level 19)", " (Level 18)", " (Level 17)"):
            aliases.append(name + suffix)
    # Common punctuation/possessive normalization is already handled by norm().
    return aliases


def prune() -> None:
    if RETENTION <= 0 or not SNAPSHOTS.exists():
        return
    cutoff = time.time() - RETENTION * 86400
    for path in SNAPSHOTS.rglob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    rewards = load_rewards()
    league_id, league_name = discover_league()
    print(f"[INFO] League id={league_id} name={league_name}")

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    historical_rel = now.strftime("%Y-%m-%d/%H.json")

    needed_categories = sorted({
        CATEGORY_BY_TYPE[r.get("type", "")]
        for r in rewards
        if r.get("type", "") in CATEGORY_BY_TYPE
    })
    if "Currency" not in needed_categories:
        needed_categories.insert(0, "Currency")

    payloads = {cat: fetch_category(cat, league_id) for cat in needed_categories}
    indexes = {cat: build_index(payload) for cat, payload in payloads.items()}

    reference = derive_reference(payloads["Currency"], indexes["Currency"])
    print(
        "[INFO] Reference: "
        f"primary={reference['primary']} "
        f"exalted_primary={reference['exalted_primary_value']} "
        f"divine_primary={reference['divine_primary_value']} "
        f"exalted_per_divine={reference['exalted_per_divine']}"
    )

    results: list[dict] = []
    failed: list[dict] = []
    seen: set[str] = set()

    for reward in rewards:
        name = str(reward.get("name") or "").strip()
        rtype = str(reward.get("type") or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)

        category = CATEGORY_BY_TYPE.get(rtype)
        if not category:
            failed.append({"name": name, "type": rtype, "error": "unsupported reward type"})
            continue

        index = indexes[category]
        row = None
        for candidate in aliases_for_reward(name, rtype):
            row = index.get(norm(candidate))
            if row:
                break

        if row is None:
            failed.append({
                "name": name,
                "type": rtype,
                "category": category,
                "error": "not found in poe.ninja exchange overview",
            })
            print(f"[MISS] {name} [{category}]")
            continue

        ex, div = reward_price(row, reference)
        results.append({
            "name": name,
            "type": rtype,
            "category": category,
            "matched_name": row.get("name"),
            "line_id": row.get("id"),
            "price_exalted": ex,
            "price_divine": div,
            "volume_primary_value": numeric(row.get("volumePrimaryValue")),
            "max_volume_currency": row.get("maxVolumeCurrency"),
            "max_volume_rate": numeric(row.get("maxVolumeRate")),
            "sparkline": row.get("sparkline"),
        })

    order = {str(r.get("name")): i for i, r in enumerate(rewards)}
    results.sort(key=lambda r: order.get(r["name"], 10**9))

    out = {
        "schema_version": 6,
        "source": "poe.ninja",
        "generated_at": stamp,
        "league": {"id": league_id, "name": league_name},
        "reference": reference,
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
    SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n"
    (DATA / "current.json").write_text(payload, encoding="utf-8")

    hp = SNAPSHOTS / historical_rel
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(payload, encoding="utf-8")

    (DATA / "meta.json").write_text(
        json.dumps({
            "schema_version": 6,
            "source": "poe.ninja",
            "generated_at": stamp,
            "league": {"id": league_id, "name": league_name},
            "current_file": "data/current.json",
            "historical_file": f"data/snapshots/{historical_rel}",
            "reward_count": len(results),
            "failed_count": len(failed),
            "exalted_per_divine": reference["exalted_per_divine"],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prune()
    print(f"[OK] {len(results)}/{len(seen)} rewards priced; {len(failed)} misses")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise
