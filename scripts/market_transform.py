"""Pure market transforms, mirrored from Server and guarded by parity tests."""

import re
from datetime import datetime, timedelta, timezone

_HUB_IDS = {"divine", "exalted", "chaos"}
HISTORY_RETENTION_HOURS = 72
RETAINED_HISTORY_COUNT = 72

def _positive(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None

def _timestamp(document: dict, fallback: datetime) -> str:
    value = document.get("snapshot_at") or document.get("updated") or document.get("updatedAt")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = fallback
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _better(candidate: dict, existing: dict) -> bool:
    candidate_observed = candidate.get("observed") is True
    existing_observed = existing.get("observed") is True
    if candidate_observed != existing_observed:
        return candidate_observed
    candidate_independent = candidate.get("independent") is True
    existing_independent = existing.get("independent") is True
    if candidate_independent != existing_independent:
        return candidate_independent
    candidate_time = str(candidate.get("observed_at") or "")
    existing_time = str(existing.get("observed_at") or "")
    if candidate_time != existing_time:
        return candidate_time > existing_time
    return float(candidate.get("available_from") or candidate.get("volume") or 0) > float(
        existing.get("available_from") or existing.get("volume") or 0)

def normalize_market_document(document: object, fallback_timestamp: datetime,
                              category_name: str = "Currency") -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("lines"), list):
        raise ValueError("market root must contain a lines array")
    details = {}
    for item in document.get("items") or []:
        if isinstance(item, dict):
            item_id = str(item.get("id") or "").strip().lower()
            if item_id and item_id not in details:
                details[item_id] = item
    primary = str(document.get("primary") or "divine").strip().lower()
    secondary = str(document.get("secondary") or "chaos").strip().lower()
    lines = document["lines"]
    line_ids = {str(item.get("id") or "").strip().lower() for item in lines if isinstance(item, dict)}
    currencies: dict[str, dict] = {}
    for hub in (primary, secondary, "exalted", "divine", "chaos"):
        if not hub or hub in line_ids or hub in currencies:
            continue
        meta = details.get(hub, {})
        currencies[hub] = {"id": hub, "name": str(meta.get("name") or hub.title()).strip(),
                           "category": "Currency", "prices": {hub: 1.0}, "volume": 0.0}
    raw_edges = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        currency_id = str(line.get("id") or "").strip().lower()
        price = _positive(line.get("primaryValue"))
        if not currency_id or price is None:
            continue
        volume = _positive(line.get("volumePrimaryValue") or line.get("volume")) or 0.0
        meta = details.get(currency_id, {})
        candidate = {"id": currency_id, "name": str(meta.get("name") or line.get("name") or currency_id).strip(),
                     "category": "Currency" if currency_id in _HUB_IDS else category_name,
                     "prices": {primary: price}, "volume": volume}
        existing = currencies.get(currency_id)
        if existing is None or volume > float(existing.get("volume") or 0):
            currencies[currency_id] = candidate
        # A normalized line is one observed quote. Never manufacture the opposite
        # book by taking 1/rate: that erases the spread and creates false 0% loops.
        raw_edges.append({"from": currency_id, "to": primary, "rate": price, "volume": volume,
                          "available_from": volume, "available_to": volume * price,
                          "observed_at": _timestamp(document, fallback_timestamp),
                          "source": "normalized-line", "trade_count": 0,
                          "independent": False, "observed": True})
    for pair in document.get("pairs") or []:
        if isinstance(pair, dict):
            raw_edges.append({"from": pair.get("from") or pair.get("have"), "to": pair.get("to") or pair.get("want"),
                              "rate": pair.get("rate") or pair.get("ratio"),
                              "volume": pair.get("volume") or pair.get("stock") or pair.get("available_from") or 0,
                              "available_from": pair.get("available_from") or pair.get("stock") or pair.get("volume") or 0,
                              "available_to": pair.get("available_to") or pair.get("receive_stock") or 0,
                              "trade_count": pair.get("trade_count") or 0,
                              "observed_at": pair.get("observed_at") or _timestamp(document, fallback_timestamp),
                              "source": pair.get("source") or "pair-book",
                              "independent": pair.get("independent") is True,
                              "observed": pair.get("observed") is True})
    edges: dict[tuple[str, str], dict] = {}
    for raw in raw_edges:
        source, target = str(raw.get("from") or "").strip().lower(), str(raw.get("to") or "").strip().lower()
        rate, volume = _positive(raw.get("rate")), _positive(raw.get("volume")) or 0.0
        if not source or not target or source == target or rate is None or source not in currencies or target not in currencies:
            continue
        available_from = _positive(raw.get("available_from")) or volume
        available_to = _positive(raw.get("available_to")) or (available_from * rate if available_from else 0.0)
        candidate = {"from": source, "to": target, "rate": rate, "volume": volume,
                     "available_from": available_from, "available_to": available_to,
                     "trade_count": int(_positive(raw.get("trade_count")) or 0),
                     "observed_at": str(raw.get("observed_at") or _timestamp(document, fallback_timestamp)),
                     "source": str(raw.get("source") or "unknown")[:64],
                     "independent": raw.get("independent") is True,
                     "observed": raw.get("observed") is True}
        key = (source, target)
        if key not in edges or _better(candidate, edges[key]):
            edges[key] = candidate
    if not currencies:
        raise ValueError("market contains no valid currencies")
    return {"snapshot_at": _timestamp(document, fallback_timestamp), "currencies": list(currencies.values()),
            "edges": list(edges.values())}

def merge_market_documents(currency_market: dict, category_market: dict, category_id: str) -> dict:
    """Merge one item category into Currency, namespacing only colliding non-hub IDs."""
    merged_currencies = {item["id"]: dict(item) for item in currency_market.get("currencies", [])}
    remap: dict[str, str] = {}
    for item in category_market.get("currencies", []):
        original = item["id"]
        target = original
        if original not in _HUB_IDS and original in merged_currencies:
            target = f"{category_id.lower()}:{original}"
        remap[original] = target
        candidate = {**item, "id": target,
                     "category": "Currency" if original in _HUB_IDS else category_id}
        existing = merged_currencies.get(target)
        if existing is None or float(candidate.get("volume") or 0) > float(existing.get("volume") or 0):
            merged_currencies[target] = candidate

    edges: dict[tuple[str, str], dict] = {}
    tagged_edges = [(edge, False) for edge in currency_market.get("edges", [])]
    tagged_edges += [(edge, True) for edge in category_market.get("edges", [])]
    for edge, is_category_edge in tagged_edges:
        source = remap.get(edge["from"], edge["from"]) if is_category_edge else edge["from"]
        target = remap.get(edge["to"], edge["to"]) if is_category_edge else edge["to"]
        if source == target or source not in merged_currencies or target not in merged_currencies:
            continue
        candidate = {**edge, "from": source, "to": target}
        key = (source, target)
        if key not in edges or _better(candidate, edges[key]):
            edges[key] = candidate
    return {"snapshot_at": currency_market["snapshot_at"],
            "currencies": list(merged_currencies.values()), "edges": list(edges.values())}

def _folder_time(name: str) -> datetime | None:
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})_(\d{2})", name)
    if not match:
        return None
    day, month, year, hour = map(int, match.groups())
    try:
        return datetime(2000 + year, month, day, hour, tzinfo=timezone.utc)
    except ValueError:
        return None

def history_projection(market: dict) -> dict:
    currencies = []
    for item in market.get("currencies", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        currencies.append({key: item[key] for key in ("id", "name", "category", "prices", "volume")
                           if key in item})
    return {"snapshot_at": market.get("snapshot_at"), "currencies": currencies}

def compact_history(points: list[dict], current: dict) -> list[dict]:
    projected = history_projection(current)
    current_at = datetime.fromisoformat(str(projected["snapshot_at"]).replace("Z", "+00:00"))
    cutoff = current_at - timedelta(hours=HISTORY_RETENTION_HOURS)
    unique = {}
    for point in [projected, *points]:
        if not isinstance(point, dict) or not point.get("snapshot_at"):
            continue
        try:
            timestamp = datetime.fromisoformat(str(point["snapshot_at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp >= cutoff:
            unique.setdefault(point["snapshot_at"], point)
    return [unique[key] for key in sorted(unique, reverse=True)[:RETAINED_HISTORY_COUNT]]

def _market_quote_observed_at(market: dict) -> str | None:
    values = [str(edge.get("observed_at")) for edge in market.get("edges", [])
              if edge.get("observed") is True and edge.get("observed_at")]
    return max(values) if values else market.get("snapshot_at")
