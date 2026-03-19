# Git Activity Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add git-commit-backed activity tracking to the Speedrift Ecosystem Hub — a cross-repo timeline and per-repo LLM summaries, updated every 15 minutes.

**Architecture:** Three new modules (activity_cache, activity, activity_summarizer) slot into the hub's existing collector-thread + snapshot-cache pattern. A new background thread scans git log for all registered repos, caches results atomically, and the existing API handler class is extended with a `/api/activity` endpoint. The dashboard gets a timeline panel at the top and an inline activity row per repo card.

**Tech Stack:** Python 3.14, `anthropic` SDK (already installed), `git` subprocess, `threading`, `unittest` + real git fixtures.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `driftdriver/ecosystem_hub/activity_cache.py` | Atomic read/write for `activity-digests.json` |
| Create | `driftdriver/ecosystem_hub/activity.py` | Git scanner — iterates repos, runs `git log`, builds digest |
| Create | `driftdriver/ecosystem_hub/activity_summarizer.py` | LLM digest — Haiku summaries for repos with new commits |
| Modify | `driftdriver/ecosystem_hub/snapshot.py` | Add `"activity"` key to `service_paths()` return dict |
| Modify | `driftdriver/ecosystem_hub/server.py` | Add `activity-scanner` background thread; pass activity path to handler |
| Modify | `driftdriver/ecosystem_hub/api.py` | Add `activity_path` class var; add `GET /api/activity` route |
| Modify | `driftdriver/ecosystem_hub/dashboard.py` | Add timeline panel HTML+JS; add inline activity row per repo |
| Create | `tests/test_activity_scanner.py` | Scanner tests with real git fixture |
| Create | `tests/test_activity_summarizer.py` | Summarizer tests with mocked anthropic client |
| Create | `tests/test_activity_api.py` | API handler tests with pre-baked fixture |

---

## Task 1: activity_cache.py — atomic read/write

**Files:**
- Create: `driftdriver/ecosystem_hub/activity_cache.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_activity_cache.py  (temporary — merged into test_activity_api.py in Task 8)
import json, tempfile, unittest
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_activity_cache.py -v
```
Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement activity_cache.py**

```python
# ABOUTME: Atomic read/write wrapper for activity-digests.json in the hub service dir.
# ABOUTME: Mirrors the _write_json/_read_json pattern from discovery.py for consistency.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_activity_digest(path: Path) -> dict[str, Any]:
    """Read activity-digests.json. Returns empty structure if file does not exist."""
    if not path.exists():
        return {"generated_at": None, "repos": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "repos": []}


def write_activity_digest(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write activity-digests.json using tmp+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_activity_cache.py -v
```
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/activity_cache.py tests/test_activity_cache.py
git commit -m "feat(activity): add atomic activity-digests cache read/write"
```

---

## Task 2: activity.py — git scanner

**Files:**
- Create: `driftdriver/ecosystem_hub/activity.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_activity_scanner.py`:

```python
# ABOUTME: Tests for the git activity scanner using real git fixture repos.
# ABOUTME: Uses subprocess git init/commit to create controlled commit histories.
from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
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
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_activity_scanner.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement activity.py**

```python
# ABOUTME: Git activity scanner for the ecosystem hub.
# ABOUTME: Scans git log for all registered repos and builds windowed commit digests.
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# 8 days gives full 7d window plus 1 day buffer for timezone boundary safety.
_LOOKBACK_DAYS = 8

_WINDOWS: dict[str, int] = {"24h": 1, "48h": 2, "72h": 3, "7d": 7}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_git(args: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, result.stdout
    except Exception as exc:
        _LOG.debug("git command failed in %s: %s", cwd, exc)
        return 1, ""


def scan_repo_activity(name: str, path: Path) -> dict[str, Any] | None:
    """Scan git log for a single repo. Returns None if repo is not git or on error."""
    if not path.exists():
        return None

    # Verify it's a git repo
    rc, _ = _run_git(["rev-parse", "--git-dir"], path)
    if rc != 0:
        return None

    since = f"{_LOOKBACK_DAYS} days ago"

    # Fetch commit metadata: hash|ISO timestamp|subject|author name|author email
    rc, log_out = _run_git(
        ["log", f"--since={since}", "--format=%H|%aI|%s|%an|%ae", "HEAD"],
        path,
    )
    if rc != 0:
        _LOG.debug("git log failed for %s", name)
        return None

    # Fetch changed filenames (blank-line separated per commit, name-only)
    _, files_out = _run_git(
        ["log", f"--since={since}", "--name-only", "--format=", "HEAD"],
        path,
    )
    changed_files = sorted(set(
        line.strip() for line in files_out.splitlines() if line.strip()
    ))

    commits: list[dict[str, Any]] = []
    now = _utc_now()

    for line in log_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 3:
            continue
        hash_, ts_raw, subject = parts[0], parts[1], parts[2]
        author = parts[3] if len(parts) > 3 else ""
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts = ts.astimezone(timezone.utc)
        except ValueError:
            continue
        commits.append({"hash": hash_, "timestamp": ts, "subject": subject, "author": author})

    # Sort newest-first
    commits.sort(key=lambda c: c["timestamp"], reverse=True)

    last_commit_hash = commits[0]["hash"] if commits else None
    last_commit_at = commits[0]["timestamp"].isoformat() if commits else None

    # Build windows
    windows: dict[str, dict[str, Any]] = {}
    for label, days in _WINDOWS.items():
        cutoff = now - timedelta(days=days)
        window_commits = [c for c in commits if c["timestamp"] >= cutoff]
        windows[label] = {
            "count": len(window_commits),
            "subjects": [c["subject"] for c in window_commits],
        }

    # Build timeline entries (for /api/activity timeline)
    timeline = [
        {
            "repo": name,
            "hash": c["hash"],
            "timestamp": c["timestamp"].isoformat(),
            "subject": c["subject"],
            "author": c["author"],
        }
        for c in commits
    ]

    return {
        "name": name,
        "path": str(path),
        "last_commit_at": last_commit_at,
        "last_commit_hash": last_commit_hash,
        "summary": None,          # filled by summarizer
        "summary_hash": None,     # hash that summary was built from
        "changed_files": changed_files[:50],  # cap for LLM prompt safety
        "windows": windows,
        "timeline": timeline,
    }


def scan_all_repos(repos: dict[str, Path]) -> list[dict[str, Any]]:
    """Scan all repos. Skips repos that are not git or have errors. Returns list of digests."""
    results = []
    for name, path in repos.items():
        try:
            entry = scan_repo_activity(name, path)
            if entry is not None:
                results.append(entry)
        except Exception:
            _LOG.debug("Unexpected error scanning %s", name, exc_info=True)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_activity_scanner.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/activity.py tests/test_activity_scanner.py
git commit -m "feat(activity): git scanner with windowed commit digest"
```

---

## Task 3: activity_summarizer.py — Haiku LLM digest

**Files:**
- Create: `driftdriver/ecosystem_hub/activity_summarizer.py`
- Create: `tests/test_activity_summarizer.py`

- [ ] **Step 1: Write the failing test**

```python
# ABOUTME: Tests for the LLM activity summarizer with mocked anthropic client.
# ABOUTME: Verifies cache-key logic, fallback on error, and prompt content.
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestActivitySummarizer(unittest.TestCase):
    def _make_digest(self, hash_: str, summary_hash: str | None = None) -> dict:
        return {
            "name": "lodestar",
            "last_commit_hash": hash_,
            "summary": None,
            "summary_hash": summary_hash,
            "changed_files": ["src/scenario.py", "src/briefings.py"],
            "windows": {
                "7d": {"count": 3, "subjects": ["feat: add regret scoring", "fix: briefing 404", "chore: deps"]},
                "24h": {"count": 1, "subjects": ["feat: add regret scoring"]},
                "48h": {"count": 2, "subjects": ["feat: add regret scoring", "fix: briefing 404"]},
                "72h": {"count": 3, "subjects": ["feat: add regret scoring", "fix: briefing 404", "chore: deps"]},
            },
        }

    def test_summary_requested_when_hash_differs(self):
        digest = self._make_digest("abc123", summary_hash=None)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Work happened on lodestar.")]
        )
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        mock_client.messages.create.assert_called_once()
        self.assertEqual(result["summary"], "Work happened on lodestar.")
        self.assertEqual(result["summary_hash"], "abc123")

    def test_summary_skipped_when_hash_matches(self):
        digest = self._make_digest("abc123", summary_hash="abc123")
        digest["summary"] = "Existing summary."
        mock_client = MagicMock()
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        mock_client.messages.create.assert_not_called()
        self.assertEqual(result["summary"], "Existing summary.")

    def test_fallback_to_none_on_api_error(self):
        digest = self._make_digest("abc123")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API down")
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        self.assertIsNone(result["summary"])
        self.assertIsNone(result["summary_hash"])

    def test_prompt_contains_commit_subjects_and_files(self):
        digest = self._make_digest("abc123")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Summary text.")]
        )
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        summarize_repo(digest, client=mock_client)
        call_kwargs = mock_client.messages.create.call_args
        prompt_text = call_kwargs[1]["messages"][0]["content"]
        self.assertIn("feat: add regret scoring", prompt_text)
        self.assertIn("src/scenario.py", prompt_text)
        self.assertIn("lodestar", prompt_text)

    def test_no_commits_skips_summarization(self):
        digest = self._make_digest(None)
        mock_client = MagicMock()
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        mock_client.messages.create.assert_not_called()
        self.assertIsNone(result["summary"])
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_activity_summarizer.py -v
```
Expected: `ImportError`

- [ ] **Step 3: Implement activity_summarizer.py**

```python
# ABOUTME: LLM-based activity summarizer for the ecosystem hub.
# ABOUTME: Calls Claude Haiku to produce 2-3 sentence repo activity summaries.
# ABOUTME: Only re-summarizes when last_commit_hash != summary_hash.
from __future__ import annotations

import logging
from typing import Any

_LOG = logging.getLogger(__name__)
_MODEL = "claude-haiku-4-5-20251001"


def _build_prompt(digest: dict[str, Any]) -> str:
    name = digest["name"]
    subjects = digest["windows"]["7d"]["subjects"]
    files = digest.get("changed_files", [])

    commit_lines = "\n".join(f"- {s}" for s in subjects[:20])
    file_dirs = sorted(set(
        "/".join(f.split("/")[:2]) if "/" in f else f
        for f in files[:20]
    ))
    file_summary = ", ".join(file_dirs[:10]) if file_dirs else "various files"

    return (
        f"Repo: {name}\n"
        f"Recent commits (last 7 days):\n{commit_lines}\n"
        f"Changed files: {file_summary}\n\n"
        f"Write 2-3 sentences describing what's been happening in this repo. "
        f"Be specific about what was built or fixed. No filler."
    )


def summarize_repo(digest: dict[str, Any], *, client: Any = None) -> dict[str, Any]:
    """
    Add or refresh the LLM summary for a single repo digest.
    Returns the digest with summary/summary_hash updated.
    Skips if hash matches or no commits exist.
    """
    last_hash = digest.get("last_commit_hash")
    if not last_hash:
        return digest

    if digest.get("summary_hash") == last_hash and digest.get("summary"):
        return digest

    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:
            _LOG.debug("anthropic not available, skipping summarization")
            return digest

    prompt = _build_prompt(digest)
    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.content[0].text.strip()
        return {**digest, "summary": summary, "summary_hash": last_hash}
    except Exception as exc:
        _LOG.debug("Haiku summarization failed for %s: %s", digest["name"], exc)
        return {**digest, "summary": None, "summary_hash": None}


def summarize_all(digests: list[dict[str, Any]], *, client: Any = None) -> list[dict[str, Any]]:
    """Run summarize_repo for all digests that need a new summary."""
    if client is None:
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:
            _LOG.debug("anthropic not available, returning digests unsummarized")
            return digests

    return [summarize_repo(d, client=client) for d in digests]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_activity_summarizer.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/activity_summarizer.py tests/test_activity_summarizer.py
git commit -m "feat(activity): Haiku LLM summarizer with cache-key dedup"
```

---

## Task 4: Wire scanner thread + service_paths

**Files:**
- Modify: `driftdriver/ecosystem_hub/snapshot.py` (service_paths function)
- Modify: `driftdriver/ecosystem_hub/server.py` (add thread + pass path to handler)

- [ ] **Step 1: Add "activity" to service_paths in snapshot.py**

Find `service_paths` (around line 879) and add one line:

```python
def service_paths(project_dir: Path) -> dict[str, Path]:
    base = project_dir / ".workgraph" / "service" / "ecosystem-hub"
    return {
        "dir": base,
        "pid": base / "pid",
        "state": base / "state.json",
        "heartbeat": base / "heartbeat.json",
        "snapshot": base / "snapshot.json",
        "activity": base / "activity-digests.json",   # ← add this
        "log": base / "hub.log",
    }
```

- [ ] **Step 2: Add activity scanner thread in server.py**

After the existing collector thread is started (around line 549), add:

```python
    from driftdriver.ecosystem_hub.activity import scan_all_repos
    from driftdriver.ecosystem_hub.activity_cache import read_activity_digest, write_activity_digest
    from driftdriver.ecosystem_hub.activity_summarizer import summarize_all
    from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repos

    _ACTIVITY_INTERVAL = 15 * 60  # 15 minutes

    def _activity_scanner_loop() -> None:
        while not stop_event.is_set():
            try:
                # Collect repos from ecosystem.toml + discovery
                repo_map: dict[str, Path] = {}
                if ecosystem_toml and ecosystem_toml.exists():
                    repo_map.update(_load_ecosystem_repos(ecosystem_toml, workspace_root))
                # Scan git log for all repos
                raw_digests = scan_all_repos(repo_map)
                # Load existing digest to preserve summaries for unchanged repos
                existing = read_activity_digest(paths["activity"])
                existing_by_name = {r["name"]: r for r in existing.get("repos", [])}
                # Merge: carry over summary if hash unchanged
                merged = []
                for d in raw_digests:
                    prev = existing_by_name.get(d["name"], {})
                    if prev.get("summary_hash") == d.get("last_commit_hash") and prev.get("summary"):
                        d = {**d, "summary": prev["summary"], "summary_hash": prev["summary_hash"]}
                    merged.append(d)
                # Summarize repos with new commits
                summarized = summarize_all(merged)
                write_activity_digest(paths["activity"], {
                    "generated_at": _iso_now(),
                    "repos": summarized,
                })
            except Exception:
                _log.debug("Activity scanner error", exc_info=True)
            stop_event.wait(_ACTIVITY_INTERVAL)

    activity_scanner = threading.Thread(
        target=_activity_scanner_loop,
        name="activity-scanner",
        daemon=True,
    )
    activity_scanner.start()
```

- [ ] **Step 3: Pass activity_path to handler factory**

In server.py, find the handler_cls line (~561) and update:

```python
    handler_cls = _handler_factory(paths["snapshot"], paths["state"], live_hub, paths["activity"])
```

- [ ] **Step 4: Run existing hub tests to verify nothing is broken**

```bash
uv run pytest tests/test_ecosystem_hub.py -v -x --timeout=30
```
Expected: existing tests pass (or same failures as before this change)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/snapshot.py driftdriver/ecosystem_hub/server.py
git commit -m "feat(activity): wire activity scanner thread into hub service"
```

---

## Task 5: /api/activity endpoint in api.py

**Files:**
- Modify: `driftdriver/ecosystem_hub/api.py`
- Create: `tests/test_activity_api.py`

- [ ] **Step 1: Write the failing test**

```python
# ABOUTME: Tests for the /api/activity endpoint using a pre-baked digest fixture.
# ABOUTME: Verifies window filtering, sort order, and missing-file fallback.
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


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
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_activity_api.py -v
```
Expected: `ImportError` on `_build_activity_payload`

- [ ] **Step 3: Add `_build_activity_payload` helper and `/api/activity` route to api.py**

Add near top of api.py (after existing imports):
```python
from .activity_cache import read_activity_digest
```

Add helper function before `_handler_factory`:
```python
def _build_activity_payload(activity_path: Path, window: str = "48h") -> dict[str, Any]:
    """Build the /api/activity response from the cached digest file."""
    from datetime import datetime, timedelta, timezone

    valid_windows = {"24h": 1, "48h": 2, "72h": 3, "7d": 7}
    days = valid_windows.get(window, 2)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    digest = read_activity_digest(activity_path)
    all_repos = digest.get("repos") or []

    # Build flat timeline filtered to window
    timeline: list[dict[str, Any]] = []
    for repo_entry in all_repos:
        for commit in repo_entry.get("timeline", []):
            try:
                ts = datetime.fromisoformat(commit["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    timeline.append(commit)
            except (ValueError, KeyError):
                continue
    timeline.sort(key=lambda c: c["timestamp"], reverse=True)

    # Build per-repo summary filtered to window
    repos_out: list[dict[str, Any]] = []
    for repo_entry in all_repos:
        window_data = (repo_entry.get("windows") or {}).get(window, {})
        count = window_data.get("count", 0)
        if count == 0 and not repo_entry.get("last_commit_at"):
            continue
        repos_out.append({
            "name": repo_entry.get("name"),
            "last_commit_at": repo_entry.get("last_commit_at"),
            "summary": repo_entry.get("summary"),
            "window_count": count,
        })

    # Sort repos by last_commit_at descending
    def _ts_key(r: dict[str, Any]) -> str:
        return r.get("last_commit_at") or ""

    repos_out.sort(key=_ts_key, reverse=True)

    return {
        "generated_at": digest.get("generated_at"),
        "window": window,
        "timeline": timeline,
        "repos": repos_out,
    }
```

Add `activity_path` class attribute and route in `_HubHandler`:

In `_HubHandler` class body, add after `state_path: Path`:
```python
    activity_path: Path
```

In `do_GET`, add after the `/api/pressure` block:
```python
        if route == "/api/activity":
            params = self.path.split("?", 1)
            window = "48h"
            if len(params) > 1:
                for part in params[1].split("&"):
                    if part.startswith("window="):
                        window = part[len("window="):]
            self._send_json(_build_activity_payload(self.activity_path, window))
            return
```

Update `_handler_factory` to accept and set `activity_path`:
```python
def _handler_factory(
    snapshot_path: Path, state_path: Path, live_hub: LiveStreamHub, activity_path: Path | None = None
) -> type[_HubHandler]:
    class Handler(_HubHandler):
        pass
    Handler.snapshot_path = snapshot_path
    Handler.state_path = state_path
    Handler.live_hub = live_hub
    if activity_path is not None:
        Handler.activity_path = activity_path
    return Handler
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_activity_api.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Run full test suite to check nothing broken**

```bash
uv run pytest tests/test_activity_scanner.py tests/test_activity_summarizer.py tests/test_activity_api.py tests/test_activity_cache.py -v
```
Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
git add driftdriver/ecosystem_hub/api.py driftdriver/ecosystem_hub/activity_cache.py tests/test_activity_api.py
git commit -m "feat(activity): /api/activity endpoint with window filtering"
```

---

## Task 6: Dashboard UI — timeline panel + inline per-repo row

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

This task adds HTML/JS to the single-page dashboard. No test file (dashboard is a 2600-line string template; integration tested by eye).

- [ ] **Step 1: Add CSS for the activity panel**

Find the closing `</style>` tag in `render_dashboard_html()`. Insert before it:

```css
    /* Activity panel */
    .activity-panel { margin-bottom: 1.2rem; }
    .activity-window-pills { display: flex; gap: 0.4rem; margin-bottom: 0.7rem; }
    .activity-pill {
      padding: 0.2rem 0.7rem; border-radius: 999px; font-size: 0.78rem;
      border: 1px solid var(--line); cursor: pointer; background: var(--panel);
      color: var(--muted);
    }
    .activity-pill.active { background: var(--accent); color: #fff; border-color: var(--accent); }
    .activity-feed { display: flex; flex-direction: column; gap: 0.3rem; }
    .activity-item {
      display: flex; align-items: baseline; gap: 0.5rem;
      font-size: 0.82rem; padding: 0.25rem 0;
      border-bottom: 1px solid var(--line);
    }
    .activity-item:last-child { border-bottom: none; }
    .activity-repo-badge {
      font-family: var(--mono); font-size: 0.75rem;
      background: var(--accent-soft); color: var(--accent);
      border-radius: 4px; padding: 0.1rem 0.4rem;
      white-space: nowrap; flex-shrink: 0; cursor: pointer;
    }
    .activity-subject { flex: 1; color: var(--ink); }
    .activity-age { color: var(--muted); font-size: 0.75rem; white-space: nowrap; }
    .activity-inline {
      font-size: 0.78rem; color: var(--muted); padding: 0.15rem 0;
      font-style: italic; max-width: 60ch; overflow: hidden;
      text-overflow: ellipsis; white-space: nowrap;
    }
    .activity-inline.no-activity { color: var(--line); }
```

- [ ] **Step 2: Add timeline panel HTML**

Find the `<section class="repo-panel card" id="repo-section">` opening tag (around line 724). Insert **before** it:

```html
    <section class="activity-panel card" id="activity-section">
      <h2>Recent Activity</h2>
      <div class="activity-window-pills" id="activity-pills">
        <button class="activity-pill" data-window="24h">24h</button>
        <button class="activity-pill active" data-window="48h">48h</button>
        <button class="activity-pill" data-window="72h">72h</button>
        <button class="activity-pill" data-window="7d">7d</button>
      </div>
      <div class="activity-feed" id="activity-feed">
        <div class="activity-item"><span class="activity-age">Loading…</span></div>
      </div>
    </section>
```

- [ ] **Step 3: Add activity JS — fetch, render timeline, render inline rows**

Find the closing `</script>` tag (end of the JS block). Insert before it:

```javascript
    // ── Activity panel ──────────────────────────────────────────────
    var currentActivityWindow = '48h';
    var activityData = null;

    function relativeTime(isoStr) {
      if (!isoStr) return '';
      var diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
      if (diff < 3600) return Math.round(diff / 60) + 'm ago';
      if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
      return Math.round(diff / 86400) + 'd ago';
    }

    function renderActivityPanel(data) {
      var feed = el('activity-feed');
      if (!feed) return;
      var items = (data && data.timeline) || [];
      if (items.length === 0) {
        feed.innerHTML = '<div class="activity-item"><span class="activity-age" style="color:var(--muted)">No recent commits in this window.</span></div>';
        return;
      }
      feed.innerHTML = items.slice(0, 40).map(function(c) {
        return '<div class="activity-item">'
          + '<span class="activity-repo-badge" onclick="selectRepo(' + JSON.stringify(c.repo) + ')">' + esc(c.repo) + '</span>'
          + '<span class="activity-subject">' + esc(c.subject) + '</span>'
          + '<span class="activity-age">' + esc(relativeTime(c.timestamp)) + '</span>'
          + '</div>';
      }).join('');
    }

    function activityInlineHtml(repoName) {
      if (!activityData || !activityData.repos) return '';
      var entry = activityData.repos.find(function(r) { return r.name === repoName; });
      if (!entry) return '<div class="activity-inline no-activity">No recent git activity</div>';
      if (entry.summary) {
        var age = entry.last_commit_at ? ' · Last active: ' + relativeTime(entry.last_commit_at) : '';
        return '<div class="activity-inline">' + esc(age) + ' ' + esc(entry.summary) + '</div>';
      }
      if (entry.window_count > 0) {
        return '<div class="activity-inline">' + esc(entry.window_count + ' commits in last ' + currentActivityWindow) + '</div>';
      }
      return '<div class="activity-inline no-activity">No recent git activity</div>';
    }

    async function loadActivityData(window) {
      try {
        var res = await fetch('/api/activity?window=' + encodeURIComponent(window));
        activityData = await res.json();
        renderActivityPanel(activityData);
      } catch (e) {
        activityData = null;
      }
    }

    document.addEventListener('click', function(e) {
      var pill = e.target && e.target.closest ? e.target.closest('.activity-pill') : null;
      if (!pill) return;
      var win = pill.getAttribute('data-window');
      if (!win) return;
      currentActivityWindow = win;
      document.querySelectorAll('.activity-pill').forEach(function(p) {
        p.classList.toggle('active', p.getAttribute('data-window') === win);
      });
      loadActivityData(win);
    });

    // Reload activity every 5 minutes
    loadActivityData(currentActivityWindow);
    setInterval(function() { loadActivityData(currentActivityWindow); }, 5 * 60 * 1000);
```

- [ ] **Step 4: Inject inline activity row into each repo row**

Find the repo row builder in `renderRepoTable` (around line 1668) where `rows.push(...)` builds each `<tr>`. The row currently ends with `'</tr>'`. Change that final `'</tr>'` to append an activity row:

```javascript
          + '<td>' + esc(lastActivity) + '</td>'
          + '</tr>'
          + '<tr class="activity-row" data-repo-name="' + escAttr(repoName) + '"><td colspan="8">'
          + activityInlineHtml(repoName)
          + '</td></tr>'
```

Also add a CSS rule to keep the activity row visually attached to its repo row:
```css
    .activity-row td { padding-top: 0; padding-bottom: 0.4rem; border-top: none; }
    .activity-row { background: var(--panel); }
```

- [ ] **Step 5: Verify hub renders without errors**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python3 -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; html = render_dashboard_html(); assert 'activity-section' in html and 'activity-feed' in html; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Spot-check live hub**

```bash
curl -s http://127.0.0.1:8777/api/activity?window=48h | python3 -m json.tool | head -20
```
Expected: JSON with `timeline`, `repos`, `window` keys. If scanner hasn't run yet, `timeline` will be `[]`.

- [ ] **Step 7: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(activity): timeline panel + inline per-repo activity row in hub dashboard"
```

---

## Task 7: Trigger first scanner run + smoke test

**Files:** None — just verification.

- [ ] **Step 1: Restart the hub to pick up all changes**

```bash
cd /Users/braydon/projects/experiments/driftdriver
scripts/ecosystem_hub_daemon.sh restart 2>/dev/null || python3 -m driftdriver.ecosystem_hub --project-dir . &
```

Or if the hub is managed by launchd / the daemon script:
```bash
# The hub auto-restarts on source change — just verify it's running
curl -s http://127.0.0.1:8777/api/status | python3 -c "import json,sys; d=json.load(sys.stdin); print('hub up, repos:', d.get('repo_count'))"
```

- [ ] **Step 2: Wait for first activity scan (up to 2 min) or trigger manually**

```bash
# Activity scanner runs after 15 min normally.
# To trigger immediately for smoke test, call the scan function directly:
cd /Users/braydon/projects/experiments/driftdriver
python3 -c "
from pathlib import Path
from driftdriver.ecosystem_hub.activity import scan_all_repos
from driftdriver.ecosystem_hub.activity_cache import write_activity_digest
from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repos
from datetime import datetime, timezone

ecosystem_toml = Path('/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml')
workspace = Path('/Users/braydon/projects/experiments')
repos = _load_ecosystem_repos(ecosystem_toml, workspace)
print(f'Scanning {len(repos)} repos...')
digests = scan_all_repos(repos)
print(f'Got {len(digests)} results with commits in last 8 days')
for d in sorted(digests, key=lambda x: x.get(\"last_commit_at\") or '', reverse=True)[:5]:
    print(f'  {d[\"name\"]}: {d[\"windows\"][\"24h\"][\"count\"]}c/24h, {d[\"windows\"][\"7d\"][\"count\"]}c/7d')
cache_path = Path('/Users/braydon/projects/experiments/driftdriver/.workgraph/service/ecosystem-hub/activity-digests.json')
write_activity_digest(cache_path, {'generated_at': datetime.now(timezone.utc).isoformat(), 'repos': digests})
print('Written to cache.')
"
```

- [ ] **Step 3: Check the API returns real data**

```bash
curl -s "http://127.0.0.1:8777/api/activity?window=7d" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'Timeline entries: {len(d[\"timeline\"])}')
print(f'Active repos: {len(d[\"repos\"])}')
for r in d['repos'][:5]:
    print(f'  {r[\"name\"]}: {r[\"window_count\"]} commits, summary={bool(r[\"summary\"])}')
"
```
Expected: real repos, real commit counts, summaries null (Haiku not yet triggered)

- [ ] **Step 4: Run full focused test suite**

```bash
uv run pytest tests/test_activity_scanner.py tests/test_activity_summarizer.py tests/test_activity_api.py tests/test_activity_cache.py -v
```
Expected: all PASSED

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(activity): git activity tracking complete — scanner, summarizer, API, UI"
```

---

## Verification Checklist

- [ ] `uv run pytest tests/test_activity_scanner.py tests/test_activity_summarizer.py tests/test_activity_api.py tests/test_activity_cache.py -v` — all pass
- [ ] `curl http://127.0.0.1:8777/api/activity?window=48h` returns valid JSON with `timeline` and `repos`
- [ ] Hub dashboard at `http://127.0.0.1:8777` shows "Recent Activity" panel above repo grid
- [ ] Window pills (24h/48h/72h/7d) switch the timeline view
- [ ] Each repo row has an inline activity row beneath it
- [ ] New repo added to ecosystem.toml appears in next 15-min scan cycle with no manual steps
