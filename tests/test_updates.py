from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from driftdriver.updates import (
    _parse_iso,
    check_ecosystem_updates,
    fetch_github_head,
    fetch_report_content,
    load_review_config,
    load_update_state,
    summarize_updates,
)


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

    def test_user_watch_detects_new_repo_after_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            calls = {"n": 0}

            def user_fetcher(_user: str, _limit: int) -> list[dict]:
                calls["n"] += 1
                if calls["n"] == 1:
                    return [
                        {
                            "full_name": "jesse/alpha",
                            "html_url": "https://github.com/jesse/alpha",
                            "description": "alpha",
                            "pushed_at": "2026-02-18T00:00:00Z",
                            "updated_at": "2026-02-18T00:00:00Z",
                        }
                    ]
                return [
                    {
                        "full_name": "jesse/alpha",
                        "html_url": "https://github.com/jesse/alpha",
                        "description": "alpha",
                        "pushed_at": "2026-02-18T00:00:00Z",
                        "updated_at": "2026-02-18T00:00:00Z",
                    },
                    {
                        "full_name": "jesse/beta",
                        "html_url": "https://github.com/jesse/beta",
                        "description": "beta",
                        "pushed_at": "2026-02-19T00:00:00Z",
                        "updated_at": "2026-02-19T00:00:00Z",
                    },
                ]

            first = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos={},
                users=["jesse"],
                user_fetcher=user_fetcher,
            )
            second = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos={},
                users=["jesse"],
                user_fetcher=user_fetcher,
            )

            self.assertFalse(first["has_discoveries"])
            self.assertTrue(second["has_discoveries"])
            self.assertEqual(len(second["user_findings"]), 1)
            self.assertEqual(second["user_findings"][0]["kind"], "new_repo")
            self.assertEqual(second["user_findings"][0]["repo"], "jesse/beta")

    def test_report_watch_detects_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            calls = {"n": 0}

            def report_fetcher(_url: str) -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    return "Initial report\nNo findings yet."
                return "Updated report\nWorkgraph and amplifier are valuable."

            first = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos={},
                reports=[
                    {
                        "name": "bibez-report",
                        "url": "https://example.com/bibez",
                        "keywords": ["workgraph"],
                    }
                ],
                report_keywords=["amplifier"],
                report_fetcher=report_fetcher,
            )
            second = check_ecosystem_updates(
                wg_dir=wg_dir,
                interval_seconds=0,
                repos={},
                reports=[
                    {
                        "name": "bibez-report",
                        "url": "https://example.com/bibez",
                        "keywords": ["workgraph"],
                    }
                ],
                report_keywords=["amplifier"],
                report_fetcher=report_fetcher,
            )

            self.assertFalse(first["has_discoveries"])
            self.assertTrue(second["has_discoveries"])
            self.assertEqual(len(second["report_findings"]), 1)
            finding = second["report_findings"][0]
            self.assertEqual(finding["kind"], "report_changed")
            self.assertEqual(finding["name"], "bibez-report")
            self.assertTrue(finding["keyword_hits"])

    def test_load_review_config_from_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            cfg_path = wg_dir / ".driftdriver" / "ecosystem-review.json"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(
                (
                    "{\n"
                    '  "extra_repos": {"workgraph": "graphwork/workgraph"},\n'
                    '  "github_users": ["jesse", "2389"],\n'
                    '  "reports": [{"name": "bibez", "url": "https://example.com/bibez"}],\n'
                    '  "report_keywords": ["workgraph", "amplifier"],\n'
                    '  "user_repo_limit": 25\n'
                    "}\n"
                ),
                encoding="utf-8",
            )
            cfg = load_review_config(wg_dir)
            self.assertTrue(cfg["exists"])
            self.assertEqual(cfg["extra_repos"]["workgraph"], "graphwork/workgraph")
            self.assertEqual(cfg["github_users"], ["jesse", "2389"])
            self.assertEqual(cfg["reports"][0]["name"], "bibez")
            self.assertEqual(cfg["user_repo_limit"], 25)


def test_parse_iso_none():
    assert _parse_iso(None) is None


def test_parse_iso_empty_string():
    assert _parse_iso("") is None


def test_parse_iso_z_suffix():
    result = _parse_iso("2024-01-15T10:30:00Z")
    assert result is not None
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15


def test_parse_iso_invalid():
    assert _parse_iso("not-a-date") is None


def test_parse_iso_valid_iso():
    result = _parse_iso("2024-01-15T10:30:00+00:00")
    assert result is not None
    assert result.year == 2024
    assert result.month == 1


def test_fetch_github_head_404_no_token() -> None:
    """HTTPError 404 without a token raises RuntimeError with '404 for' prefix, not NameError."""
    err = HTTPError(
        url="https://api.github.com/repos/some/repo/commits/main",
        code=404,
        msg="Not Found",
        hdrs={},  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )
    clean_env = {k: v for k, v in os.environ.items()
                 if k not in ("DRIFTDRIVER_GITHUB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")}
    with patch("driftdriver.updates.urlopen", side_effect=err), \
            patch.dict("os.environ", clean_env, clear=True):
        try:
            fetch_github_head("some/repo")
            assert False, "Expected RuntimeError"
        except RuntimeError as exc:
            assert "404" in str(exc), f"Expected '404' in message, got: {exc}"


def test_fetch_report_rejects_file_url() -> None:
    """file:// URLs must raise RuntimeError immediately without reading the file."""
    try:
        fetch_report_content("file:///etc/passwd")
        assert False, "Expected RuntimeError for file:// URL"
    except RuntimeError as exc:
        assert "file" in str(exc).lower(), f"Unexpected message: {exc}"


if __name__ == "__main__":
    unittest.main()
