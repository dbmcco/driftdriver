from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from driftdriver.northstardrift import (
    apply_northstardrift,
    compute_northstardrift,
    emit_northstar_review_tasks,
    load_previous_northstardrift,
    read_northstardrift_history,
    write_northstardrift_artifacts,
)


def _repo(
    name: str,
    *,
    workgraph_exists: bool = True,
    reporting: bool = True,
    stalled: bool = False,
    service_running: bool = True,
    blocked_open: int = 0,
    missing_dependencies: int = 0,
    stale_open: int = 0,
    stale_active: int = 0,
    git_dirty: bool = False,
    dirty_file_count: int = 0,
    behind: int = 0,
    in_progress: int = 0,
    ready: int = 0,
    quality_score: int = 95,
    quality_high: int = 0,
    quality_critical: int = 0,
    security_high: int = 0,
    security_critical: int = 0,
) -> dict:
    return {
        "name": name,
        "exists": True,
        "workgraph_exists": workgraph_exists,
        "reporting": reporting,
        "heartbeat_age_seconds": 120,
        "service_running": service_running,
        "errors": [],
        "blocked_open": blocked_open,
        "missing_dependencies": missing_dependencies,
        "stale_open": [{"id": f"{name}-open-{idx}"} for idx in range(stale_open)],
        "stale_in_progress": [{"id": f"{name}-active-{idx}"} for idx in range(stale_active)],
        "stalled": stalled,
        "stall_reasons": ["dependency chain aged"] if stalled else [],
        "git_dirty": git_dirty,
        "dirty_file_count": dirty_file_count,
        "untracked_file_count": 0,
        "behind": behind,
        "ahead": 0,
        "source": "ecosystem-toml",
        "activity_state": "active" if in_progress else ("stalled" if stalled else "idle"),
        "in_progress": [{"id": f"{name}-task-{idx}", "title": "active"} for idx in range(in_progress)],
        "ready": [{"id": f"{name}-ready-{idx}", "title": "ready"} for idx in range(ready)],
        "task_counts": {"open": ready, "ready": ready, "in-progress": in_progress, "done": 1},
        "security": {
            "critical": security_critical,
            "high": security_high,
            "medium": 0,
            "low": 0,
            "findings_total": security_critical + security_high,
            "at_risk": security_critical > 0 or security_high > 0,
        },
        "quality": {
            "critical": quality_critical,
            "high": quality_high,
            "medium": 0,
            "low": 0,
            "findings_total": quality_critical + quality_high,
            "quality_score": quality_score,
            "at_risk": quality_critical > 0 or quality_high > 0 or quality_score < 80,
        },
    }


def _snapshot(*repos: dict, generated_at: str = "2026-03-06T12:00:00Z") -> dict:
    repo_rows = list(repos)
    tasks_ready = sum(len(row.get("ready") or []) for row in repo_rows)
    tasks_active = sum(len(row.get("in_progress") or []) for row in repo_rows)
    blocked_open = sum(int(row.get("blocked_open") or 0) for row in repo_rows)
    missing_dependencies = sum(int(row.get("missing_dependencies") or 0) for row in repo_rows)
    stale_open = sum(len(row.get("stale_open") or []) for row in repo_rows)
    stale_active = sum(len(row.get("stale_in_progress") or []) for row in repo_rows)
    repos_stalled = sum(1 for row in repo_rows if bool(row.get("stalled")))
    repos_dirty = sum(1 for row in repo_rows if bool(row.get("git_dirty")))
    repos_quality_risk = sum(1 for row in repo_rows if bool((row.get("quality") or {}).get("at_risk")))
    repos_security_risk = sum(1 for row in repo_rows if bool((row.get("security") or {}).get("at_risk")))
    security_critical = sum(int((row.get("security") or {}).get("critical") or 0) for row in repo_rows)
    security_high = sum(int((row.get("security") or {}).get("high") or 0) for row in repo_rows)
    quality_critical = sum(int((row.get("quality") or {}).get("critical") or 0) for row in repo_rows)
    quality_high = sum(int((row.get("quality") or {}).get("high") or 0) for row in repo_rows)
    total_behind = sum(int(row.get("behind") or 0) for row in repo_rows)
    return {
        "generated_at": generated_at,
        "repos": repo_rows,
        "overview": {
            "repos_total": len(repo_rows),
            "tasks_ready": tasks_ready,
            "tasks_in_progress": tasks_active,
            "blocked_open": blocked_open,
            "missing_dependencies": missing_dependencies,
            "stale_open": stale_open,
            "stale_in_progress": stale_active,
            "repos_stalled": repos_stalled,
            "repos_dirty": repos_dirty,
            "repos_quality_risk": repos_quality_risk,
            "repos_security_risk": repos_security_risk,
            "quality_critical": quality_critical,
            "quality_high": quality_high,
            "security_critical": security_critical,
            "security_high": security_high,
            "total_behind": total_behind,
        },
        "repo_dependency_overview": {
            "summary": {
                "repo_count": len(repo_rows),
                "edge_count": 1 if len(repo_rows) > 1 else 0,
                "linked_repos": len(repo_rows) if len(repo_rows) > 1 else 0,
            }
        },
        "updates": {"has_updates": False, "has_discoveries": False, "summary": ""},
        "upstream_candidates": [],
        "supervisor": {"attempted": 0, "started": 0, "failed": 0},
        "factory": {
            "enabled": True,
            "execution": {"attempted": 2, "succeeded": 2, "failed": 0},
        },
    }


class NorthstarDriftTests(unittest.TestCase):
    def test_compute_northstardrift_scores_axes_and_repos(self) -> None:
        snapshot = _snapshot(
            _repo("driftdriver", in_progress=2, ready=1, quality_score=96),
            _repo(
                "meridian",
                stalled=True,
                service_running=False,
                blocked_open=2,
                missing_dependencies=1,
                stale_open=2,
                stale_active=1,
                git_dirty=True,
                dirty_file_count=4,
                quality_score=71,
                quality_high=1,
                security_high=1,
            ),
        )
        northstar = compute_northstardrift(snapshot)
        self.assertIn("summary", northstar)
        self.assertIn("axes", northstar)
        self.assertIn("repo_scores", northstar)
        self.assertEqual(set(northstar["axes"].keys()), {"continuity", "autonomy", "quality", "coordination", "self_improvement"})
        self.assertEqual(len(northstar["repo_scores"]), 2)
        worst = northstar["repo_scores"][0]
        self.assertEqual(worst["repo"], "meridian")
        self.assertEqual(worst["tier"], "at-risk")
        self.assertGreater(len(northstar["operator_prompts"]), 0)
        self.assertGreater(len(northstar["recommended_reviews"]), 0)
        self.assertIn("Dark factory effectiveness", northstar["summary"]["narrative"])

    def test_apply_northstardrift_attaches_repo_payloads(self) -> None:
        snapshot = _snapshot(_repo("driftdriver", in_progress=1))
        northstar = apply_northstardrift(snapshot)
        self.assertIn("northstardrift", snapshot)
        self.assertEqual(snapshot["northstardrift"]["summary"]["overall_score"], northstar["summary"]["overall_score"])
        self.assertIn("northstar", snapshot["repos"][0])
        self.assertEqual(snapshot["repos"][0]["northstar"]["repo"], "driftdriver")

    def test_compute_northstardrift_detects_worsening_against_previous(self) -> None:
        healthy_snapshot = _snapshot(_repo("driftdriver", in_progress=1), _repo("meridian", ready=1))
        apply_northstardrift(healthy_snapshot)
        degraded = _snapshot(
            _repo("driftdriver", in_progress=1),
            _repo(
                "meridian",
                stalled=True,
                service_running=False,
                blocked_open=3,
                missing_dependencies=2,
                stale_open=3,
                stale_active=1,
                quality_score=68,
                quality_high=2,
                security_high=1,
            ),
            generated_at="2026-03-06T13:00:00Z",
        )
        current = compute_northstardrift(degraded, previous=healthy_snapshot["northstardrift"])
        self.assertEqual(current["summary"]["overall_trend"], "worsening")
        meridian = next(row for row in current["repo_scores"] if row["repo"] == "meridian")
        self.assertEqual(meridian["trend"], "worsening")

    def test_latent_repo_is_capped_at_watch_without_extra_pressure(self) -> None:
        snapshot = _snapshot(
            _repo("driftdriver", in_progress=1),
            _repo("archdrift", workgraph_exists=False, reporting=False, service_running=False),
        )
        northstar = compute_northstardrift(snapshot)
        latent = next(row for row in northstar["repo_scores"] if row["repo"] == "archdrift")
        self.assertGreaterEqual(latent["score"], 60.0)
        self.assertNotEqual(latent["tier"], "at-risk")

    def test_write_northstardrift_artifacts_persists_current_and_ledgers(self) -> None:
        northstar = compute_northstardrift(_snapshot(_repo("driftdriver", in_progress=1)))
        with tempfile.TemporaryDirectory() as td:
            service_dir = Path(td) / "service"
            central = Path(td) / "central"
            meta = write_northstardrift_artifacts(
                service_dir=service_dir,
                central_repo=central,
                northstardrift=northstar,
            )
            current = central / "northstardrift" / "current.json"
            self.assertTrue(current.exists())
            self.assertTrue((central / "northstardrift" / "ledgers" / "effectiveness.jsonl").exists())
            self.assertTrue((central / "northstardrift" / "daily" / "2026-03-06.json").exists())
            self.assertEqual(load_previous_northstardrift(service_dir=service_dir, central_repo=central)["summary"]["overall_score"], northstar["summary"]["overall_score"])
            self.assertEqual(meta["current_path"], str(current))
            history = read_northstardrift_history(service_dir=service_dir, central_repo=central, current=northstar)
            self.assertGreaterEqual(history["summary"]["count"], 1)

    def test_emit_northstar_review_tasks_creates_local_followups(self) -> None:
        snapshot = _snapshot(
            _repo("driftdriver", in_progress=1),
            _repo(
                "meridian",
                stalled=True,
                service_running=False,
                blocked_open=2,
                missing_dependencies=1,
                stale_open=2,
                stale_active=1,
                quality_score=71,
                quality_high=1,
                security_high=1,
            ),
        )
        northstar = compute_northstardrift(snapshot)
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "meridian"
            repo.mkdir(parents=True)
            subprocess.run(["wg", "init"], cwd=str(repo), check=True, capture_output=True)
            snapshot["repos"][1]["path"] = str(repo)
            result = emit_northstar_review_tasks(snapshot=snapshot, report=northstar)
            self.assertEqual(result["created"], 1)
            graph = repo / ".workgraph" / "graph.jsonl"
            self.assertTrue(graph.exists())
            self.assertIn("northstardrift:", graph.read_text(encoding="utf-8"))

    def test_emit_northstar_review_tasks_skips_dirty_repos_when_blocked(self) -> None:
        snapshot = _snapshot(
            _repo(
                "meridian",
                stalled=True,
                service_running=False,
                blocked_open=2,
                missing_dependencies=1,
                git_dirty=True,
                dirty_file_count=3,
                quality_score=71,
            ),
        )
        northstar = compute_northstardrift(snapshot)
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "meridian"
            repo.mkdir(parents=True)
            subprocess.run(["wg", "init"], cwd=str(repo), check=True, capture_output=True)
            snapshot["repos"][0]["path"] = str(repo)
            result = emit_northstar_review_tasks(snapshot=snapshot, report=northstar)
            self.assertEqual(result["created"], 0)
            self.assertEqual(result["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
