# ABOUTME: Integration tests for authority-gated drift task creation.
# ABOUTME: Verifies single-path guarded_add_drift_task uses actor budgets correctly.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from driftdriver.actor import Actor
from driftdriver.authority import Budget
from driftdriver.drift_task_guard import (
    DEFAULT_GLOBAL_CEILING,
    guarded_add_drift_task,
    guarded_add_drift_task_with_authority,
)


def _mock_run_wg(*, existing_ids: set[str] | None = None, active_tasks: list[dict] | None = None):
    """Return a mock _run_wg that handles show, list, and add commands."""
    existing = existing_ids or set()
    tasks = active_tasks or []

    def _run(cmd, *, cwd=None, timeout=40.0):
        if "show" in cmd:
            try:
                show_idx = cmd.index("show")
                tid = cmd[show_idx + 1]
            except (ValueError, IndexError):
                tid = ""
            if tid in existing:
                return (0, json.dumps({"id": tid}), "")
            return (1, "", "not found")
        if "list" in cmd:
            return (0, json.dumps(tasks), "")
        if "add" in cmd:
            return (0, "", "")
        return (1, "", "no match")

    return _run


class TestLaneBudget(unittest.TestCase):
    """Default lane actor respects the lane budget (max_active_tasks=3)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_caps_at_three(self) -> None:
        """With 3 active drift tasks, a default lane actor is capped."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-new",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "capped")

    def test_allows_under_limit(self) -> None:
        """With 2 active drift tasks, a default lane actor can create."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock), \
             patch("driftdriver.executor_shim.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-new",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "created")

    def test_dedup_existing_task(self) -> None:
        """An existing task_id returns 'existing' regardless of budget."""
        mock = _mock_run_wg(existing_ids={"qadrift-dup"})
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-dup",
                title="dup",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "existing")

    def test_no_actor_defaults_to_lane(self) -> None:
        """Without explicit actor, a lane actor is created from lane_tag."""
        mock = _mock_run_wg()
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock), \
             patch("driftdriver.executor_shim.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-auto",
                title="auto actor",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "created")

    def test_records_in_budget_ledger(self) -> None:
        """A created task is recorded in the budget ledger."""
        mock = _mock_run_wg()
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock), \
             patch("driftdriver.executor_shim.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-ledger",
                title="ledger test",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "created")
        ledger = self.wg_dir / "budget-ledger.jsonl"
        self.assertTrue(ledger.exists())
        entries = [json.loads(line) for line in ledger.read_text().strip().split("\n")]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["detail"], "qadrift-ledger")


class TestUnauthorized(unittest.TestCase):
    """Actor class lacking 'create' permission returns 'unauthorized'."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_restricted_policy(self) -> None:
        """A policy that strips 'create' from lane grants returns unauthorized."""
        policy = {"grants": {"lane": frozenset({"read"})}}
        mock = _mock_run_wg()
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            with patch("driftdriver.drift_task_guard.load_authority_policy", return_value=policy):
                result = guarded_add_drift_task(
                    wg_dir=self.wg_dir,
                    task_id="qadrift-blocked",
                    title="should be blocked",
                    description="desc",
                    lane_tag="qadrift",
                    policy_path=Path("fake-policy.toml"),
                )
        self.assertEqual(result, "unauthorized")

    def test_worker_lacks_create(self) -> None:
        """Worker actor class does not have 'create' authority by default."""
        mock = _mock_run_wg()
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-worker",
                title="worker attempt",
                description="desc",
                lane_tag="qadrift",
                actor=Actor(id="w-1", actor_class="worker", name="impl"),
            )
        self.assertEqual(result, "unauthorized")


class TestPolicyOverride(unittest.TestCase):
    """Policy override changing lane max_active_tasks allows more tasks."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_raises_lane_cap_to_five(self) -> None:
        """With policy setting lane max_active_tasks=5, 3 active tasks are allowed."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        policy = {
            "budgets": {
                "lane": Budget(max_active_tasks=5, max_creates_per_hour=10, max_dispatches_per_hour=0),
            },
        }
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock), \
             patch("driftdriver.executor_shim.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")):
            with patch("driftdriver.drift_task_guard.load_authority_policy", return_value=policy):
                result = guarded_add_drift_task(
                    wg_dir=self.wg_dir,
                    task_id="qadrift-extra",
                    title="extra finding",
                    description="desc",
                    lane_tag="qadrift",
                    policy_path=Path("fake-policy.toml"),
                )
        self.assertEqual(result, "created")

    def test_at_exact_limit_still_caps(self) -> None:
        """With policy setting lane max_active_tasks=5, 5 active tasks are capped."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        policy = {
            "budgets": {
                "lane": Budget(max_active_tasks=5, max_creates_per_hour=10, max_dispatches_per_hour=0),
            },
        }
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            with patch("driftdriver.drift_task_guard.load_authority_policy", return_value=policy):
                result = guarded_add_drift_task(
                    wg_dir=self.wg_dir,
                    task_id="qadrift-over",
                    title="over limit",
                    description="desc",
                    lane_tag="qadrift",
                    policy_path=Path("fake-policy.toml"),
                )
        self.assertEqual(result, "capped")


class TestHumanActor(unittest.TestCase):
    """Human actor (max_active_tasks=999) bypasses the default cap."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_bypasses_lane_cap(self) -> None:
        """A human actor can create even with many active drift tasks."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]}
            for _ in range(10)
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock), \
             patch("driftdriver.executor_shim.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-human",
                title="human override",
                description="desc",
                lane_tag="qadrift",
                actor=Actor(id="h-1", actor_class="human", name="braydon"),
            )
        self.assertEqual(result, "created")


class TestGlobalCeiling(unittest.TestCase):
    """Global ceiling prevents runaway across all lanes."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_caps_at_global_ceiling(self) -> None:
        """With total drift tasks >= ceiling, creation is blocked."""
        # 50 drift tasks across multiple lanes
        active_tasks = [
            {"status": "open", "tags": ["drift", f"lane-{i % 10}"]}
            for i in range(DEFAULT_GLOBAL_CEILING)
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-overflow",
                title="should be capped globally",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "capped")

    def test_allows_under_global_ceiling(self) -> None:
        """With total drift tasks under ceiling, creation proceeds."""
        active_tasks = [
            {"status": "open", "tags": ["drift", f"lane-{i}"]}
            for i in range(10)  # Well under 50
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock), \
             patch("driftdriver.executor_shim.subprocess.run",
                   return_value=MagicMock(returncode=0, stdout="", stderr="")):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-ok",
                title="under ceiling",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "created")

    def test_escalation_on_global_ceiling(self) -> None:
        """Global ceiling cap records an escalation."""
        active_tasks = [
            {"status": "open", "tags": ["drift", f"lane-{i % 10}"]}
            for i in range(DEFAULT_GLOBAL_CEILING)
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-esc",
                title="escalation test",
                description="desc",
                lane_tag="qadrift",
            )
        esc_path = self.wg_dir / "escalations.jsonl"
        self.assertTrue(esc_path.exists())
        entry = json.loads(esc_path.read_text().strip().split("\n")[-1])
        self.assertIn("global_ceiling", entry["reason"])


class TestBackwardAlias(unittest.TestCase):
    """The old name guarded_add_drift_task_with_authority still works."""

    def test_alias_is_same_function(self) -> None:
        self.assertIs(guarded_add_drift_task, guarded_add_drift_task_with_authority)


if __name__ == "__main__":
    unittest.main()
