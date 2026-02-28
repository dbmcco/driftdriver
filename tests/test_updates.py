from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driftdriver.updates import _parse_iso, check_ecosystem_updates, load_update_state, summarize_updates


class UpdateChecksTests(unittest.TestCase):
    def test_first_run_records_sha_without_flagging_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            result = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos={"coredrift": "dbmcco/coredrift"},
                fetcher=lambda _repo: ("abc123", "2026-02-18T00:00:00Z"),
            )

            self.assertFalse(result["skipped"])
            self.assertFalse(result["has_updates"])
            self.assertEqual(result["updates"], [])

            state = load_update_state(wg_dir)
            self.assertEqual(state["repos"]["coredrift"]["sha"], "abc123")

    def test_second_run_detects_sha_change(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            repos = {"coredrift": "dbmcco/coredrift"}
            calls = {"n": 0}

            def fetch(_repo: str) -> tuple[str, str]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return ("abc123", "2026-02-18T00:00:00Z")
                return ("def456", "2026-02-19T00:00:00Z")

            first = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos=repos,
                fetcher=fetch,
            )
            second = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos=repos,
                fetcher=fetch,
            )

            self.assertFalse(first["has_updates"])
            self.assertTrue(second["has_updates"])
            self.assertEqual(len(second["updates"]), 1)
            self.assertEqual(second["updates"][0]["previous_sha"], "abc123")
            self.assertEqual(second["updates"][0]["current_sha"], "def456")

    def test_interval_skip_avoids_remote_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            calls = {"n": 0}

            def fetch(_repo: str) -> tuple[str, str]:
                calls["n"] += 1
                return ("abc123", "2026-02-18T00:00:00Z")

            check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=3600,
                repos={"coredrift": "dbmcco/coredrift"},
                fetcher=fetch,
            )
            skipped = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=3600,
                repos={"coredrift": "dbmcco/coredrift"},
                fetcher=fetch,
            )

            self.assertEqual(calls["n"], 1)
            self.assertTrue(skipped["skipped"])
            self.assertEqual(skipped["reason"], "interval_not_elapsed")

    def test_fetch_errors_are_reported_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            def failing_fetcher(_repo: str) -> tuple[str, str]:
                raise RuntimeError("boom")

            result = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos={"coredrift": "dbmcco/coredrift"},
                fetcher=failing_fetcher,
            )

            self.assertFalse(result["has_updates"])
            self.assertEqual(len(result["repos"]), 1)
            self.assertIn("boom", str(result["repos"][0]["error"]))

    def test_summary_contains_self_update_prompt(self) -> None:
        summary = summarize_updates(
            {
                "updates": [
                    {
                        "tool": "coredrift",
                        "previous_sha": "abc123",
                        "current_sha": "def456",
                    }
                ]
            }
        )
        self.assertIn("Speedrift ecosystem updates detected", summary)
        self.assertIn("Decision needed: should the model/toolchain self-update now?", summary)


def test_parse_iso_none():
    assert _parse_iso(None) is None


def test_parse_iso_empty_string():
    assert _parse_iso("") is None


def test_parse_iso_z_suffix():
    result = _parse_iso("2024-01-15T10:30:00Z")
    assert result is not None


def test_parse_iso_invalid():
    assert _parse_iso("not-a-date") is None


def test_parse_iso_valid_iso():
    result = _parse_iso("2024-01-15T10:30:00+00:00")
    assert result is not None


if __name__ == "__main__":
    unittest.main()
