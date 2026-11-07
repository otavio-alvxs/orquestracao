import unittest
import math

from rate_limits import RateLimitMonitor, sanitize_limits


class RateLimitTests(unittest.TestCase):
    def test_full_snapshot_is_sanitized_and_preserves_window_duration(self):
        result = sanitize_limits({"rateLimitsByLimitId": {"codex": {
            "limitId": "codex", "limitName": "Codex", "planType": "plus",
            "primary": {"windowDurationMins": 300, "usedPercent": 42, "resetsAt": 1234},
            "secondary": {"windowDurationMins": 10080, "usedPercent": 9, "resetsAt": 5678},
            "secret": "must not escape"}}})
        self.assertEqual(result["buckets"]["codex"]["primary"],
                         {"window_minutes": 300, "used_percent": 42, "resets_at": 1234})
        self.assertEqual(result["buckets"]["codex"]["secondary"]["window_minutes"], 10080)
        self.assertNotIn("secret", repr(result))

    def test_sparse_update_merges_bucket_windows_and_metadata(self):
        previous = sanitize_limits({"rateLimits": {"limitId": "codex", "limitName": "Codex",
            "primary": {"windowDurationMins": 300, "usedPercent": 20, "resetsAt": 10},
            "secondary": {"windowDurationMins": 10080, "usedPercent": 30, "resetsAt": 20}}})
        updated = sanitize_limits({"rateLimits": {"limitId": "codex",
            "primary": {"windowDurationMins": 300, "usedPercent": 50, "resetsAt": 11}}}, previous)
        bucket = updated["buckets"]["codex"]
        self.assertEqual(bucket["limit_name"], "Codex")
        self.assertEqual(bucket["primary"]["used_percent"], 50)
        self.assertEqual(bucket["secondary"]["used_percent"], 30)

    def test_partial_window_merges_fields_and_accepts_finite_float(self):
        previous = sanitize_limits({"rateLimits": {"limitId": "codex", "primary": {
            "windowDurationMins": 300, "usedPercent": 20, "resetsAt": 10}}})
        updated = sanitize_limits({"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 42.5}}}, previous)
        self.assertEqual(updated["buckets"]["codex"]["primary"],
                         {"window_minutes": 300, "used_percent": 42.5, "resets_at": 10})
        self.assertIsNone(sanitize_limits({"rateLimits": {"primary": {
            "usedPercent": math.inf}}})["buckets"]["codex"]["primary"]["used_percent"])

    def test_multiple_buckets_partial_notification_keeps_other_bucket(self):
        monitor = RateLimitMonitor()
        initial = sanitize_limits({"rateLimitsByLimitId": {
            "model-a": {"limitId": "model-a", "primary": {"windowDurationMins": 300, "usedPercent": 10}},
            "model-b": {"limitId": "model-b", "secondary": {"windowDurationMins": 10080, "usedPercent": 90}}}})
        initial["buckets"]["model-b"]["window_updated_at"]["secondary"] = 1
        monitor.snapshot.update(initial)
        monitor._merge_update({"rateLimits": {"limitId": "model-a", "primary": {"usedPercent": 30}}})
        buckets = monitor.snapshot["buckets"]
        self.assertEqual(buckets["model-a"]["primary"]["used_percent"], 30)
        self.assertEqual(buckets["model-b"]["secondary"]["used_percent"], 90)
        self.assertEqual(buckets["model-b"]["window_updated_at"]["secondary"], 1)
        self.assertIsNone(buckets["model-a"]["secondary"])
        self.assertIsNone(buckets["model-b"]["primary"])
        self.assertEqual(sanitize_limits({})["buckets"], {})

    def test_only_read_methods_are_allowed(self):
        monitor = RateLimitMonitor()
        with self.assertRaises(ValueError):
            import asyncio
            asyncio.run(monitor._rpc("thread/start"))


class RateLimitLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_codex_does_not_block_start_and_retries(self):
        import asyncio
        from unittest.mock import patch

        monitor = RateLimitMonitor(interval=.01)
        updates = []
        monitor.on_update = updates.append
        with patch("rate_limits.shutil.which", return_value=None):
            await asyncio.wait_for(monitor.start("/tmp"), .1)
            await asyncio.sleep(.035)
        self.assertTrue(monitor.poller and not monitor.poller.done())
        self.assertEqual(monitor.snapshot["status"], "disconnected")
        self.assertGreaterEqual(len(updates), 1)
        await monitor.close()


if __name__ == "__main__":
    unittest.main()
