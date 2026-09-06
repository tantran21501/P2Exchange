#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

CATEGORIES = ("Abyss", "Breach", "Currency", "Delirium", "Essences", "Expedition",
              "Fragments", "Idols", "LineageSupportGems", "Ritual", "Runes",
              "SoulCores", "UncutGems", "Verisium")
RETRY_DELAYS = (10, 20, 40)


def signed_headers(secret, path, body, timestamp=None, nonce=None):
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or secrets.token_hex(24)
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(("POST", path, timestamp, nonce, body_hash))
    signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Market-Timestamp": timestamp,
            "X-Market-Nonce": nonce, "X-Market-Signature": signature,
            "User-Agent": "P2Exchange-Market-Sync/2.0"}


def post_json(base_url, endpoint, payload, secret, opener=urlopen, sleeper=time.sleep):
    body = json.dumps(payload, separators=(",", ":")).encode()
    url = base_url.rstrip("/") + endpoint
    for attempt in range(len(RETRY_DELAYS) + 1):
        request = Request(url, data=body, method="POST",
                          headers=signed_headers(secret, urlsplit(url).path, body))
        try:
            with opener(request, timeout=60) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            ray = exc.headers.get("cf-ray", "-")
            error_type = exc.headers.get("cf-error-type", "-")
            retryable = exc.code == 429 or exc.code >= 500
            print(f"category={payload.get('category', '-')} part={payload.get('index', '-')} HTTP {exc.code} endpoint={endpoint} cf-ray={ray} cf-error-type={error_type} detail={detail}")
            if not retryable or attempt == len(RETRY_DELAYS):
                raise RuntimeError(f"Webhook HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            print(f"Network error endpoint={endpoint}: {exc}")
            if attempt == len(RETRY_DELAYS):
                raise
        sleeper(RETRY_DELAYS[attempt])


def snapshot_payload(folder, commit_sha=None):
    manifest = json.loads((folder / "_manifest.json").read_text(encoding="utf-8"))
    pair_books = manifest.get("pair_books") or {}
    total = int(pair_books.get("total") or 0)
    if total <= 0 or not pair_books.get("observed_at"):
        raise ValueError("Snapshot pair books are not ready")
    if commit_sha is None:
        commit_sha = subprocess.check_output(
            ["git", "log", "-1", "--format=%H", "--", str(folder / "_manifest.json")],
            text=True).strip()
    return {"snapshot_folder": folder.name, "commit_sha": commit_sha,
            "completed_at": pair_books["observed_at"], "pair_books_total": total}


def sync_snapshot(base_url, secret, payload, request_fn=post_json):
    base_url = base_url.rstrip("/")
    if base_url.endswith("/internal/market/snapshot-ready"):
        base_url = base_url.removesuffix("/internal/market/snapshot-ready")
    registration = request_fn(base_url, "/internal/market/snapshot-ready", payload, secret)
    if registration.get("status") == "CURRENT":
        print(f"snapshot={payload['snapshot_folder']} already CURRENT")
        return registration
    sync_id = registration["sync_id"]
    completed = set(registration.get("completed_categories") or [])
    for index, category in enumerate(CATEGORIES, 1):
        if category in completed:
            print(f"category={category} progress={index}/{len(CATEGORIES)} already staged")
            continue
        result = request_fn(base_url, "/internal/market/snapshot-category",
                            {**payload, "sync_id": sync_id, "category": category}, secret)
        print(f"category={category} progress={result.get('completed_category_count', index)}/{len(CATEGORIES)} staged")
    result = request_fn(base_url, "/internal/market/snapshot-finalize",
                        {**payload, "sync_id": sync_id}, secret)
    print(json.dumps(result, separators=(",", ":")))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--url", default=os.environ.get("MARKET_SYNC_WEBHOOK_URL", ""))
    parser.add_argument("--secret", default=os.environ.get("MARKET_COMPACT_WEBHOOK_SECRET", "") or os.environ.get("MARKET_SYNC_WEBHOOK_SECRET", ""))
    parser.add_argument("--legacy", action="store_true", help="Use the pre-compact transport during rollback")
    args = parser.parse_args()
    if not args.url.strip() or not args.secret:
        raise SystemExit("Market sync webhook URL/secret is not configured")
    payload = snapshot_payload(args.snapshot_dir)
    if args.legacy:
        sync_snapshot(args.url.strip(), args.secret, payload)
    else:
        from prepare_market_compact import prepare, upload
        upload(args.url.strip(), args.secret, payload, prepare(args.snapshot_dir, CATEGORIES), post_json)


if __name__ == "__main__":
    main()
