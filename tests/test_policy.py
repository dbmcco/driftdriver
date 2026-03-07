from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driftdriver.cli import _ordered_optional_plugins
from driftdriver.policy import ensure_drift_policy, load_drift_policy
from driftdriver.policy_enforcement import evaluate_enforcement


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
            self.assertEqual(p.secdrift["interval_seconds"], 14400)
            self.assertEqual(p.secdrift["max_review_tasks_per_repo"], 3)
            self.assertFalse(p.secdrift["run_pentest"])
            self.assertTrue(p.qadrift["include_playwright"])
            self.assertEqual(p.qadrift["interval_seconds"], 21600)
            self.assertTrue(p.sessiondriver["enabled"])
            self.assertTrue(p.sessiondriver["require_session_driver"])
            self.assertFalse(p.sessiondriver["allow_cli_fallback"])
            self.assertTrue(p.speedriftd["enabled"])
            self.assertEqual(p.speedriftd["default_mode"], "observe")
            self.assertEqual(p.speedriftd["default_lease_ttl_seconds"], 0)
            self.assertEqual(p.speedriftd["interval_seconds"], 30)
            self.assertEqual(p.speedriftd["heartbeat_stale_after_seconds"], 300)
            self.assertTrue(p.plandrift["enabled"])
            self.assertEqual(p.plandrift["continuation_runtime"], "double-shot-latte")
            self.assertEqual(p.plandrift["orchestration_runtime"], "claude-session-driver")
            self.assertEqual(p.plandrift["review_loop_mode"], "trycycle-inspired")
            self.assertTrue(p.plandrift["fresh_reviewer_required"])
            self.assertEqual(p.plandrift["review_rounds"], 2)
            self.assertEqual(p.northstardrift["weekly_rollup_weeks"], 8)
            self.assertEqual(p.northstardrift["dirty_repo_review_task_mode"], "workgraph-only")
            self.assertEqual(p.northstardrift["targets"]["overall"], 82.0)
            self.assertEqual(p.northstardrift["targets"]["axes"]["quality"], 80.0)
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
                        "[secdrift]",
                        "enabled = false",
                        "interval_seconds = -10",
                        "max_findings_per_repo = 0",
                        "scan_max_files = -1",
                        "scan_max_file_bytes = -4096",
                        "run_pentest = true",
                        "allow_network_scans = true",
                        "target_urls = [\"\", \"https://example.com\"]",
                        "emit_review_tasks = false",
                        "max_review_tasks_per_repo = -3",
                        "hard_stop_on_critical = true",
                        "",
                        "[qadrift]",
                        "enabled = false",
                        "interval_seconds = -1",
                        "max_findings_per_repo = 0",
                        "emit_review_tasks = false",
                        "max_review_tasks_per_repo = -2",
                        "include_playwright = false",
                        "include_test_health = false",
                        "include_workgraph_health = false",
                        "",
                        "[sessiondriver]",
                        "enabled = false",
                        "require_session_driver = false",
                        "allow_cli_fallback = true",
                        "max_dispatch_per_repo = -1",
                        "worker_timeout_seconds = -5",
                        "drift_failure_threshold = 0",
                        "",
                        "[speedriftd]",
                        "enabled = false",
                        "default_mode = \"wild\"",
                        "default_lease_ttl_seconds = -60",
                        "interval_seconds = 0",
                        "max_concurrent_workers = -2",
                        "heartbeat_stale_after_seconds = 0",
                        "output_stale_after_seconds = -30",
                        "worker_timeout_seconds = -60",
                        "retry_cooldown_seconds = -90",
                        "max_retries_per_task = -1",
                        "",
                        "[plandrift]",
                        "enabled = false",
                        "interval_seconds = -99",
                        "max_findings_per_repo = 0",
                        "emit_review_tasks = false",
                        "max_review_tasks_per_repo = -2",
                        "require_integration_tests = false",
                        "require_e2e_tests = false",
                        "require_failure_loopbacks = false",
                        "require_continuation_edges = false",
                        "continuation_runtime = \"\"",
                        "orchestration_runtime = \"\"",
                        "review_loop_mode = \"custom\"",
                        "fresh_reviewer_required = false",
                        "review_rounds = 0",
                        "allow_tmux_fallback = false",
                        "hard_stop_on_critical = true",
                        "",
                        "[northstardrift]",
                        "enabled = false",
                        "emit_review_tasks = false",
                        "emit_operator_prompts = false",
                        "daily_rollup = false",
                        "weekly_trends = false",
                        "score_window = \"2d\"",
                        "comparison_window = \"14d\"",
                        "dirty_repo_blocks_auto_mutation = false",
                        "max_auto_interventions_per_cycle = -3",
                        "max_review_tasks_per_repo = -2",
                        "require_metric_evidence = false",
                        "history_points = 2",
                        "weekly_rollup_weeks = -4",
                        "latent_repo_floor_score = \"bad\"",
                        "target_gap_watch = \"oops\"",
                        "target_gap_critical = \"oops\"",
                        "dirty_repo_review_task_mode = \"wild\"",
                        "",
                        "[northstardrift.targets]",
                        "overall = \"bad\"",
                        "continuity = 91.0",
                        "autonomy = \"bad\"",
                        "quality = 79.0",
                        "coordination = \"bad\"",
                        "self_improvement = 75.0",
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
            self.assertFalse(p.secdrift["enabled"])
            self.assertEqual(p.secdrift["interval_seconds"], 0)
            self.assertEqual(p.secdrift["max_findings_per_repo"], 1)
            self.assertEqual(p.secdrift["scan_max_files"], 20)
            self.assertEqual(p.secdrift["scan_max_file_bytes"], 2048)
            self.assertTrue(p.secdrift["run_pentest"])
            self.assertTrue(p.secdrift["allow_network_scans"])
            self.assertEqual(p.secdrift["target_urls"], ["https://example.com"])
            self.assertFalse(p.secdrift["emit_review_tasks"])
            self.assertEqual(p.secdrift["max_review_tasks_per_repo"], 1)
            self.assertTrue(p.secdrift["hard_stop_on_critical"])
            self.assertFalse(p.qadrift["enabled"])
            self.assertEqual(p.qadrift["interval_seconds"], 0)
            self.assertEqual(p.qadrift["max_findings_per_repo"], 1)
            self.assertFalse(p.qadrift["emit_review_tasks"])
            self.assertEqual(p.qadrift["max_review_tasks_per_repo"], 1)
            self.assertFalse(p.qadrift["include_playwright"])
            self.assertFalse(p.qadrift["include_test_health"])
            self.assertFalse(p.qadrift["include_workgraph_health"])
            self.assertFalse(p.sessiondriver["enabled"])
            self.assertFalse(p.sessiondriver["require_session_driver"])
            self.assertTrue(p.sessiondriver["allow_cli_fallback"])
            self.assertEqual(p.sessiondriver["max_dispatch_per_repo"], 1)
            self.assertEqual(p.sessiondriver["worker_timeout_seconds"], 60)
            self.assertEqual(p.sessiondriver["drift_failure_threshold"], 1)
            self.assertFalse(p.speedriftd["enabled"])
            self.assertEqual(p.speedriftd["default_mode"], "observe")
            self.assertEqual(p.speedriftd["default_lease_ttl_seconds"], 0)
            self.assertEqual(p.speedriftd["interval_seconds"], 5)
            self.assertEqual(p.speedriftd["max_concurrent_workers"], 1)
            self.assertEqual(p.speedriftd["heartbeat_stale_after_seconds"], 30)
            self.assertEqual(p.speedriftd["output_stale_after_seconds"], 30)
            self.assertEqual(p.speedriftd["worker_timeout_seconds"], 30)
            self.assertEqual(p.speedriftd["retry_cooldown_seconds"], 0)
            self.assertEqual(p.speedriftd["max_retries_per_task"], 0)
            self.assertFalse(p.plandrift["enabled"])
            self.assertEqual(p.plandrift["interval_seconds"], 0)
            self.assertEqual(p.plandrift["max_findings_per_repo"], 1)
            self.assertFalse(p.plandrift["emit_review_tasks"])
            self.assertEqual(p.plandrift["max_review_tasks_per_repo"], 1)
            self.assertFalse(p.plandrift["require_integration_tests"])
            self.assertFalse(p.plandrift["require_e2e_tests"])
            self.assertFalse(p.plandrift["require_failure_loopbacks"])
            self.assertFalse(p.plandrift["require_continuation_edges"])
            self.assertEqual(p.plandrift["continuation_runtime"], "double-shot-latte")
            self.assertEqual(p.plandrift["orchestration_runtime"], "claude-session-driver")
            self.assertEqual(p.plandrift["review_loop_mode"], "custom")
            self.assertFalse(p.plandrift["fresh_reviewer_required"])
            self.assertEqual(p.plandrift["review_rounds"], 1)
            self.assertFalse(p.plandrift["allow_tmux_fallback"])
            self.assertTrue(p.plandrift["hard_stop_on_critical"])
            self.assertFalse(p.northstardrift["enabled"])
            self.assertFalse(p.northstardrift["emit_review_tasks"])
            self.assertFalse(p.northstardrift["emit_operator_prompts"])
            self.assertFalse(p.northstardrift["daily_rollup"])
            self.assertFalse(p.northstardrift["weekly_trends"])
            self.assertEqual(p.northstardrift["score_window"], "2d")
            self.assertEqual(p.northstardrift["comparison_window"], "14d")
            self.assertFalse(p.northstardrift["dirty_repo_blocks_auto_mutation"])
            self.assertEqual(p.northstardrift["max_auto_interventions_per_cycle"], 1)
            self.assertEqual(p.northstardrift["max_review_tasks_per_repo"], 1)
            self.assertFalse(p.northstardrift["require_metric_evidence"])
            self.assertEqual(p.northstardrift["history_points"], 6)
            self.assertEqual(p.northstardrift["weekly_rollup_weeks"], 4)
            self.assertEqual(p.northstardrift["latent_repo_floor_score"], 68.0)
            self.assertEqual(p.northstardrift["target_gap_watch"], 5.0)
            self.assertEqual(p.northstardrift["target_gap_critical"], 12.0)
            self.assertEqual(p.northstardrift["dirty_repo_review_task_mode"], "workgraph-only")
            self.assertEqual(p.northstardrift["targets"]["overall"], 82.0)
            self.assertEqual(p.northstardrift["targets"]["axes"]["continuity"], 91.0)
            self.assertEqual(p.northstardrift["targets"]["axes"]["autonomy"], 82.0)
            self.assertEqual(p.northstardrift["targets"]["axes"]["quality"], 79.0)
            self.assertEqual(p.northstardrift["targets"]["axes"]["coordination"], 78.0)
            self.assertEqual(p.northstardrift["targets"]["axes"]["self_improvement"], 75.0)
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

    def test_enforcement_defaults_in_policy(self) -> None:
        """Default enforcement is disabled."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            ensure_drift_policy(wg_dir)
            p = load_drift_policy(wg_dir)
            self.assertFalse(p.enforcement["enabled"])
            self.assertTrue(p.enforcement["block_on_critical"])
            self.assertTrue(p.enforcement["warn_on_error"])
            self.assertEqual(p.enforcement["max_unresolved_warnings"], 10)

    def test_enforcement_parsed_from_toml(self) -> None:
        """Explicit [enforcement] values are parsed correctly."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join([
                    "schema = 1",
                    'mode = "redirect"',
                    "",
                    "[enforcement]",
                    "enabled = true",
                    "block_on_critical = false",
                    "warn_on_error = false",
                    "max_unresolved_warnings = 5",
                ]),
                encoding="utf-8",
            )
            p = load_drift_policy(wg_dir)
            self.assertTrue(p.enforcement["enabled"])
            self.assertFalse(p.enforcement["block_on_critical"])
            self.assertFalse(p.enforcement["warn_on_error"])
            self.assertEqual(p.enforcement["max_unresolved_warnings"], 5)

    def test_evaluate_enforcement_disabled(self) -> None:
        """When enforcement is disabled, always returns clean."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            ensure_drift_policy(wg_dir)
            p = load_drift_policy(wg_dir)
            findings = [{"severity": "critical", "category": "test"}]
            result = evaluate_enforcement(p, findings)
            self.assertFalse(result["blocked"])
            self.assertEqual(result["exit_code"], 0)

    def test_evaluate_enforcement_blocks_on_critical(self) -> None:
        """Critical findings block when enforcement enabled + block_on_critical."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join([
                    "schema = 1",
                    'mode = "redirect"',
                    "",
                    "[enforcement]",
                    "enabled = true",
                    "block_on_critical = true",
                ]),
                encoding="utf-8",
            )
            p = load_drift_policy(wg_dir)
            findings = [
                {"severity": "critical", "category": "credential-leak"},
                {"severity": "warning", "category": "test-gap"},
            ]
            result = evaluate_enforcement(p, findings)
            self.assertTrue(result["blocked"])
            self.assertEqual(result["exit_code"], 2)
            self.assertEqual(result["counts"]["critical"], 1)
            self.assertEqual(result["counts"]["warning"], 1)
            self.assertIn("BLOCKED", result["warnings"][0])

    def test_evaluate_enforcement_warns_on_error(self) -> None:
        """Error findings produce warnings but don't block."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join([
                    "schema = 1",
                    'mode = "redirect"',
                    "",
                    "[enforcement]",
                    "enabled = true",
                    "block_on_critical = true",
                    "warn_on_error = true",
                ]),
                encoding="utf-8",
            )
            p = load_drift_policy(wg_dir)
            findings = [
                {"severity": "error", "category": "mock-violation"},
                {"severity": "info", "category": "note"},
            ]
            result = evaluate_enforcement(p, findings)
            self.assertFalse(result["blocked"])
            self.assertEqual(result["exit_code"], 1)
            self.assertEqual(result["counts"]["error"], 1)
            self.assertEqual(result["counts"]["info"], 1)

    def test_evaluate_enforcement_threshold_exceeded(self) -> None:
        """Exceeding max_unresolved_warnings triggers warning."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join([
                    "schema = 1",
                    'mode = "redirect"',
                    "",
                    "[enforcement]",
                    "enabled = true",
                    "max_unresolved_warnings = 2",
                ]),
                encoding="utf-8",
            )
            p = load_drift_policy(wg_dir)
            findings = [
                {"severity": "warning"},
                {"severity": "warning"},
                {"severity": "warning"},
            ]
            result = evaluate_enforcement(p, findings)
            self.assertFalse(result["blocked"])
            self.assertEqual(result["exit_code"], 1)
            self.assertTrue(any("exceed threshold" in w for w in result["warnings"]))

    def test_evaluate_enforcement_clean_when_all_info(self) -> None:
        """Info-only findings produce no warnings or blocks."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "drift-policy.toml").write_text(
                "\n".join([
                    "schema = 1",
                    'mode = "redirect"',
                    "",
                    "[enforcement]",
                    "enabled = true",
                ]),
                encoding="utf-8",
            )
            p = load_drift_policy(wg_dir)
            findings = [{"severity": "info"}, {"severity": "info"}]
            result = evaluate_enforcement(p, findings)
            self.assertFalse(result["blocked"])
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["warnings"], [])

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
