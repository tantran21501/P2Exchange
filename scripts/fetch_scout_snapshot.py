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
DEFAULT_LEAGUE = "current"
DEFAULT_REFERENCE_CURRENCIES = ("exalted", "divine")
DEFAULT_USER_AGENT = "P2Exchange-POE2ScoutRewardSnapshot/1.0 (local script)"
AUTO_LEAGUE_KEYS = {"auto", "current", "latest"}
SCHEMA_VERSION = 2


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


def price_field(reference_currency: str) -> str:
    return f"price_{normalize_key(reference_currency)}"


def is_auto_league_request(requested: str | None) -> bool:
    return not requested or normalize_key(requested) in AUTO_LEAGUE_KEYS


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

    if not is_auto_league_request(requested):
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

    current_softcore = [
        league
        for league in leagues
        if league.get("IsCurrent") and not str(league.get("Value", "")).lower().startswith("hc ")
    ]
    if current_softcore:
        return current_softcore[0]

    for league in leagues:
        value = normalize_key(league.get("Value"))
        short_name = normalize_key(league.get("ShortName"))
        if value == "standard" or short_name == "standard":
            return league

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


def find_matches(name: str, price_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return dedupe_matches(price_index.get(normalize_key(name), []))


def find_match_by_candidate_id(
    matches: list[dict[str, Any]],
    candidate_id: tuple[str, str, str, str],
) -> dict[str, Any] | None:
    for match in matches:
        match_id = stable_candidate_id(str(match.get("_matched_category") or ""), match)
        if match_id == candidate_id:
            return match
    return None


def candidate_match_rows(
    candidates: list[dict[str, Any]],
    matches_by_reference: dict[str, list[dict[str, Any]]],
    reference_currencies: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = stable_candidate_id(str(candidate.get("_matched_category") or ""), candidate)
        row = {
            "matched_text": candidate.get("Text"),
            "matched_api_id": candidate.get("ApiId"),
            "matched_category": candidate.get("_matched_category"),
            "item_id": integer(candidate.get("ItemId")),
            "currency_item_id": integer(candidate.get("CurrencyItemId")),
        }
        for reference_currency in reference_currencies:
            matched = find_match_by_candidate_id(
                matches_by_reference.get(reference_currency, []),
                candidate_id,
            )
            row[price_field(reference_currency)] = (
                numeric(matched.get("CurrentPrice")) if matched else None
            )
        rows.append(row)
    return rows


def merge_price_logs(
    items_by_reference: dict[str, dict[str, Any]],
    reference_currencies: list[str],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for reference_currency in reference_currencies:
        item = items_by_reference.get(reference_currency)
        logs = item.get("PriceLogs") if isinstance(item, dict) else None
        if not isinstance(logs, list):
            continue

        for log in logs:
            if not isinstance(log, dict):
                continue
            time_value = str(log.get("Time") or "").strip()
            if not time_value:
                continue
            if time_value not in merged:
                merged[time_value] = {
                    "time": time_value,
                    "quantity": integer(log.get("Quantity")),
                }
                order.append(time_value)
            elif merged[time_value].get("quantity") is None:
                merged[time_value]["quantity"] = integer(log.get("Quantity"))
            merged[time_value][price_field(reference_currency)] = numeric(log.get("Price"))

    rows = [merged[time_value] for time_value in order]
    for row in rows:
        for reference_currency in reference_currencies:
            row.setdefault(price_field(reference_currency), None)
    return rows


def empty_reward_row(
    name: str,
    reward_type: str,
    reference_currencies: list[str],
) -> dict[str, Any]:
    row = {
        "name": name,
        "type": reward_type,
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
    for reference_currency in reference_currencies:
        row[price_field(reference_currency)] = None
    return row


def match_reward(
    reward: dict[str, Any],
    price_indexes: dict[str, dict[str, list[dict[str, Any]]]],
    reference_currencies: list[str],
) -> dict[str, Any]:
    name = str(reward.get("name") or "").strip()
    reward_type = str(reward.get("type") or "").strip()
    row = empty_reward_row(name, reward_type, reference_currencies)

    if not name:
        row["unpriced_reason"] = "missing_reward_name"
        return row

    if is_non_deterministic_reward(name):
        row["unpriced_reason"] = "non_deterministic_reward"
        return row

    if is_generic_uncut_gem(name):
        row["unpriced_reason"] = "ambiguous_level_required"
        return row

    matches_by_reference = {
        reference_currency: find_matches(name, price_indexes.get(reference_currency, {}))
        for reference_currency in reference_currencies
    }
    canonical_matches: list[dict[str, Any]] = []
    for reference_currency in reference_currencies:
        canonical_matches = matches_by_reference.get(reference_currency, [])
        if canonical_matches:
            break

    if not canonical_matches:
        row["unpriced_reason"] = "not_found_in_poe2scout"
        return row

    if len(canonical_matches) > 1:
        row["unpriced_reason"] = "ambiguous_match"
        row["candidate_matches"] = candidate_match_rows(
            canonical_matches,
            matches_by_reference,
            reference_currencies,
        )
        return row

    canonical_match = canonical_matches[0]
    canonical_id = stable_candidate_id(
        str(canonical_match.get("_matched_category") or ""),
        canonical_match,
    )
    items_by_reference: dict[str, dict[str, Any]] = {}

    for reference_currency in reference_currencies:
        matches = matches_by_reference.get(reference_currency, [])
        if len(matches) > 1 and not find_match_by_candidate_id(matches, canonical_id):
            row["unpriced_reason"] = "ambiguous_match"
            row["candidate_matches"] = candidate_match_rows(
                matches,
                matches_by_reference,
                reference_currencies,
            )
            return row

        match = find_match_by_candidate_id(matches, canonical_id)
        if match:
            items_by_reference[reference_currency] = match
            row[price_field(reference_currency)] = numeric(match.get("CurrentPrice"))

    row.update(
        {
            "source": "poe2scout",
            "matched_text": canonical_match.get("Text"),
            "matched_api_id": canonical_match.get("ApiId"),
            "matched_category": canonical_match.get("_matched_category"),
            "item_id": integer(canonical_match.get("ItemId")),
            "currency_item_id": integer(canonical_match.get("CurrencyItemId")),
            "current_quantity": integer(canonical_match.get("CurrentQuantity")),
            "price_logs": merge_price_logs(items_by_reference, reference_currencies),
        }
    )

    if all(row[price_field(reference_currency)] is None for reference_currency in reference_currencies):
        row["unpriced_reason"] = "price_missing_in_poe2scout"

    return row


def build_snapshot(
    rewards_doc: dict[str, Any],
    league: dict[str, Any],
    category_items_by_reference: dict[str, dict[str, list[dict[str, Any]]]],
    reference_currencies: list[str],
    generated_at: str | None = None,
) -> dict[str, Any]:
    rewards = rewards_doc.get("rewards") or []
    price_indexes = {
        reference_currency: build_price_index(category_items)
        for reference_currency, category_items in category_items_by_reference.items()
    }
    rows = [match_reward(reward, price_indexes, reference_currencies) for reward in rewards]
    fields = [price_field(reference_currency) for reference_currency in reference_currencies]
    ambiguous = sum(
        1
        for row in rows
        if row["unpriced_reason"] in {"ambiguous_match", "ambiguous_level_required"}
    )
    categories_fetched = sorted(
        {
            category
            for category_items in category_items_by_reference.values()
            for category in category_items
        }
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
        "reference_currencies": reference_currencies,
        "stats": {
            "requested": len(rows),
            "priced": sum(1 for row in rows if all(row[field] is not None for field in fields)),
            "unpriced": sum(1 for row in rows if not all(row[field] is not None for field in fields)),
            "ambiguous": ambiguous,
        },
        "categories_fetched": categories_fetched,
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


def parse_reference_currencies(
    reference_currencies: str,
    reference_currency: str,
) -> list[str]:
    raw = reference_currency.strip() or reference_currencies
    currencies: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,\s]+", raw):
        currency = part.strip().lower()
        if not currency or currency in seen:
            continue
        seen.add(currency)
        currencies.append(currency)
    if not currencies:
        raise ValueError("At least one reference currency is required")
    return currencies


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch POE2 Scout Exalted and Divine prices for rewards.json."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-base", default=os.getenv("SCOUT_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--realm", default=os.getenv("SCOUT_REALM", DEFAULT_REALM))
    parser.add_argument("--league", default=os.getenv("SCOUT_LEAGUE", DEFAULT_LEAGUE))
    parser.add_argument("--user-agent", default=os.getenv("SCOUT_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument(
        "--reference-currencies",
        default=os.getenv("SCOUT_REFERENCE_CURRENCIES", ",".join(DEFAULT_REFERENCE_CURRENCIES)),
        help="Comma or space separated Scout reference currencies.",
    )
    parser.add_argument(
        "--reference-currency",
        default=os.getenv("SCOUT_REFERENCE_CURRENCY", ""),
        help="Deprecated single-currency override.",
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
    requested_league = args.league.strip() or None
    reference_currencies = parse_reference_currencies(
        args.reference_currencies,
        args.reference_currency,
    )
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

    category_items_by_reference: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for reference_currency in reference_currencies:
        print(f"[INFO] Reference currency: {reference_currency}", file=sys.stderr)
        category_items: dict[str, list[dict[str, Any]]] = {}
        for category_id in category_ids:
            category_items[category_id] = fetch_currency_category(
                client=client,
                realm=args.realm,
                league=selected_league,
                category=category_id,
                reference_currency=reference_currency,
                per_page=args.per_page,
                data_points=args.data_points,
                frequency_hours=args.frequency_hours,
            )
            print(
                f"[INFO] {reference_currency}/{category_id}: {len(category_items[category_id])} items",
                file=sys.stderr,
            )
        category_items_by_reference[reference_currency] = category_items

    snapshot = build_snapshot(
        rewards_doc=rewards_doc,
        league=league,
        category_items_by_reference=category_items_by_reference,
        reference_currencies=reference_currencies,
    )
    write_json(args.output, snapshot)
    print(
        "[OK] "
        f"{snapshot['stats']['priced']}/{snapshot['stats']['requested']} rewards fully priced; "
        f"{snapshot['stats']['unpriced']} unpriced or partial; "
        f"wrote {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
