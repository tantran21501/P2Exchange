#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "rewards.json"
DEFAULT_OUTPUT = ROOT / "data" / "current.json"
DEFAULT_API_BASE = "https://api.poe2scout.com"
DEFAULT_REALM = "poe2"
DEFAULT_REFERENCE_CURRENCY = "exalted"
DEFAULT_USER_AGENT = "P2Exchange-POE2ScoutRewardSnapshot/1.0 (local script)"
SCHEMA_VERSION = 1


class ScoutApiError(RuntimeError):
    pass


def normalize_key(value: Any) -> str:
    text = str(value or "").replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_non_deterministic_reward(name: str) -> bool:
    return bool(re.search(r"\brandom\b", name, flags=re.IGNORECASE))


def is_generic_uncut_gem(name: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*Uncut\s+(?:Skill|Spirit|Support)\s+Gem\s*",
            name,
            flags=re.IGNORECASE,
        )
    )


def load_rewards(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    rewards = data.get("rewards")
    if not isinstance(rewards, list) or not rewards:
        raise ValueError(f"{path} does not contain a non-empty rewards array")
    return data


def select_league(leagues: list[dict[str, Any]], requested: str | None) -> dict[str, Any]:
    if not leagues:
        raise ScoutApiError("POE2 Scout returned no leagues")

    if requested:
        needle = normalize_key(requested)
        for league in leagues:
            values = [
                league.get("Value"),
                league.get("ShortName"),
                league.get("Label"),
                league.get("name"),
                league.get("id"),
            ]
            if any(normalize_key(value) == needle for value in values):
                return league

        available = ", ".join(str(item.get("Value") or item) for item in leagues)
        raise ScoutApiError(
            f"Could not find POE2 Scout league {requested!r}. Available leagues: {available}"
        )

    current = [
        league
        for league in leagues
        if league.get("IsCurrent") and not str(league.get("Value", "")).lower().startswith("hc ")
    ]
    if current:
        return current[0]

    for league in leagues:
        if league.get("IsCurrent"):
            return league

    return leagues[0]


def league_value(league: dict[str, Any]) -> str:
    value = league.get("Value") or league.get("name") or league.get("id")
    if not value:
        raise ScoutApiError(f"Cannot resolve league value from {league!r}")
    return str(value)


def currency_category_ids(categories_payload: dict[str, Any]) -> list[str]:
    categories = categories_payload.get("CurrencyCategories")
    if not isinstance(categories, list):
        raise ScoutApiError("POE2 Scout categories response has no CurrencyCategories array")

    ids: list[str] = []
    seen: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            continue
        api_id = str(category.get("ApiId") or "").strip()
        if api_id and api_id not in seen:
            seen.add(api_id)
            ids.append(api_id)
    if not ids:
        raise ScoutApiError("POE2 Scout returned no currency category ids")
    return ids


def stable_candidate_id(category: str, item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        category,
        str(item.get("CurrencyItemId") or ""),
        str(item.get("ItemId") or ""),
        str(item.get("ApiId") or item.get("Text") or ""),
    )


def item_match_keys(item: dict[str, Any]) -> set[str]:
    metadata = item.get("ItemMetadata") if isinstance(item.get("ItemMetadata"), dict) else {}
    raw_values = [
        item.get("Text"),
        item.get("ApiId"),
        metadata.get("name"),
        metadata.get("base_type"),
    ]
    return {normalize_key(value) for value in raw_values if normalize_key(value)}


def build_price_index(category_items: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for category, items in category_items.items():
        for item in items:
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            enriched["_matched_category"] = category
            for key in item_match_keys(item):
                index.setdefault(key, []).append(enriched)
    return index


def dedupe_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for match in matches:
        candidate_id = stable_candidate_id(str(match.get("_matched_category") or ""), match)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        result.append(match)
    return result


def compact_price_logs(item: dict[str, Any]) -> list[dict[str, Any]]:
    logs = item.get("PriceLogs")
    if not isinstance(logs, list):
        return []

    result: list[dict[str, Any]] = []
    for log in logs:
        if not isinstance(log, dict):
            continue
        result.append(
            {
                "price_exalted": numeric(log.get("Price")),
                "time": log.get("Time"),
                "quantity": integer(log.get("Quantity")),
            }
        )
    return result


def match_reward(
    reward: dict[str, Any],
    price_index: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    name = str(reward.get("name") or "").strip()
    reward_type = str(reward.get("type") or "").strip()
    base_row = {
        "name": name,
        "type": reward_type,
        "price_exalted": None,
        "source": None,
        "matched_text": None,
        "matched_api_id": None,
        "matched_category": None,
        "item_id": None,
        "currency_item_id": None,
        "current_quantity": None,
        "price_logs": [],
        "unpriced_reason": None,
    }

    if not name:
        base_row["unpriced_reason"] = "missing_reward_name"
        return base_row

    if is_non_deterministic_reward(name):
        base_row["unpriced_reason"] = "non_deterministic_reward"
        return base_row

    if is_generic_uncut_gem(name):
        base_row["unpriced_reason"] = "ambiguous_level_required"
        return base_row

    matches = dedupe_matches(price_index.get(normalize_key(name), []))
    if not matches:
        base_row["unpriced_reason"] = "not_found_in_poe2scout"
        return base_row

    if len(matches) > 1:
        base_row["unpriced_reason"] = "ambiguous_match"
        base_row["candidate_matches"] = [
            {
                "matched_text": match.get("Text"),
                "matched_api_id": match.get("ApiId"),
                "matched_category": match.get("_matched_category"),
                "item_id": integer(match.get("ItemId")),
                "currency_item_id": integer(match.get("CurrencyItemId")),
                "price_exalted": numeric(match.get("CurrentPrice")),
            }
            for match in matches
        ]
        return base_row

    match = matches[0]
    price = numeric(match.get("CurrentPrice"))
    if price is None:
        base_row["unpriced_reason"] = "price_missing_in_poe2scout"
        return base_row

    base_row.update(
        {
            "price_exalted": price,
            "source": "poe2scout",
            "matched_text": match.get("Text"),
            "matched_api_id": match.get("ApiId"),
            "matched_category": match.get("_matched_category"),
            "item_id": integer(match.get("ItemId")),
            "currency_item_id": integer(match.get("CurrencyItemId")),
            "current_quantity": integer(match.get("CurrentQuantity")),
            "price_logs": compact_price_logs(match),
            "unpriced_reason": None,
        }
    )
    return base_row


def build_snapshot(
    rewards_doc: dict[str, Any],
    league: dict[str, Any],
    category_items: dict[str, list[dict[str, Any]]],
    reference_currency: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    rewards = rewards_doc.get("rewards") or []
    price_index = build_price_index(category_items)
    rows = [match_reward(reward, price_index) for reward in rewards]
    ambiguous = sum(
        1
        for row in rows
        if row["unpriced_reason"] in {"ambiguous_match", "ambiguous_level_required"}
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "poe2scout",
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league": {
            "value": league_value(league),
            "short_name": league.get("ShortName"),
            "is_current": league.get("IsCurrent"),
        },
        "reference_currency": reference_currency,
        "stats": {
            "requested": len(rows),
            "priced": sum(1 for row in rows if row["price_exalted"] is not None),
            "unpriced": sum(1 for row in rows if row["price_exalted"] is None),
            "ambiguous": ambiguous,
        },
        "categories_fetched": sorted(category_items),
        "rewards": rows,
    }


class ScoutClient:
    def __init__(
        self,
        api_base: str,
        user_agent: str,
        timeout: int,
        retries: int,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries

    def url(self, *segments: str, params: dict[str, Any] | None = None) -> str:
        path = "/".join(quote(str(segment).strip("/"), safe="") for segment in segments)
        url = f"{self.api_base}/{path}"
        if params:
            url += "?" + urlencode(params)
        return url

    def get_json(self, *segments: str, params: dict[str, Any] | None = None) -> Any:
        url = self.url(*segments, params=params)
        last_error: Exception | None = None
        for attempt in range(max(self.retries, 1)):
            try:
                request = Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": self.user_agent,
                    },
                    method="GET",
                )
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < max(self.retries, 1):
                    time.sleep(min(2**attempt, 8))
        raise ScoutApiError(f"GET failed: {url}: {last_error}")


def fetch_currency_category(
    client: ScoutClient,
    realm: str,
    league: str,
    category: str,
    reference_currency: str,
    per_page: int,
    data_points: int,
    frequency_hours: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    pages = 1

    while page <= pages:
        payload = client.get_json(
            realm,
            "Leagues",
            league,
            "Currencies",
            "ByCategory",
            params={
                "category": category,
                "page": page,
                "perPage": per_page,
                "dataPoints": data_points,
                "frequencyHours": frequency_hours,
                "referenceCurrency": reference_currency,
            },
        )
        if not isinstance(payload, dict):
            raise ScoutApiError(f"Unexpected category payload for {category!r}: {payload!r}")

        pages = integer(payload.get("Pages")) or 0
        raw_items = payload.get("Items") or []
        if not isinstance(raw_items, list):
            raise ScoutApiError(f"Category {category!r} response has no Items array")
        items.extend(item for item in raw_items if isinstance(item, dict))
        if pages == 0:
            break
        page += 1

    return items


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch POE2 Scout Exalted prices for rewards.json."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-base", default=os.getenv("SCOUT_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--realm", default=os.getenv("SCOUT_REALM", DEFAULT_REALM))
    parser.add_argument("--league", default=os.getenv("SCOUT_LEAGUE", ""))
    parser.add_argument("--user-agent", default=os.getenv("SCOUT_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument(
        "--reference-currency",
        default=os.getenv("SCOUT_REFERENCE_CURRENCY", DEFAULT_REFERENCE_CURRENCY),
    )
    parser.add_argument("--timeout", type=int, default=int(os.getenv("SCOUT_TIMEOUT", "30")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("SCOUT_RETRIES", "4")))
    parser.add_argument("--per-page", type=int, default=int(os.getenv("SCOUT_PER_PAGE", "100")))
    parser.add_argument("--data-points", type=int, default=int(os.getenv("SCOUT_DATA_POINTS", "7")))
    parser.add_argument(
        "--frequency-hours",
        type=int,
        default=int(os.getenv("SCOUT_FREQUENCY_HOURS", "24")),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.per_page < 1 or args.per_page > 100:
        raise ValueError("--per-page must be between 1 and 100 for POE2 Scout")

    rewards_doc = load_rewards(args.input)
    requested_league = args.league.strip() or str(rewards_doc.get("league") or "").strip() or None
    client = ScoutClient(args.api_base, args.user_agent, args.timeout, args.retries)

    leagues_payload = client.get_json(args.realm, "Leagues")
    if not isinstance(leagues_payload, list):
        raise ScoutApiError("POE2 Scout leagues response is not an array")
    league = select_league(leagues_payload, requested_league)
    selected_league = league_value(league)
    print(f"[INFO] League: {selected_league}", file=sys.stderr)

    categories_payload = client.get_json(args.realm, "Leagues", selected_league, "Items", "Categories")
    if not isinstance(categories_payload, dict):
        raise ScoutApiError("POE2 Scout categories response is not an object")
    category_ids = currency_category_ids(categories_payload)
    print(f"[INFO] Fetching {len(category_ids)} currency categories", file=sys.stderr)

    category_items: dict[str, list[dict[str, Any]]] = {}
    for category_id in category_ids:
        category_items[category_id] = fetch_currency_category(
            client=client,
            realm=args.realm,
            league=selected_league,
            category=category_id,
            reference_currency=args.reference_currency,
            per_page=args.per_page,
            data_points=args.data_points,
            frequency_hours=args.frequency_hours,
        )
        print(
            f"[INFO] {category_id}: {len(category_items[category_id])} items",
            file=sys.stderr,
        )

    snapshot = build_snapshot(
        rewards_doc=rewards_doc,
        league=league,
        category_items=category_items,
        reference_currency=args.reference_currency,
    )
    write_json(args.output, snapshot)
    print(
        "[OK] "
        f"{snapshot['stats']['priced']}/{snapshot['stats']['requested']} rewards priced; "
        f"{snapshot['stats']['unpriced']} unpriced; "
        f"wrote {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
