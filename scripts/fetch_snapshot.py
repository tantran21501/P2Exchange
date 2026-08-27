#!/usr/bin/env python3
"""
POE2 Scout Currency Snapshot Collector

Fetches hourly currency market data from POE2 Scout and writes:

    data/current.json
    data/meta.json
    data/snapshots/YYYY-MM-DD/HH.json

The collector:
    1. Resolves the configured POE2 league.
    2. Fetches reference currencies.
    3. Fetches ExchangeSnapshot.
    4. Fetches SnapshotPairs.
    5. Fetches currency prices normalized to Exalted.
    6. Fetches currency prices normalized to Divine.
    7. Merges the two price indexes.
    8. Writes a single JSON file consumed by the Expedition Radar tool.

Environment variables:

    SCOUT_API_BASE
    SCOUT_REALM
    SCOUT_LEAGUE
    SCOUT_USER_AGENT
    SCOUT_TIMEOUT
    SCOUT_RETRIES
    SNAPSHOT_RETENTION_DAYS
"""

from __future__ import annotations

import json
import os
import sys
import time

from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


# ============================================================================
# CONFIGURATION
# ============================================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"

BASE_URL = os.getenv(
    "SCOUT_API_BASE",
    "https://api.poe2scout.com",
).rstrip("/")

REALM = os.getenv(
    "SCOUT_REALM",
    "poe2",
).strip()

LEAGUE = os.getenv(
    "SCOUT_LEAGUE",
    "Runes of Aldur",
).strip()

USER_AGENT = os.getenv(
    "SCOUT_USER_AGENT",
    "POE2-Expedition-Radar-CurrencySnapshot/1.0",
).strip()

TIMEOUT = int(
    os.getenv(
        "SCOUT_TIMEOUT",
        "30",
    )
)

RETRIES = int(
    os.getenv(
        "SCOUT_RETRIES",
        "3",
    )
)

RETENTION_DAYS = int(
    os.getenv(
        "SNAPSHOT_RETENTION_DAYS",
        "30",
    )
)


# ============================================================================
# HTTP
# ============================================================================

def get_json(path: str):
    """
    GET JSON from POE2 Scout.

    Example:

        get_json(
            "poe2/Leagues/Runes%20of%20Aldur/ReferenceCurrencies"
        )
    """

    url = f"{BASE_URL}/{path.lstrip('/')}"

    last_error = None

    for attempt in range(RETRIES):
        try:
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                method="GET",
            )

            with urlopen(
                request,
                timeout=TIMEOUT,
            ) as response:

                status = getattr(
                    response,
                    "status",
                    200,
                )

                body = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

                if status < 200 or status >= 300:
                    raise RuntimeError(
                        f"HTTP {status}: {body[:1000]}"
                    )

                if not body.strip():
                    return None

                return json.loads(body)

        except HTTPError as error:
            last_error = error

            try:
                error_body = error.read().decode(
                    "utf-8",
                    errors="replace",
                )
            except Exception:
                error_body = ""

            print(
                f"[WARN] GET {url} failed "
                f"(attempt {attempt + 1}/{RETRIES}) "
                f"HTTP {error.code}: {error_body[:1000]}",
                file=sys.stderr,
            )

        except (
            URLError,
            TimeoutError,
            json.JSONDecodeError,
            RuntimeError,
        ) as error:

            last_error = error

            print(
                f"[WARN] GET {url} failed "
                f"(attempt {attempt + 1}/{RETRIES}): {error}",
                file=sys.stderr,
            )

        if attempt + 1 < RETRIES:
            time.sleep(2 ** attempt)

    raise RuntimeError(
        f"GET failed: {url}: {last_error}"
    )


# ============================================================================
# GENERIC JSON HELPERS
# ============================================================================

def first_value(obj, keys):
    """
    Recursively find the first non-empty value matching one of the keys.
    """

    if isinstance(obj, dict):

        for key in keys:
            value = obj.get(key)

            if value not in (None, ""):
                return value

        for value in obj.values():

            result = first_value(
                value,
                keys,
            )

            if result is not None:
                return result

    elif isinstance(obj, list):

        for value in obj:

            result = first_value(
                value,
                keys,
            )

            if result is not None:
                return result

    return None


def list_candidates(obj):
    """
    Extract likely list containers from Scout responses.

    Handles responses such as:

        [...]
        {"items": [...]}
        {"data": [...]}
        {"pairs": [...]}
        {"currencies": [...]}
    """

    if isinstance(obj, list):
        return obj

    if not isinstance(obj, dict):
        return []

    preferred_keys = (
        "items",
        "data",
        "pairs",
        "currencies",
        "leagues",
        "results",
        "value",
    )

    for key in preferred_keys:

        value = obj.get(key)

        if isinstance(value, list):
            return value

    for value in obj.values():

        if isinstance(value, list):
            return value

    return []


def numeric_value(value):
    """
    Convert common Scout numeric structures into float.
    """

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):

        try:
            return float(value)
        except ValueError:
            return None

    if isinstance(value, dict):

        for key in (
            "price",
            "value",
            "amount",
            "currentPrice",
            "current_price",
        ):

            if key in value:

                result = numeric_value(
                    value[key]
                )

                if result is not None:
                    return result

    return None


# ============================================================================
# LEAGUE
# ============================================================================

def select_league(league_response):
    """
    Select the configured league.

    If SCOUT_LEAGUE is explicitly set, use it.

    Otherwise:
        current non-HC league
        ↓
        first non-HC league
    """

    if LEAGUE:
        return LEAGUE

    rows = list_candidates(
        league_response
    )

    # Current non-HC league first.
    for row in rows:

        if not isinstance(row, dict):
            continue

        name = first_value(
            row,
            (
                "value",
                "name",
                "id",
            ),
        )

        current = (
            row.get("isCurrent")
            or row.get("is_current")
            or row.get("active")
            or row.get("isActive")
            or row.get("current")
        )

        if (
            current
            and name
            and not str(name).lower().startswith("hc ")
        ):
            return str(name)

    # Fallback: first non-HC league.
    for row in rows:

        if isinstance(row, dict):

            name = first_value(
                row,
                (
                    "value",
                    "name",
                    "id",
                ),
            )

        else:
            name = row

        if (
            name
            and not str(name).lower().startswith("hc ")
        ):
            return str(name)

    raise RuntimeError(
        "Cannot determine softcore league. "
        "Set SCOUT_LEAGUE explicitly."
    )


# ============================================================================
# REFERENCE CURRENCIES
# ============================================================================

def flatten_reference_items(obj):
    """
    Return all dictionary-like reference currency objects.
    """

    result = []

    def walk(value):

        if isinstance(value, dict):

            # A likely reference currency object.
            if (
                any(
                    key in value
                    for key in (
                        "apiId",
                        "api_id",
                        "itemId",
                        "item_id",
                    )
                )
                and any(
                    key in value
                    for key in (
                        "name",
                        "text",
                        "displayName",
                    )
                )
            ):
                result.append(value)

            for child in value.values():
                walk(child)

        elif isinstance(value, list):

            for child in value:
                walk(child)

    walk(obj)

    return result


def resolve_reference_currency(
    references,
    target: str,
):
    """
    Resolve Exalted / Divine API ID from ReferenceCurrencies.

    The function intentionally checks multiple fields because
    Scout payload versions have used different naming conventions.
    """

    target = target.lower().strip()

    objects = flatten_reference_items(
        references
    )

    # Exact / strong match first.
    for item in objects:

        api_id = first_value(
            item,
            (
                "apiId",
                "api_id",
                "itemId",
                "item_id",
                "id",
            ),
        )

        name = first_value(
            item,
            (
                "name",
                "text",
                "displayName",
            ),
        )

        if not api_id:
            continue

        combined = " ".join(
            str(x)
            for x in (
                api_id,
                name,
            )
            if x
        ).lower()

        if target in combined:
            return str(api_id)

    # Recursive fallback.
    api_id = find_string_containing(
        references,
        target,
    )

    if api_id:
        return api_id

    raise RuntimeError(
        f"Cannot resolve reference currency: {target}. "
        f"ReferenceCurrencies response:\n"
        f"{json.dumps(references, ensure_ascii=False, indent=2)[:5000]}"
    )


def find_string_containing(
    obj,
    needle: str,
):
    """
    Recursive fallback search.
    """

    needle = needle.lower()

    if isinstance(obj, dict):

        for key, value in obj.items():

            if isinstance(value, str):

                if needle in value.lower():

                    # Prefer ID-like fields.
                    if key.lower() in (
                        "apiid",
                        "api_id",
                        "itemid",
                        "item_id",
                        "id",
                    ):
                        return value

            result = find_string_containing(
                value,
                needle,
            )

            if result:
                return result

    elif isinstance(obj, list):

        for value in obj:

            result = find_string_containing(
                value,
                needle,
            )

            if result:
                return result

    return None


# ============================================================================
# CURRENCY ENDPOINT
# ============================================================================

def fetch_currency_category(
    root: str,
    reference_currency: str,
):
    """
    Fetch currencies using Scout's required Category parameter.

    IMPORTANT:
        /Currencies/ByCategory without Category returns HTTP 400.

    We use query parameters rather than string concatenation so the
    values are correctly URL encoded.
    """

    query = urlencode(
        {
            "Category": "currency",
            "ReferenceCurrency": reference_currency,
        }
    )

    path = (
        f"{root}/Currencies/ByCategory"
        f"?{query}"
    )

    print(
        f"[INFO] Fetching currency prices "
        f"with reference={reference_currency}"
    )

    return get_json(path)


# ============================================================================
# CURRENCY NORMALIZATION
# ============================================================================

def get_currency_items(obj):
    """
    Extract currency item list.
    """

    if isinstance(obj, dict):

        items = obj.get("items")

        if isinstance(items, list):
            return items

    return list_candidates(obj)


def normalize_currency_items(obj):
    """
    Normalize basic currency metadata.
    """

    result = []

    for item in get_currency_items(obj):

        if not isinstance(item, dict):
            continue

        api_id = first_value(
            item,
            (
                "apiId",
                "api_id",
                "itemId",
                "item_id",
                "id",
            ),
        )

        name = first_value(
            item,
            (
                "text",
                "name",
                "displayName",
            ),
        )

        icon = first_value(
            item,
            (
                "icon",
                "iconUrl",
                "icon_url",
                "image",
            ),
        )

        if not api_id:
            continue

        result.append(
            {
                "api_id": str(api_id),
                "name": str(name) if name else None,
                "icon": str(icon) if icon else None,
            }
        )

    # Deduplicate.
    output = []
    seen = set()

    for item in result:

        key = item["api_id"]

        if key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output


def extract_price_rows(
    obj,
    target_reference: str,
):
    """
    Extract normalized prices from Scout's currency response.

    Scout can expose camelCase or snake_case depending on API version,
    so both are supported.

    The price returned by this function is already normalized to
    target_reference by Scout.
    """

    result = []

    for item in get_currency_items(obj):

        if not isinstance(item, dict):
            continue

        api_id = first_value(
            item,
            (
                "apiId",
                "api_id",
                "itemId",
                "item_id",
                "id",
            ),
        )

        name = first_value(
            item,
            (
                "text",
                "name",
                "displayName",
            ),
        )

        if not api_id:
            continue

        current_price = item.get(
            "currentPrice",
            item.get(
                "current_price"
            ),
        )

        price = numeric_value(
            current_price
        )

        current_quantity = item.get(
            "currentQuantity",
            item.get(
                "current_quantity"
            ),
        )

        result.append(
            {
                "api_id": str(api_id),
                "name": str(name) if name else None,
                "price": price,
                "current_quantity": current_quantity,
                "reference_currency": target_reference,
            }
        )

    # Deduplicate.
    output = []
    seen = set()

    for item in result:

        key = item["api_id"]

        if key in seen:
            continue

        seen.add(key)
        output.append(item)

    return output


# ============================================================================
# PAIRS
# ============================================================================

def normalize_pairs(obj):
    """
    Normalize SnapshotPairs without making assumptions about every
    field returned by Scout.
    """

    output = []

    for item in list_candidates(obj):

        if not isinstance(item, dict):
            continue

        row = dict(item)

        row["currency_one_id"] = first_value(
            item,
            (
                "currencyOneItemId",
                "currency_one_item_id",
                "itemOneId",
                "item1Id",
                "fromId",
                "from_id",
            ),
        )

        row["currency_two_id"] = first_value(
            item,
            (
                "currencyTwoItemId",
                "currency_two_item_id",
                "itemTwoId",
                "item2Id",
                "toId",
                "to_id",
            ),
        )

        row["rate"] = first_value(
            item,
            (
                "rate",
                "ratio",
                "value",
                "price",
            ),
        )

        row["volume"] = first_value(
            item,
            (
                "volume",
                "traded",
                "quantity",
                "count",
            ),
        )

        output.append(row)

    return output


# ============================================================================
# MERGE EXALTED + DIVINE
# ============================================================================

def merge_prices(
    exalted_rows,
    divine_rows,
):
    """
    Merge the two reference-currency price indexes by API ID.

    Output:

        {
            "api_id": "...",
            "name": "...",
            "price_exalted": 123.0,
            "price_divine": 0.5
        }
    """

    merged = {}

    # ------------------------------------------------------------
    # EXALTED
    # ------------------------------------------------------------

    for row in exalted_rows:

        api_id = row.get("api_id")

        if not api_id:
            continue

        merged[api_id] = {
            "api_id": api_id,
            "name": row.get("name"),
            "price_exalted": row.get("price"),
            "price_divine": None,
            "current_quantity": row.get(
                "current_quantity"
            ),
        }

    # ------------------------------------------------------------
    # DIVINE
    # ------------------------------------------------------------

    for row in divine_rows:

        api_id = row.get("api_id")

        if not api_id:
            continue

        if api_id not in merged:

            merged[api_id] = {
                "api_id": api_id,
                "name": row.get("name"),
                "price_exalted": None,
                "price_divine": row.get("price"),
                "current_quantity": row.get(
                    "current_quantity"
                ),
            }

        else:

            merged[api_id]["price_divine"] = row.get(
                "price"
            )

            if not merged[api_id].get("name"):
                merged[api_id]["name"] = row.get(
                    "name"
                )

            if (
                merged[api_id].get("current_quantity")
                is None
            ):
                merged[api_id][
                    "current_quantity"
                ] = row.get(
                    "current_quantity"
                )

    return list(
        merged.values()
    )


# ============================================================================
# SNAPSHOT RETENTION
# ============================================================================

def prune_old_snapshots():
    """
    Delete snapshots older than SNAPSHOT_RETENTION_DAYS.
    """

    if RETENTION_DAYS <= 0:
        return

    cutoff = (
        time.time()
        - RETENTION_DAYS * 86400
    )

    deleted = 0

    for path in SNAPSHOTS_DIR.rglob(
        "*.json"
    ):

        try:

            if path.stat().st_mtime < cutoff:

                path.unlink()
                deleted += 1

        except FileNotFoundError:
            pass

    if deleted:
        print(
            f"[INFO] Deleted {deleted} old snapshots"
        )


# ============================================================================
# MAIN
# ============================================================================

def main():

    now = datetime.now(
        timezone.utc
    )

    generated_at = now.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    snapshot_path = now.strftime(
        "%Y-%m-%d/%H.json"
    )

    # ------------------------------------------------------------
    # Realm
    # ------------------------------------------------------------

    encoded_realm = quote(
        REALM,
        safe="",
    )

    # ------------------------------------------------------------
    # Resolve league
    # ------------------------------------------------------------

    print(
        f"[INFO] Fetching leagues from Scout..."
    )

    leagues = get_json(
        f"{encoded_realm}/Leagues"
    )

    selected_league = select_league(
        leagues
    )

    encoded_league = quote(
        selected_league,
        safe="",
    )

    root = (
        f"{encoded_realm}/Leagues/"
        f"{encoded_league}"
    )

    print(
        f"[INFO] Realm : {REALM}"
    )

    print(
        f"[INFO] League: {selected_league}"
    )

    # ------------------------------------------------------------
    # Reference currencies
    # ------------------------------------------------------------

    print(
        "[INFO] Fetching reference currencies..."
    )

    references = get_json(
        f"{root}/ReferenceCurrencies"
    )

    print(
        "[DEBUG] Reference currencies:"
    )

    print(
        json.dumps(
            references,
            ensure_ascii=False,
            indent=2,
        )[:5000]
    )

    # ------------------------------------------------------------
    # Resolve EX / DIV
    # ------------------------------------------------------------

    exalted_reference = resolve_reference_currency(
        references,
        "exalted",
    )

    divine_reference = resolve_reference_currency(
        references,
        "divine",
    )

    print(
        f"[INFO] Exalted reference: "
        f"{exalted_reference}"
    )

    print(
        f"[INFO] Divine reference: "
        f"{divine_reference}"
    )

    # ------------------------------------------------------------
    # Exchange snapshot
    # ------------------------------------------------------------

    print(
        "[INFO] Fetching ExchangeSnapshot..."
    )

    exchange_snapshot = get_json(
        f"{root}/ExchangeSnapshot"
    )

    # ------------------------------------------------------------
    # Snapshot pairs
    # ------------------------------------------------------------

    print(
        "[INFO] Fetching SnapshotPairs..."
    )

    snapshot_pairs = get_json(
        f"{root}/SnapshotPairs"
    )

    # ------------------------------------------------------------
    # Currency prices - EXALTED
    # ------------------------------------------------------------

    currencies_exalted = fetch_currency_category(
        root,
        exalted_reference,
    )

    # ------------------------------------------------------------
    # Currency prices - DIVINE
    # ------------------------------------------------------------

    currencies_divine = fetch_currency_category(
        root,
        divine_reference,
    )

    # ------------------------------------------------------------
    # Extract prices
    # ------------------------------------------------------------

    exalted_rows = extract_price_rows(
        currencies_exalted,
        exalted_reference,
    )

    divine_rows = extract_price_rows(
        currencies_divine,
        divine_reference,
    )

    print(
        f"[INFO] Exalted price rows: "
        f"{len(exalted_rows)}"
    )

    print(
        f"[INFO] Divine price rows: "
        f"{len(divine_rows)}"
    )

    # ------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------

    price_rows = merge_prices(
        exalted_rows,
        divine_rows,
    )

    # ------------------------------------------------------------
    # Normalize currency metadata
    # ------------------------------------------------------------

    normalized_currencies = (
        normalize_currency_items(
            currencies_exalted
        )
    )

    normalized_pairs = normalize_pairs(
        snapshot_pairs
    )

    normalized_references = (
        normalize_currency_items(
            references
        )
    )

    # ------------------------------------------------------------
    # Build final snapshot
    # ------------------------------------------------------------

    snapshot = {
        "schema_version": 2,

        "source": "poe2scout",

        "generated_at": generated_at,

        "realm": REALM,

        "league": selected_league,

        "reference_currencies": {
            "exalted": exalted_reference,
            "divine": divine_reference,
        },

        "exchange_snapshot": exchange_snapshot,

        "snapshot_pairs": snapshot_pairs,

        "currencies": currencies_exalted,

        "currencies_divine": currencies_divine,

        "normalized": {
            "currencies": normalized_currencies,

            "pairs": normalized_pairs,

            "reference_currencies": normalized_references,
        },

        "price_index": {
            "base_currency": "exalted_orb",

            "reference_currency_ids": {
                "exalted": exalted_reference,
                "divine": divine_reference,
            },

            "currencies": price_rows,
        },
    }

    # ------------------------------------------------------------
    # Create directories
    # ------------------------------------------------------------

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    SNAPSHOTS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ------------------------------------------------------------
    # Serialize
    # ------------------------------------------------------------

    payload = (
        json.dumps(
            snapshot,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    # ------------------------------------------------------------
    # current.json
    # ------------------------------------------------------------

    current_file = (
        DATA_DIR / "current.json"
    )

    current_file.write_text(
        payload,
        encoding="utf-8",
    )

    # ------------------------------------------------------------
    # historical snapshot
    # ------------------------------------------------------------

    historical_file = (
        SNAPSHOTS_DIR / snapshot_path
    )

    historical_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    historical_file.write_text(
        payload,
        encoding="utf-8",
    )

    # ------------------------------------------------------------
    # meta.json
    # ------------------------------------------------------------

    meta = {
        "schema_version": 2,

        "source": "poe2scout",

        "generated_at": generated_at,

        "realm": REALM,

        "league": selected_league,

        "reference_currencies": {
            "exalted": exalted_reference,
            "divine": divine_reference,
        },

        "current_file": (
            "data/current.json"
        ),

        "historical_file": (
            f"data/snapshots/{snapshot_path}"
        ),

        "currency_count": len(
            price_rows
        ),

        "exalted_price_count": len(
            exalted_rows
        ),

        "divine_price_count": len(
            divine_rows
        ),

        "pair_count": len(
            normalized_pairs
        ),
    }

    meta_file = (
        DATA_DIR / "meta.json"
    )

    meta_file.write_text(
        json.dumps(
            meta,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    # ------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------

    prune_old_snapshots()

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    print("")
    print("=" * 70)
    print("POE2 SCOUT SNAPSHOT COMPLETE")
    print("=" * 70)

    print(
        f"League          : {selected_league}"
    )

    print(
        f"Generated       : {generated_at}"
    )

    print(
        f"Exalted ref     : {exalted_reference}"
    )

    print(
        f"Divine ref      : {divine_reference}"
    )

    print(
        f"Currency rows   : {len(price_rows)}"
    )

    print(
        f"Pair rows       : {len(normalized_pairs)}"
    )

    print(
        f"Current file    : {current_file}"
    )

    print(
        f"Snapshot file   : {historical_file}"
    )

    print("=" * 70)


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print(
            f"[ERROR] {error}",
            file=sys.stderr,
        )

        raise