# ABOUTME: Tests for the /api/activity endpoint using a pre-baked digest fixture.
# ABOUTME: Verifies window filtering, sort order, and missing-file fallback.
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _make_digest(path: Path, repos: list[dict]) -> None:
    path.write_text(json.dumps({"generated_at": "2026-03-18T14:00:00Z", "repos": repos}))


def _now_minus(hours: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.isoformat()


def _build_activity_response(digest_path: Path, window: str = "48h") -> dict:
    """Call the activity API logic directly (extracted helper)."""
    from driftdriver.ecosystem_hub.api import _build_activity_payload
    return _build_activity_payload(digest_path, window)


class TestActivityAPI(unittest.TestCase):
    def test_missing_digest_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            result = _build_activity_response(path)
            self.assertEqual(result["timeline"], [])
            self.assertEqual(result["repos"], [])

    def test_window_48h_filters_timeline(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            recent_ts = _now_minus(2)    # 2 hours ago — inside 48h
            old_ts = _now_minus(60)     # 60 hours ago — outside 48h
            _make_digest(path, [
                {
                    "name": "lodestar",
                    "last_commit_at": recent_ts,
                    "last_commit_hash": "abc",
                    "summary": "Some work.",
                    "summary_hash": "abc",
                    "windows": {
                        "24h": {"count": 1, "subjects": ["feat: x"]},
                        "48h": {"count": 1, "subjects": ["feat: x"]},
                        "72h": {"count": 1, "subjects": ["feat: x"]},
                        "7d": {"count": 2, "subjects": ["feat: x", "old"]},
                    },
                    "timeline": [
                        {"repo": "lodestar", "hash": "abc", "timestamp": recent_ts, "subject": "feat: x", "author": "B"},
                        {"repo": "lodestar", "hash": "old", "timestamp": old_ts, "subject": "old", "author": "B"},
                    ],
                }
            ])
            result = _build_activity_response(path, "48h")
            self.assertEqual(len(result["timeline"]), 1)
            self.assertEqual(result["timeline"][0]["hash"], "abc")

    def test_repos_sorted_by_last_commit_at_descending(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            ts_new = _now_minus(1)
            ts_old = _now_minus(10)
            _make_digest(path, [
                {"name": "older", "last_commit_at": ts_old, "last_commit_hash": "x",
                 "summary": None, "summary_hash": None,
                 "windows": {"24h": {"count":1,"subjects":["a"]}, "48h": {"count":1,"subjects":["a"]},
                             "72h": {"count":1,"subjects":["a"]}, "7d": {"count":1,"subjects":["a"]}},
                 "timeline": [{"repo":"older","hash":"x","timestamp":ts_old,"subject":"a","author":"B"}]},
                {"name": "newer", "last_commit_at": ts_new, "last_commit_hash": "y",
                 "summary": None, "summary_hash": None,
                 "windows": {"24h": {"count":1,"subjects":["b"]}, "48h": {"count":1,"subjects":["b"]},
                             "72h": {"count":1,"subjects":["b"]}, "7d": {"count":1,"subjects":["b"]}},
                 "timeline": [{"repo":"newer","hash":"y","timestamp":ts_new,"subject":"b","author":"B"}]},
            ])
            result = _build_activity_response(path, "48h")
            self.assertEqual(result["repos"][0]["name"], "newer")
            self.assertEqual(result["repos"][1]["name"], "older")

    def test_window_count_reflects_selected_window(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "activity-digests.json"
            ts = _now_minus(3)
            _make_digest(path, [
                {"name": "repo", "last_commit_at": ts, "last_commit_hash": "h",
                 "summary": "Text.", "summary_hash": "h",
                 "windows": {"24h": {"count":1,"subjects":["a"]}, "48h": {"count":3,"subjects":["a","b","c"]},
                             "72h": {"count":5,"subjects":[]}, "7d": {"count":8,"subjects":[]}},
                 "timeline": [{"repo":"repo","hash":"h","timestamp":ts,"subject":"a","author":"B"}]},
            ])
            result = _build_activity_response(path, "48h")
            self.assertEqual(result["repos"][0]["window_count"], 3)
