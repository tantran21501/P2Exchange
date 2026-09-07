import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_market_compact import LEAGUE, PART_BYTES, prepare, upload, validate_manifest


def manifest(league=LEAGUE):
    return {"league": {"value": league}, "pair_books": {
        "league": league, "observed_at": "2026-09-06T11:27:55Z", "total": 2}}


def document(price=2):
    return {"lines": [{"id": "chaos", "primaryValue": price}], "pairs": [
        {"from": "divine", "to": "chaos", "rate": 0.4, "volume": 12,
         "observed": True, "independent": False, "source": "poe2scout-snapshot-estimate"}]}


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
