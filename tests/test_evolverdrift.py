# ABOUTME: Tests for evolverdrift lane — evolver liveness, orphaned tasks, graph corruption.
# ABOUTME: Covers check_liveness, check_orphaned_tasks, check_graph_corruption, and run_as_lane.
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from driftdriver.evolverdrift import (
    check_graph_corruption,
    check_liveness,
    check_orphaned_tasks,
    run_as_lane,
)


def _make_evolve_run(repo: Path, name: str, config: dict | None = None) -> Path:
    """Create a fake evolve-run directory with optional config.json."""
    run_dir = repo / ".workgraph" / "evolve-runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (run_dir / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
    return run_dir


def _write_graph_jsonl(repo: Path, lines: list[dict]) -> Path:
    """Write graph.jsonl into .workgraph/."""
    wg = repo / ".workgraph"
    wg.mkdir(parents=True, exist_ok=True)
    graph = wg / "graph.jsonl"
    graph.write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n",
        encoding="utf-8",
    )
    return graph


def _write_agents_json(repo: Path, agents: dict) -> Path:
    """Write agents.json into .workgraph/service/."""
    svc = repo / ".workgraph" / "service"
    svc.mkdir(parents=True, exist_ok=True)
    path = svc / "agents.json"
    path.write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return path


class LivenessCheckTests(unittest.TestCase):
    """Tests for check_liveness."""

    def test_no_evolve_runs_dir(self) -> None:
        """Missing evolve-runs dir yields info finding with no-history tag."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            findings = check_liveness(repo)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "info")
            self.assertIn("no-history", findings[0].tags)
            self.assertIn("never run", findings[0].message.lower())

    def test_empty_evolve_runs_dir(self) -> None:
        """Empty evolve-runs dir yields info finding with no-history tag."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph" / "evolve-runs").mkdir(parents=True)
            findings = check_liveness(repo)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "info")
            self.assertIn("no-history", findings[0].tags)

    def test_recent_run_no_findings(self) -> None:
        """A recent evolve run produces no findings."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            now = datetime.now(timezone.utc)
            ts = now.strftime("%Y%m%d-%H%M%S")
            _make_evolve_run(
                repo,
                f"run-{ts}",
                {"timestamp": now.isoformat()},
            )
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 0)

    def test_stale_run_warning(self) -> None:
        """A run older than stale_days but within 2x yields warning."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            stale = datetime.now(timezone.utc) - timedelta(days=10)
            ts = stale.strftime("%Y%m%d-%H%M%S")
            _make_evolve_run(
                repo,
                f"run-{ts}",
                {"timestamp": stale.isoformat()},
            )
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "warning")

    def test_very_stale_run_error(self) -> None:
        """A run older than 2x stale_days yields error."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            old = datetime.now(timezone.utc) - timedelta(days=20)
            ts = old.strftime("%Y%m%d-%H%M%S")
            _make_evolve_run(
                repo,
                f"run-{ts}",
                {"timestamp": old.isoformat()},
            )
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "error")

    def test_liveness_uses_dir_name_when_no_config(self) -> None:
        """Falls back to parsing dir name when config.json is absent."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            recent = datetime.now(timezone.utc) - timedelta(hours=2)
            ts = recent.strftime("%Y%m%d-%H%M%S")
            _make_evolve_run(repo, f"run-{ts}")  # no config.json
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 0)


class OrphanedTasksTests(unittest.TestCase):
    """Tests for check_orphaned_tasks."""

    def test_detects_in_progress_with_dead_agent(self) -> None:
        """In-progress task assigned to a non-alive agent yields warning."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "in-progress", "assigned": "agent-1"},
            ])
            _write_agents_json(repo, {
                "agent-1": {"id": "agent-1", "alive": False, "pid": 999, "task_id": "t1"},
            })
            findings = check_orphaned_tasks(repo)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "warning")
            self.assertIn("t1", findings[0].message)
            self.assertIn("orphaned-task", findings[0].tags)

    def test_no_finding_when_agent_alive(self) -> None:
        """In-progress task with alive agent produces no findings."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "in-progress", "assigned": "agent-1"},
            ])
            _write_agents_json(repo, {
                "agent-1": {"id": "agent-1", "alive": True, "pid": 123, "task_id": "t1"},
            })
            findings = check_orphaned_tasks(repo)
            self.assertEqual(len(findings), 0)

    def test_no_finding_when_no_graph(self) -> None:
        """Missing graph.jsonl produces no findings (not an error)."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            findings = check_orphaned_tasks(repo)
            self.assertEqual(len(findings), 0)

    def test_no_finding_when_no_agents_json(self) -> None:
        """Missing agents.json with in-progress tasks yields orphaned findings."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "in-progress", "assigned": "agent-1"},
            ])
            findings = check_orphaned_tasks(repo)
            # No agents.json means no agent registry, so assigned agent can't be confirmed alive
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "warning")

    def test_skips_done_tasks(self) -> None:
        """Done tasks are not flagged even if agent is dead."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "done", "assigned": "agent-1"},
            ])
            _write_agents_json(repo, {
                "agent-1": {"id": "agent-1", "alive": False, "pid": 999, "task_id": "t1"},
            })
            findings = check_orphaned_tasks(repo)
            self.assertEqual(len(findings), 0)


class GraphCorruptionTests(unittest.TestCase):
    """Tests for check_graph_corruption."""

    def test_detects_duplicate_node_ids(self) -> None:
        """Duplicate IDs in graph.jsonl yield a warning."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "open"},
                {"type": "task", "id": "t1", "status": "done"},
                {"type": "task", "id": "t2", "status": "open"},
            ])
            findings = check_graph_corruption(repo)
            dup_findings = [f for f in findings if "duplicate" in f.message.lower()]
            self.assertEqual(len(dup_findings), 1)
            self.assertEqual(dup_findings[0].severity, "warning")
            self.assertIn("duplicate-ids", dup_findings[0].tags)

    def test_detects_orphan_dependency_refs(self) -> None:
        """References to non-existent IDs in 'after' yield a warning."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "open", "after": ["t0"]},
                {"type": "task", "id": "t2", "status": "open", "after": ["t1"]},
            ])
            findings = check_graph_corruption(repo)
            orphan_findings = [f for f in findings if "orphan" in f.message.lower()]
            self.assertEqual(len(orphan_findings), 1)
            self.assertEqual(orphan_findings[0].severity, "warning")
            self.assertIn("orphan-deps", orphan_findings[0].tags)

    def test_no_findings_for_clean_graph(self) -> None:
        """A valid graph produces no corruption findings."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "open"},
                {"type": "task", "id": "t2", "status": "open", "after": ["t1"]},
            ])
            findings = check_graph_corruption(repo)
            self.assertEqual(len(findings), 0)

    def test_no_findings_when_no_graph(self) -> None:
        """Missing graph.jsonl produces no findings."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            findings = check_graph_corruption(repo)
            self.assertEqual(len(findings), 0)


class RunAsLaneTests(unittest.TestCase):
    """Tests for run_as_lane."""

    def test_returns_valid_lane_result(self) -> None:
        """run_as_lane returns LaneResult with lane='evolverdrift'."""
        from driftdriver.lane_contract import LaneResult

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            result = run_as_lane(repo)
            self.assertIsInstance(result, LaneResult)
            self.assertEqual(result.lane, "evolverdrift")
            self.assertIsInstance(result.findings, list)
            self.assertIsInstance(result.exit_code, int)
            self.assertIsInstance(result.summary, str)

    def test_no_history_suppresses_evolver_checks_but_runs_graph_checks(self) -> None:
        """When evolver has never run, graph checks still execute."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Add a corrupted graph so we can verify graph checks still run
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "open"},
                {"type": "task", "id": "t1", "status": "done"},
            ])
            result = run_as_lane(repo)
            self.assertEqual(result.lane, "evolverdrift")
            # Should have findings: no-history info + duplicate IDs warning
            tags = [tag for f in result.findings for tag in f.tags]
            self.assertIn("no-history", tags)
            self.assertIn("duplicate-ids", tags)

    def test_clean_repo_returns_exit_code_zero(self) -> None:
        """A clean repo with recent evolve run returns exit_code 0."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            now = datetime.now(timezone.utc)
            ts = now.strftime("%Y%m%d-%H%M%S")
            _make_evolve_run(repo, f"run-{ts}", {"timestamp": now.isoformat()})
            _write_graph_jsonl(repo, [
                {"type": "task", "id": "t1", "status": "open"},
                {"type": "task", "id": "t2", "status": "open", "after": ["t1"]},
            ])
            _write_agents_json(repo, {})
            result = run_as_lane(repo)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(len(result.findings), 0)

    def test_findings_produce_nonzero_exit(self) -> None:
        """Any findings should set exit_code to 1."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Create a very stale run to trigger an error finding
            old = datetime.now(timezone.utc) - timedelta(days=30)
            ts = old.strftime("%Y%m%d-%H%M%S")
            _make_evolve_run(repo, f"run-{ts}", {"timestamp": old.isoformat()})
            result = run_as_lane(repo)
            self.assertGreater(result.exit_code, 0)
            self.assertGreater(len(result.findings), 0)

    def test_contract_validation(self) -> None:
        """LaneResult from run_as_lane passes contract validation."""
        from driftdriver.lane_contract import LaneResult, validate_lane_output

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            result = run_as_lane(repo)
            raw = json.dumps({
                "lane": result.lane,
                "findings": [
                    {
                        "message": f.message,
                        "severity": f.severity,
                        "file": f.file,
                        "line": f.line,
                        "tags": f.tags,
                    }
                    for f in result.findings
                ],
                "exit_code": result.exit_code,
                "summary": result.summary,
            })
            validated = validate_lane_output(raw)
            self.assertIsNotNone(validated)
            self.assertEqual(validated.lane, "evolverdrift")


if __name__ == "__main__":
    unittest.main()
