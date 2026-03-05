from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driftdriver.cli import _ordered_optional_plugins
from driftdriver.policy import ensure_drift_policy, load_drift_policy


class PolicyTests(unittest.TestCase):
    def test_ensure_and_load_default_policy(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote = ensure_drift_policy(wg_dir)
            self.assertTrue(wrote)
            self.assertTrue((wg_dir / "drift-policy.toml").exists())

            p = load_drift_policy(wg_dir)
            self.assertEqual(p.mode, "redirect")
            self.assertIn("coredrift", p.order)
            self.assertIn("specdrift", p.order)
            self.assertIn("fixdrift", p.order)
            self.assertGreaterEqual(p.max_auto_depth, 1)
            self.assertTrue(p.contracts_auto_ensure)
            self.assertTrue(p.updates_enabled)
            self.assertEqual(p.updates_check_interval_seconds, 21600)
            self.assertFalse(p.updates_create_followup)
            self.assertEqual(p.loop_max_redrift_depth, 2)
            self.assertEqual(p.loop_max_ready_drift_followups, 20)
            self.assertTrue(p.loop_block_followup_creation)
            self.assertFalse(p.factory["enabled"])
            self.assertEqual(p.factory["cycle_seconds"], 90)
            self.assertTrue(p.factory["plan_only"])
            self.assertFalse(p.factory["emit_followups"])
            self.assertEqual(p.factory["max_followups_per_repo"], 2)
            self.assertEqual(p.model["planner_profile"], "default")
            self.assertEqual(p.model["worker_profile"], "default")
            self.assertEqual(p.model["max_tool_rounds"], 6)
            self.assertEqual(p.sourcedrift["interval_seconds"], 21600)
            self.assertFalse(p.syncdrift["allow_destructive_sync"])
            self.assertEqual(p.stalledrift["open_without_progress_minutes"], 120)
            self.assertEqual(p.servicedrift["restart_budget_per_cycle"], 4)
            self.assertEqual(p.federatedrift["required_checks"], ["drifts", "tests", "lint"])
            self.assertEqual(p.autonomy_default["level"], "observe")
            self.assertFalse(p.autonomy_default["can_push"])
            self.assertEqual(p.autonomy_repos, [])

    def test_load_policy_sanitizes_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join(
                    [
                        "schema = 1",
                        "mode = \"invalid\"",
                        "order = [\"yagnidrift\"]",
                        "",
                        "[recursion]",
                        "cooldown_seconds = -1",
                        "max_auto_actions_per_hour = -3",
                        "max_auto_depth = 0",
                        "",
                        "[updates]",
                        "enabled = false",
                        "check_interval_seconds = -10",
                        "create_followup = true",
                        "",
                        "[contracts]",
                        "auto_ensure = false",
                        "",
                        "[loop_safety]",
                        "max_redrift_depth = -2",
                        "max_ready_drift_followups = -3",
                        "block_followup_creation = false",
                        "",
                        "[factory]",
                        "enabled = true",
                        "cycle_seconds = 0",
                        "plan_only = false",
                        "max_repos_per_cycle = 0",
                        "max_actions_per_cycle = -8",
                        "emit_followups = true",
                        "max_followups_per_repo = -2",
                        "",
                        "[model]",
                        "planner_profile = \"p-high\"",
                        "worker_profile = \"w-mid\"",
                        "temperature = 2.5",
                        "adversarial_prompts = false",
                        "max_tool_rounds = 0",
                        "",
                        "[sourcedrift]",
                        "enabled = false",
                        "interval_seconds = -99",
                        "max_deltas_per_cycle = 0",
                        "auto_create_followups = false",
                        "allow_auto_integrate = true",
                        "",
                        "[syncdrift]",
                        "enabled = false",
                        "allow_rebase = false",
                        "allow_merge = false",
                        "allow_destructive_sync = true",
                        "require_clean_before_pr = false",
                        "",
                        "[stalledrift]",
                        "enabled = false",
                        "open_without_progress_minutes = -1",
                        "max_auto_unblock_actions = -2",
                        "auto_split_large_tasks = false",
                        "",
                        "[servicedrift]",
                        "enabled = false",
                        "restart_budget_per_cycle = -10",
                        "restart_cooldown_seconds = 0",
                        "escalate_after_consecutive_failures = -7",
                        "",
                        "[federatedrift]",
                        "enabled = false",
                        "open_draft_prs = false",
                        "auto_update_existing_drafts = false",
                        "allow_auto_merge = true",
                        "required_checks = [\"\", \"unit\"]",
                        "",
                        "[autonomy.default]",
                        "level = \"invalid-tier\"",
                        "can_push = true",
                        "can_open_pr = true",
                        "can_merge = true",
                        "max_actions_per_cycle = -2",
                        "",
                        "[[autonomy.repo]]",
                        "name = \"repo-a\"",
                        "level = \"safe-pr\"",
                        "can_push = true",
                        "can_open_pr = true",
                        "can_merge = false",
                        "max_actions_per_cycle = 5",
                    ]
                ),
                encoding="utf-8",
            )

            p = load_drift_policy(wg_dir)
            self.assertEqual(p.mode, "redirect")
            self.assertEqual(p.order[0], "coredrift")
            self.assertIn("yagnidrift", p.order)
            self.assertIn("fixdrift", p.order)
            self.assertEqual(p.cooldown_seconds, 0)
            self.assertEqual(p.max_auto_actions_per_hour, 0)
            self.assertEqual(p.max_auto_depth, 1)
            self.assertFalse(p.updates_enabled)
            self.assertEqual(p.updates_check_interval_seconds, 0)
            self.assertTrue(p.updates_create_followup)
            self.assertFalse(p.contracts_auto_ensure)
            self.assertEqual(p.loop_max_redrift_depth, 0)
            self.assertEqual(p.loop_max_ready_drift_followups, 0)
            self.assertFalse(p.loop_block_followup_creation)
            self.assertTrue(p.factory["enabled"])
            self.assertEqual(p.factory["cycle_seconds"], 5)
            self.assertFalse(p.factory["plan_only"])
            self.assertEqual(p.factory["max_repos_per_cycle"], 1)
            self.assertEqual(p.factory["max_actions_per_cycle"], 1)
            self.assertTrue(p.factory["emit_followups"])
            self.assertEqual(p.factory["max_followups_per_repo"], 1)
            self.assertEqual(p.model["planner_profile"], "p-high")
            self.assertEqual(p.model["worker_profile"], "w-mid")
            self.assertEqual(p.model["temperature"], 2.0)
            self.assertFalse(p.model["adversarial_prompts"])
            self.assertEqual(p.model["max_tool_rounds"], 1)
            self.assertFalse(p.sourcedrift["enabled"])
            self.assertEqual(p.sourcedrift["interval_seconds"], 0)
            self.assertEqual(p.sourcedrift["max_deltas_per_cycle"], 1)
            self.assertFalse(p.sourcedrift["auto_create_followups"])
            self.assertTrue(p.sourcedrift["allow_auto_integrate"])
            self.assertFalse(p.syncdrift["enabled"])
            self.assertTrue(p.syncdrift["allow_destructive_sync"])
            self.assertFalse(p.stalledrift["enabled"])
            self.assertEqual(p.stalledrift["open_without_progress_minutes"], 0)
            self.assertEqual(p.stalledrift["max_auto_unblock_actions"], 0)
            self.assertFalse(p.stalledrift["auto_split_large_tasks"])
            self.assertFalse(p.servicedrift["enabled"])
            self.assertEqual(p.servicedrift["restart_budget_per_cycle"], 0)
            self.assertEqual(p.servicedrift["restart_cooldown_seconds"], 1)
            self.assertEqual(p.servicedrift["escalate_after_consecutive_failures"], 1)
            self.assertFalse(p.federatedrift["enabled"])
            self.assertEqual(p.federatedrift["required_checks"], ["unit"])
            self.assertEqual(p.autonomy_default["level"], "observe")
            self.assertTrue(p.autonomy_default["can_push"])
            self.assertTrue(p.autonomy_default["can_open_pr"])
            self.assertTrue(p.autonomy_default["can_merge"])
            self.assertEqual(p.autonomy_default["max_actions_per_cycle"], 0)
            self.assertEqual(len(p.autonomy_repos), 1)
            self.assertEqual(p.autonomy_repos[0]["name"], "repo-a")
            self.assertEqual(p.autonomy_repos[0]["level"], "safe-pr")

    def test_reporting_section_defaults(self) -> None:
        """No [reporting] section → sensible defaults."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            ensure_drift_policy(wg_dir)
            p = load_drift_policy(wg_dir)
            self.assertEqual(p.reporting_central_repo, "")
            self.assertTrue(p.reporting_auto_report)
            self.assertTrue(p.reporting_include_knowledge)

    def test_reporting_section_explicit_values(self) -> None:
        """Explicit [reporting] values are parsed correctly."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join(
                    [
                        "schema = 1",
                        'mode = "redirect"',
                        'order = ["coredrift"]',
                        "",
                        "[reporting]",
                        'central_repo = "/tmp/my-central"',
                        "auto_report = false",
                        "include_knowledge = false",
                    ]
                ),
                encoding="utf-8",
            )
            p = load_drift_policy(wg_dir)
            self.assertEqual(p.reporting_central_repo, "/tmp/my-central")
            self.assertFalse(p.reporting_auto_report)
            self.assertFalse(p.reporting_include_knowledge)

    def test_ordered_optional_plugins(self) -> None:
        ordered = _ordered_optional_plugins(["yagnidrift", "specdrift", "unknown", "specdrift", "redrift"])
        self.assertEqual(ordered[0], "yagnidrift")
        self.assertEqual(ordered[1], "specdrift")
        self.assertEqual(len(ordered), 9)
        self.assertIn("archdrift", ordered)
        self.assertIn("uxdrift", ordered)
        self.assertIn("fixdrift", ordered)
        self.assertIn("redrift", ordered)


if __name__ == "__main__":
    unittest.main()
