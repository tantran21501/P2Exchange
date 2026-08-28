#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "rewards.json"
DEFAULT_OUTPUT = ROOT / "data" / "current.json"
DEFAULT_API_BASE = "https://poe.ninja"
DEFAULT_CATEGORIES = ("Currency", "Runes", "Expedition", "Verisium", "UncutGems")
DEFAULT_REFERENCE_CURRENCIES = ("exalted", "divine")
DEFAULT_USER_AGENT = "P2Exchange-POENinjaRewardSnapshot/1.0 (local script)"
AUTO_LEAGUE_KEYS = {"auto", "current", "latest"}
SCHEMA_VERSION = 2
CANONICAL_CATEGORIES = {
    "currency": "Currency",
    "fragments": "Fragments",
    "abyss": "Abyss",
    "uncutgems": "UncutGems",
    "lineagesupportgems": "LineageSupportGems",
    "essences": "Essences",
    "soulcores": "SoulCores",
    "idols": "Idols",
    "runes": "Runes",
    "ritual": "Ritual",
    "expedition": "Expedition",
    "delirium": "Delirium",
    "breach": "Breach",
    "verisium": "Verisium",
}


class PoeNinjaApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class PriceCandidate:
    category: str
    api_id: str
    name: str | None
    details_id: str | None
    line: dict[str, Any]


@dataclass(frozen=True)
class ConversionContext:
    primary_currency: str
    anchor_primary_values: dict[str, float]


def normalize_key(value: Any) -> str:
    text = str(value or "").replace("’", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def certifi_ssl_context() -> ssl.SSLContext | None:
    try:
        import certifi  # type: ignore
    except ImportError:
        return None

    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


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


def parse_list(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,\n]+", raw):
        value = part.strip()
        if not value:
            continue
        key = normalize_key(value)
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def parse_reference_currencies(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[,\s]+", raw):
        value = part.strip().lower()
        if not value:
            continue
        key = normalize_key(value)
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    if not values:
        raise ValueError("At least one reference currency is required")
    return values


def parse_categories(raw: str) -> list[str]:
    categories = [
        CANONICAL_CATEGORIES.get(normalize_key(category), category)
        for category in parse_list(raw)
    ]
    if not categories:
        raise ValueError("At least one poe.ninja category is required")
    if not any(normalize_key(category) == "currency" for category in categories):
        categories.insert(0, "Currency")
    return categories


def league_value(league: dict[str, Any]) -> str:
    value = league.get("id") or league.get("name")
    if not value:
        raise PoeNinjaApiError(f"Cannot resolve league id from {league!r}")
    return str(value)


def league_label(league: dict[str, Any]) -> str | None:
    value = league.get("name") or league.get("id")
    return str(value) if value else None


def select_league(leagues: list[dict[str, Any]], requested: str | None) -> dict[str, Any]:
    if not leagues:
        raise PoeNinjaApiError("poe.ninja returned no leagues")

    if not is_auto_league_request(requested):
        needle = normalize_key(requested)
        for league in leagues:
            values = [league.get("id"), league.get("name")]
            if any(normalize_key(value) == needle for value in values):
                return league

        available = ", ".join(str(item.get("id") or item.get("name") or item) for item in leagues)
        raise PoeNinjaApiError(
            f"Could not find poe.ninja league {requested!r}. Available leagues: {available}"
        )

    for league in leagues:
        value = normalize_key(league.get("id") or league.get("name"))
        if value and value not in {"standard", "hardcore"} and not value.startswith("hc"):
            return league

    for league in leagues:
        if normalize_key(league.get("id") or league.get("name")) == "standard":
            return league

    return leagues[0]


def requested_league(args: argparse.Namespace, rewards_doc: dict[str, Any]) -> str | None:
    return (
        str(args.league or "").strip()
        or str(args.league_name or "").strip()
        or str(rewards_doc.get("league") or "").strip()
        or None
    )


class PoeNinjaClient:
    def __init__(
        self,
        api_base: str,
        user_agent: str,
        timeout: int,
        retries: int,
        opener: Callable[..., Any] | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.opener = opener or urlopen
        self.ssl_context = ssl_context if ssl_context is not None else certifi_ssl_context()

    def url(self, *segments: str, params: dict[str, Any] | None = None) -> str:
        path_segments = ["poe2", "api", "economy", *segments]
        path = "/".join(quote(str(segment).strip("/"), safe="") for segment in path_segments)
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
                if self.opener is urlopen and self.ssl_context is not None:
                    response_context = self.opener(
                        request,
                        timeout=self.timeout,
                        context=self.ssl_context,
                    )
                else:
                    response_context = self.opener(request, timeout=self.timeout)

                with response_context as response:
                    return json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < max(self.retries, 1):
                    time.sleep(min(2**attempt, 8))
        raise PoeNinjaApiError(f"GET failed: {url}: {last_error}")


def fetch_leagues(client: PoeNinjaClient) -> list[dict[str, Any]]:
    payload = client.get_json("leagues")
    if not isinstance(payload, list):
        raise PoeNinjaApiError("poe.ninja leagues response is not an array")
    return [league for league in payload if isinstance(league, dict)]


def fetch_overview(client: PoeNinjaClient, league: str, category: str) -> dict[str, Any]:
    payload = client.get_json(
        "exchange",
        "current",
        "overview",
        params={"league": league, "type": category},
    )
    if not isinstance(payload, dict):
        raise PoeNinjaApiError(f"Unexpected overview payload for {category!r}: {payload!r}")
    if not isinstance(payload.get("lines"), list):
        raise PoeNinjaApiError(f"Overview payload for {category!r} has no lines array")
    return payload


def line_by_id(overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lines = overview.get("lines")
    if not isinstance(lines, list):
        raise PoeNinjaApiError("Overview payload has no lines array")

    result: dict[str, dict[str, Any]] = {}
    for line in lines:
        if not isinstance(line, dict):
            continue
        api_id = str(line.get("id") or "").strip()
        if api_id and api_id not in result:
            result[api_id] = line

    core = overview.get("core") if isinstance(overview.get("core"), dict) else {}
    primary = str(core.get("primary") or "").strip()
    if primary and primary not in result:
        result[primary] = {"id": primary, "primaryValue": 1}

    return result


def overview_items(
    overview: dict[str, Any],
    *,
    include_core_items: bool = False,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    raw_groups = [overview.get("items")]
    if include_core_items:
        core = overview.get("core") if isinstance(overview.get("core"), dict) else {}
        raw_groups.append(core.get("items"))

    for raw_items in raw_groups:
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            key = (
                str(item.get("id") or ""),
                str(item.get("name") or ""),
                str(item.get("detailsId") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
    return result


def candidates_for_category(category: str, overview: dict[str, Any]) -> list[PriceCandidate]:
    lines = line_by_id(overview)
    candidates: list[PriceCandidate] = []
    seen: set[str] = set()

    for item in overview_items(
        overview,
        include_core_items=normalize_key(category) == "currency",
    ):
        api_id = str(item.get("id") or "").strip()
        if not api_id or api_id in seen:
            continue
        seen.add(api_id)
        candidates.append(
            PriceCandidate(
                category=category,
                api_id=api_id,
                name=str(item.get("name")) if item.get("name") is not None else None,
                details_id=str(item.get("detailsId"))
                if item.get("detailsId") is not None
                else None,
                line=lines.get(api_id, {}),
            )
        )

    for api_id, line in lines.items():
        if api_id in seen:
            continue
        seen.add(api_id)
        candidates.append(
            PriceCandidate(
                category=category,
                api_id=api_id,
                name=None,
                details_id=None,
                line=line,
            )
        )

    return candidates


def candidate_match_keys(candidate: PriceCandidate) -> set[str]:
    return {
        key
        for key in (
            normalize_key(candidate.name),
            normalize_key(candidate.api_id),
            normalize_key(candidate.details_id),
        )
        if key
    }


def stable_candidate_id(candidate: PriceCandidate) -> tuple[str, str]:
    return candidate.category, candidate.api_id


def build_price_index(
    overviews_by_category: dict[str, dict[str, Any]],
) -> dict[str, list[PriceCandidate]]:
    index: dict[str, list[PriceCandidate]] = {}
    for category, overview in overviews_by_category.items():
        for candidate in candidates_for_category(category, overview):
            for key in candidate_match_keys(candidate):
                index.setdefault(key, []).append(candidate)
    return index


def dedupe_matches(matches: list[PriceCandidate]) -> list[PriceCandidate]:
    result: list[PriceCandidate] = []
    seen: set[tuple[str, str]] = set()
    for match in matches:
        key = stable_candidate_id(match)
        if key in seen:
            continue
        seen.add(key)
        result.append(match)
    return result


def find_matches(name: str, price_index: dict[str, list[PriceCandidate]]) -> list[PriceCandidate]:
    return dedupe_matches(price_index.get(normalize_key(name), []))


def find_anchor_line(
    currency_overview: dict[str, Any],
    reference_currency: str,
) -> dict[str, Any] | None:
    reference_key = normalize_key(reference_currency)
    lines = line_by_id(currency_overview)

    for api_id, line in lines.items():
        if normalize_key(api_id) == reference_key:
            return line

    for candidate in candidates_for_category("Currency", currency_overview):
        if reference_key in candidate_match_keys(candidate):
            return candidate.line

    return None


def rate_anchor_primary_value(currency_overview: dict[str, Any], reference_currency: str) -> float | None:
    core = currency_overview.get("core") if isinstance(currency_overview.get("core"), dict) else {}
    rates = core.get("rates") if isinstance(core.get("rates"), dict) else {}
    reference_key = normalize_key(reference_currency)

    for key, value in rates.items():
        if normalize_key(key) != reference_key:
            continue
        rate = numeric(value)
        if rate and rate > 0:
            return 1 / rate
    return None


def build_conversion_context(
    currency_overview: dict[str, Any],
    reference_currencies: list[str],
) -> ConversionContext:
    core = currency_overview.get("core") if isinstance(currency_overview.get("core"), dict) else {}
    primary_currency = str(core.get("primary") or "").strip()
    if not primary_currency:
        raise PoeNinjaApiError("Currency overview has no core.primary")

    anchor_primary_values: dict[str, float] = {}
    primary_key = normalize_key(primary_currency)

    for reference_currency in reference_currencies:
        reference_key = normalize_key(reference_currency)
        anchor_value: float | None
        if reference_key == primary_key:
            anchor_value = 1.0
        else:
            anchor_line = find_anchor_line(currency_overview, reference_currency)
            anchor_value = numeric(anchor_line.get("primaryValue")) if anchor_line else None
            if not anchor_value or anchor_value <= 0:
                anchor_value = rate_anchor_primary_value(currency_overview, reference_currency)

        if not anchor_value or anchor_value <= 0:
            raise PoeNinjaApiError(
                f"Cannot resolve {reference_currency!r} conversion from Currency overview"
            )
        anchor_primary_values[reference_currency] = anchor_value

    return ConversionContext(
        primary_currency=primary_currency,
        anchor_primary_values=anchor_primary_values,
    )


def price_in_reference(
    primary_value: float | None,
    reference_currency: str,
    conversion_context: ConversionContext,
) -> float | None:
    if primary_value is None:
        return None
    anchor_value = conversion_context.anchor_primary_values.get(reference_currency)
    if not anchor_value or anchor_value <= 0:
        return None
    return primary_value / anchor_value


def candidate_price(
    candidate: PriceCandidate,
    reference_currency: str,
    conversion_context: ConversionContext,
) -> float | None:
    return price_in_reference(
        numeric(candidate.line.get("primaryValue")),
        reference_currency,
        conversion_context,
    )


def candidate_match_rows(
    candidates: list[PriceCandidate],
    reference_currencies: list[str],
    conversion_context: ConversionContext,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row: dict[str, Any] = {
            "matched_text": candidate.name,
            "matched_api_id": candidate.api_id,
            "matched_category": candidate.category,
        }
        for reference_currency in reference_currencies:
            row[price_field(reference_currency)] = candidate_price(
                candidate,
                reference_currency,
                conversion_context,
            )
        rows.append(row)
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
    price_index: dict[str, list[PriceCandidate]],
    reference_currencies: list[str],
    conversion_context: ConversionContext,
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

    matches = find_matches(name, price_index)
    if not matches:
        row["unpriced_reason"] = "not_found_in_poeninja"
        return row

    if len(matches) > 1:
        row["unpriced_reason"] = "ambiguous_match"
        row["candidate_matches"] = candidate_match_rows(
            matches,
            reference_currencies,
            conversion_context,
        )
        return row

    match = matches[0]
    row.update(
        {
            "source": "poeninja",
            "matched_text": match.name,
            "matched_api_id": match.api_id,
            "matched_category": match.category,
            "item_id": None,
            "currency_item_id": None,
            "current_quantity": integer(match.line.get("quantity")),
            "price_logs": [],
        }
    )
    for reference_currency in reference_currencies:
        row[price_field(reference_currency)] = candidate_price(
            match,
            reference_currency,
            conversion_context,
        )

    if all(row[price_field(reference_currency)] is None for reference_currency in reference_currencies):
        row["unpriced_reason"] = "price_missing_in_poeninja"

    return row


def build_snapshot(
    rewards_doc: dict[str, Any],
    league: dict[str, Any],
    overviews_by_category: dict[str, dict[str, Any]],
    reference_currencies: list[str],
    generated_at: str | None = None,
) -> dict[str, Any]:
    rewards = rewards_doc.get("rewards")
    if not isinstance(rewards, list):
        raise ValueError("Input rewards document must contain a rewards array")

    currency_overview = None
    for category, overview in overviews_by_category.items():
        if normalize_key(category) == "currency":
            currency_overview = overview
            break
    if currency_overview is None:
        raise PoeNinjaApiError("Currency overview is required for Exalted/Divine conversion")

    conversion_context = build_conversion_context(currency_overview, reference_currencies)
    price_index = build_price_index(overviews_by_category)
    rows = [
        match_reward(reward, price_index, reference_currencies, conversion_context)
        for reward in rewards
        if isinstance(reward, dict)
    ]
    fields = [price_field(reference_currency) for reference_currency in reference_currencies]
    ambiguous = sum(
        1
        for row in rows
        if row["unpriced_reason"] in {"ambiguous_match", "ambiguous_level_required"}
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "source": "poeninja",
        "generated_at": generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "league": {
            "value": league_value(league),
            "name": league_label(league),
        },
        "reference_currencies": reference_currencies,
        "stats": {
            "requested": len(rows),
            "priced": sum(1 for row in rows if all(row[field] is not None for field in fields)),
            "unpriced": sum(1 for row in rows if not all(row[field] is not None for field in fields)),
            "ambiguous": ambiguous,
        },
        "categories_fetched": list(overviews_by_category),
        "rewards": rows,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch poe.ninja Exalted and Divine prices for rewards.json."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-base", default=os.getenv("NINJA_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--league", default=os.getenv("NINJA_LEAGUE", ""))
    parser.add_argument("--league-name", default=os.getenv("NINJA_LEAGUE_NAME", ""))
    parser.add_argument(
        "--categories",
        default=os.getenv("NINJA_CATEGORIES", ",".join(DEFAULT_CATEGORIES)),
        help="Comma separated poe.ninja exchange categories.",
    )
    parser.add_argument(
        "--reference-currencies",
        default=os.getenv("NINJA_REFERENCE_CURRENCIES", ",".join(DEFAULT_REFERENCE_CURRENCIES)),
        help="Comma or space separated output reference currencies.",
    )
    parser.add_argument("--user-agent", default=os.getenv("NINJA_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("NINJA_TIMEOUT", "30")))
    parser.add_argument("--retries", type=int, default=int(os.getenv("NINJA_RETRIES", "4")))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    rewards_doc = load_rewards(args.input)
    reference_currencies = parse_reference_currencies(args.reference_currencies)
    categories = parse_categories(args.categories)
    client = PoeNinjaClient(args.api_base, args.user_agent, args.timeout, args.retries)

    leagues = fetch_leagues(client)
    league = select_league(leagues, requested_league(args, rewards_doc))
    selected_league = league_value(league)
    print(f"[INFO] League: {selected_league}", file=sys.stderr)

    overviews_by_category: dict[str, dict[str, Any]] = {}
    for category in categories:
        overview = fetch_overview(client, selected_league, category)
        overviews_by_category[category] = overview
        print(
            f"[INFO] {category}: "
            f"{len(overview.get('items') or [])} items, {len(overview.get('lines') or [])} lines",
            file=sys.stderr,
        )

    snapshot = build_snapshot(
        rewards_doc=rewards_doc,
        league=league,
        overviews_by_category=overviews_by_category,
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
