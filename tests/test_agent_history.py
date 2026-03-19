# ABOUTME: Tests for build_agent_history() in driftdriver/ecosystem_hub/agent_history.py.
# ABOUTME: Uses real JSONL fixtures in tempfile dirs — no mocks.
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from driftdriver.ecosystem_hub.agent_history import build_agent_history


def _make_repo(tmp: str) -> Path:
    """Create a repo dir with .workgraph/service/runtime/ structure."""
    repo = Path(tmp) / "myrepo"
    runtime = repo / ".workgraph" / "service" / "runtime"
    runtime.mkdir(parents=True)
    return repo


def _write_events(repo: Path, events: list[dict]) -> None:
    events_path = repo / ".workgraph" / "service" / "runtime" / "events.jsonl"
    lines = [json.dumps(e) for e in events]
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_graph(repo: Path, tasks: list[dict]) -> None:
    graph_path = repo / ".workgraph" / "graph.jsonl"
    lines = [json.dumps(t) for t in tasks]
    graph_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


NOW = 1742300000.0  # Fixed epoch for deterministic tests (~2026-03-18)


class TestBuildAgentHistoryNoFile(unittest.TestCase):
    def test_missing_events_jsonl_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "bare"
            repo.mkdir()
            result = build_agent_history(repo)
            self.assertEqual(result["sessions"], [])
            self.assertEqual(result["total_sessions_in_file"], 0)
            self.assertIsNone(result["history_since"])


class TestBuildAgentHistorySingleCleanSession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "claude-code", "actor_id": "session-001"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW - 0,
             "payload": {"actor_id": "session-001"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_session_parsed(self):
        result = build_agent_history(self.repo)
        self.assertEqual(len(result["sessions"]), 1)
        s = result["sessions"][0]
        self.assertEqual(s["session_id"], "session-001")
        self.assertEqual(s["agent_type"], "claude-code")
        self.assertEqual(s["outcome"], "clean_exit")
        self.assertEqual(s["duration_seconds"], 3600)

    def test_timestamps_are_iso_strings(self):
        s = build_agent_history(self.repo)["sessions"][0]
        # ISO strings contain 'T' and 'Z'
        self.assertIn("T", s["started_at"])
        self.assertIn("T", s["ended_at"])

    def test_history_since_set_to_oldest_event(self):
        result = build_agent_history(self.repo)
        self.assertIsNotNone(result["history_since"])


class TestBuildAgentHistoryNoCrashOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # session.started but no session.ended, ts is old (> 10 min ago)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 7200,
             "payload": {"cli": "codex", "actor_id": "session-002"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_unknown_when_no_end_and_old(self):
        result = build_agent_history(self.repo)
        s = result["sessions"][0]
        self.assertEqual(s["outcome"], "unknown")
        self.assertIsNone(s["ended_at"])

    def test_duration_is_none_when_no_end(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertIsNone(s["duration_seconds"])


class TestBuildAgentHistoryStillRunning(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # session.started just now (< 10 min ago)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": time.time() - 60,
             "payload": {"cli": "claude-code", "actor_id": "session-003"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_still_running_when_recent(self):
        result = build_agent_history(self.repo)
        s = result["sessions"][0]
        self.assertEqual(s["outcome"], "still_running")


class TestBuildAgentHistoryCrashedOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "codex", "actor_id": "session-004"}},
            {"kind": "loop.crashed", "repo": "myrepo", "ts": NOW - 1800,
             "payload": {}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_crashed(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertEqual(s["outcome"], "crashed")


class TestBuildAgentHistoryStalledOutcome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "codex", "actor_id": "session-005"}},
            {"kind": "heartbeat.stale", "repo": "myrepo", "ts": NOW - 1800,
             "payload": {}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_outcome_is_stalled(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertEqual(s["outcome"], "stalled")


class TestBuildAgentHistoryMultipleSessions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            # Older session
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 7200,
             "payload": {"cli": "codex", "actor_id": "session-A"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW - 3601,
             "payload": {"actor_id": "session-A"}},
            # Newer session
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "claude-code", "actor_id": "session-B"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW - 0,
             "payload": {"actor_id": "session-B"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_sessions_sorted_most_recent_first(self):
        result = build_agent_history(self.repo)
        sessions = result["sessions"]
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["session_id"], "session-B")
        self.assertEqual(sessions[1]["session_id"], "session-A")

    def test_total_sessions_in_file(self):
        result = build_agent_history(self.repo)
        self.assertEqual(result["total_sessions_in_file"], 2)


class TestBuildAgentHistoryLimitCap(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # Write 25 sessions
        events = []
        for i in range(25):
            events.append({
                "kind": "session.started", "repo": "myrepo",
                "ts": NOW - (25 - i) * 3600,
                "payload": {"cli": "codex", "actor_id": f"session-{i:03d}"},
            })
            events.append({
                "kind": "session.ended", "repo": "myrepo",
                "ts": NOW - (25 - i) * 3600 + 1800,
                "payload": {"actor_id": f"session-{i:03d}"},
            })
        _write_events(self.repo, events)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_limit_is_20(self):
        result = build_agent_history(self.repo)
        self.assertEqual(len(result["sessions"]), 20)
        self.assertEqual(result["total_sessions_in_file"], 25)

    def test_custom_limit_respected(self):
        result = build_agent_history(self.repo, limit=5)
        self.assertEqual(len(result["sessions"]), 5)


class TestBuildAgentHistoryTaskCorrelation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
             "payload": {"cli": "claude-code", "actor_id": "session-T"}},
            {"kind": "session.ended", "repo": "myrepo", "ts": NOW,
             "payload": {"actor_id": "session-T"}},
        ])
        # Two tasks completed within the session window
        import datetime as dt
        start_iso = dt.datetime.fromtimestamp(NOW - 3600, tz=dt.timezone.utc).isoformat()
        end_iso = dt.datetime.fromtimestamp(NOW - 60, tz=dt.timezone.utc).isoformat()
        _write_graph(self.repo, [
            {"id": "task-1", "title": "Write the tests", "status": "done",
             "started_at": start_iso, "completed_at": end_iso, "type": "task"},
            {"id": "task-2", "title": "Implement the handler", "status": "done",
             "started_at": start_iso, "completed_at": end_iso, "type": "task"},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_tasks_completed_attributed_to_session(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertEqual(len(s["tasks_completed"]), 2)
        self.assertIn("task-1", s["tasks_completed"])

    def test_task_titles_capped_at_3(self):
        s = build_agent_history(self.repo)["sessions"][0]
        self.assertLessEqual(len(s["task_titles"]), 3)


class TestBuildAgentHistoryMalformedLines(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        # Mix valid and malformed JSON lines
        events_path = self.repo / ".workgraph" / "service" / "runtime" / "events.jsonl"
        events_path.write_text(
            'not valid json\n'
            + json.dumps({"kind": "session.started", "repo": "myrepo",
                          "ts": NOW - 3600,
                          "payload": {"cli": "codex", "actor_id": "session-M"}})
            + '\n{broken}\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_malformed_lines_skipped_silently(self):
        result = build_agent_history(self.repo)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["session_id"], "session-M")


class TestBuildAgentHistoryRepodiscoveredFiltered(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _make_repo(self._tmp.name)
        _write_events(self.repo, [
            {"kind": "repo.discovered", "repo": "myrepo", "ts": NOW - 100,
             "payload": {"path": str(self.repo), "source": "driftdriver-install"}},
            {"kind": "session.started", "repo": "myrepo", "ts": NOW - 60,
             "payload": {"cli": "codex", "actor_id": "session-R"}},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def test_repo_discovered_events_are_filtered(self):
        result = build_agent_history(self.repo)
        # Only one session from session.started — repo.discovered not counted
        self.assertEqual(len(result["sessions"]), 1)


class TestAgentHistoryInRepoDetailEndpoint(unittest.TestCase):
    """Verify build_agent_history output shape matches what the API serializes."""

    def test_return_shape_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            _write_events(repo, [
                {"kind": "session.started", "repo": "myrepo", "ts": NOW - 3600,
                 "payload": {"cli": "claude-code", "actor_id": "session-API"}},
                {"kind": "session.ended", "repo": "myrepo", "ts": NOW,
                 "payload": {"actor_id": "session-API"}},
            ])
            result = build_agent_history(repo)
            # Top-level keys
            self.assertIn("sessions", result)
            self.assertIn("total_sessions_in_file", result)
            self.assertIn("history_since", result)
            # Session keys
            s = result["sessions"][0]
            for key in ("session_id", "agent_type", "started_at", "ended_at",
                        "duration_seconds", "tasks_completed", "task_titles",
                        "commits_in_window", "outcome"):
                self.assertIn(key, s, f"Missing key: {key}")


if __name__ == "__main__":
    unittest.main()
