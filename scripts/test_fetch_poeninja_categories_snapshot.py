#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import timezone
from io import StringIO
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_poeninja_categories_snapshot as pcs
import fetch_poeninja_snapshot as pn


def overview(category: str, primary_value: float = 1.0) -> dict:
    api_id = category.lower()
    return {
        "core": {
            "primary": "divine",
            "secondary": "chaos",
        },
        "items": [
            {
                "id": api_id,
                "name": f"{category} Item",
                "category": category,
                "detailsId": api_id,
            }
        ],
        "lines": [
            {
                "id": api_id,
                "primaryValue": primary_value,
                "volumePrimaryValue": 10,
            }
        ],
    }


class FakeClient:
    def __init__(self, payloads: dict[str, dict]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str]] = []

    def get_json(self, *segments: str, params: dict | None = None) -> dict:
        if segments != ("exchange", "current", "overview"):
            raise AssertionError(f"Unexpected segments: {segments!r}")
        category = str((params or {}).get("type") or "")
        league = str((params or {}).get("league") or "")
        self.calls.append((league, category))
        return self.payloads[category]


class FetchPoeNinjaCategoriesSnapshotTests(unittest.TestCase):
    def test_snapshot_folder_name_uses_utc_ddmmyy_hour_format(self) -> None:
        timestamp = pcs.parse_snapshot_time("2026-08-28T13:45:00+07:00")

        self.assertEqual(timestamp.tzinfo, timezone.utc)
        self.assertEqual(pcs.generated_at(timestamp), "2026-08-28T06:45:00Z")
        self.assertEqual(pcs.snapshot_folder_name(timestamp), "280826_06")

    def test_default_categories_include_all_poeninja_currency_categories(self) -> None:
        self.assertEqual(
            pcs.DEFAULT_CATEGORIES,
            (
                "Currency",
                "Fragments",
                "Abyss",
                "UncutGems",
                "LineageSupportGems",
                "Essences",
                "SoulCores",
                "Idols",
                "Runes",
                "Ritual",
                "Expedition",
                "Delirium",
                "Breach",
                "Verisium",
            ),
        )
        self.assertEqual(len(pcs.DEFAULT_CATEGORIES), 14)

    def test_fake_client_writes_category_files_and_manifest(self) -> None:
        timestamp = pcs.parse_snapshot_time("2026-08-28T13:05:00Z")
        payloads = {
            "Currency": overview("Currency", primary_value=1.0),
            "Runes": overview("Runes", primary_value=2.5),
        }
        fake_client = FakeClient(payloads)

        with redirect_stderr(StringIO()):
            overviews = pcs.fetch_category_overviews(
                fake_client,
                "Runes of Aldur",
                ["Currency", "Runes"],
            )

        with tempfile.TemporaryDirectory() as tmp:
            snapshot_dir, manifest = pcs.write_snapshot_folder(
                Path(tmp),
                timestamp,
                {"id": "Runes of Aldur", "name": "Runes of Aldur"},
                overviews,
            )

            self.assertEqual(
                fake_client.calls,
                [("Runes of Aldur", "Currency"), ("Runes of Aldur", "Runes")],
            )
            self.assertEqual(snapshot_dir.name, "280826_13")
            self.assertEqual(
                json.loads((snapshot_dir / "Currency.json").read_text(encoding="utf-8")),
                payloads["Currency"],
            )
            self.assertEqual(
                json.loads((snapshot_dir / "Runes.json").read_text(encoding="utf-8")),
                payloads["Runes"],
            )
            self.assertEqual(
                json.loads((snapshot_dir / "_manifest.json").read_text(encoding="utf-8")),
                manifest,
            )
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["source"], "poeninja")
            self.assertEqual(manifest["generated_at"], "2026-08-28T13:05:00Z")
            self.assertEqual(manifest["snapshot_folder"], "280826_13")
            self.assertEqual(manifest["league"]["value"], "Runes of Aldur")
            self.assertEqual(manifest["categories_fetched"], ["Currency", "Runes"])
            self.assertEqual(
                manifest["category_counts"],
                {
                    "Currency": {"items": 1, "lines": 1},
                    "Runes": {"items": 1, "lines": 1},
                },
            )

    def test_missing_lines_array_raises(self) -> None:
        timestamp = pcs.parse_snapshot_time("2026-08-28T13:05:00Z")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(pn.PoeNinjaApiError, "lines array"):
                pcs.write_snapshot_folder(
                    Path(tmp),
                    timestamp,
                    {"id": "Runes of Aldur"},
                    {"Currency": {"items": []}},
                )

    def test_rerun_same_hour_overwrites_snapshot_folder(self) -> None:
        timestamp = pcs.parse_snapshot_time("2026-08-28T13:05:00Z")

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            first_dir, _ = pcs.write_snapshot_folder(
                output_dir,
                timestamp,
                {"id": "Runes of Aldur"},
                {"Currency": overview("Currency", primary_value=1.0)},
            )
            second_dir, _ = pcs.write_snapshot_folder(
                output_dir,
                timestamp,
                {"id": "Runes of Aldur"},
                {"Currency": overview("Currency", primary_value=2.0)},
            )

            self.assertEqual(first_dir, second_dir)
            payload = json.loads((second_dir / "Currency.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["lines"][0]["primaryValue"], 2.0)


if __name__ == "__main__":
    unittest.main()
