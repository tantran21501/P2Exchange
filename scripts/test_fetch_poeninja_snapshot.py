#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import unittest
from pathlib import Path
from urllib.error import URLError


sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_poeninja_snapshot as pn


def item(api_id: str, name: str, details_id: str | None = None) -> dict:
    return {
        "id": api_id,
        "name": name,
        "detailsId": details_id or api_id,
    }


def line(api_id: str, primary_value: float | None) -> dict:
    return {
        "id": api_id,
        "primaryValue": primary_value,
        "volumePrimaryValue": 10,
        "maxVolumeCurrency": "divine",
        "maxVolumeRate": 1,
    }


def overview(
    items: list[dict],
    lines: list[dict],
    *,
    primary: str = "divine",
    rates: dict | None = None,
) -> dict:
    return {
        "core": {
            "items": [
                item("divine", "Divine Orb", "divine-orb"),
                item("exalted", "Exalted Orb", "exalted-orb"),
            ],
            "rates": rates if rates is not None else {"exalted": 400.0},
            "primary": primary,
            "secondary": "chaos",
        },
        "items": items,
        "lines": lines,
    }


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FetchPoeNinjaSnapshotTests(unittest.TestCase):
    def test_build_snapshot_converts_primary_value_to_exalted_and_divine(self) -> None:
        rewards_doc = {
            "rewards": [
                {"name": "Divine Orb", "type": "currency"},
                {"name": "Exalted Orb", "type": "currency"},
                {"name": "Chaos Orb", "type": "currency"},
            ]
        }
        currency = overview(
            items=[item("chaos", "Chaos Orb", "chaos-orb")],
            lines=[
                line("divine", 1.0),
                line("exalted", 0.0025),
                line("chaos", 0.1),
            ],
        )

        snapshot = pn.build_snapshot(
            rewards_doc,
            {"id": "Runes of Aldur", "name": "Runes of Aldur"},
            {"Currency": currency},
            ["exalted", "divine"],
            generated_at="2026-08-28T00:00:00Z",
        )

        self.assertEqual(snapshot["source"], "poeninja")
        self.assertEqual(snapshot["stats"], {"requested": 3, "priced": 3, "unpriced": 0, "ambiguous": 0})
        self.assertEqual(snapshot["rewards"][0]["price_exalted"], 400.0)
        self.assertEqual(snapshot["rewards"][0]["price_divine"], 1.0)
        self.assertEqual(snapshot["rewards"][1]["price_exalted"], 1.0)
        self.assertEqual(snapshot["rewards"][1]["price_divine"], 0.0025)
        self.assertEqual(snapshot["rewards"][2]["price_exalted"], 40.0)
        self.assertEqual(snapshot["rewards"][2]["price_divine"], 0.1)

    def test_core_currency_items_from_non_currency_categories_are_not_indexed(self) -> None:
        rewards_doc = {"rewards": [{"name": "Divine Orb", "type": "currency"}]}
        currency = overview(
            items=[],
            lines=[line("divine", 1.0), line("exalted", 0.0025)],
        )
        runes = overview(
            items=[item("body-rune", "Body Rune")],
            lines=[line("body-rune", 0.01)],
        )

        snapshot = pn.build_snapshot(
            rewards_doc,
            {"id": "Runes of Aldur"},
            {"Currency": currency, "Runes": runes},
            ["exalted", "divine"],
            generated_at="2026-08-28T00:00:00Z",
        )

        row = snapshot["rewards"][0]
        self.assertIsNone(row["unpriced_reason"])
        self.assertEqual(row["matched_category"], "Currency")
        self.assertEqual(row["price_exalted"], 400.0)
        self.assertEqual(snapshot["stats"], {"requested": 1, "priced": 1, "unpriced": 0, "ambiguous": 0})

    def test_reward_can_match_a_different_poeninja_category_than_reward_type(self) -> None:
        rewards_doc = {"rewards": [{"name": "Aldur's Saga", "type": "currency"}]}
        currency = overview(
            items=[],
            lines=[line("divine", 1.0), line("exalted", 0.0025)],
        )
        expedition = overview(
            items=[item("aldurs-saga", "Aldur's Saga", "aldurs-saga")],
            lines=[line("aldurs-saga", 0.25)],
        )

        snapshot = pn.build_snapshot(
            rewards_doc,
            {"id": "Runes of Aldur"},
            {"Currency": currency, "Expedition": expedition},
            ["exalted", "divine"],
            generated_at="2026-08-28T00:00:00Z",
        )

        row = snapshot["rewards"][0]
        self.assertEqual(row["source"], "poeninja")
        self.assertEqual(row["matched_api_id"], "aldurs-saga")
        self.assertEqual(row["matched_category"], "Expedition")
        self.assertEqual(row["price_exalted"], 100.0)
        self.assertEqual(row["price_divine"], 0.25)

    def test_missing_exact_matches_and_random_rewards_are_not_priced(self) -> None:
        rewards_doc = {
            "rewards": [
                {"name": "Rain of Blades (Level 20)", "type": "gems"},
                {"name": "5x Random Currency", "type": "currency"},
            ]
        }
        currency = overview(
            items=[],
            lines=[line("divine", 1.0), line("exalted", 0.0025)],
        )
        uncut_gems = overview(
            items=[item("uncut-skill-gem-20", "Uncut Skill Gem (Level 20)")],
            lines=[line("uncut-skill-gem-20", 0.05)],
        )

        snapshot = pn.build_snapshot(
            rewards_doc,
            {"id": "Runes of Aldur"},
            {"Currency": currency, "UncutGems": uncut_gems},
            ["exalted", "divine"],
            generated_at="2026-08-28T00:00:00Z",
        )

        self.assertEqual(snapshot["stats"]["priced"], 0)
        self.assertEqual(snapshot["rewards"][0]["unpriced_reason"], "not_found_in_poeninja")
        self.assertIsNone(snapshot["rewards"][0]["price_exalted"])
        self.assertEqual(snapshot["rewards"][1]["unpriced_reason"], "non_deterministic_reward")
        self.assertIsNone(snapshot["rewards"][1]["matched_api_id"])

    def test_ambiguous_duplicate_exact_match_is_not_priced(self) -> None:
        rewards_doc = {"rewards": [{"name": "Duplicated Thing", "type": "currency"}]}
        currency = overview(
            items=[item("duplicate-currency", "Duplicated Thing")],
            lines=[
                line("divine", 1.0),
                line("exalted", 0.0025),
                line("duplicate-currency", 0.05),
            ],
        )
        expedition = overview(
            items=[item("duplicate-expedition", "Duplicated Thing")],
            lines=[line("duplicate-expedition", 0.1)],
        )

        snapshot = pn.build_snapshot(
            rewards_doc,
            {"id": "Runes of Aldur"},
            {"Currency": currency, "Expedition": expedition},
            ["exalted", "divine"],
            generated_at="2026-08-28T00:00:00Z",
        )

        row = snapshot["rewards"][0]
        self.assertEqual(row["unpriced_reason"], "ambiguous_match")
        self.assertIsNone(row["price_exalted"])
        self.assertIsNone(row["price_divine"])
        self.assertEqual(len(row["candidate_matches"]), 2)
        self.assertEqual(snapshot["stats"]["ambiguous"], 1)
        self.assertEqual(row["candidate_matches"][0]["price_exalted"], 20.0)
        self.assertEqual(row["candidate_matches"][1]["price_divine"], 0.1)

    def test_select_league_uses_requested_value_or_current_softcore_or_standard(self) -> None:
        leagues = [
            {"id": "HC Runes of Aldur", "name": "HC Runes of Aldur"},
            {"id": "Runes of Aldur", "name": "Runes of Aldur"},
            {"id": "Standard", "name": "Standard"},
        ]
        archived_leagues = [
            {"id": "Hardcore", "name": "Hardcore"},
            {"id": "Standard", "name": "Standard"},
        ]

        self.assertEqual(pn.select_league(leagues, "runesofaldur")["id"], "Runes of Aldur")
        self.assertEqual(pn.select_league(leagues, "current")["id"], "Runes of Aldur")
        self.assertEqual(pn.select_league(leagues, None)["id"], "Runes of Aldur")
        self.assertEqual(pn.select_league(archived_leagues, "latest")["id"], "Standard")
        with self.assertRaisesRegex(pn.PoeNinjaApiError, "Could not find"):
            pn.select_league(leagues, "Missing League")

    def test_requested_league_prefers_league_then_league_name_then_rewards_doc(self) -> None:
        rewards_doc = {"league": "Runes of Aldur"}
        self.assertEqual(
            pn.requested_league(
                argparse.Namespace(league="Standard", league_name="Hardcore"),
                rewards_doc,
            ),
            "Standard",
        )
        self.assertEqual(
            pn.requested_league(
                argparse.Namespace(league="", league_name="Hardcore"),
                rewards_doc,
            ),
            "Hardcore",
        )
        self.assertEqual(
            pn.requested_league(argparse.Namespace(league="", league_name=""), rewards_doc),
            "Runes of Aldur",
        )

    def test_parse_categories_canonicalizes_names_and_keeps_currency_anchor(self) -> None:
        self.assertEqual(
            pn.parse_categories("runes, uncut gems"),
            ["Currency", "Runes", "UncutGems"],
        )

    def test_client_retries_transient_errors_and_reports_failures(self) -> None:
        calls: list[str] = []

        def flaky_opener(request, timeout: int):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise URLError("temporary failure")
            return FakeResponse({"ok": True})

        original_sleep = pn.time.sleep
        pn.time.sleep = lambda _: None
        try:
            client = pn.PoeNinjaClient(
                "https://example.test",
                "UnitTest/1.0",
                timeout=5,
                retries=2,
                opener=flaky_opener,
            )
            self.assertEqual(client.get_json("leagues"), {"ok": True})
        finally:
            pn.time.sleep = original_sleep

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], "https://example.test/poe2/api/economy/leagues")

        failed_calls = 0

        def failing_opener(request, timeout: int):
            nonlocal failed_calls
            failed_calls += 1
            raise URLError("still down")

        original_sleep = pn.time.sleep
        pn.time.sleep = lambda _: None
        try:
            client = pn.PoeNinjaClient(
                "https://example.test",
                "UnitTest/1.0",
                timeout=5,
                retries=2,
                opener=failing_opener,
            )
            with self.assertRaisesRegex(pn.PoeNinjaApiError, "GET failed"):
                client.get_json("leagues")
        finally:
            pn.time.sleep = original_sleep

        self.assertEqual(failed_calls, 2)


if __name__ == "__main__":
    unittest.main()
