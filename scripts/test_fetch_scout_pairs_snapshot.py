#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_scout_pairs_snapshot as pairs


def document(item_id: str, name: str) -> dict:
    return {
        "core": {"primary": "divine", "secondary": "chaos"},
        "items": [{"id": item_id, "name": name, "detailsId": item_id}],
        "lines": [{"id": item_id, "primaryValue": 1, "volumePrimaryValue": 10}],
    }


def side(api_id: str, text: str, category: str) -> dict:
    return {"ApiId": api_id, "Text": text, "CategoryApiId": category}


class PairSnapshotTests(unittest.TestCase):
    def test_filters_to_hubs_and_emits_both_estimated_directions(self) -> None:
        documents = {"Currency": document("divine", "Divine Orb"),
                     "Delirium": document("omen", "Omen")}
        raw = [{
            "CurrencyOne": side("omen", "Omen", "delirium"),
            "CurrencyTwo": side("divine", "Divine Orb", "currency"),
            "CurrencyOneData": {"RelativePrice": 20, "VolumeTraded": 100, "HighestStock": 50},
            "CurrencyTwoData": {"RelativePrice": 400, "VolumeTraded": 5, "HighestStock": 10},
        }]
        result = pairs.compact_pairs(raw, documents, "2026-09-01T12:00:00Z")
        self.assertEqual(result["Currency"], [])
        self.assertEqual(len(result["Delirium"]), 2)
        forward = next(row for row in result["Delirium"] if row["from"] == "omen")
        self.assertEqual(forward["to"], "divine")
        self.assertEqual(forward["rate"], 0.05)
        self.assertEqual(forward["available_to"], 10)
        self.assertEqual(forward["available_from"], 200)
        self.assertFalse(forward["independent"])
        self.assertEqual(forward["source"], "poe2scout-snapshot-estimate")

    def test_rejects_zero_liquidity_and_non_hub_pairs(self) -> None:
        documents = {"Currency": document("divine", "Divine Orb"),
                     "Delirium": document("omen", "Omen")}
        raw = [{
            "CurrencyOne": side("omen", "Omen", "delirium"),
            "CurrencyTwo": side("other", "Other", "delirium"),
            "CurrencyOneData": {"RelativePrice": 20, "VolumeTraded": 1, "HighestStock": 1},
            "CurrencyTwoData": {"RelativePrice": 10, "VolumeTraded": 1, "HighestStock": 1},
        }, {
            "CurrencyOne": side("omen", "Omen", "delirium"),
            "CurrencyTwo": side("divine", "Divine Orb", "currency"),
            "CurrencyOneData": {"RelativePrice": 20, "VolumeTraded": 1, "HighestStock": 1},
            "CurrencyTwoData": {"RelativePrice": 400, "VolumeTraded": 0, "HighestStock": 0},
        }]
        result = pairs.compact_pairs(raw, documents, "2026-09-01T12:00:00Z")
        self.assertEqual(len(result["Delirium"]), 1)  # reverse direction still has target-side liquidity

    def test_writes_only_compact_pairs_and_updates_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Currency.json").write_text(json.dumps(document("divine", "Divine Orb")), encoding="utf-8")
            (root / "Delirium.json").write_text(json.dumps(document("omen", "Omen")), encoding="utf-8")
            (root / "_manifest.json").write_text('{"source":"poeninja"}', encoding="utf-8")
            raw = [{
                "CurrencyOne": side("omen", "Omen", "delirium"),
                "CurrencyTwo": side("divine", "Divine Orb", "currency"),
                "CurrencyOneData": {"RelativePrice": 20, "VolumeTraded": 100, "HighestStock": 50},
                "CurrencyTwoData": {"RelativePrice": 400, "VolumeTraded": 5, "HighestStock": 10},
            }]
            counts = pairs.write_pairs(root, raw, "2026-09-01T12:00:00Z")
            payload = json.loads((root / "Delirium.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(counts["Delirium"], 2)
            self.assertEqual(set(payload["pairs"][0]), {
                "from", "to", "rate", "available_from", "available_to", "volume",
                "trade_count", "observed_at", "source", "observed", "independent",
            })
            self.assertEqual(manifest["pair_books"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
