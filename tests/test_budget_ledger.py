# ABOUTME: Tests for the budget ledger — append-only JSONL tracking of actor operations.
# ABOUTME: Covers recording, time-windowed counting, filtering, and integration with the guard.

from __future__ import annotations

import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from driftdriver.actor import Actor
from driftdriver.budget_ledger import (
    BudgetEntry,
    recent_count,
    recent_count_by_class,
    record_operation,
)


class TestRecordOperation(unittest.TestCase):
    """record_operation creates file and appends entries."""

    def test_creates_file_and_appends_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            entry = record_operation(
                ledger,
                actor_id="lane-qadrift",
                actor_class="lane",
                operation="create",
                repo="/some/repo",
                detail="task-abc",
            )
            self.assertTrue(ledger.exists())
            self.assertEqual(entry.actor_id, "lane-qadrift")
            self.assertEqual(entry.actor_class, "lane")
            self.assertEqual(entry.operation, "create")
            self.assertEqual(entry.repo, "/some/repo")
            self.assertEqual(entry.detail, "task-abc")
            # Timestamp should be valid ISO 8601
            datetime.fromisoformat(entry.timestamp)

            lines = ledger.read_text().strip().split("\n")
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["actor_id"], "lane-qadrift")
            self.assertEqual(data["operation"], "create")

    def test_appends_multiple_entries(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            record_operation(ledger, actor_id="a1", actor_class="lane", operation="create")
            record_operation(ledger, actor_id="a2", actor_class="daemon", operation="dispatch")
            record_operation(ledger, actor_id="a1", actor_class="lane", operation="create")

            lines = ledger.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)

    def test_creates_parent_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "sub" / "dir" / "budget-ledger.jsonl"
            record_operation(ledger, actor_id="a1", actor_class="lane", operation="create")
            self.assertTrue(ledger.exists())


class TestRecentCount(unittest.TestCase):
    """recent_count returns correct counts within time windows."""

    def _write_entry(self, ledger: Path, actor_id: str, operation: str, ts: datetime) -> None:
        entry = {
            "actor_id": actor_id,
            "actor_class": "lane",
            "operation": operation,
            "repo": "",
            "timestamp": ts.isoformat(),
            "detail": "",
        }
        with open(ledger, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_returns_correct_count_within_window(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            now = datetime.now(timezone.utc)
            # 3 creates within last hour
            for i in range(3):
                self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=i * 10))
            count = recent_count(ledger, "lane-qa", "create", window_seconds=3600)
            self.assertEqual(count, 3)

    def test_excludes_entries_outside_window(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            now = datetime.now(timezone.utc)
            # 2 within window
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=10))
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=30))
            # 1 outside window (2 hours ago)
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(hours=2))
            count = recent_count(ledger, "lane-qa", "create", window_seconds=3600)
            self.assertEqual(count, 2)

    def test_filters_by_actor_id(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            now = datetime.now(timezone.utc)
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=5))
            self._write_entry(ledger, "lane-sec", "create", now - timedelta(minutes=5))
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=15))
            count = recent_count(ledger, "lane-qa", "create", window_seconds=3600)
            self.assertEqual(count, 2)

    def test_filters_by_operation_type(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            now = datetime.now(timezone.utc)
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=5))
            self._write_entry(ledger, "lane-qa", "dispatch", now - timedelta(minutes=5))
            self._write_entry(ledger, "lane-qa", "create", now - timedelta(minutes=10))
            count = recent_count(ledger, "lane-qa", "create", window_seconds=3600)
            self.assertEqual(count, 2)

    def test_empty_ledger_returns_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            ledger.write_text("")
            count = recent_count(ledger, "lane-qa", "create", window_seconds=3600)
            self.assertEqual(count, 0)

    def test_missing_ledger_returns_zero(self) -> None:
        count = recent_count(Path("/nonexistent/budget-ledger.jsonl"), "lane-qa", "create")
        self.assertEqual(count, 0)


class TestRecentCountByClass(unittest.TestCase):
    """recent_count_by_class aggregates across actors of same class."""

    def _write_entry(self, ledger: Path, actor_id: str, actor_class: str, operation: str, ts: datetime) -> None:
        entry = {
            "actor_id": actor_id,
            "actor_class": actor_class,
            "operation": operation,
            "repo": "",
            "timestamp": ts.isoformat(),
            "detail": "",
        }
        with open(ledger, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def test_aggregates_across_actors_of_same_class(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            now = datetime.now(timezone.utc)
            self._write_entry(ledger, "lane-qa", "lane", "create", now - timedelta(minutes=5))
            self._write_entry(ledger, "lane-sec", "lane", "create", now - timedelta(minutes=10))
            self._write_entry(ledger, "lane-plan", "lane", "create", now - timedelta(minutes=15))
            # Different class — should not count
            self._write_entry(ledger, "d-1", "daemon", "create", now - timedelta(minutes=5))
            count = recent_count_by_class(ledger, "lane", "create", window_seconds=3600)
            self.assertEqual(count, 3)

    def test_excludes_entries_outside_window(self) -> None:
        with TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "budget-ledger.jsonl"
            now = datetime.now(timezone.utc)
            self._write_entry(ledger, "lane-qa", "lane", "create", now - timedelta(minutes=5))
            self._write_entry(ledger, "lane-sec", "lane", "create", now - timedelta(hours=2))
            count = recent_count_by_class(ledger, "lane", "create", window_seconds=3600)
            self.assertEqual(count, 1)

    def test_missing_ledger_returns_zero(self) -> None:
        count = recent_count_by_class(Path("/nonexistent/ledger.jsonl"), "lane", "create")
        self.assertEqual(count, 0)


class TestGuardIntegration(unittest.TestCase):
    """Integration: guarded_add_drift_task_with_authority records creates and respects hourly limit."""

    def test_records_create_and_respects_hourly_limit(self) -> None:
        """After enough creates, the hourly budget should deny further creation."""
        from driftdriver.drift_task_guard import guarded_add_drift_task_with_authority

        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()
            ledger = wg_dir / "budget-ledger.jsonl"

            # Actor: worker class (max_creates_per_hour=5, max_active_tasks=1)
            # We use "lane" class: max_creates_per_hour=10, max_active_tasks=3
            actor = Actor(id="lane-qadrift", actor_class="lane", name="qadrift")

            call_idx = 0

            def mock_run(cmd, *, cwd=None, timeout=40.0):
                nonlocal call_idx
                if "show" in cmd:
                    return (1, "", "not found")
                if "list" in cmd:
                    return (0, "[]", "")
                if "add" in cmd:
                    return (0, "", "")
                return (1, "", "")

            # Pre-fill ledger with 9 creates (just under limit of 10)
            now = datetime.now(timezone.utc)
            for i in range(9):
                entry = {
                    "actor_id": "lane-qadrift",
                    "actor_class": "lane",
                    "operation": "create",
                    "repo": "",
                    "timestamp": (now - timedelta(minutes=i)).isoformat(),
                    "detail": f"task-{i}",
                }
                with open(ledger, "a") as f:
                    f.write(json.dumps(entry) + "\n")

            # 10th create should succeed (9 recent + this one = check sees 9 < 10)
            with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
                result = guarded_add_drift_task_with_authority(
                    wg_dir=wg_dir,
                    task_id="qadrift-ten",
                    title="tenth task",
                    description="desc",
                    lane_tag="qadrift",
                    actor=actor,
                )
            self.assertEqual(result, "created")

            # The ledger should now have 10 entries (9 pre-filled + 1 recorded)
            lines = ledger.read_text().strip().split("\n")
            self.assertEqual(len(lines), 10)

            # 11th create should be capped (recent_count=10 >= max_creates_per_hour=10)
            with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
                result = guarded_add_drift_task_with_authority(
                    wg_dir=wg_dir,
                    task_id="qadrift-eleven",
                    title="eleventh task",
                    description="desc",
                    lane_tag="qadrift",
                    actor=actor,
                )
            self.assertEqual(result, "capped")

            # Ledger should still have 10 entries (capped = no new record)
            lines = ledger.read_text().strip().split("\n")
            self.assertEqual(len(lines), 10)


if __name__ == "__main__":
    unittest.main()
