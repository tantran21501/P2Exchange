import hashlib
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import prepare_market_compact as compact
from prepare_market_compact import LEAGUE, PART_BYTES, prepare, upload, validate_manifest


def manifest(league=LEAGUE):
    return {"league": {"value": league}, "pair_books": {
        "league": league, "observed_at": "2026-09-06T11:27:55Z", "total": 2}}


def document(price=2):
    return {"lines": [{"id": "chaos", "primaryValue": price}], "pairs": [
        {"from": "divine", "to": "chaos", "rate": 0.4, "volume": 12,
         "observed": True, "independent": False, "source": "poe2scout-snapshot-estimate"}]}


def rich_document(price=2, width=8):
    payload = document(price)
    payload["items"] = []
    for index in range(width):
        item_id = f"rune{price}_{index}"
        payload["items"].append({"id": item_id, "name": f"Rune {index} {'x' * 24}",
                                 "detailsId": item_id})
        payload["lines"].append({"id": item_id, "primaryValue": price + index + 1,
                                 "volumePrimaryValue": 10 + index})
    return payload


class CompactTests(unittest.TestCase):
    def test_history_excludes_unknown_or_different_leagues_and_keeps_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, league in (("060926_11", LEAGUE), ("060926_10", LEAGUE),
                                   ("060926_09", "Runes of Aldur"), ("060926_08", None)):
                path = root / folder
                path.mkdir()
                (path / "_manifest.json").write_text(json.dumps(manifest(league)), encoding="utf-8")
                (path / "Currency.json").write_text(json.dumps(document()), encoding="utf-8")
            encoded = prepare(root / "060926_11", ["Currency"])["Currency"]
            bundle = json.loads(encoded["current"])
            history = json.loads(encoded["history"])
            self.assertEqual(len(history["points"]), 2)
            pair = next(e for e in bundle["current"]["edges"] if e["from"] == "divine")
            self.assertEqual(pair["rate"], 0.4)
            self.assertTrue(pair["observed"])
            self.assertFalse(pair["independent"])
            self.assertEqual(pair["source"], "poe2scout-snapshot-estimate")

    def test_oversized_history_trims_oldest_points(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder, price in (("060926_11", 4), ("060926_10", 3),
                                  ("060926_09", 2), ("060926_08", 1)):
                path = root / folder
                path.mkdir()
                (path / "_manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
                (path / "Currency.json").write_text(json.dumps(rich_document(price)), encoding="utf-8")

            previous_limit = compact.LIMITS["history"]
            compact.LIMITS["history"] = 3000
            try:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    encoded = prepare(root / "060926_11", ["Currency"])["Currency"]
            finally:
                compact.LIMITS["history"] = previous_limit

            history = json.loads(encoded["history"])
            snapshot_times = [point["snapshot_at"] for point in history["points"]]
            self.assertLess(len(snapshot_times), 4)
            self.assertLessEqual(len(encoded["history"]), 3000)
            self.assertEqual(snapshot_times, sorted(snapshot_times, reverse=True))
            self.assertIn("2026-09-06T11:00:00Z", snapshot_times)
            self.assertNotIn("2026-09-06T08:00:00Z", snapshot_times)
            self.assertIn("trimmed category=Currency history_points=4->", output.getvalue())

    def test_oversized_single_history_point_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "060926_11"
            folder.mkdir()
            (folder / "_manifest.json").write_text(json.dumps(manifest()), encoding="utf-8")
            (folder / "Currency.json").write_text(json.dumps(rich_document()), encoding="utf-8")

            previous_limit = compact.LIMITS["history"]
            compact.LIMITS["history"] = 200
            try:
                with self.assertRaises(ValueError) as error:
                    prepare(folder, ["Currency"])
            finally:
                compact.LIMITS["history"] = previous_limit
            self.assertIn("Currency/history current point exceeds 200 byte safety limit", str(error.exception))

    def test_large_upload_parts_are_bounded_and_checksummed(self):
        calls = []
        content = "x" * (PART_BYTES * 2 + 19)
        def request(url, endpoint, payload, secret):
            calls.append((endpoint, payload))
            if endpoint.endswith("ready"):
                return {"status": "SYNCING", "sync_id": "a" * 24}
            if endpoint.endswith("part"):
                self.assertLessEqual(len(payload["data"]), PART_BYTES)
                self.assertEqual(hashlib.sha256(payload["data"].encode()).hexdigest(), payload["sha256"])
            return {"status": "CURRENT", "completed_category_count": 1}
        upload("https://example.test/internal/market/snapshot-ready", "secret", {},
               {"Currency": {"current": content}}, request)
        parts = [p for endpoint, p in calls if endpoint.endswith("part")]
        self.assertEqual("".join(p["data"] for p in parts), content)
        self.assertEqual(len(parts), 3)
        self.assertTrue(calls[-1][0].endswith("finalize"))

    def test_raw_upload_hook_receives_metadata_and_chunk_separately(self):
        calls = []
        raw_parts = []
        content = "x" * (PART_BYTES + 19)

        def request(url, endpoint, payload, secret):
            calls.append((endpoint, payload))
            if endpoint.endswith("ready"):
                return {"status": "SYNCING", "sync_id": "a" * 24}
            return {"status": "CURRENT", "completed_category_count": 1}

        def raw_request(url, endpoint, metadata, chunk, secret):
            raw_parts.append((endpoint, metadata, chunk))
            self.assertTrue(endpoint.endswith("compact-part-raw"))
            self.assertNotIn("data", metadata)
            self.assertEqual(hashlib.sha256(chunk.encode("ascii")).hexdigest(), metadata["sha256"])
            return {"status": "UPLOADED"}

        upload("https://example.test/internal/market/snapshot-ready", "secret", {},
               {"Currency": {"current": content}}, request, raw_request)
        self.assertEqual("".join(chunk for _, _, chunk in raw_parts), content)
        self.assertEqual(len(raw_parts), 2)

    def test_unknown_pair_league_is_rejected(self):
        value = manifest()
        del value["pair_books"]["league"]
        with self.assertRaises(ValueError):
            validate_manifest(value)


if __name__ == "__main__":
    unittest.main()
