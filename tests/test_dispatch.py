# ABOUTME: Tests for the extracted Dispatch module.
# ABOUTME: Covers worker ID gen, runtime detection, health normalization, and worker snapshot building.

from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from driftdriver.dispatch import (
    build_worker_snapshots,
    current_cycle_id,
    event_timestamp,
    latest_worker_events,
    normalize_health_status,
    worker_id,
    worker_runtime,
)


class TestWorkerRuntime(unittest.TestCase):
    def test_session_id_implies_claude(self) -> None:
        self.assertEqual(worker_runtime({}, "sess-abc"), "claude")

    def test_codex_in_worker_name(self) -> None:
        self.assertEqual(worker_runtime({"worker_name": "codex-agent"}, ""), "codex")

    def test_tmux_in_worker_name(self) -> None:
        self.assertEqual(worker_runtime({"worker_name": "tmux-runner"}, ""), "tmux")

    def test_unknown_fallback(self) -> None:
        self.assertEqual(worker_runtime({"worker_name": "custom"}, ""), "unknown")

    def test_empty_worker_name(self) -> None:
        self.assertEqual(worker_runtime({}, ""), "unknown")

    def test_session_id_takes_precedence_over_worker_name(self) -> None:
        self.assertEqual(worker_runtime({"worker_name": "codex-agent"}, "sess-1"), "claude")


class TestNormalizeHealthStatus(unittest.TestCase):
    def test_alive_becomes_running(self) -> None:
        result = normalize_health_status(
            raw_status="alive",
            last_seen_ts=time.time(),
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "running")

    def test_stale_becomes_watch(self) -> None:
        result = normalize_health_status(
            raw_status="stale",
            last_seen_ts=time.time(),
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "watch")

    def test_dead_becomes_stalled(self) -> None:
        result = normalize_health_status(
            raw_status="dead",
            last_seen_ts=time.time(),
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "stalled")

    def test_finished_becomes_done(self) -> None:
        result = normalize_health_status(
            raw_status="finished",
            last_seen_ts=time.time(),
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "done")

    def test_zero_last_seen_becomes_watch(self) -> None:
        result = normalize_health_status(
            raw_status="",
            last_seen_ts=0.0,
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "watch")

    def test_recent_unknown_status_is_running(self) -> None:
        result = normalize_health_status(
            raw_status="",
            last_seen_ts=time.time(),
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "running")

    def test_old_timestamp_becomes_stalled(self) -> None:
        result = normalize_health_status(
            raw_status="",
            last_seen_ts=time.time() - 3600,
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "stalled")

    def test_medium_age_becomes_watch(self) -> None:
        # After output_stale but before worker_timeout
        result = normalize_health_status(
            raw_status="",
            last_seen_ts=time.time() - 700,
            heartbeat_stale_after_seconds=300,
            output_stale_after_seconds=600,
            worker_timeout_seconds=1800,
        )
        self.assertEqual(result, "watch")


class TestEventTimestamp(unittest.TestCase):
    def test_valid_timestamp(self) -> None:
        self.assertEqual(event_timestamp({"ts": 100.0}), 100.0)

    def test_none_returns_zero(self) -> None:
        self.assertEqual(event_timestamp(None), 0.0)

    def test_non_dict_returns_zero(self) -> None:
        self.assertEqual(event_timestamp("not a dict"), 0.0)

    def test_missing_ts_returns_zero(self) -> None:
        self.assertEqual(event_timestamp({}), 0.0)

    def test_string_ts_converted(self) -> None:
        self.assertEqual(event_timestamp({"ts": "42.5"}), 42.5)


class TestWorkerId(unittest.TestCase):
    def test_basic_generation(self) -> None:
        wid = worker_id("myrepo", "task-1", "sess-abc", "worker-1")
        self.assertIn("myrepo", wid)
        self.assertIn("task-1", wid)
        self.assertIn("sess-abc", wid)

    def test_prefers_session_id(self) -> None:
        wid = worker_id("repo", "t1", "sess-1", "w-1")
        self.assertIn("sess-1", wid)

    def test_falls_back_to_worker_name(self) -> None:
        wid = worker_id("repo", "t1", "", "w-1")
        self.assertIn("w-1", wid)

    def test_falls_back_to_task_id(self) -> None:
        wid = worker_id("repo", "t1", "", "")
        self.assertIn("t1", wid)
        # Should have the task_id used as seed
        parts = wid.split("-")
        # repo-task_id-seed format: last part is the seed (which is task_id)
        self.assertTrue(wid.endswith("t1"))

    def test_all_empty_uses_worker_default(self) -> None:
        wid = worker_id("repo", "", "", "")
        self.assertIn("worker", wid)


class TestCurrentCycleId(unittest.TestCase):
    def test_starts_with_speedriftd(self) -> None:
        cid = current_cycle_id()
        self.assertTrue(cid.startswith("speedriftd-"))

    def test_contains_timestamp_pattern(self) -> None:
        cid = current_cycle_id()
        # Format: speedriftd-YYYYMMDDTHHMMSSz
        self.assertRegex(cid, r"speedriftd-\d{8}T\d{6}Z")


class TestLatestWorkerEvents(unittest.TestCase):
    def test_empty_when_no_events(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = latest_worker_events(Path(td))
            self.assertEqual(result, {})

    def test_returns_latest_event_per_task(self) -> None:
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / ".workgraph" / ".autopilot"
            d.mkdir(parents=True)
            events = [
                {"task_id": "t1", "ts": 100.0, "event": "started"},
                {"task_id": "t1", "ts": 200.0, "event": "heartbeat"},
                {"task_id": "t2", "ts": 150.0, "event": "started"},
            ]
            (d / "workers.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n"
            )
            result = latest_worker_events(Path(td))
            self.assertEqual(result["t1"]["ts"], 200.0)
            self.assertEqual(result["t1"]["event"], "heartbeat")
            self.assertEqual(result["t2"]["ts"], 150.0)

    def test_skips_entries_without_task_id(self) -> None:
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / ".workgraph" / ".autopilot"
            d.mkdir(parents=True)
            events = [
                {"ts": 100.0, "event": "orphan"},
                {"task_id": "t1", "ts": 200.0, "event": "ok"},
            ]
            (d / "workers.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n"
            )
            result = latest_worker_events(Path(td))
            self.assertEqual(len(result), 1)
            self.assertIn("t1", result)


class TestBuildWorkerSnapshots(unittest.TestCase):
    def _make_cfg(self) -> dict[str, Any]:
        return {
            "heartbeat_stale_after_seconds": 300,
            "output_stale_after_seconds": 600,
            "worker_timeout_seconds": 1800,
        }

    def test_empty_workers_returns_empty_lists(self) -> None:
        active, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers={},
            latest_events={},
            cfg=self._make_cfg(),
        )
        self.assertEqual(active, [])
        self.assertEqual(terminal, [])

    def test_running_worker_with_alive_health(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Task 1",
                "worker_name": "ap-t1",
                "session_id": "sess-1",
                "started_at": 100.0,
                "status": "running",
                "drift_fail_count": 0,
                "drift_findings": [],
            }
        }
        with patch("driftdriver.dispatch.check_worker_liveness") as mock_health:
            mock_health.return_value.status = "alive"
            mock_health.return_value.last_event_ts = 200.0
            mock_health.return_value.last_event_type = "pre_tool_use"
            mock_health.return_value.event_count = 5

            active, terminal = build_worker_snapshots(
                repo_name="repo",
                project_dir=Path("/tmp/fake"),
                workers=workers,
                latest_events={},
                cfg=self._make_cfg(),
            )

        self.assertEqual(len(active), 1)
        self.assertEqual(len(terminal), 0)
        self.assertEqual(active[0]["state"], "running")
        self.assertEqual(active[0]["runtime"], "claude")
        self.assertEqual(active[0]["task_id"], "t1")

    def test_completed_worker_goes_to_terminal(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Done Task",
                "worker_name": "ap-t1",
                "session_id": "",
                "started_at": 100.0,
                "status": "completed",
                "drift_fail_count": 0,
                "drift_findings": [],
            }
        }
        active, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers=workers,
            latest_events={},
            cfg=self._make_cfg(),
        )
        self.assertEqual(len(active), 0)
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["state"], "done")

    def test_failed_worker_goes_to_terminal(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Failed",
                "worker_name": "ap-t1",
                "session_id": "",
                "started_at": 100.0,
                "status": "failed",
                "drift_fail_count": 3,
                "drift_findings": ["scope-drift"],
            }
        }
        active, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers=workers,
            latest_events={},
            cfg=self._make_cfg(),
        )
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["state"], "failed")
        self.assertEqual(terminal[0]["drift_fail_count"], 3)
        self.assertEqual(terminal[0]["drift_findings"], ["scope-drift"])

    def test_pending_with_alive_health_is_starting(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Pending",
                "worker_name": "ap-t1",
                "session_id": "sess-new",
                "started_at": time.time(),
                "status": "pending",
                "drift_fail_count": 0,
                "drift_findings": [],
            }
        }
        with patch("driftdriver.dispatch.check_worker_liveness") as mock_health:
            mock_health.return_value.status = "alive"
            mock_health.return_value.last_event_ts = time.time()
            mock_health.return_value.last_event_type = "init"
            mock_health.return_value.event_count = 1

            active, terminal = build_worker_snapshots(
                repo_name="repo",
                project_dir=Path("/tmp/fake"),
                workers=workers,
                latest_events={},
                cfg=self._make_cfg(),
            )

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["state"], "starting")

    def test_dead_worker_is_stalled(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Dead",
                "worker_name": "ap-t1",
                "session_id": "sess-dead",
                "started_at": 100.0,
                "status": "running",
                "drift_fail_count": 0,
                "drift_findings": [],
            }
        }
        with patch("driftdriver.dispatch.check_worker_liveness") as mock_health:
            mock_health.return_value.status = "dead"
            mock_health.return_value.last_event_ts = 150.0
            mock_health.return_value.last_event_type = "pre_tool_use"
            mock_health.return_value.event_count = 2

            active, terminal = build_worker_snapshots(
                repo_name="repo",
                project_dir=Path("/tmp/fake"),
                workers=workers,
                latest_events={},
                cfg=self._make_cfg(),
            )

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["state"], "stalled")

    def test_non_dict_workers_skipped(self) -> None:
        workers = {"t1": "not-a-dict", "t2": None}
        active, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers=workers,
            latest_events={},
            cfg=self._make_cfg(),
        )
        self.assertEqual(active, [])
        self.assertEqual(terminal, [])

    def test_workers_sorted_by_task_and_worker_id(self) -> None:
        workers = {
            "t2": {
                "task_id": "t2",
                "task_title": "Second",
                "worker_name": "ap-t2",
                "session_id": "",
                "started_at": 100.0,
                "status": "completed",
                "drift_fail_count": 0,
                "drift_findings": [],
            },
            "t1": {
                "task_id": "t1",
                "task_title": "First",
                "worker_name": "ap-t1",
                "session_id": "",
                "started_at": 100.0,
                "status": "done",
                "drift_fail_count": 0,
                "drift_findings": [],
            },
        }
        _, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers=workers,
            latest_events={},
            cfg=self._make_cfg(),
        )
        self.assertEqual(terminal[0]["task_id"], "t1")
        self.assertEqual(terminal[1]["task_id"], "t2")

    def test_escalated_worker_state_preserved(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Escalated",
                "worker_name": "ap-t1",
                "session_id": "",
                "started_at": 100.0,
                "status": "escalated",
                "drift_fail_count": 0,
                "drift_findings": [],
            }
        }
        active, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers=workers,
            latest_events={},
            cfg=self._make_cfg(),
        )
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]["state"], "escalated")

    def test_worker_snapshot_has_expected_keys(self) -> None:
        workers = {
            "t1": {
                "task_id": "t1",
                "task_title": "Task",
                "worker_name": "ap-t1",
                "session_id": "",
                "started_at": 100.0,
                "status": "completed",
                "drift_fail_count": 0,
                "drift_findings": [],
            }
        }
        _, terminal = build_worker_snapshots(
            repo_name="repo",
            project_dir=Path("/tmp/fake"),
            workers=workers,
            latest_events={},
            cfg=self._make_cfg(),
        )
        expected_keys = {
            "worker_id", "task_id", "task_title", "worker_name",
            "session_id", "runtime", "state", "started_at",
            "last_heartbeat_at", "last_output_at", "last_event_type",
            "event_count", "drift_fail_count", "drift_findings",
            "project_dir",
        }
        self.assertEqual(set(terminal[0].keys()), expected_keys)


if __name__ == "__main__":
    unittest.main()
