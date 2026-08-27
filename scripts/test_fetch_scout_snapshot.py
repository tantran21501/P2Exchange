#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_scout_snapshot as fs


def item(
    text: str,
    price: float,
    category: str,
    *,
    api_id: str | None = None,
    item_id: int = 1,
    currency_item_id: int = 10,
    quantity: int = 3,
) -> dict:
    return {
        "CurrencyItemId": currency_item_id,
        "ItemId": item_id,
        "CurrencyCategoryId": 99,
        "ApiId": api_id or text.lower().replace(" ", "-"),
        "BaseItemTypeId": f"Metadata/{text}",
        "Text": text,
        "CategoryApiId": category,
        "IconUrl": None,
        "ItemMetadata": {
            "name": text,
            "base_type": text,
        },
        "PriceLogs": [
            {"Price": price, "Time": "2026-08-27T00:00:00.0000000Z", "Quantity": quantity}
        ],
        "CurrentPrice": price,
        "CurrentQuantity": quantity,
    }


class FakeClient:
    def __init__(self, pages: dict[tuple[str, int], dict]) -> None:
        self.pages = pages
        self.calls: list[dict] = []

    def get_json(self, *segments: str, params: dict | None = None):
        assert params is not None
        self.calls.append({"segments": segments, "params": dict(params)})
        return self.pages[(params["category"], params["page"])]


class FetchScoutSnapshotTests(unittest.TestCase):
    def test_build_snapshot_matches_cross_category_rewards_and_keeps_misses(self) -> None:
        rewards_doc = {
            "league": "Runes of Aldur",
            "rewards": [
                {"name": "Perfect Chaos Orb", "type": "currency"},
                {"name": "Aldur's Saga", "type": "currency"},
                {"name": "Transcendent Alloy", "type": "alloys"},
                {"name": "Rain of Blades (Level 20)", "type": "gems"},
                {"name": "5x Random Currency", "type": "currency"},
            ],
        }
        category_items = {
            "currency": [item("Perfect Chaos Orb", 2063.143, "currency", item_id=1)],
            "expedition": [item("Aldur's Saga", 14.5, "expedition", item_id=2)],
            "verisium": [item("Transcendent Alloy", 88.25, "verisium", item_id=3)],
        }

        snapshot = fs.build_snapshot(
            rewards_doc,
            {"Value": "Runes of Aldur", "ShortName": "runes", "IsCurrent": True},
            category_items,
            "exalted",
            generated_at="2026-08-27T00:00:00Z",
        )

        self.assertEqual(snapshot["stats"], {"requested": 5, "priced": 3, "unpriced": 2, "ambiguous": 0})
        self.assertEqual(snapshot["rewards"][0]["price_exalted"], 2063.143)
        self.assertEqual(snapshot["rewards"][1]["matched_category"], "expedition")
        self.assertEqual(snapshot["rewards"][2]["matched_category"], "verisium")
        self.assertEqual(snapshot["rewards"][3]["unpriced_reason"], "not_found_in_poe2scout")
        self.assertEqual(snapshot["rewards"][4]["unpriced_reason"], "non_deterministic_reward")
        self.assertEqual(snapshot["rewards"][0]["price_logs"][0]["price_exalted"], 2063.143)

    def test_fetch_currency_category_paginates(self) -> None:
        client = FakeClient(
            {
                ("currency", 1): {
                    "CurrentPage": 1,
                    "Pages": 2,
                    "Total": 2,
                    "Items": [item("Chaos Orb", 33.0, "currency", item_id=1)],
                },
                ("currency", 2): {
                    "CurrentPage": 2,
                    "Pages": 2,
                    "Total": 2,
                    "Items": [item("Divine Orb", 354.0, "currency", item_id=2)],
                },
            }
        )

        rows = fs.fetch_currency_category(
            client, "poe2", "Runes of Aldur", "currency", "exalted", 100, 7, 24
        )

        self.assertEqual([row["Text"] for row in rows], ["Chaos Orb", "Divine Orb"])
        self.assertEqual([call["params"]["page"] for call in client.calls], [1, 2])
        self.assertTrue(all(call["params"]["perPage"] == 100 for call in client.calls))

    def test_generic_uncut_gem_without_level_gets_specific_reason(self) -> None:
        rewards_doc = {
            "rewards": [
                {"name": "Uncut Skill Gem", "type": "gems"},
                {"name": "Uncut Skill Gem (Level 20)", "type": "gems"},
            ]
        }
        category_items = {
            "uncutgems": [
                item(
                    "Uncut Skill Gem (Level 19)",
                    5.0,
                    "uncutgems",
                    api_id="uncut-skill-gem-19",
                    item_id=19,
                ),
                item(
                    "Uncut Skill Gem (Level 20)",
                    100.0,
                    "uncutgems",
                    api_id="uncut-skill-gem-20",
                    item_id=20,
                ),
            ]
        }

        snapshot = fs.build_snapshot(
            rewards_doc,
            {"Value": "Runes of Aldur"},
            category_items,
            "exalted",
            generated_at="2026-08-27T00:00:00Z",
        )

        self.assertEqual(snapshot["stats"]["ambiguous"], 1)
        self.assertEqual(snapshot["rewards"][0]["unpriced_reason"], "ambiguous_level_required")
        self.assertEqual(snapshot["rewards"][1]["price_exalted"], 100.0)

    def test_ambiguous_duplicate_exact_match_is_not_priced(self) -> None:
        rewards_doc = {"rewards": [{"name": "Duplicated Thing", "type": "currency"}]}
        category_items = {
            "currency": [item("Duplicated Thing", 1.0, "currency", item_id=1)],
            "fragments": [item("Duplicated Thing", 2.0, "fragments", item_id=2)],
        }

        snapshot = fs.build_snapshot(
            rewards_doc,
            {"Value": "Runes of Aldur"},
            category_items,
            "exalted",
            generated_at="2026-08-27T00:00:00Z",
        )

        row = snapshot["rewards"][0]
        self.assertIsNone(row["price_exalted"])
        self.assertEqual(row["unpriced_reason"], "ambiguous_match")
        self.assertEqual(len(row["candidate_matches"]), 2)

    def test_select_league_uses_requested_value_or_current_softcore(self) -> None:
        leagues = [
            {"Value": "HC Runes of Aldur", "ShortName": "runeshc", "IsCurrent": True},
            {"Value": "Runes of Aldur", "ShortName": "runes", "IsCurrent": True},
        ]

        self.assertEqual(fs.select_league(leagues, "runes")["Value"], "Runes of Aldur")
        self.assertEqual(fs.select_league(leagues, None)["Value"], "Runes of Aldur")


if __name__ == "__main__":
    unittest.main()
