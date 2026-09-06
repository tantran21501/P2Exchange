"""Build compact market bundles on the Actions runner, never in a Worker."""
import hashlib
import json
from datetime import timedelta

from market_transform import (normalize_market_document, merge_market_documents,
                              _folder_time, _market_quote_observed_at, compact_history)

LEAGUE = "Forbidden Rites"
PART_BYTES = 128 * 1024


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def league_value(value):
    return (value.get("value") or value.get("name")) if isinstance(value, dict) else value


def validate_manifest(manifest):
    if league_value(manifest.get("league")) != LEAGUE:
        raise ValueError("snapshot league must be Forbidden Rites")
    pairs = manifest.get("pair_books") or {}
    if pairs.get("league") != LEAGUE or not pairs.get("observed_at") or int(pairs.get("total", 0)) <= 0:
        raise ValueError("completed Forbidden Rites pair books are required")


def load_market(folder, category):
    timestamp = _folder_time(folder.name)
    def load(name):
        document = read(folder / f"{name}.json")
        if "pairs" not in document:
            raise ValueError(f"incomplete pair book: {folder.name}/{name}")
        return normalize_market_document(document, timestamp, name)
    currency = load("Currency")
    return currency if category == "Currency" else merge_market_documents(currency, load(category), category)


def prepare(folder, categories):
    manifest = read(folder / "_manifest.json")
    validate_manifest(manifest)
    current_time = _folder_time(folder.name)
    candidates = []
    for path in folder.parent.iterdir():
        timestamp = _folder_time(path.name)
        if not path.is_dir() or timestamp is None or not current_time - timedelta(hours=72) <= timestamp < current_time:
            continue
        try:
            validate_manifest(read(path / "_manifest.json"))
        except (ValueError, OSError):
            continue
        candidates.append((timestamp, path))
    candidates = [path for _, path in sorted(candidates, reverse=True)[:71]]
    result = {}
    for category in categories:
        market = load_market(folder, category)
        points = []
        for previous in candidates:
            try:
                points.append(load_market(previous, category))
            except (ValueError, OSError):
                continue
        ready = manifest["pair_books"]["observed_at"]
        market.update({"source_snapshot_at": market["snapshot_at"],
                       "quote_observed_at": _market_quote_observed_at(market), "pair_ready_at": ready})
        canonical = json.dumps({"category": category, "market": market}, ensure_ascii=False,
                               separators=(",", ":"), sort_keys=True)
        bundle = {"schema_version": 3, "league": LEAGUE, "current": market,
                  "market_version": hashlib.sha256(canonical.encode()).hexdigest()[:16],
                  "selected_category": category, "source_folder": folder.name,
                  "source_snapshot_at": market["snapshot_at"], "pair_ready_at": ready,
                  "quote_observed_at": market["quote_observed_at"]}
        history = {"schema_version": 3, "league": LEAGUE, "category": category,
                   "points": compact_history(points, market)}
        encoded = {"current": json.dumps(bundle, separators=(",", ":"), sort_keys=True, allow_nan=False),
                   "history": json.dumps(history, separators=(",", ":"), sort_keys=True, allow_nan=False)}
        for kind, content in encoded.items():
            limit = (2 if kind == "current" else 1) * 1024 * 1024
            if len(content) > limit:
                raise ValueError(f"{category}/{kind} exceeds {limit} byte safety limit")
        result[category] = encoded
        print(f"prepared category={category} current_bytes={len(encoded['current'])} history_bytes={len(encoded['history'])}")
    return result


def upload(base_url, secret, payload, bundles, request_fn):
    base_url = base_url.rstrip("/").removesuffix("/internal/market/snapshot-ready")
    payload = {**payload, "protocol": 3, "league": LEAGUE}
    registration = request_fn(base_url, "/internal/market/compact-ready", payload, secret)
    if registration["status"] == "CURRENT":
        return registration
    payload = {**payload, "sync_id": registration["sync_id"]}
    for category, documents in bundles.items():
        if category in registration.get("completed_categories", []):
            continue
        category_payload = {**payload, "category": category}
        for kind, content in documents.items():
            chunks = [content[i:i + PART_BYTES] for i in range(0, len(content), PART_BYTES)]
            for index, chunk in enumerate(chunks):
                request_fn(base_url, "/internal/market/compact-part", {
                    **category_payload, "kind": kind, "index": index, "count": len(chunks),
                    "data": chunk, "sha256": hashlib.sha256(chunk.encode("ascii")).hexdigest()}, secret)
        staged = request_fn(base_url, "/internal/market/compact-category", category_payload, secret)
        print(f"category={category} progress={staged['completed_category_count']}/{len(bundles)} staged")
    result = request_fn(base_url, "/internal/market/compact-finalize", payload, secret)
    print(json.dumps(result))
    return result
