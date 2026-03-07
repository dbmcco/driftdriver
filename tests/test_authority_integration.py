# ABOUTME: Integration tests for authority-aware drift task guard.
# ABOUTME: Verifies that guarded_add_drift_task_with_authority uses actor budgets correctly.

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.actor import Actor
from driftdriver.authority import Budget
from driftdriver.drift_task_guard import (
    DEFAULT_CAP_PER_LANE,
    guarded_add_drift_task,
    guarded_add_drift_task_with_authority,
)


def _mock_run_wg(*, existing_ids: set[str] | None = None, active_tasks: list[dict] | None = None):
    """Return a mock _run_wg that handles show, list, and add commands.

    Args:
        existing_ids: Task IDs that already exist (show returns 0).
        active_tasks: List of task dicts returned by wg list.
    """
    existing = existing_ids or set()
    tasks = active_tasks or []

    def _run(cmd, *, cwd=None, timeout=40.0):
        cmd_str = " ".join(cmd)
        if "show" in cmd:
            # Extract task_id: it follows "show" in the command
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


class TestAuthorityIntegrationLaneBudget(unittest.TestCase):
    """Default lane actor respects the lane budget (max_active_tasks=3)."""

    def test_lane_budget_caps_at_three(self) -> None:
        """With 3 active drift tasks, a default lane actor is capped."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task_with_authority(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-new",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "capped")

    def test_lane_budget_allows_under_limit(self) -> None:
        """With 2 active drift tasks, a default lane actor can create."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task_with_authority(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-new",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "created")


class TestAuthorityIntegrationUnauthorized(unittest.TestCase):
    """Actor class lacking 'create' permission returns 'unauthorized'."""

    def test_unauthorized_with_restricted_policy(self) -> None:
        """A policy that strips 'create' from lane grants returns unauthorized."""
        # Custom policy: lane can only read (no create)
        policy = {
            "grants": {"lane": frozenset({"read"})},
        }
        # Write a policy TOML file and load it
        mock = _mock_run_wg()
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            with patch("driftdriver.drift_task_guard.load_authority_policy", return_value=policy):
                result = guarded_add_drift_task_with_authority(
                    wg_dir=Path(".workgraph"),
                    task_id="qadrift-blocked",
                    title="should be blocked",
                    description="desc",
                    lane_tag="qadrift",
                    policy_path=Path("fake-policy.toml"),
                )
        self.assertEqual(result, "unauthorized")

    def test_worker_lacks_create_by_default(self) -> None:
        """Worker actor class does not have 'create' authority by default."""
        mock = _mock_run_wg()
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task_with_authority(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-worker",
                title="worker attempt",
                description="desc",
                lane_tag="qadrift",
                actor=Actor(id="w-1", actor_class="worker", name="impl"),
            )
        self.assertEqual(result, "unauthorized")


class TestAuthorityIntegrationPolicyOverride(unittest.TestCase):
    """Policy override changing lane max_active_tasks allows more tasks."""

    def test_policy_raises_lane_cap_to_five(self) -> None:
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
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            with patch("driftdriver.drift_task_guard.load_authority_policy", return_value=policy):
                result = guarded_add_drift_task_with_authority(
                    wg_dir=Path(".workgraph"),
                    task_id="qadrift-extra",
                    title="extra finding",
                    description="desc",
                    lane_tag="qadrift",
                    policy_path=Path("fake-policy.toml"),
                )
        self.assertEqual(result, "created")

    def test_policy_at_exact_limit_still_caps(self) -> None:
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
                result = guarded_add_drift_task_with_authority(
                    wg_dir=Path(".workgraph"),
                    task_id="qadrift-over",
                    title="over limit",
                    description="desc",
                    lane_tag="qadrift",
                    policy_path=Path("fake-policy.toml"),
                )
        self.assertEqual(result, "capped")


class TestAuthorityIntegrationHumanActor(unittest.TestCase):
    """Human actor (max_active_tasks=999) bypasses the default cap."""

    def test_human_bypasses_lane_cap(self) -> None:
        """A human actor can create even with many active drift tasks."""
        # 10 active drift tasks — way over the lane default of 3
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]}
            for _ in range(10)
        ]
        mock = _mock_run_wg(active_tasks=active_tasks)
        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock):
            result = guarded_add_drift_task_with_authority(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-human",
                title="human override",
                description="desc",
                lane_tag="qadrift",
                actor=Actor(id="h-1", actor_class="human", name="braydon"),
            )
        self.assertEqual(result, "created")


class TestBackwardCompatibility(unittest.TestCase):
    """Original guarded_add_drift_task still works unchanged with its hardcoded cap."""

    def test_default_cap_constant_unchanged(self) -> None:
        self.assertEqual(DEFAULT_CAP_PER_LANE, 3)

    def test_guarded_add_still_caps_at_default(self) -> None:
        """The original function caps at DEFAULT_CAP_PER_LANE without authority."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, json.dumps(active_tasks), "")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-old",
                title="old style",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "capped")

    def test_guarded_add_creates_when_under_cap(self) -> None:
        """The original function creates when under DEFAULT_CAP_PER_LANE."""
        active_tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, json.dumps(active_tasks), "")
            if "add" in cmd:
                return (0, "", "")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-old2",
                title="old style works",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "created")


if __name__ == "__main__":
    unittest.main()
