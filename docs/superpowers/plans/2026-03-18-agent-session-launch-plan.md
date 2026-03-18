# Agent Session Launch — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `build_context_package()` and `launch_session()` to a new `session_launcher.py` module. Wire it into `api.py` as `POST /api/repo/:name/launch`. Add a Launch Agent section to the detail page with agent type selector, mode radio buttons, and a Launch button. Freshell is mocked in tests via `unittest.mock.patch`; all other data sources use real fixtures.

**Architecture:** One new Python file (`driftdriver/ecosystem_hub/session_launcher.py`). Two existing files modified (`api.py` for the POST endpoint, `dashboard.py` for the launch UI). The session launcher calls `build_agent_history()` (Sub-project 3) for the last session data and reads the snapshot + activity digest directly. Freshell is the only external dependency: called via `urllib.request` (stdlib) to avoid adding `requests` as a dependency. For Continuation mode, `continuation_intent` is written atomically to a sidecar JSON file next to the repo's snapshot entry.

**Tech Stack:** Python 3.11+, `urllib.request` + `json` + `pathlib` + `datetime` (stdlib), vanilla JS in `dashboard.py`, `unittest` + `tempfile` + `unittest.mock.patch` for tests (Freshell mocked, all other data real). Test runner: `uv run pytest`.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `driftdriver/ecosystem_hub/session_launcher.py` | `build_context_package()`, `render_context_prompt()`, `launch_session()`, `build_continuation_intent()`, `persist_continuation_intent()`, `detect_live_session()` |
| Modify | `driftdriver/ecosystem_hub/api.py` | Add `POST /api/repo/:name/launch` handler |
| Modify | `driftdriver/ecosystem_hub/dashboard.py` | Add Launch Agent section HTML + JS (`launchAgent()`, mode selector, error display) |
| Create | `tests/test_session_launcher.py` | Full test coverage for all launcher functions |

---

## Step 1 — Create test file with failing tests for `session_launcher.py`

- [ ] Create `tests/test_session_launcher.py`

```python
# ABOUTME: Tests for session_launcher.py — context package, prompt rendering, and Freshell call.
# ABOUTME: Freshell HTTP calls are mocked. All other data sources use real tempfile fixtures.
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.ecosystem_hub.session_launcher import (
    build_context_package,
    build_continuation_intent,
    detect_live_session,
    launch_session,
    persist_continuation_intent,
    render_context_prompt,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_snapshot_repo(tmp: str, name: str = "lodestar",
                        path: str | None = None) -> dict:
    """Return a minimal repo dict as it appears in the snapshot repos list."""
    repo_path = path or str(Path(tmp) / name)
    Path(repo_path).mkdir(parents=True, exist_ok=True)
    return {
        "name": name,
        "path": repo_path,
        "role": "experiment",
        "tags": ["ai", "nextjs"],
        "workgraph_snapshot": {
            "tasks": [
                {"id": "t-1", "title": "Implement regret scoring",
                 "status": "in_progress", "description": "Add P6 regret score"},
                {"id": "t-2", "title": "Wire briefing history",
                 "status": "pending", "description": ""},
            ]
        },
        "presence_actors": [],
    }


def _make_activity_digest(repo_name: str, repo_path: str) -> dict:
    return {
        "generated_at": "2026-03-18T12:00:00Z",
        "repos": [
            {
                "name": repo_name,
                "last_commit_at": "2026-03-18T11:00:00Z",
                "summary": "Recent work on scenario engine and regret scoring.",
                "timeline": [
                    {"sha": "abc1234", "subject": "Add regret scoring",
                     "timestamp": "2026-03-18T11:00:00Z", "author": "Braydon"},
                    {"sha": "def5678", "subject": "Wire briefing history to UI",
                     "timestamp": "2026-03-17T20:00:00Z", "author": "Braydon"},
                ],
                "windows": {"48h": {"count": 2}},
            }
        ],
    }


# ---------------------------------------------------------------------------
# build_context_package
# ---------------------------------------------------------------------------

class TestBuildContextPackage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_snapshot_repo(self._tmp.name)
        self.digest = _make_activity_digest("lodestar", self.repo["path"])

    def tearDown(self):
        self._tmp.cleanup()

    def test_returns_required_keys(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        for key in ("repo_name", "repo_path", "role", "tags",
                    "recent_commits", "activity_summary", "in_progress_tasks"):
            self.assertIn(key, pkg)

    def test_repo_name_and_path_populated(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        self.assertEqual(pkg["repo_name"], "lodestar")
        self.assertEqual(pkg["repo_path"], self.repo["path"])

    def test_recent_commits_capped_at_5(self):
        # Add 7 commits to the digest
        many_commits = [
            {"sha": f"sha{i}", "subject": f"Commit {i}",
             "timestamp": "2026-03-18T10:00:00Z", "author": "B"}
            for i in range(7)
        ]
        digest = {**self.digest,
                  "repos": [{**self.digest["repos"][0], "timeline": many_commits}]}
        pkg = build_context_package(self.repo, activity_digest=digest)
        self.assertLessEqual(len(pkg["recent_commits"]), 5)

    def test_in_progress_tasks_only(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        statuses = [t["status"] for t in pkg["in_progress_tasks"]]
        self.assertTrue(all(s == "in_progress" for s in statuses))

    def test_activity_summary_from_digest(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        self.assertIn("scenario engine", pkg["activity_summary"])

    def test_missing_digest_gracefully_degrades(self):
        pkg = build_context_package(self.repo, activity_digest=None)
        self.assertEqual(pkg["recent_commits"], [])
        self.assertIsNone(pkg["activity_summary"])

    def test_continuation_intent_included_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_snapshot_repo(tmp)
            intent = {
                "set_at": "2026-03-18T14:00:00Z",
                "agent_type": "claude-code",
                "summary": "Resume the handler implementation.",
                "in_progress_tasks": ["t-1"],
                "last_commit": "abc1234",
            }
            intent_path = Path(repo["path"]) / ".hub-continuation-intent.json"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            pkg = build_context_package(repo, activity_digest=None)
            self.assertIsNotNone(pkg["continuation_intent"])
            self.assertEqual(pkg["continuation_intent"]["summary"],
                             "Resume the handler implementation.")

    def test_stale_continuation_intent_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_snapshot_repo(tmp)
            # set_at more than 7 days ago
            intent = {
                "set_at": "2026-03-01T00:00:00Z",
                "agent_type": "claude-code",
                "summary": "Old stale intent",
                "in_progress_tasks": [],
                "last_commit": "abc0000",
            }
            intent_path = Path(repo["path"]) / ".hub-continuation-intent.json"
            intent_path.write_text(json.dumps(intent), encoding="utf-8")
            pkg = build_context_package(repo, activity_digest=None)
            self.assertIsNone(pkg["continuation_intent"])


# ---------------------------------------------------------------------------
# render_context_prompt
# ---------------------------------------------------------------------------

class TestRenderContextPrompt(unittest.TestCase):
    def _pkg(self, **overrides):
        base = {
            "repo_name": "lodestar",
            "repo_path": "/path/to/lodestar",
            "role": "experiment",
            "tags": ["ai"],
            "recent_commits": [
                {"sha": "abc1234", "subject": "Add regret scoring",
                 "timestamp": "2026-03-18T11:00:00Z"}
            ],
            "activity_summary": "Recent work on scenario engine.",
            "in_progress_tasks": [
                {"id": "t-1", "title": "Implement regret scoring",
                 "status": "in_progress", "description": "P6 regret score"}
            ],
            "last_agent_session": None,
            "continuation_intent": None,
        }
        base.update(overrides)
        return base

    def test_fresh_mode_returns_none(self):
        result = render_context_prompt(self._pkg(), mode="fresh")
        self.assertIsNone(result)

    def test_seeded_mode_contains_repo_name(self):
        prompt = render_context_prompt(self._pkg(), mode="seeded")
        self.assertIsNotNone(prompt)
        self.assertIn("lodestar", prompt)

    def test_seeded_mode_contains_recent_commits(self):
        prompt = render_context_prompt(self._pkg(), mode="seeded")
        self.assertIn("abc1234", prompt)
        self.assertIn("Add regret scoring", prompt)

    def test_seeded_mode_contains_in_progress_tasks(self):
        prompt = render_context_prompt(self._pkg(), mode="seeded")
        self.assertIn("Implement regret scoring", prompt)

    def test_continuation_mode_includes_intent_header(self):
        pkg = self._pkg(continuation_intent={
            "set_at": "2026-03-18T14:00:00Z",
            "summary": "Resume the handler implementation.",
            "in_progress_tasks": ["t-1"],
            "last_commit": "abc1234",
        })
        prompt = render_context_prompt(pkg, mode="continuation")
        self.assertIn("CONTINUATION INTENT", prompt)
        self.assertIn("Resume the handler implementation.", prompt)

    def test_missing_sections_omitted_cleanly(self):
        pkg = self._pkg(recent_commits=[], activity_summary=None,
                        in_progress_tasks=[])
        prompt = render_context_prompt(pkg, mode="seeded")
        self.assertIsNotNone(prompt)
        self.assertIn("lodestar", prompt)
        # No empty section headers or "None" literals
        self.assertNotIn("None", prompt)


# ---------------------------------------------------------------------------
# build_continuation_intent
# ---------------------------------------------------------------------------

class TestBuildContinuationIntent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_snapshot_repo(self._tmp.name)
        self.digest = _make_activity_digest("lodestar", self.repo["path"])

    def tearDown(self):
        self._tmp.cleanup()

    def test_intent_has_required_keys(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        intent = build_continuation_intent(self.repo, pkg, agent_type="claude-code")
        for key in ("set_at", "agent_type", "summary",
                    "in_progress_tasks", "last_commit"):
            self.assertIn(key, intent)

    def test_agent_type_preserved(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        intent = build_continuation_intent(self.repo, pkg, agent_type="codex")
        self.assertEqual(intent["agent_type"], "codex")

    def test_in_progress_task_ids_listed(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        intent = build_continuation_intent(self.repo, pkg, agent_type="claude-code")
        self.assertIn("t-1", intent["in_progress_tasks"])

    def test_set_at_is_iso_string(self):
        pkg = build_context_package(self.repo, activity_digest=self.digest)
        intent = build_continuation_intent(self.repo, pkg, agent_type="claude-code")
        # Should parse as ISO without error
        dt = datetime.fromisoformat(intent["set_at"].replace("Z", "+00:00"))
        self.assertIsNotNone(dt)


# ---------------------------------------------------------------------------
# persist_continuation_intent
# ---------------------------------------------------------------------------

class TestPersistContinuationIntent(unittest.TestCase):
    def test_writes_json_file_to_repo_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            intent = {"set_at": "2026-03-18T14:00:00Z", "summary": "test"}
            persist_continuation_intent(repo_path, intent)
            intent_file = repo_path / ".hub-continuation-intent.json"
            self.assertTrue(intent_file.exists())
            loaded = json.loads(intent_file.read_text(encoding="utf-8"))
            self.assertEqual(loaded["summary"], "test")

    def test_write_is_atomic_via_tmp_replace(self):
        """Verify no partial file is left on disk (we trust the implementation,
        but we can verify the final file is valid JSON)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "myrepo"
            repo_path.mkdir()
            intent = {"set_at": "2026-03-18T14:00:00Z", "data": "x" * 10000}
            persist_continuation_intent(repo_path, intent)
            intent_file = repo_path / ".hub-continuation-intent.json"
            loaded = json.loads(intent_file.read_text(encoding="utf-8"))
            self.assertEqual(loaded["data"], "x" * 10000)


# ---------------------------------------------------------------------------
# detect_live_session
# ---------------------------------------------------------------------------

class TestDetectLiveSession(unittest.TestCase):
    def test_no_presence_actors_returns_none(self):
        repo = {"presence_actors": [], "path": "/some/repo"}
        result = detect_live_session(repo, freshell_base_url=None)
        self.assertIsNone(result)

    def test_fresh_presence_actor_returns_url(self):
        import time
        repo = {
            "presence_actors": [
                {
                    "kind": "session",
                    "heartbeat_age_seconds": 120,  # < 600
                    "cli": "claude-code",
                    "actor_id": "session-XYZ",
                }
            ],
            "path": "/some/repo",
        }
        result = detect_live_session(repo, freshell_base_url=None)
        self.assertIsNotNone(result)

    def test_stale_presence_actor_returns_none(self):
        repo = {
            "presence_actors": [
                {
                    "kind": "session",
                    "heartbeat_age_seconds": 900,  # > 600 → stale
                    "cli": "claude-code",
                    "actor_id": "session-OLD",
                }
            ],
            "path": "/some/repo",
        }
        result = detect_live_session(repo, freshell_base_url=None)
        self.assertIsNone(result)

    def test_freshell_api_queried_when_url_provided(self):
        repo = {"presence_actors": [], "path": "/some/repo"}
        mock_response_data = {
            "sessions": [
                {
                    "session_id": "abc123",
                    "url": "http://localhost:3550/session/abc123",
                    "agent_type": "claude-code",
                }
            ]
        }
        with patch("driftdriver.ecosystem_hub.session_launcher._http_get_json",
                   return_value=mock_response_data):
            result = detect_live_session(
                repo, freshell_base_url="http://localhost:3550"
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["url"], "http://localhost:3550/session/abc123")

    def test_freshell_api_failure_returns_none(self):
        repo = {"presence_actors": [], "path": "/some/repo"}
        with patch("driftdriver.ecosystem_hub.session_launcher._http_get_json",
                   side_effect=Exception("connection refused")):
            result = detect_live_session(
                repo, freshell_base_url="http://localhost:3550"
            )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# launch_session
# ---------------------------------------------------------------------------

class TestLaunchSessionFresh(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_snapshot_repo(self._tmp.name)
        self.digest = _make_activity_digest("lodestar", self.repo["path"])

    def tearDown(self):
        self._tmp.cleanup()

    def _mock_freshell(self, session_url: str = "http://localhost:3550/session/new123"):
        return patch(
            "driftdriver.ecosystem_hub.session_launcher._http_post_json",
            return_value={"session_id": "new123", "url": session_url},
        )

    def test_fresh_mode_no_initial_prompt_sent(self):
        with self._mock_freshell() as mock_post:
            result = launch_session(
                self.repo, mode="fresh", agent_type="claude-code",
                activity_digest=self.digest,
            )
        call_kwargs = mock_post.call_args
        payload_sent = call_kwargs[0][1] if call_kwargs[0] else call_kwargs[1].get("payload", {})
        self.assertNotIn("initial_prompt", payload_sent)
        self.assertEqual(result["session_url"], "http://localhost:3550/session/new123")
        self.assertFalse(result["resumed"])

    def test_seeded_mode_sends_initial_prompt(self):
        with self._mock_freshell() as mock_post:
            launch_session(
                self.repo, mode="seeded", agent_type="claude-code",
                activity_digest=self.digest,
            )
        call_args = mock_post.call_args
        payload = call_args[0][1] if call_args[0] else call_args[1].get("payload", {})
        self.assertIn("initial_prompt", payload)
        self.assertIn("lodestar", payload["initial_prompt"])

    def test_continuation_mode_writes_intent_file(self):
        with self._mock_freshell():
            launch_session(
                self.repo, mode="continuation", agent_type="claude-code",
                activity_digest=self.digest,
            )
        intent_file = Path(self.repo["path"]) / ".hub-continuation-intent.json"
        self.assertTrue(intent_file.exists())
        intent = json.loads(intent_file.read_text(encoding="utf-8"))
        self.assertIn("summary", intent)

    def test_working_directory_sent_to_freshell(self):
        with self._mock_freshell() as mock_post:
            launch_session(
                self.repo, mode="fresh", agent_type="codex",
                activity_digest=None,
            )
        call_args = mock_post.call_args
        payload = call_args[0][1] if call_args[0] else call_args[1].get("payload", {})
        self.assertEqual(payload.get("working_directory"), self.repo["path"])
        self.assertEqual(payload.get("agent_type"), "codex")

    def test_freshell_unavailable_raises_with_message(self):
        from driftdriver.ecosystem_hub.session_launcher import FreshellUnavailableError
        with patch("driftdriver.ecosystem_hub.session_launcher._http_post_json",
                   side_effect=ConnectionError("Connection refused")):
            with self.assertRaises(FreshellUnavailableError):
                launch_session(
                    self.repo, mode="fresh", agent_type="claude-code",
                    activity_digest=None,
                )

    def test_resume_mode_returns_existing_url_when_live_session_found(self):
        live = {"url": "http://localhost:3550/session/existing999"}
        with patch("driftdriver.ecosystem_hub.session_launcher.detect_live_session",
                   return_value=live):
            result = launch_session(
                self.repo, mode="resume", agent_type="claude-code",
                activity_digest=None,
            )
        self.assertEqual(result["session_url"], "http://localhost:3550/session/existing999")
        self.assertTrue(result["resumed"])

    def test_resume_mode_falls_back_to_seeded_when_no_live_session(self):
        with patch("driftdriver.ecosystem_hub.session_launcher.detect_live_session",
                   return_value=None), \
             self._mock_freshell() as mock_post:
            result = launch_session(
                self.repo, mode="resume", agent_type="claude-code",
                activity_digest=self.digest,
            )
        self.assertFalse(result["resumed"])
        call_args = mock_post.call_args
        payload = call_args[0][1] if call_args[0] else call_args[1].get("payload", {})
        # Fell back to seeded — initial_prompt should be present
        self.assertIn("initial_prompt", payload)


if __name__ == "__main__":
    unittest.main()
```

**Run (expect failures):**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_session_launcher.py -x 2>&1 | head -30
```

---

## Step 2 — Implement `driftdriver/ecosystem_hub/session_launcher.py`

- [ ] Create `driftdriver/ecosystem_hub/session_launcher.py`

```python
# ABOUTME: Agent session launch logic for the ecosystem hub.
# ABOUTME: Builds context packages from snapshot data and POSTs to Freshell to open sessions.
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_FRESHELL_BASE = "http://localhost:3550"
_LIVE_SESSION_HEARTBEAT_THRESHOLD = 600  # 10 minutes in seconds
_CONTINUATION_INTENT_STALENESS_DAYS = 7
_INTENT_FILENAME = ".hub-continuation-intent.json"


class FreshellUnavailableError(Exception):
    """Raised when Freshell cannot be reached at the configured URL."""


# ---------------------------------------------------------------------------
# Low-level HTTP helpers — patched in tests
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict[str, Any], timeout: int = 5) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Continuation intent persistence
# ---------------------------------------------------------------------------

def persist_continuation_intent(repo_path: Path, intent: dict[str, Any]) -> None:
    """Atomically write continuation intent JSON to repo_path/.hub-continuation-intent.json."""
    target = repo_path / _INTENT_FILENAME
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def _load_continuation_intent(repo_path: str) -> dict[str, Any] | None:
    """Load continuation intent if it exists and is not stale."""
    intent_file = Path(repo_path) / _INTENT_FILENAME
    if not intent_file.exists():
        return None
    try:
        intent = json.loads(intent_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    set_at_str = intent.get("set_at") or ""
    try:
        set_at = datetime.fromisoformat(set_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if datetime.now(timezone.utc) - set_at > timedelta(days=_CONTINUATION_INTENT_STALENESS_DAYS):
        return None
    return intent


# ---------------------------------------------------------------------------
# Context package
# ---------------------------------------------------------------------------

def build_context_package(
    repo: dict[str, Any],
    *,
    activity_digest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble context from snapshot data and activity digest.

    All fields degrade gracefully when data is absent.
    """
    repo_name = str(repo.get("name") or "")
    repo_path = str(repo.get("path") or "")

    # Recent commits — last 5 from activity digest for this repo
    recent_commits: list[dict[str, Any]] = []
    activity_summary: str | None = None
    if activity_digest:
        for repo_entry in (activity_digest.get("repos") or []):
            if str(repo_entry.get("name") or "") == repo_name:
                timeline = repo_entry.get("timeline") or []
                recent_commits = timeline[:5]
                activity_summary = repo_entry.get("summary") or None
                break

    # In-progress tasks from workgraph_snapshot
    wg_snapshot = repo.get("workgraph_snapshot") or {}
    tasks = wg_snapshot.get("tasks") or []
    in_progress_tasks = [
        {"id": str(t.get("id") or ""),
         "title": str(t.get("title") or ""),
         "status": str(t.get("status") or ""),
         "description": str(t.get("description") or "")}
        for t in tasks
        if str(t.get("status") or "") == "in_progress"
    ]

    # Last agent session — call build_agent_history if available
    last_agent_session: dict[str, Any] | None = None
    if repo_path and Path(repo_path).is_dir():
        try:
            from .agent_history import build_agent_history
            history = build_agent_history(Path(repo_path))
            sessions = history.get("sessions") or []
            if sessions:
                last_agent_session = sessions[0]
        except Exception:
            pass

    # Continuation intent
    continuation_intent = _load_continuation_intent(repo_path) if repo_path else None

    return {
        "repo_name": repo_name,
        "repo_path": repo_path,
        "role": str(repo.get("role") or ""),
        "tags": list(repo.get("tags") or []),
        "recent_commits": recent_commits,
        "activity_summary": activity_summary,
        "in_progress_tasks": in_progress_tasks,
        "last_agent_session": last_agent_session,
        "continuation_intent": continuation_intent,
    }


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def render_context_prompt(
    pkg: dict[str, Any],
    mode: str,
) -> str | None:
    """Render the initial_prompt string for Freshell from the context package.

    Returns None for fresh mode (no prompt injected).
    """
    if mode == "fresh":
        return None

    lines: list[str] = []

    # Continuation mode header
    if mode == "continuation":
        intent = pkg.get("continuation_intent")
        if intent:
            set_at = intent.get("set_at", "unknown time")
            summary = intent.get("summary", "")
            in_prog = intent.get("in_progress_tasks") or []
            last_commit = intent.get("last_commit", "")
            lines.append(f"CONTINUATION INTENT (set {set_at}):")
            if summary:
                lines.append(summary)
            if in_prog:
                lines.append(f"In-progress tasks at time of suspension: {', '.join(in_prog)}")
            if last_commit:
                lines.append(f"Last commit: {last_commit}")
            lines.append("")
            lines.append("Resume from this point.")
            lines.append("")
            lines.append("---")
            lines.append("")

    lines.append(f"You are beginning a session in the {pkg['repo_name']} repository.")
    lines.append("")

    # Repo section
    lines.append("## Repo")
    lines.append(f"- Path: {pkg['repo_path']}")
    if pkg.get("role"):
        lines.append(f"- Role: {pkg['role']}")
    tags = pkg.get("tags") or []
    if tags:
        lines.append(f"- Tags: {', '.join(str(t) for t in tags)}")
    lines.append("")

    # Recent activity
    commits = pkg.get("recent_commits") or []
    summary = pkg.get("activity_summary")
    if commits or summary:
        lines.append("## Recent Activity")
        for c in commits:
            sha = str(c.get("sha") or "")[:7]
            subject = str(c.get("subject") or "")
            ts = str(c.get("timestamp") or "")
            lines.append(f"- {sha} {subject} ({ts})")
        if summary:
            lines.append(summary)
        lines.append("")

    # In-progress tasks
    tasks = pkg.get("in_progress_tasks") or []
    if tasks:
        lines.append("## In-Progress Tasks")
        for t in tasks:
            title = str(t.get("title") or "")
            desc = str(t.get("description") or "")
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
        lines.append("")

    # Last agent session
    last_session = pkg.get("last_agent_session")
    if last_session:
        agent_type = str(last_session.get("agent_type") or "unknown")
        started = str(last_session.get("started_at") or "")
        duration = last_session.get("duration_seconds")
        task_count = len(last_session.get("tasks_completed") or [])
        dur_str = f"{duration // 60}m" if duration else "unknown duration"
        lines.append("## Last Agent Session")
        lines.append(f"- Agent: {agent_type}, started {started}, {dur_str}, {task_count} tasks completed")
        lines.append("")

    lines.append("Orient yourself from this context and wait for instructions.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Continuation intent builder
# ---------------------------------------------------------------------------

def build_continuation_intent(
    repo: dict[str, Any],
    context_pkg: dict[str, Any],
    *,
    agent_type: str,
) -> dict[str, Any]:
    """Build a continuation_intent dict from available context. Not LLM-generated."""
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    in_progress_ids = [t["id"] for t in (context_pkg.get("in_progress_tasks") or [])]

    commits = context_pkg.get("recent_commits") or []
    last_commit = commits[0].get("sha", "")[:7] if commits else ""

    # Summary: structured template — not LLM
    tasks_str = ", ".join(
        t["title"] for t in (context_pkg.get("in_progress_tasks") or [])
    )
    summary_parts = []
    if tasks_str:
        summary_parts.append(f"In progress: {tasks_str}.")
    if last_commit:
        summary_parts.append(f"Last commit: {last_commit}.")
    summary = " ".join(summary_parts) if summary_parts else "Session state captured."

    return {
        "set_at": now_iso,
        "agent_type": agent_type,
        "summary": summary,
        "in_progress_tasks": in_progress_ids,
        "last_commit": last_commit,
    }


# ---------------------------------------------------------------------------
# Live session detection
# ---------------------------------------------------------------------------

def detect_live_session(
    repo: dict[str, Any],
    *,
    freshell_base_url: str | None = _FRESHELL_BASE,
) -> dict[str, Any] | None:
    """Detect an active session via presence_actors or Freshell API.

    Returns a dict with at least {"url": str} if a live session is found,
    or None if no live session detected.
    """
    # Strategy 1: presence_actors heartbeat
    actors = repo.get("presence_actors") or []
    for actor in actors:
        if str(actor.get("kind") or "") == "session":
            age = actor.get("heartbeat_age_seconds") or 9999
            if int(age) < _LIVE_SESSION_HEARTBEAT_THRESHOLD:
                actor_id = str(actor.get("actor_id") or "")
                url = (f"{freshell_base_url}/session/{actor_id}"
                       if freshell_base_url and actor_id else None)
                return {"url": url, "actor_id": actor_id, "source": "presence"}

    # Strategy 2: Freshell session API
    if freshell_base_url:
        repo_path = str(repo.get("path") or "")
        try:
            import urllib.parse
            encoded = urllib.parse.quote(repo_path, safe="")
            data = _http_get_json(
                f"{freshell_base_url}/api/sessions?repo={encoded}&active=true"
            )
            sessions = (data.get("sessions") or []) if isinstance(data, dict) else []
            if sessions:
                return {
                    "url": str(sessions[0].get("url") or ""),
                    "session_id": str(sessions[0].get("session_id") or ""),
                    "source": "freshell",
                }
        except Exception:
            # Graceful fallback — Freshell may not implement this endpoint
            pass

    return None


# ---------------------------------------------------------------------------
# Main launch function
# ---------------------------------------------------------------------------

def launch_session(
    repo: dict[str, Any],
    *,
    mode: str,
    agent_type: str,
    activity_digest: dict[str, Any] | None = None,
    freshell_base_url: str = _FRESHELL_BASE,
) -> dict[str, Any]:
    """Launch (or resume) an agent session in Freshell.

    Returns {"session_url": str, "resumed": bool}.
    Raises FreshellUnavailableError if Freshell cannot be reached.
    """
    repo_name = str(repo.get("name") or "")
    repo_path = str(repo.get("path") or "")

    # Mode 4: Resume — check for live session first
    if mode == "resume":
        live = detect_live_session(repo, freshell_base_url=freshell_base_url)
        if live and live.get("url"):
            return {"session_url": live["url"], "resumed": True}
        # Fall back to seeded
        mode = "seeded"

    # Build context package for seeded and continuation modes
    context_pkg: dict[str, Any] | None = None
    if mode in ("seeded", "continuation"):
        context_pkg = build_context_package(repo, activity_digest=activity_digest)

    # Mode 3: Continuation — persist intent before calling Freshell
    if mode == "continuation" and context_pkg is not None:
        intent = build_continuation_intent(repo, context_pkg, agent_type=agent_type)
        persist_continuation_intent(Path(repo_path), intent)

    # Build Freshell payload
    payload: dict[str, Any] = {
        "working_directory": repo_path,
        "agent_type": agent_type,
        "title": f"{repo_name} — {mode}",
    }
    if context_pkg is not None:
        prompt = render_context_prompt(context_pkg, mode=mode)
        if prompt:
            payload["initial_prompt"] = prompt

    # Call Freshell
    try:
        resp = _http_post_json(
            f"{freshell_base_url}/api/sessions",
            payload,
            timeout=5,
        )
        session_url = str(resp.get("url") or "")
        return {"session_url": session_url, "resumed": False}
    except ConnectionError as exc:
        raise FreshellUnavailableError(
            f"Freshell is not running at {freshell_base_url}. "
            "Start it with: npm start in the freshell directory, "
            "or check the launchd service."
        ) from exc
    except Exception as exc:
        raise FreshellUnavailableError(
            f"Freshell error: {exc!s}"
        ) from exc
```

**Run tests (expect green):**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_session_launcher.py -x -v
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/session_launcher.py tests/test_session_launcher.py
git commit -m "feat(session-launch): add session_launcher.py with context package, prompt, and Freshell integration"
```

---

## Step 3 — Add `POST /api/repo/:name/launch` to `api.py`

- [ ] Modify `driftdriver/ecosystem_hub/api.py`

Add the import at the top alongside the other ecosystem hub imports:

```python
from .session_launcher import (
    FreshellUnavailableError as _FreshellUnavailableError,
    launch_session as _launch_session,
)
```

Add the handler inside `do_POST`, before the final `not_found` return:

```python
if route.startswith("/api/repo/") and route.endswith("/launch"):
    repo_name = route[len("/api/repo/"):-len("/launch")]
    if not repo_name:
        self._send_json({"error": "missing_repo_name"}, status=HTTPStatus.BAD_REQUEST)
        return
    body = self._read_body()
    try:
        body_data = json.loads(body) if body else {}
    except (json.JSONDecodeError, ValueError):
        self._send_json({"error": "invalid_json"}, status=HTTPStatus.BAD_REQUEST)
        return
    mode = str(body_data.get("mode") or "fresh")
    agent_type = str(body_data.get("agent_type") or "claude-code")
    valid_modes = {"fresh", "seeded", "continuation", "resume"}
    valid_agents = {"claude-code", "codex", "shell"}
    if mode not in valid_modes:
        self._send_json(
            {"error": "invalid_mode", "valid": sorted(valid_modes)},
            status=HTTPStatus.BAD_REQUEST,
        )
        return
    if agent_type not in valid_agents:
        self._send_json(
            {"error": "invalid_agent_type", "valid": sorted(valid_agents)},
            status=HTTPStatus.BAD_REQUEST,
        )
        return
    snapshot = self._read_snapshot()
    repo = None
    for r in (snapshot.get("repos") or []):
        if isinstance(r, dict) and str(r.get("name") or "") == repo_name:
            repo = r
            break
    if not repo:
        self._send_json({"error": "repo_not_found", "repo": repo_name}, status=HTTPStatus.NOT_FOUND)
        return
    # Load activity digest for context seeding
    activity_digest: dict | None = None
    activity_path = getattr(self.__class__, "activity_path", None)
    if activity_path and activity_path.exists():
        from .activity_cache import read_activity_digest
        activity_digest = read_activity_digest(activity_path)
    try:
        result = _launch_session(
            repo,
            mode=mode,
            agent_type=agent_type,
            activity_digest=activity_digest,
        )
        self._send_json(result)
    except _FreshellUnavailableError as exc:
        self._send_json(
            {"error": "freshell_unavailable", "message": str(exc)},
            status=HTTPStatus.SERVICE_UNAVAILABLE,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug("launch_session failed", exc_info=True)
        self._send_json(
            {"error": "launch_failed", "message": str(exc)[:200]},
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return
```

**Verify import:**
```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python -c "from driftdriver.ecosystem_hub.api import _HubHandler; print('ok')"
```

**Commit:**
```bash
git add driftdriver/ecosystem_hub/api.py
git commit -m "feat(session-launch): add POST /api/repo/:name/launch endpoint to api.py"
```

---

## Step 4 — Add Launch Agent section to `dashboard.py`

- [ ] Modify `driftdriver/ecosystem_hub/dashboard.py`

**New JS to add inside the `<script>` block:**

```javascript
function initLaunchSection(repoName) {
  const section = document.getElementById('section-launch');
  if (!section) return;

  // Restore saved mode from localStorage
  const savedMode = localStorage.getItem(`hub_launch_mode_${repoName}`) || 'seeded';
  const modeInput = section.querySelector(`input[name="launch-mode"][value="${savedMode}"]`);
  if (modeInput) modeInput.checked = true;
}

async function launchAgent(repoName) {
  const section = document.getElementById('section-launch');
  if (!section) return;

  const agentSelect = section.querySelector('#launch-agent-type');
  const modeInputs = section.querySelectorAll('input[name="launch-mode"]');
  const btn = section.querySelector('#launch-btn');
  const errDiv = section.querySelector('#launch-error');

  const agentType = agentSelect ? agentSelect.value : 'claude-code';
  let mode = 'fresh';
  modeInputs.forEach(inp => { if (inp.checked) mode = inp.value; });

  // Save mode preference
  localStorage.setItem(`hub_launch_mode_${repoName}`, mode);

  btn.disabled = true;
  const origText = btn.textContent;
  btn.textContent = 'Launching…';
  if (errDiv) { errDiv.style.display = 'none'; errDiv.textContent = ''; }

  try {
    const resp = await fetch(`/api/repo/${encodeURIComponent(repoName)}/launch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, agent_type: agentType }),
    });
    const data = await resp.json();

    if (!resp.ok || data.error) {
      const msg = data.message || data.error || `HTTP ${resp.status}`;
      if (errDiv) {
        errDiv.innerHTML = `<strong>Error:</strong> ${escHtml(msg)}`;
        if (data.error === 'freshell_unavailable') {
          errDiv.innerHTML += `<br><code>npm start</code> in the freshell directory, or check the launchd service.`;
        }
        errDiv.style.display = 'block';
      }
      return;
    }

    if (data.session_url) {
      if (data.resumed) btn.textContent = 'Resuming…';
      window.open(data.session_url, '_blank');
    }
  } catch (err) {
    if (errDiv) {
      errDiv.textContent = 'Error: ' + String(err);
      errDiv.style.display = 'block';
    }
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}
```

**New HTML section** — add inside the repo detail view div, at the bottom, below the Services section (Sub-project 4):

```html
<div class="detail-section">
  <h3>Launch Agent</h3>
  <div id="section-launch">
    <div class="launch-row">
      <label for="launch-agent-type">Agent type:</label>
      <select id="launch-agent-type">
        <option value="claude-code">Claude Code</option>
        <option value="codex">Codex</option>
        <option value="shell">Shell</option>
      </select>
    </div>
    <div class="launch-modes">
      <label class="launch-mode-option">
        <input type="radio" name="launch-mode" value="fresh">
        <strong>Fresh</strong> — Open a clean terminal in this repo. No context injected.
      </label>
      <label class="launch-mode-option">
        <input type="radio" name="launch-mode" value="seeded" checked>
        <strong>Context-seeded</strong> — Load recent commits + tasks as a prompt so the agent orients quickly.
      </label>
      <label class="launch-mode-option">
        <input type="radio" name="launch-mode" value="continuation">
        <strong>Continuation</strong> — Resume the last session's thread via continuation_intent.
      </label>
      <label class="launch-mode-option">
        <input type="radio" name="launch-mode" value="resume">
        <strong>Resume</strong> — Re-join a live session if one exists, otherwise fall back to context-seeded.
      </label>
    </div>
    <div class="launch-actions">
      <button id="launch-btn" onclick="launchAgent(currentRepoName)">Launch →</button>
    </div>
    <div id="launch-error" class="error-banner" style="display:none"></div>
  </div>
</div>
```

**Wire into `openRepoDetail(name)`** — after other section initializations, add:

```javascript
currentRepoName = name;  // ensure global is set before initLaunchSection
initLaunchSection(name);
```

Note: `currentRepoName` must be a variable accessible to the `onclick` handler. Set it at the top of `openRepoDetail()`.

**Commit:**
```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(session-launch): add Launch Agent section to detail page with mode selector and Freshell integration"
```

---

## Step 5 — Run full test suite

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_session_launcher.py -v
uv run pytest tests/test_ecosystem_hub.py -x -v
```

---

## Completion Checklist

- [ ] `tests/test_session_launcher.py` — all tests green
- [ ] `driftdriver/ecosystem_hub/session_launcher.py` — created, passes tests
- [ ] `driftdriver/ecosystem_hub/api.py` — `POST /api/repo/:name/launch` added
- [ ] `driftdriver/ecosystem_hub/dashboard.py` — `launchAgent()`, `initLaunchSection()`, Launch Agent HTML
- [ ] `FreshellUnavailableError` returned as HTTP 503 with human-readable message
- [ ] Resume mode falls back to seeded when no live session found
- [ ] Continuation mode writes `.hub-continuation-intent.json` before calling Freshell
- [ ] `localStorage` saves and restores mode preference per repo
- [ ] `uv run pytest tests/test_session_launcher.py -v` — all green
- [ ] No new external dependencies (uses `urllib.request` from stdlib)
