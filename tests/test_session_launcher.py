# ABOUTME: Tests for session_launcher.py — context package, prompt rendering, and Freshell call.
# ABOUTME: Freshell HTTP calls are mocked. All other data sources use real tempfile fixtures.
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from driftdriver.ecosystem_hub.session_launcher import (
    FreshellUnavailableError,
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
                "set_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
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
            intent = {
                "set_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat().replace("+00:00", "Z"),
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
        repo = {
            "presence_actors": [
                {
                    "kind": "session",
                    "heartbeat_age_seconds": 120,
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
                    "heartbeat_age_seconds": 900,
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

class TestLaunchSession(unittest.TestCase):
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
        self.assertIn("initial_prompt", payload)


if __name__ == "__main__":
    unittest.main()
