from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from driftdriver.northstardrift import (
    apply_northstardrift,
    compute_alignment_score,
    compute_northstardrift,
    emit_northstar_review_tasks,
    load_previous_northstardrift,
    read_northstardrift_history,
    run_as_lane,
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
    repo_north_star: dict | None = None,
) -> dict:
    north_star = repo_north_star
    if north_star is None:
        north_star = {
            "present": True,
            "status": "present",
            "canonical": True,
            "approved": True,
            "source_path": "README.md",
            "title": "North Star",
            "summary": "Default test north star",
            "confidence": "high",
            "signals": ["heading"],
        }
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
        "repo_north_star": north_star,
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
        self.assertEqual(set(northstar["axes"].keys()), {"continuity", "autonomy", "product_quality", "coordination", "self_improvement", "operational_health"})
        self.assertEqual(len(northstar["repo_scores"]), 2)
        worst = northstar["repo_scores"][0]
        self.assertEqual(worst["repo"], "meridian")
        self.assertEqual(worst["tier"], "at-risk")
        self.assertGreater(len(northstar["operator_prompts"]), 0)
        self.assertGreater(len(northstar["recommended_reviews"]), 0)
        self.assertIn("targets", northstar)
        self.assertIn("priority_gaps", northstar["targets"])
        self.assertIn("gap", northstar["targets"]["overall"])
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

    def test_compute_northstardrift_emits_missing_repo_north_star_review(self) -> None:
        snapshot = _snapshot(
            _repo(
                "meridian",
                ready=1,
                repo_north_star={
                    "present": False,
                    "status": "missing",
                    "canonical": False,
                    "approved": False,
                    "source_path": "",
                    "title": "",
                    "summary": "",
                    "confidence": "low",
                    "signals": ["none"],
                },
            )
        )
        northstar = compute_northstardrift(snapshot)
        self.assertEqual(northstar["counts"]["repos_missing_north_star"], 1)
        review = next(
            row for row in northstar["recommended_reviews"] if row["category"] == "missing-repo-north-star"
        )
        self.assertEqual(review["repo"], "meridian")
        self.assertTrue(review["human_approval_required"])

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
            self.assertIn("weekly_points", history)
            self.assertIn("windows", history)

    def test_read_northstardrift_history_includes_daily_weekly_and_window_rollups(self) -> None:
        older = compute_northstardrift(_snapshot(_repo("driftdriver", ready=1), generated_at="2026-02-25T12:00:00Z"))
        current = compute_northstardrift(_snapshot(_repo("driftdriver", in_progress=1), generated_at="2026-03-06T12:00:00Z"))
        with tempfile.TemporaryDirectory() as td:
            service_dir = Path(td) / "service"
            central = Path(td) / "central"
            write_northstardrift_artifacts(service_dir=service_dir, central_repo=central, northstardrift=older)
            write_northstardrift_artifacts(service_dir=service_dir, central_repo=central, northstardrift=current)
            history = read_northstardrift_history(
                service_dir=service_dir,
                central_repo=central,
                current=current,
                limit=12,
                weekly_limit=8,
            )
            self.assertGreaterEqual(history["summary"]["daily_count"], 2)
            self.assertGreaterEqual(len(history["weekly_points"]), 2)
            self.assertIn("7d", history["windows"])
            self.assertIn("30d", history["windows"])

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
            subprocess.run(
                ["wg", "--dir", str(repo / ".workgraph"), "init", "--model", "claude:opus"],
                cwd=str(repo),
                check=True,
                capture_output=True,
            )
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
            subprocess.run(
                ["wg", "--dir", str(repo / ".workgraph"), "init", "--model", "claude:opus"],
                cwd=str(repo),
                check=True,
                capture_output=True,
            )
            snapshot["repos"][0]["path"] = str(repo)
            result = emit_northstar_review_tasks(snapshot=snapshot, report=northstar)
            self.assertEqual(result["created"], 0)
            self.assertEqual(result["skipped"], 1)

    def test_emit_northstar_review_tasks_allows_dirty_repo_when_workgraph_is_ignored(self) -> None:
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
            subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
            (repo / ".gitignore").write_text(".workgraph/\n", encoding="utf-8")
            (repo / "scratch.txt").write_text("dirty\n", encoding="utf-8")
            subprocess.run(
                ["wg", "--dir", str(repo / ".workgraph"), "init", "--model", "claude:opus"],
                cwd=str(repo),
                check=True,
                capture_output=True,
            )
            snapshot["repos"][0]["path"] = str(repo)
            result = emit_northstar_review_tasks(snapshot=snapshot, report=northstar)
            self.assertEqual(result["created"], 1)
            self.assertEqual(result["skipped"], 0)


class NorthstarDriftLaneTests(unittest.TestCase):
    """Tests for the run_as_lane adapter."""

    def test_run_as_lane_returns_lane_result(self) -> None:
        """run_as_lane returns a valid LaneResult that passes contract validation."""
        import json

        from driftdriver.lane_contract import LaneResult, validate_lane_output

        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            result = run_as_lane(project_dir)

            self.assertIsInstance(result, LaneResult)
            self.assertEqual(result.lane, "northstardrift")
            self.assertIsInstance(result.findings, list)
            self.assertIsInstance(result.exit_code, int)
            self.assertIsInstance(result.summary, str)
            self.assertIn("northstardrift:", result.summary)
            self.assertIn("score=", result.summary)
            self.assertIn("tier=", result.summary)

            # Verify it validates through the contract
            raw = json.dumps({
                "lane": result.lane,
                "findings": [
                    {"message": f.message, "severity": f.severity, "file": f.file, "line": f.line, "tags": f.tags}
                    for f in result.findings
                ],
                "exit_code": result.exit_code,
                "summary": result.summary,
            })
            validated = validate_lane_output(raw)
            self.assertIsNotNone(validated)
            self.assertEqual(validated.lane, "northstardrift")

    def test_run_as_lane_handles_exception(self) -> None:
        """run_as_lane returns an error LaneResult if compute_northstardrift raises."""
        from unittest.mock import patch

        with patch("driftdriver.northstardrift.compute_northstardrift", side_effect=RuntimeError("boom")):
            result = run_as_lane(Path("/nonexistent"))

        self.assertEqual(result.lane, "northstardrift")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, "error")
        self.assertIn("boom", result.findings[0].message)


class AlignmentScoringTests(unittest.TestCase):
    def test_aligned_task_scores_high(self) -> None:
        config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory", "context"],
            "anti_patterns": ["pipeline", "funnel"],
        }
        task = {"id": "t1", "title": "Add relationship context to actor view", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertGreater(score, 0.5)

    def test_anti_pattern_task_scores_low(self) -> None:
        config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": ["pipeline", "funnel", "conversion"],
        }
        task = {"id": "t2", "title": "Add deal pipeline funnel stage conversion tracking", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertLess(score, 0.5)

    def test_neutral_task_scores_middle(self) -> None:
        config = {"statement": "Understand relationships", "keywords": ["relationships"], "anti_patterns": ["pipeline"]}
        task = {"id": "t3", "title": "Fix CSS button styling", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertGreaterEqual(score, 0.3)
        self.assertLessEqual(score, 0.7)

    def test_empty_config_returns_neutral(self) -> None:
        config: dict = {"statement": "", "keywords": [], "anti_patterns": []}
        task = {"id": "t4", "title": "Anything", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertAlmostEqual(score, 0.5)


class AlignmentIntegrationTests(unittest.TestCase):
    def test_compute_northstardrift_includes_alignment_section(self) -> None:
        snapshot = _snapshot(_repo("lfw", in_progress=1))
        alignment_config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": ["pipeline"],
        }
        northstar = compute_northstardrift(snapshot, alignment_config=alignment_config)
        self.assertIn("alignment", northstar)
        self.assertIn("overall_alignment", northstar["alignment"])
        self.assertTrue(northstar["alignment"]["configured"])

    def test_compute_northstardrift_without_alignment_config(self) -> None:
        snapshot = _snapshot(_repo("lfw", in_progress=1))
        northstar = compute_northstardrift(snapshot)
        self.assertIn("alignment", northstar)
        self.assertFalse(northstar["alignment"]["configured"])

    def test_run_as_lane_emits_alignment_finding_for_low_score(self) -> None:
        """run_as_lane emits a LaneFinding when alignment is configured and low."""
        from unittest.mock import patch

        alignment_config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": ["pipeline", "funnel", "conversion"],
        }

        # Patch _load_alignment_config to return our test config
        with patch("driftdriver.northstardrift._load_alignment_config", return_value=alignment_config):
            # Create a snapshot with anti-pattern tasks so alignment is low
            with tempfile.TemporaryDirectory() as td:
                project_dir = Path(td)
                (project_dir / ".workgraph").mkdir()
                result = run_as_lane(project_dir)

        self.assertEqual(result.lane, "northstardrift")
        # With no tasks at all, alignment defaults to neutral (0.5) which is below
        # the default threshold — but configured=True should still surface
        alignment_findings = [f for f in result.findings if "alignment" in f.tags]
        # Since there are no tasks, overall_alignment is 0.5 which is below 0.7
        self.assertGreater(len(alignment_findings), 0)

    def test_run_as_lane_no_alignment_finding_when_unconfigured(self) -> None:
        """run_as_lane does NOT emit alignment findings when config is empty."""
        from unittest.mock import patch

        with patch("driftdriver.northstardrift._load_alignment_config", return_value=None):
            with tempfile.TemporaryDirectory() as td:
                project_dir = Path(td)
                (project_dir / ".workgraph").mkdir()
                result = run_as_lane(project_dir)

        alignment_findings = [f for f in result.findings if "alignment" in f.tags]
        self.assertEqual(len(alignment_findings), 0)

    def test_run_as_lane_no_alignment_finding_when_score_high(self) -> None:
        """run_as_lane does NOT emit alignment findings when alignment score is above threshold."""
        from unittest.mock import patch

        alignment_config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": [],
            "alignment_threshold_proceed": 0.7,
        }

        def fake_snapshot(project_dir: Path) -> dict:
            return _snapshot(
                _repo("test-repo", in_progress=1),
            )

        # Override snapshot to have tasks with aligned titles
        with (
            patch("driftdriver.northstardrift._load_alignment_config", return_value=alignment_config),
            patch("driftdriver.northstardrift._minimal_northstar_snapshot") as mock_snap,
        ):
            mock_snap.return_value = _snapshot(
                {
                    **_repo("test-repo"),
                    "in_progress": [
                        {"id": "t1", "title": "Add relationship memory context"},
                        {"id": "t2", "title": "Improve memory relationships tracking"},
                    ],
                }
            )
            result = run_as_lane(Path("/tmp/test"))

        alignment_findings = [f for f in result.findings if "alignment" in f.tags]
        self.assertEqual(len(alignment_findings), 0)


    def test_llm_path_returns_score_and_findings(self) -> None:
        """LLM scoring path returns numeric score 0-100 and findings list."""
        from unittest.mock import MagicMock, patch

        from driftdriver.northstardrift import _score_alignment_with_llm

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "result": '{"score": 72, "findings": ["Tasks focus on infra but north star is about UX"]}',
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })

        with patch("driftdriver.northstardrift.subprocess.run", return_value=mock_result):
            score, findings = _score_alignment_with_llm(
                "Build the best UX for users",
                [{"id": "t1", "title": "Migrate database schema"}],
            )

        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertEqual(score, 72.0)
        self.assertIsInstance(findings, list)
        self.assertEqual(len(findings), 1)
        self.assertIn("UX", findings[0])

    def test_llm_alignment_cache_prevents_redundant_calls(self) -> None:
        """Second call with identical statement+tasks returns cached result without subprocess."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import MagicMock, patch

        tasks = [{"id": "t1", "title": "Build user onboarding flow"}]
        statement = "Deliver delightful user experiences"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({
            "result": '{"score": 85, "findings": []}',
            "usage": {"input_tokens": 80, "output_tokens": 30},
        })

        # Clear module-level cache to isolate this test
        ns_mod._alignment_cache.clear()

        with patch("driftdriver.northstardrift.subprocess.run", return_value=mock_result) as mock_run:
            score1, findings1 = ns_mod._score_alignment_with_llm(statement, tasks)
            score2, findings2 = ns_mod._score_alignment_with_llm(statement, tasks)

        # subprocess called exactly once despite two invocations
        mock_run.assert_called_once()
        self.assertEqual(score1, score2)
        self.assertEqual(findings1, findings2)
        self.assertEqual(score1, 85.0)

    def test_llm_failure_falls_back_to_keyword(self) -> None:
        """When LLM call fails, alignment falls back to keyword scoring."""
        from unittest.mock import patch

        alignment_config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": ["pipeline"],
        }
        snapshot = _snapshot(
            {
                **_repo("test-repo"),
                "in_progress": [{"id": "t1", "title": "Add relationship memory context"}],
                "ready": [],
            }
        )

        with patch(
            "driftdriver.northstardrift._score_alignment_with_llm",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            northstar = compute_northstardrift(snapshot, alignment_config=alignment_config)

        # Fallback should still produce a valid alignment section
        self.assertIn("alignment", northstar)
        self.assertTrue(northstar["alignment"]["configured"])
        alignment = northstar["alignment"]["overall_alignment"]
        self.assertIsInstance(alignment, float)
        self.assertGreaterEqual(alignment, 0.0)
        self.assertLessEqual(alignment, 1.0)
        # Keyword path was used — task title matches keywords, so score > 0.5
        self.assertGreater(alignment, 0.5)
        # LLM was NOT used
        self.assertFalse(northstar["alignment"]["llm_used"])

    def test_agency_eval_score_improves_self_improvement(self) -> None:
        """High agency eval score should not decrease self_improvement vs no score."""
        base_snapshot = _snapshot(_repo("driftdriver", in_progress=1))
        base_snapshot["agency_eval_inputs"] = {"eval_score": None}
        score_none = compute_northstardrift(base_snapshot)["axes"]["self_improvement"]["score"]

        high_snapshot = _snapshot(_repo("driftdriver", in_progress=1))
        high_snapshot["agency_eval_inputs"] = {"eval_score": 100.0}
        score_high = compute_northstardrift(high_snapshot)["axes"]["self_improvement"]["score"]

        self.assertGreaterEqual(score_high, score_none)

    def test_agency_eval_score_zero_does_not_crash(self) -> None:
        """Zero eval score blends without error."""
        snapshot = _snapshot(_repo("driftdriver", in_progress=1))
        snapshot["agency_eval_inputs"] = {"eval_score": 0.0}
        northstar = compute_northstardrift(snapshot)
        self.assertIn("self_improvement", northstar["axes"])


class SignalGateTests(unittest.TestCase):
    """Tests for the disk-persisted content-hash signal gate."""

    def _alignment_cfg(self) -> dict:
        return {
            "statement": "Deliver delightful user experiences",
            "keywords": ["user", "experience"],
            "anti_patterns": ["pipeline"],
        }

    def _mock_llm_result(self, score: int = 80) -> "MagicMock":
        """Return a mock matching the Claude CLI --output-format json envelope."""
        import json as _json
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.returncode = 0
        inner = _json.dumps({"score": score, "findings": []})
        mock.stdout = _json.dumps({
            "result": inner,
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        })
        return mock

    def test_signal_gate_skips_llm_when_hash_matches(self) -> None:
        """Gate: LLM not called when snapshot hash matches the disk state."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import MagicMock, patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        # Compute the hash that would be stored
        expected_hash = ns_mod._snapshot_gate_hash(snapshot)

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            # Pre-seed gate state with matching hash and cached result.
            # Use a recent last_evidence_at so the 4h canary alert does NOT fire.
            recent_ts = datetime.now(timezone.utc).isoformat()
            ns_mod._save_gate_state(gate_path, {
                "snapshot_hash": expected_hash,
                "last_alignment_score": 75.0,
                "last_alignment_findings": [],
                "last_evidence_at": recent_ts,
                "last_checked_at": recent_ts,
                "alignment_cache": {},
            })
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run") as mock_run:
                result = ns_mod.compute_northstardrift(
                    snapshot,
                    alignment_config=alignment_cfg,
                    signal_gate_enabled=True,
                    gate_state_path=gate_path,
                )

        # LLM subprocess must NOT have been called (subprocess.run not called at all)
        mock_run.assert_not_called()
        # Result uses cached score (75.0 / 100 = 0.75)
        self.assertAlmostEqual(result["alignment"]["overall_alignment"], 0.75)
        self.assertFalse(result["alignment"]["llm_used"])

    def test_signal_gate_calls_llm_when_hash_differs(self) -> None:
        """Gate: LLM is called when snapshot hash differs from disk state."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            # Store a different hash so the gate misses
            ns_mod._save_gate_state(gate_path, {
                "snapshot_hash": "differenthash",
                "last_alignment_score": 50.0,
                "last_alignment_findings": [],
                "last_evidence_at": "2026-03-25T10:00:00+00:00",
                "alignment_cache": {},
            })
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run", return_value=self._mock_llm_result(88)) as mock_run:
                result = ns_mod.compute_northstardrift(
                    snapshot,
                    alignment_config=alignment_cfg,
                    signal_gate_enabled=True,
                    gate_state_path=gate_path,
                )

        # LLM must have been called once
        mock_run.assert_called_once()
        self.assertAlmostEqual(result["alignment"]["overall_alignment"], 0.88)
        self.assertTrue(result["alignment"]["llm_used"])

    def test_signal_gate_persists_state_to_disk(self) -> None:
        """Gate: after LLM call, snapshot hash and score are saved to gate state file."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run", return_value=self._mock_llm_result(91)):
                ns_mod.compute_northstardrift(
                    snapshot,
                    alignment_config=alignment_cfg,
                    signal_gate_enabled=True,
                    gate_state_path=gate_path,
                )

            self.assertTrue(gate_path.exists())
            state = ns_mod._load_gate_state(gate_path)
            expected_hash = ns_mod._snapshot_gate_hash(snapshot)
            self.assertEqual(state["snapshot_hash"], expected_hash)
            self.assertAlmostEqual(float(state["last_alignment_score"]), 91.0)
            self.assertIn("alignment_cache", state)
            self.assertIn("last_evidence_at", state)

    def test_signal_gate_loads_disk_cache_into_memory(self) -> None:
        """Gate: disk alignment cache is seeded into _alignment_cache on process start."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        # Pre-seed a fake cache entry in the gate state file
        fake_cache_key = "abc123def456"
        fake_cache_val = [82.0, ["pre-cached finding"]]

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            ns_mod._save_gate_state(gate_path, {
                "snapshot_hash": "nomatch",
                "last_alignment_score": 82.0,
                "last_alignment_findings": [],
                "last_evidence_at": "2026-03-25T09:00:00+00:00",
                "alignment_cache": {fake_cache_key: fake_cache_val},
            })
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run", return_value=self._mock_llm_result(70)):
                ns_mod.compute_northstardrift(
                    snapshot,
                    alignment_config=alignment_cfg,
                    signal_gate_enabled=True,
                    gate_state_path=gate_path,
                )

            # Disk cache entry should now be in-process cache
            self.assertIn(fake_cache_key, ns_mod._alignment_cache)
            self.assertEqual(ns_mod._alignment_cache[fake_cache_key][0], 82.0)

    def test_signal_gate_disabled_always_calls_llm(self) -> None:
        """Gate: when signal_gate_enabled=False, gate is bypassed and LLM is always called."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            expected_hash = ns_mod._snapshot_gate_hash(snapshot)
            # Even if hash matches, gate is disabled
            ns_mod._save_gate_state(gate_path, {
                "snapshot_hash": expected_hash,
                "last_alignment_score": 60.0,
                "last_alignment_findings": [],
                "alignment_cache": {},
            })
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run", return_value=self._mock_llm_result(95)) as mock_run:
                result = ns_mod.compute_northstardrift(
                    snapshot,
                    alignment_config=alignment_cfg,
                    signal_gate_enabled=False,
                    gate_state_path=gate_path,
                )

        mock_run.assert_called_once()
        self.assertTrue(result["alignment"]["llm_used"])

    def test_signal_gate_canary_logs_no_change(self) -> None:
        """Gate: 'northstar-gate: no change, skipping' is printed to stdout on hash match."""
        import driftdriver.northstardrift as ns_mod
        from io import StringIO
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()
        expected_hash = ns_mod._snapshot_gate_hash(snapshot)

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            recent_ts = datetime.now(timezone.utc).isoformat()
            ns_mod._save_gate_state(gate_path, {
                "snapshot_hash": expected_hash,
                "last_alignment_score": 78.0,
                "last_alignment_findings": [],
                "last_evidence_at": recent_ts,
                "alignment_cache": {},
            })
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run"):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    ns_mod.compute_northstardrift(
                        snapshot,
                        alignment_config=alignment_cfg,
                        signal_gate_enabled=True,
                        gate_state_path=gate_path,
                    )
                    output = mock_stdout.getvalue()

        self.assertIn("northstar-gate: no change, skipping", output)

    def test_signal_gate_canary_logs_evidence(self) -> None:
        """Gate: 'northstar-gate: evidence detected' is printed to stdout on hash mismatch."""
        import driftdriver.northstardrift as ns_mod
        from io import StringIO
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            ns_mod._save_gate_state(gate_path, {
                "snapshot_hash": "stale_different_hash",
                "last_alignment_score": 50.0,
                "last_alignment_findings": [],
                "last_evidence_at": "2026-03-25T10:00:00+00:00",
                "alignment_cache": {},
            })
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run", return_value=self._mock_llm_result(80)):
                with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                    ns_mod.compute_northstardrift(
                        snapshot,
                        alignment_config=alignment_cfg,
                        signal_gate_enabled=True,
                        gate_state_path=gate_path,
                    )
                    output = mock_stdout.getvalue()

        self.assertIn("northstar-gate: evidence detected", output)

    def test_signal_gate_hash_is_stable_for_same_snapshot(self) -> None:
        """Gate: _snapshot_gate_hash returns the same value for identical snapshots."""
        import driftdriver.northstardrift as ns_mod

        snapshot = _snapshot(
            _repo("alpha", in_progress=2, ready=1, quality_score=88),
            _repo("beta", stalled=True, blocked_open=3),
        )
        h1 = ns_mod._snapshot_gate_hash(snapshot)
        h2 = ns_mod._snapshot_gate_hash(snapshot)
        self.assertEqual(h1, h2)

    def test_signal_gate_hash_differs_for_different_snapshots(self) -> None:
        """Gate: _snapshot_gate_hash returns different values when tasks change."""
        import driftdriver.northstardrift as ns_mod

        snap_a = _snapshot(_repo("alpha", in_progress=1, ready=0))
        snap_b = _snapshot(_repo("alpha", in_progress=0, ready=2))
        self.assertNotEqual(
            ns_mod._snapshot_gate_hash(snap_a),
            ns_mod._snapshot_gate_hash(snap_b),
        )

    def test_signal_gate_no_gate_state_file_calls_llm(self) -> None:
        """Gate: when no gate state file exists, LLM is called and file is created."""
        import driftdriver.northstardrift as ns_mod
        from unittest.mock import patch

        snapshot = _snapshot(_repo("testrepo", in_progress=1))
        alignment_cfg = self._alignment_cfg()

        with tempfile.TemporaryDirectory() as td:
            gate_path = Path(td) / "northstar-gate-state.json"
            self.assertFalse(gate_path.exists())
            ns_mod._alignment_cache.clear()

            with patch("driftdriver.northstardrift.subprocess.run", return_value=self._mock_llm_result(77)) as mock_run:
                ns_mod.compute_northstardrift(
                    snapshot,
                    alignment_config=alignment_cfg,
                    signal_gate_enabled=True,
                    gate_state_path=gate_path,
                )

            mock_run.assert_called_once()
            self.assertTrue(gate_path.exists())


if __name__ == "__main__":
    unittest.main()
