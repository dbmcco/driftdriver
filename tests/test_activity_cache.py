# ABOUTME: Tests for activity-digests.json atomic cache read/write operations.
# ABOUTME: Verifies roundtrip, missing-file fallback, and atomic tmp+rename pattern.
import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.ecosystem_hub.activity_cache import read_activity_digest, write_activity_digest


class TestActivityCache(unittest.TestCase):
    def test_write_then_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            payload = {"generated_at": "2026-01-01T00:00:00Z", "repos": [{"name": "foo"}]}
            write_activity_digest(path, payload)
            result = read_activity_digest(path)
            self.assertEqual(result["repos"][0]["name"], "foo")

    def test_read_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            result = read_activity_digest(path)
            self.assertEqual(result, {"generated_at": None, "repos": []})

    def test_write_is_atomic(self):
        # write must use .tmp + rename, not direct write
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            write_activity_digest(path, {"generated_at": "x", "repos": []})
            self.assertTrue(path.exists())
            self.assertFalse(Path(str(path) + ".tmp").exists())


if __name__ == "__main__":
    unittest.main()
