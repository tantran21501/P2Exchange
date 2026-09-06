#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "current.json"
DEFAULT_OUTPUT = ROOT / "data" / "currency.json"
OUTPUT_FIELDS = ("name", "matched_api_id", "price_exalted", "price_divine")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def build_currency_payload(snapshot: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rewards = snapshot.get("rewards")
    if not isinstance(rewards, list):
        raise ValueError("Input snapshot must contain a rewards array")

    return {
        **({"league": snapshot["league"]} if "league" in snapshot else {}),
        "rewards": [
            {field: reward.get(field) for field in OUTPUT_FIELDS}
            for reward in rewards
            if isinstance(reward, dict)
        ]
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build compact currency.json from data/current.json."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_currency_payload(load_json(args.input))
    write_json(args.output, payload)
    print(f"[OK] wrote {args.output} with {len(payload['rewards'])} rewards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
