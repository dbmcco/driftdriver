from __future__ import annotations

import unittest

from driftdriver.health import (
    blockers_done,
    compute_scoreboard,
    detect_cycle_from,
    find_duplicate_open_drift_groups,
    has_contract,
    is_active,
    is_drift_task,
    normalize_drift_key,
    rank_ready_drift_queue,
    redrift_depth,
)


class HealthTests(unittest.TestCase):
    def test_detects_drift_tasks_and_contracts(self) -> None:
        drift_task = {"id": "drift-harden-abc", "title": "harden: abc", "status": "open"}
        plain_task = {"id": "abc", "title": "Feature work", "status": "open", "description": ""}
        contract_task = {"id": "x", "description": "```wg-contract\nschema=1\n```"}

        self.assertTrue(is_drift_task(drift_task))
        self.assertFalse(is_drift_task(plain_task))
        self.assertTrue(has_contract(contract_task))
        self.assertTrue(is_active(drift_task))
        self.assertEqual(redrift_depth("redrift-build-redrift-app"), 2)

    def test_blockers_done_and_cycle_detection(self) -> None:
        tasks = {
            "a": {"id": "a", "status": "done"},
            "b": {"id": "b", "status": "open", "blocked_by": ["a"]},
            "c": {"id": "c", "status": "open", "blocked_by": ["d"]},
            "d": {"id": "d", "status": "open", "blocked_by": ["c"]},
        }
        self.assertTrue(blockers_done(tasks["b"], tasks))
        self.assertFalse(blockers_done(tasks["c"], tasks))
        self.assertTrue(detect_cycle_from("c", tasks))
        self.assertFalse(detect_cycle_from("b", tasks))

    def test_queue_ranking_and_duplicates(self) -> None:
        tasks = [
            {"id": "parent-1", "status": "done"},
            {"id": "parent-2", "status": "done"},
            {
                "id": "coredrift-pit-parent-1",
                "title": "pit-stop: Parent",
                "status": "open",
                "blocked_by": ["parent-1"],
                "created_at": "2026-02-18T12:00:00+00:00",
            },
            {
                "id": "drift-harden-parent-2",
                "title": "harden: Parent",
                "status": "open",
                "blocked_by": ["parent-2"],
                "created_at": "2026-02-18T12:01:00+00:00",
            },
            {
                "id": "drift-scope-parent-2",
                "title": "scope: Parent",
                "status": "open",
                "blocked_by": ["parent-2"],
                "created_at": "2026-02-18T12:01:30+00:00",
                "not_before": "2099-01-01T00:00:00+00:00",
            },
            {
                "id": "redrift-build-redrift-app",
                "title": "redrift build: redrift analyze: App",
                "status": "open",
                "blocked_by": ["parent-1"],
                "created_at": "2026-02-18T12:02:00+00:00",
            },
            {
                "id": "redrift-design-redrift-app",
                "title": "redrift design: redrift analyze: App",
                "status": "open",
                "blocked_by": ["parent-1"],
                "created_at": "2026-02-18T12:03:00+00:00",
            },
        ]
        ranked = rank_ready_drift_queue(tasks, limit=10)
        self.assertGreaterEqual(len(ranked), 3)
        self.assertEqual(ranked[0]["task_id"], "coredrift-pit-parent-1")
        self.assertNotIn("drift-scope-parent-2", [x["task_id"] for x in ranked])
        self.assertEqual(normalize_drift_key(tasks[5]), "app")

        dups = find_duplicate_open_drift_groups(tasks)
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0]["key"], "app")
        self.assertEqual(dups[0]["count"], 2)

    def test_scoreboard_status_progression(self) -> None:
        healthy = [
            {"id": "a", "status": "done", "description": "```wg-contract\nx\n```"},
            {"id": "b", "status": "done", "description": "```wg-contract\nx\n```"},
        ]
        risk = [
            {"id": "task-1", "status": "open", "description": ""},
            {"id": "task-2", "status": "open", "description": ""},
            {"id": "redrift-build-redrift-redrift-app", "title": "drift", "status": "open", "blocked_by": ["task-1"]},
            {"id": "drift-harden-task-2", "title": "drift", "status": "open", "blocked_by": ["task-2"]},
        ]

        healthy_score = compute_scoreboard(healthy)
        risk_score = compute_scoreboard(risk)

        self.assertEqual(healthy_score["status"], "healthy")
        self.assertEqual(risk_score["status"], "risk")


if __name__ == "__main__":
    unittest.main()
