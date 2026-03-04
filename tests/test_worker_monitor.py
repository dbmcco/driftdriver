# ABOUTME: Tests for worker_monitor.py - dead agent detection via event stream
# ABOUTME: Uses real temp JSONL files for liveness checks and triage strategies

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.worker_monitor import (
    TriageAction,
    WorkerHealthState,
    check_worker_liveness,
    detect_dead_workers,
    parse_last_event,
    triage_dead_worker,
)


class ParseLastEventTests(unittest.TestCase):
    def test_parse_last_event_no_file(self) -> None:
        result = parse_last_event(Path("/tmp/nonexistent-events-test.jsonl"))
        self.assertIsNone(result)

    def test_parse_last_event_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            tmp_path = Path(f.name)
        try:
            result = parse_last_event(tmp_path)
            self.assertIsNone(result)
        finally:
            os.unlink(tmp_path)

    def test_parse_last_event_returns_last_valid_line(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"event": "session_start", "ts": 1000}) + "\n")
            f.write(json.dumps({"event": "pre_tool_use", "ts": 2000}) + "\n")
            f.write(json.dumps({"event": "stop", "ts": 3000}) + "\n")
            tmp_path = Path(f.name)
        try:
            result = parse_last_event(tmp_path)
            self.assertIsNotNone(result)
            self.assertEqual(result["event"], "stop")
            self.assertEqual(result["ts"], 3000)
        finally:
            os.unlink(tmp_path)

    def test_parse_last_event_skips_malformed_trailing_lines(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"event": "good", "ts": 1000}) + "\n")
            f.write("not valid json\n")
            tmp_path = Path(f.name)
        try:
            result = parse_last_event(tmp_path)
            self.assertIsNotNone(result)
            self.assertEqual(result["event"], "good")
        finally:
            os.unlink(tmp_path)


class CheckWorkerLivenessTests(unittest.TestCase):
    def test_no_events_file_returns_unknown(self) -> None:
        with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path("/tmp/nonexistent-dir-test")):
            state = check_worker_liveness("sess-nonexistent")
            self.assertEqual(state.status, "unknown")
            self.assertEqual(state.session_id, "sess-nonexistent")
            self.assertEqual(state.event_count, 0)

    def test_recent_event_returns_alive(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            events_file = Path(tmpdir) / "sess-alive.events.jsonl"
            events_file.write_text(
                json.dumps({"event": "pre_tool_use", "ts": time.time()}) + "\n"
            )
            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                state = check_worker_liveness("sess-alive")
                self.assertEqual(state.status, "alive")
                self.assertEqual(state.event_count, 1)
                self.assertEqual(state.last_event_type, "pre_tool_use")
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)

    def test_stale_event_returns_stale(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            events_file = Path(tmpdir) / "sess-stale.events.jsonl"
            # 400 seconds ago = stale (between 300 and 600)
            events_file.write_text(
                json.dumps({"event": "pre_tool_use", "ts": time.time() - 400}) + "\n"
            )
            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                state = check_worker_liveness("sess-stale")
                self.assertEqual(state.status, "stale")
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)

    def test_old_event_returns_dead(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            events_file = Path(tmpdir) / "sess-dead.events.jsonl"
            # 700 seconds ago = dead (>600)
            events_file.write_text(
                json.dumps({"event": "pre_tool_use", "ts": time.time() - 700}) + "\n"
            )
            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                state = check_worker_liveness("sess-dead")
                self.assertEqual(state.status, "dead")
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)

    def test_terminal_event_returns_finished(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            events_file = Path(tmpdir) / "sess-done.events.jsonl"
            events_file.write_text(
                json.dumps({"event": "session_end", "ts": time.time() - 1000}) + "\n"
            )
            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                state = check_worker_liveness("sess-done")
                self.assertEqual(state.status, "finished")
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)


class DetectDeadWorkersTests(unittest.TestCase):
    def test_detect_dead_workers_with_mix(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            # One alive, one dead
            alive_file = Path(tmpdir) / "sess-a.events.jsonl"
            alive_file.write_text(json.dumps({"event": "pre_tool_use", "ts": time.time()}) + "\n")

            dead_file = Path(tmpdir) / "sess-d.events.jsonl"
            dead_file.write_text(json.dumps({"event": "pre_tool_use", "ts": time.time() - 700}) + "\n")

            workers = {"sess-a": "task-1", "sess-d": "task-2"}
            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                dead = detect_dead_workers(workers)
                self.assertIn("sess-d", dead)
                self.assertNotIn("sess-a", dead)
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)

    def test_detect_dead_workers_all_alive(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            alive_file = Path(tmpdir) / "sess-ok.events.jsonl"
            alive_file.write_text(json.dumps({"event": "pre_tool_use", "ts": time.time()}) + "\n")

            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                dead = detect_dead_workers({"sess-ok": "task-1"})
                self.assertEqual(dead, [])
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)


class TriageDeadWorkerTests(unittest.TestCase):
    def test_conservative_strategy_escalates(self) -> None:
        ctx = {"session_id": "sess-1", "task_id": "t-1", "drift_fail_count": 0}
        action = triage_dead_worker(ctx, strategy="conservative")
        self.assertIsInstance(action, TriageAction)
        self.assertEqual(action.action, "escalate")
        self.assertEqual(action.task_id, "t-1")

    def test_aggressive_strategy_restarts(self) -> None:
        ctx = {"session_id": "sess-2", "task_id": "t-2", "drift_fail_count": 1}
        action = triage_dead_worker(ctx, strategy="aggressive")
        self.assertEqual(action.action, "restart")

    def test_aggressive_with_many_drift_fails_escalates(self) -> None:
        ctx = {"session_id": "sess-3", "task_id": "t-3", "drift_fail_count": 5}
        action = triage_dead_worker(ctx, strategy="aggressive")
        self.assertEqual(action.action, "escalate")
        self.assertIn("drift failures", action.reason)

    def test_abandon_strategy(self) -> None:
        ctx = {"session_id": "sess-4", "task_id": "t-4", "drift_fail_count": 0}
        action = triage_dead_worker(ctx, strategy="abandon")
        self.assertEqual(action.action, "abandon")


class WorkerHealthStateDataclassTests(unittest.TestCase):
    def test_worker_health_state_fields(self) -> None:
        state = WorkerHealthState(
            session_id="s1",
            last_event_ts=1000.0,
            last_event_type="pre_tool_use",
            event_count=5,
            status="alive",
        )
        self.assertEqual(state.session_id, "s1")
        self.assertEqual(state.status, "alive")
        self.assertEqual(state.event_count, 5)


if __name__ == "__main__":
    unittest.main()
