# ABOUTME: Tests for automated graph healing — orphan unclaim, log fix, agent purge.
# ABOUTME: Covers heal_repo_graph and heal_ecosystem for wg daemon workarounds.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.ecosystem_hub.heal import heal_repo_graph, heal_ecosystem


class HealOrphanedTasksTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path, tasks: list[dict], agents: dict | None = None) -> Path:
        wg = tmp / ".workgraph"
        wg.mkdir(parents=True)
        with (wg / "graph.jsonl").open("w") as f:
            for t in tasks:
                f.write(json.dumps(t) + "\n")
        if agents is not None:
            svc = wg / "service"
            svc.mkdir(parents=True, exist_ok=True)
            (svc / "registry.json").write_text(json.dumps({"agents": agents}))
        return tmp

    def test_unclaims_in_progress_with_no_agents(self) -> None:
        with TemporaryDirectory() as td:
            repo = self._setup_repo(Path(td), [
                {"kind": "task", "id": "t1", "status": "in-progress", "assigned": "dead-agent", "log": [], "created_at": "2026-03-16T00:00:00Z"},
                {"kind": "task", "id": "t2", "status": "open", "log": [], "created_at": "2026-03-16T00:00:00Z"},
            ])
            result = heal_repo_graph(repo)
            self.assertEqual(result["unclaimed_tasks"], 1)
            # Verify graph was rewritten
            nodes = [json.loads(l) for l in (repo / ".workgraph" / "graph.jsonl").read_text().splitlines() if l.strip()]
            t1 = next(n for n in nodes if n["id"] == "t1")
            self.assertEqual(t1["status"], "open")
            self.assertIn("Auto-healed", t1["log"][-1]["message"])

    def test_leaves_open_tasks_alone(self) -> None:
        with TemporaryDirectory() as td:
            repo = self._setup_repo(Path(td), [
                {"kind": "task", "id": "t1", "status": "open", "log": [], "created_at": "2026-03-16T00:00:00Z"},
            ])
            result = heal_repo_graph(repo)
            self.assertEqual(result["unclaimed_tasks"], 0)

    def test_leaves_in_progress_with_alive_agent(self) -> None:
        with TemporaryDirectory() as td:
            repo = self._setup_repo(
                Path(td),
                [{"kind": "task", "id": "t1", "status": "in-progress", "assigned": "agent-1", "log": [], "created_at": "2026-03-16T00:00:00Z"}],
                agents={"agent-1": {"id": "agent-1", "alive": True, "pid": 12345}},
            )
            result = heal_repo_graph(repo)
            self.assertEqual(result["unclaimed_tasks"], 0)


class HealLogEntriesTests(unittest.TestCase):
    def test_fixes_missing_timestamps(self) -> None:
        with TemporaryDirectory() as td:
            wg = Path(td) / ".workgraph"
            wg.mkdir(parents=True)
            task = {
                "kind": "task", "id": "t1", "status": "done",
                "log": [{"message": "no timestamp here"}],
                "created_at": "2026-03-16T00:00:00Z",
            }
            (wg / "graph.jsonl").write_text(json.dumps(task) + "\n")
            result = heal_repo_graph(Path(td))
            self.assertEqual(result["fixed_log_entries"], 1)
            nodes = [json.loads(l) for l in (wg / "graph.jsonl").read_text().splitlines() if l.strip()]
            self.assertIn("timestamp", nodes[0]["log"][0])


class HealAgentPurgeTests(unittest.TestCase):
    def test_purges_dead_agents_when_over_threshold(self) -> None:
        with TemporaryDirectory() as td:
            wg = Path(td) / ".workgraph"
            svc = wg / "service"
            svc.mkdir(parents=True)
            (wg / "graph.jsonl").write_text("")
            agents = {f"agent-{i}": {"id": f"agent-{i}", "alive": False, "pid": i} for i in range(60)}
            agents["alive-1"] = {"id": "alive-1", "alive": True, "pid": 99999}
            (svc / "registry.json").write_text(json.dumps({"agents": agents}))
            result = heal_repo_graph(Path(td))
            self.assertEqual(result["purged_agents"], 60)
            data = json.loads((svc / "registry.json").read_text())
            self.assertEqual(len(data["agents"]), 1)
            self.assertIn("alive-1", data["agents"])

    def test_does_not_purge_when_under_threshold(self) -> None:
        with TemporaryDirectory() as td:
            wg = Path(td) / ".workgraph"
            svc = wg / "service"
            svc.mkdir(parents=True)
            (wg / "graph.jsonl").write_text("")
            agents = {f"agent-{i}": {"id": f"agent-{i}", "alive": False} for i in range(10)}
            (svc / "registry.json").write_text(json.dumps({"agents": agents}))
            result = heal_repo_graph(Path(td))
            self.assertEqual(result["purged_agents"], 0)


class HealEcosystemTests(unittest.TestCase):
    def test_heals_multiple_repos(self) -> None:
        with TemporaryDirectory() as td:
            ws = Path(td)
            for name in ["repo-a", "repo-b"]:
                wg = ws / name / ".workgraph"
                wg.mkdir(parents=True)
                (wg / "graph.jsonl").write_text(
                    json.dumps({"kind": "task", "id": "stuck", "status": "in-progress", "assigned": "dead", "log": [], "created_at": "2026-03-16T00:00:00Z"}) + "\n"
                )
            results = heal_ecosystem(ws)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(r["unclaimed_tasks"] == 1 for r in results))

    def test_skips_repos_without_workgraph(self) -> None:
        with TemporaryDirectory() as td:
            ws = Path(td)
            (ws / "no-wg-repo").mkdir()
            results = heal_ecosystem(ws)
            self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main()
