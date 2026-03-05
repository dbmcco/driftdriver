from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from driftdriver.factorydrift import (
    build_factory_cycle,
    emit_factory_followups,
    execute_factory_cycle,
    resolve_repo_autonomy,
    summarize_factory_cycle,
    write_factory_ledger,
)


def _policy() -> SimpleNamespace:
    return SimpleNamespace(
        factory={
            "enabled": True,
            "cycle_seconds": 90,
            "plan_only": True,
            "max_repos_per_cycle": 4,
            "max_actions_per_cycle": 12,
            "write_decision_ledger": True,
            "hard_stop_on_failed_verification": True,
        },
        model={
            "planner_profile": "default",
            "worker_profile": "default",
            "temperature": 0.2,
            "max_tool_rounds": 6,
        },
        sourcedrift={"enabled": True},
        syncdrift={"enabled": True, "allow_rebase": True, "allow_merge": True},
        stalledrift={"enabled": True},
        servicedrift={"enabled": True},
        federatedrift={"enabled": True, "open_draft_prs": True},
        secdrift={
            "enabled": True,
            "emit_review_tasks": True,
            "max_review_tasks_per_repo": 2,
            "hard_stop_on_critical": False,
        },
        qadrift={
            "enabled": True,
            "emit_review_tasks": True,
            "max_review_tasks_per_repo": 2,
        },
        autonomy_default={
            "level": "safe-fix",
            "can_push": False,
            "can_open_pr": False,
            "can_merge": False,
            "max_actions_per_cycle": 3,
        },
        autonomy_repos=[
            {
                "name": "repo-upstream",
                "level": "safe-pr",
                "can_push": True,
                "can_open_pr": True,
                "can_merge": False,
                "max_actions_per_cycle": 4,
            }
        ],
    )


class FactoryDriftTests(unittest.TestCase):
    def test_resolve_repo_autonomy_applies_repo_override(self) -> None:
        policy = _policy()
        default = resolve_repo_autonomy(policy, "repo-a")
        self.assertEqual(default["level"], "safe-fix")
        self.assertFalse(default["can_open_pr"])

        override = resolve_repo_autonomy(policy, "repo-upstream")
        self.assertEqual(override["level"], "safe-pr")
        self.assertTrue(override["can_open_pr"])
        self.assertEqual(override["max_actions_per_cycle"], 4)

    def test_build_factory_cycle_generates_stall_and_service_actions(self) -> None:
        policy = _policy()
        snapshot = {
            "overview": {
                "attention_repos": [
                    {"repo": "repo-stalled", "score": 35, "reasons": ["service down"]},
                    {"repo": "repo-active", "score": 10, "reasons": ["active"]},
                ]
            },
            "repos": [
                {
                    "name": "repo-stalled",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": False,
                    "activity_state": "stalled",
                    "missing_dependencies": 1,
                    "blocked_open": 2,
                    "stale_open": [{"id": "o1"}],
                    "stale_in_progress": [],
                    "behind": 2,
                    "git_dirty": True,
                    "ready": [{"id": "r1"}],
                    "in_progress": [],
                },
                {
                    "name": "repo-active",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": True,
                    "activity_state": "active",
                    "missing_dependencies": 0,
                    "blocked_open": 0,
                    "stale_open": [],
                    "stale_in_progress": [],
                    "behind": 0,
                    "git_dirty": False,
                    "ready": [],
                    "in_progress": [{"id": "ip1"}],
                },
            ],
            "upstream_candidates": [{"repo": "repo-stalled"}, {"repo": "repo-upstream"}],
            "updates": {"raw": {"updates": [{"tool": "repo-stalled"}]}},
        }

        cycle = build_factory_cycle(snapshot=snapshot, policy=policy, project_name="driftdriver")
        summary = summarize_factory_cycle(cycle)
        self.assertEqual(summary["execution_mode"], "plan_only")
        self.assertGreater(summary["planned_actions"], 0)

        actions = cycle.get("action_plan") or []
        kinds = [row.get("kind") for row in actions if isinstance(row, dict)]
        self.assertIn("restart_workgraph_service", kinds)
        self.assertIn("unblock_stalled_execution", kinds)
        self.assertIn("repair_dependency_chain", kinds)

    def test_build_factory_cycle_includes_sec_and_quality_actions(self) -> None:
        policy = _policy()
        snapshot = {
            "overview": {"attention_repos": [{"repo": "repo-a", "score": 10}]},
            "repos": [
                {
                    "name": "repo-a",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": True,
                    "activity_state": "active",
                    "missing_dependencies": 0,
                    "blocked_open": 0,
                    "stale_open": [],
                    "stale_in_progress": [],
                    "behind": 0,
                    "git_dirty": False,
                    "ready": [],
                    "in_progress": [{"id": "ip1"}],
                    "security": {"findings_total": 2, "critical": 0, "high": 1},
                    "quality": {"findings_total": 3, "quality_score": 68, "at_risk": True},
                }
            ],
            "upstream_candidates": [],
            "updates": {},
        }
        cycle = build_factory_cycle(snapshot=snapshot, policy=policy, project_name="driftdriver")
        actions = [row for row in (cycle.get("action_plan") or []) if isinstance(row, dict)]
        kinds = {str(row.get("kind") or "") for row in actions}
        self.assertIn("run_security_scan", kinds)
        self.assertIn("run_quality_audit", kinds)

    def test_build_factory_cycle_respects_repo_and_global_budgets(self) -> None:
        policy = _policy()
        policy.factory["max_repos_per_cycle"] = 1
        policy.factory["max_actions_per_cycle"] = 2
        policy.autonomy_default["max_actions_per_cycle"] = 1

        snapshot = {
            "overview": {
                "attention_repos": [
                    {"repo": "repo-a", "score": 40},
                    {"repo": "repo-b", "score": 30},
                ]
            },
            "repos": [
                {
                    "name": "repo-a",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": False,
                    "activity_state": "stalled",
                    "missing_dependencies": 1,
                    "blocked_open": 1,
                    "stale_open": [{"id": "o1"}],
                    "stale_in_progress": [],
                    "behind": 1,
                    "git_dirty": True,
                    "ready": [{"id": "r1"}],
                    "in_progress": [],
                },
                {
                    "name": "repo-b",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": True,
                    "activity_state": "stalled",
                    "missing_dependencies": 0,
                    "blocked_open": 1,
                    "stale_open": [],
                    "stale_in_progress": [],
                    "behind": 1,
                    "git_dirty": False,
                    "ready": [{"id": "r2"}],
                    "in_progress": [],
                },
            ],
            "upstream_candidates": [],
            "updates": {},
        }

        cycle = build_factory_cycle(snapshot=snapshot, policy=policy, project_name="driftdriver")
        selected = cycle.get("selected_repos") or []
        actions = cycle.get("action_plan") or []
        self.assertEqual(len(selected), 1)
        self.assertLessEqual(len(actions), 2)
        self.assertLessEqual(sum(1 for row in actions if row.get("repo") == selected[0]["repo"]), 1)

    def test_write_factory_ledger_writes_local_and_central(self) -> None:
        cycle = {
            "cycle_id": "factory-test",
            "generated_at": "2026-03-05T12:00:00+00:00",
            "action_plan": [],
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            project_dir = root / "driftdriver"
            project_dir.mkdir(parents=True, exist_ok=True)
            central_repo = root / "central"

            written = write_factory_ledger(
                project_dir=project_dir,
                cycle=cycle,
                central_repo=central_repo,
                write_decision_ledger=True,
            )
            self.assertTrue(Path(written["local_latest"]).exists())
            self.assertTrue(Path(written["local_history"]).exists())
            self.assertTrue(Path(written["central_latest"]).exists())
            self.assertTrue(Path(written["central_history"]).exists())
            self.assertTrue(written["central_written"])

            latest = json.loads(Path(written["local_latest"]).read_text(encoding="utf-8"))
            self.assertEqual(latest["cycle_id"], "factory-test")

    def test_emit_factory_followups_creates_or_reuses_repo_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo-a"
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            snapshot = {"repos": [{"name": "repo-a", "path": str(repo)}]}
            cycle = {
                "cycle_id": "factory-test",
                "generated_at": "2026-03-05T12:00:00+00:00",
                "action_plan": [
                    {"repo": "repo-a", "module": "stalledrift", "kind": "repair_dependency_chain", "prompt": "p1"},
                    {"repo": "repo-a", "module": "syncdrift", "kind": "sync_with_upstream", "prompt": "p2"},
                ],
            }

            responses = [
                subprocess.CompletedProcess(["wg"], 1, "", "not found"),
                subprocess.CompletedProcess(["wg"], 0, "", ""),
                subprocess.CompletedProcess(["wg"], 0, "{}", ""),
            ]
            calls: list[list[str]] = []

            def _fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(cmd)
                return responses.pop(0)

            with patch("driftdriver.factorydrift.subprocess.run", side_effect=_fake_run):
                out = emit_factory_followups(cycle=cycle, snapshot=snapshot, max_followups_per_repo=2)

            self.assertEqual(out["attempted"], 2)
            self.assertEqual(out["created"], 1)
            self.assertEqual(out["existing"], 1)
            self.assertEqual(out["skipped"], 0)
            self.assertEqual(len(out["errors"]), 0)
            self.assertGreaterEqual(len(calls), 3)

    def test_emit_factory_followups_respects_per_repo_limit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo-a"
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            snapshot = {"repos": [{"name": "repo-a", "path": str(repo)}]}
            cycle = {
                "cycle_id": "factory-test",
                "generated_at": "2026-03-05T12:00:00+00:00",
                "action_plan": [
                    {"repo": "repo-a", "module": "stalledrift", "kind": "unblock_stalled_execution", "prompt": "p1"},
                    {"repo": "repo-a", "module": "syncdrift", "kind": "sync_with_upstream", "prompt": "p2"},
                ],
            }

            responses = [
                subprocess.CompletedProcess(["wg"], 1, "", "not found"),
                subprocess.CompletedProcess(["wg"], 0, "", ""),
            ]

            def _fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                return responses.pop(0)

            with patch("driftdriver.factorydrift.subprocess.run", side_effect=_fake_run):
                out = emit_factory_followups(cycle=cycle, snapshot=snapshot, max_followups_per_repo=1)

            self.assertEqual(out["attempted"], 1)
            self.assertEqual(out["created"], 1)
            self.assertEqual(out["existing"], 0)
            self.assertEqual(out["skipped"], 1)

    def test_execute_factory_cycle_runs_safe_actions(self) -> None:
        policy = _policy()
        cycle = {
            "cycle_id": "factory-test",
            "generated_at": "2026-03-05T12:00:00+00:00",
            "execution_mode": "execute",
            "execution_status": "planned_only",
            "policy": {"factory": {"hard_stop_on_failed_verification": True}},
            "action_plan": [
                {
                    "id": "repo-a:restart_workgraph_service:1",
                    "repo": "repo-a",
                    "module": "servicedrift",
                    "kind": "restart_workgraph_service",
                    "automation_allowed": True,
                },
                {
                    "id": "repo-a:sync_with_upstream:2",
                    "repo": "repo-a",
                    "module": "syncdrift",
                    "kind": "sync_with_upstream",
                    "automation_allowed": True,
                },
            ],
            "outcomes": {"planned_actions": 2, "executed_actions": 0},
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo-a"
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            snapshot = {"repos": [{"name": "repo-a", "path": str(repo)}]}

            responses = [
                subprocess.CompletedProcess(["wg"], 0, "started", ""),
                subprocess.CompletedProcess(["git"], 0, "", ""),
            ]

            def _fake_run(_cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                return responses.pop(0)

            with patch("driftdriver.factorydrift.subprocess.run", side_effect=_fake_run):
                execution = execute_factory_cycle(
                    cycle=cycle,
                    snapshot=snapshot,
                    policy=policy,
                    project_dir=root,
                    emit_followups=False,
                    max_followups_per_repo=2,
                    allow_execute_draft_prs=False,
                )

            self.assertEqual(execution["executed"], 2)
            self.assertEqual(execution["failed"], 0)
            self.assertEqual(cycle["execution_status"], "executed")
            self.assertEqual(cycle["outcomes"]["executed_actions"], 2)

    def test_execute_factory_cycle_hard_stops_on_failure(self) -> None:
        policy = _policy()
        cycle = {
            "cycle_id": "factory-test",
            "generated_at": "2026-03-05T12:00:00+00:00",
            "execution_mode": "execute",
            "execution_status": "planned_only",
            "policy": {"factory": {"hard_stop_on_failed_verification": True}},
            "action_plan": [
                {
                    "id": "repo-a:restart_workgraph_service:1",
                    "repo": "repo-a",
                    "module": "servicedrift",
                    "kind": "restart_workgraph_service",
                    "automation_allowed": True,
                },
                {
                    "id": "repo-a:sync_with_upstream:2",
                    "repo": "repo-a",
                    "module": "syncdrift",
                    "kind": "sync_with_upstream",
                    "automation_allowed": True,
                },
            ],
            "outcomes": {"planned_actions": 2, "executed_actions": 0},
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo-a"
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            snapshot = {"repos": [{"name": "repo-a", "path": str(repo)}]}

            responses = [
                subprocess.CompletedProcess(["wg"], 1, "", "boom"),
            ]

            def _fake_run(_cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                return responses.pop(0)

            with patch("driftdriver.factorydrift.subprocess.run", side_effect=_fake_run):
                execution = execute_factory_cycle(
                    cycle=cycle,
                    snapshot=snapshot,
                    policy=policy,
                    project_dir=root,
                    emit_followups=False,
                    max_followups_per_repo=2,
                    allow_execute_draft_prs=False,
                )

            self.assertEqual(execution["failed"], 1)
            self.assertTrue(execution["stopped_early"])
            self.assertEqual(cycle["execution_status"], "failed")
            attempts = execution.get("attempts") or []
            self.assertEqual(str(attempts[1]["status"]), "skipped")

    def test_execute_factory_cycle_runs_sec_and_quality_actions(self) -> None:
        policy = _policy()
        cycle = {
            "cycle_id": "factory-test",
            "generated_at": "2026-03-05T12:00:00+00:00",
            "execution_mode": "execute",
            "execution_status": "planned_only",
            "policy": {"factory": {"hard_stop_on_failed_verification": True}},
            "action_plan": [
                {
                    "id": "repo-a:run_security_scan:1",
                    "repo": "repo-a",
                    "module": "secdrift",
                    "kind": "run_security_scan",
                    "automation_allowed": True,
                },
                {
                    "id": "repo-a:run_quality_audit:2",
                    "repo": "repo-a",
                    "module": "qadrift",
                    "kind": "run_quality_audit",
                    "automation_allowed": True,
                },
            ],
            "outcomes": {"planned_actions": 2, "executed_actions": 0},
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo-a"
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            snapshot = {
                "repos": [
                    {
                        "name": "repo-a",
                        "path": str(repo),
                        "stalled": False,
                        "missing_dependencies": 0,
                        "blocked_open": 0,
                        "workgraph_exists": True,
                        "service_running": True,
                        "in_progress": [],
                        "ready": [],
                    }
                ]
            }

            sec_report = {
                "summary": {"findings_total": 2, "critical": 0, "high": 1},
                "recommended_reviews": [{"fingerprint": "abc", "severity": "high"}],
                "model_contract": {"decision_owner": "model"},
            }
            qa_report = {
                "summary": {"findings_total": 3, "quality_score": 72},
                "recommended_reviews": [{"fingerprint": "def", "severity": "medium"}],
                "model_contract": {"decision_owner": "model"},
            }
            with patch("driftdriver.factorydrift.run_secdrift_scan", return_value=sec_report), patch(
                "driftdriver.factorydrift.emit_security_review_tasks",
                return_value={"enabled": True, "attempted": 1, "created": 1, "existing": 0, "skipped": 0, "errors": [], "tasks": []},
            ), patch(
                "driftdriver.factorydrift.run_program_quality_scan",
                return_value=qa_report,
            ), patch(
                "driftdriver.factorydrift.emit_quality_review_tasks",
                return_value={"enabled": True, "attempted": 1, "created": 1, "existing": 0, "skipped": 0, "errors": [], "tasks": []},
            ):
                execution = execute_factory_cycle(
                    cycle=cycle,
                    snapshot=snapshot,
                    policy=policy,
                    project_dir=root,
                    emit_followups=False,
                    max_followups_per_repo=2,
                    allow_execute_draft_prs=False,
                )

            self.assertEqual(execution["executed"], 2)
            self.assertEqual(execution["failed"], 0)
            attempts = execution.get("attempts") or []
            self.assertEqual(str(attempts[0]["status"]), "succeeded")
            self.assertEqual(str(attempts[1]["status"]), "succeeded")


if __name__ == "__main__":
    unittest.main()
