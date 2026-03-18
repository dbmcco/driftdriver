# ABOUTME: Tests for the git activity scanner using real git fixture repos.
# ABOUTME: Uses subprocess git init/commit to create controlled commit histories.
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from driftdriver.ecosystem_hub.activity import scan_repo_activity, scan_all_repos


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(
        ["git"] + args, cwd=cwd, check=True,
        capture_output=True, env={
            "HOME": str(Path.home()),
            "GIT_AUTHOR_NAME": "Braydon",
            "GIT_AUTHOR_EMAIL": "b@mcco.us",
            "GIT_COMMITTER_NAME": "Braydon",
            "GIT_COMMITTER_EMAIL": "b@mcco.us",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
    )


def _make_repo(path: Path, commits: list[tuple[str, str]]) -> None:
    """Create a git repo with commits. Each commit is (subject, file_content)."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init"], path)
    _git(["config", "user.email", "b@mcco.us"], path)
    _git(["config", "user.name", "Braydon"], path)
    for subject, content in commits:
        f = path / "file.txt"
        f.write_text(content, encoding="utf-8")
        _git(["add", "."], path)
        _git(["commit", "-m", subject], path)


class TestScanRepoActivity(unittest.TestCase):
    def test_recent_commit_appears_in_24h_window(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "myrepo"
            _make_repo(repo, [("feat: add thing", "v1")])
            result = scan_repo_activity("myrepo", repo)
            self.assertIsNotNone(result)
            self.assertEqual(result["windows"]["24h"]["count"], 1)
            self.assertIn("feat: add thing", result["windows"]["24h"]["subjects"])

    def test_non_git_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "notgit"
            path.mkdir()
            result = scan_repo_activity("notgit", path)
            self.assertIsNone(result)

    def test_missing_repo_path_returns_none(self):
        result = scan_repo_activity("ghost", Path("/does/not/exist"))
        self.assertIsNone(result)

    def test_zero_commit_repo_returns_empty_windows(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "empty"
            repo.mkdir()
            _git(["init"], repo)
            _git(["config", "user.email", "b@mcco.us"], repo)
            _git(["config", "user.name", "Braydon"], repo)
            result = scan_repo_activity("empty", repo)
            self.assertIsNotNone(result)
            self.assertEqual(result["last_commit_hash"], None)
            self.assertEqual(result["windows"]["24h"]["count"], 0)

    def test_multiple_commits_counted_correctly(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "multi"
            _make_repo(repo, [
                ("feat: one", "v1"),
                ("fix: two", "v2"),
                ("chore: three", "v3"),
            ])
            result = scan_repo_activity("multi", repo)
            self.assertEqual(result["windows"]["24h"]["count"], 3)
            self.assertEqual(result["windows"]["7d"]["count"], 3)

    def test_scan_all_repos_skips_none_results(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "good"
            _make_repo(good, [("feat: x", "v1")])
            bad = Path(td) / "notgit"
            bad.mkdir()
            repos = {"good": good, "notgit": bad}
            results = scan_all_repos(repos)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["name"], "good")
