import hashlib
import hmac
import unittest

from sync_market_snapshot import CATEGORIES, signed_headers, sync_snapshot


class MarketSyncTests(unittest.TestCase):
    def test_signature_binds_path_and_body(self):
        body = b'{"ok":true}'
        headers = signed_headers("secret", "/internal/market/snapshot-ready", body, "100", "nonce")
        canonical = "POST\n/internal/market/snapshot-ready\n100\nnonce\n" + hashlib.sha256(body).hexdigest()
        expected = hmac.new(b"secret", canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Market-Signature"], expected)

    def test_fanout_skips_completed_and_finalizes(self):
        calls = []
        def request(url, endpoint, payload, secret):
            calls.append((endpoint, payload.get("category")))
            if endpoint.endswith("snapshot-ready"):
                return {"status": "SOURCE_PENDING", "sync_id": "a" * 16,
                        "completed_categories": [CATEGORIES[0]]}
            if endpoint.endswith("snapshot-category"):
                return {"status": "STAGED", "completed_category_count": len(calls) - 1}
            return {"status": "CURRENT", "version": "a" * 16}
        result = sync_snapshot("https://example.test", "secret", {
            "snapshot_folder": "030926_05", "commit_sha": "abcdef1",
            "completed_at": "2026-09-03T05:27:53Z", "pair_books_total": 10}, request)
        staged = [category for endpoint, category in calls if endpoint.endswith("snapshot-category")]
        self.assertEqual(staged, list(CATEGORIES[1:]))
        self.assertEqual(calls[-1][0], "/internal/market/snapshot-finalize")
        self.assertEqual(result["status"], "CURRENT")

    def test_current_registration_does_not_stage(self):
        calls = []
        def request(url, endpoint, payload, secret):
            calls.append(endpoint)
            return {"status": "CURRENT", "sync_id": "b" * 16}
        sync_snapshot("https://example.test", "secret", {
            "snapshot_folder": "030926_05", "commit_sha": "abcdef1",
            "completed_at": "2026-09-03T05:27:53Z", "pair_books_total": 10}, request)
        self.assertEqual(calls, ["/internal/market/snapshot-ready"])

    def test_legacy_full_webhook_url_is_normalized(self):
        bases = []
        def request(url, endpoint, payload, secret):
            bases.append(url)
            return {"status": "CURRENT", "sync_id": "b" * 16}
        sync_snapshot("https://example.test/internal/market/snapshot-ready", "secret", {
            "snapshot_folder": "030926_05", "commit_sha": "abcdef1",
            "completed_at": "2026-09-03T05:27:53Z", "pair_books_total": 10}, request)
        self.assertEqual(bases, ["https://example.test"])


if __name__ == "__main__":
    unittest.main()
