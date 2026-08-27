#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_currency_json as bc


class BuildCurrencyJsonTests(unittest.TestCase):
    def test_build_currency_payload_keeps_only_requested_fields(self) -> None:
        payload = bc.build_currency_payload(
            {
                "source": "poe2scout",
                "rewards": [
                    {
                        "name": "Divine Orb",
                        "type": "currency",
                        "matched_api_id": "divine",
                        "price_exalted": 354.2528,
                        "price_divine": 1.0,
                        "price_logs": [{"price_exalted": 354.2528, "price_divine": 1.0}],
                    },
                    {
                        "name": "Unknown Reward",
                        "matched_api_id": None,
                        "price_exalted": None,
                        "price_divine": None,
                        "unpriced_reason": "not_found_in_poe2scout",
                    },
                ],
            }
        )

        self.assertEqual(
            payload,
            {
                "rewards": [
                    {
                        "name": "Divine Orb",
                        "matched_api_id": "divine",
                        "price_exalted": 354.2528,
                        "price_divine": 1.0,
                    },
                    {
                        "name": "Unknown Reward",
                        "matched_api_id": None,
                        "price_exalted": None,
                        "price_divine": None,
                    },
                ]
            },
        )

    def test_main_writes_currency_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "current.json"
            output = root / "currency.json"
            source.write_text(
                json.dumps(
                    {
                        "rewards": [
                            {
                                "name": "Exalted Orb",
                                "matched_api_id": "exalted",
                                "price_exalted": 1.0,
                                "price_divine": 0.0028,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with redirect_stdout(StringIO()):
                self.assertEqual(bc.main(["--input", str(source), "--output", str(output)]), 0)

            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(list(data), ["rewards"])
            self.assertEqual(data["rewards"][0]["name"], "Exalted Orb")
            self.assertEqual(data["rewards"][0]["matched_api_id"], "exalted")

    def test_missing_rewards_array_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "rewards array"):
            bc.build_currency_payload({"rewards": None})


if __name__ == "__main__":
    unittest.main()
