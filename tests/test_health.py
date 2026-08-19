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
        fix_drift_task = {"id": "drift-fix-abc", "title": "fix-quality: abc", "status": "open"}
        plain_task = {"id": "abc", "title": "Feature work", "status": "open", "description": ""}
        contract_task = {"id": "x", "description": "```wg-contract\nschema=1\n```"}

        self.assertTrue(is_drift_task(drift_task))
        self.assertTrue(is_drift_task(fix_drift_task))
        self.assertFalse(is_drift_task(plain_task))
        self.assertTrue(has_contract(contract_task))
        self.assertTrue(is_active(drift_task))
        self.assertEqual(redrift_depth("redrift-build-redrift-app"), 2)

    def test_blockers_done_and_cycle_detection(self) -> None:
        tasks = {
            "a": {"id": "a", "status": "done"},
            "b": {"id": "b", "status": "open", "after": ["a"]},
            "c": {"id": "c", "status": "open", "after": ["d"]},
            "d": {"id": "d", "status": "open", "after": ["c"]},
        }
        self.assertTrue(blockers_done(tasks["b"], tasks))
        self.assertFalse(blockers_done(tasks["c"], tasks))
        self.assertTrue(detect_cycle_from("c", tasks))
        self.assertFalse(detect_cycle_from("b", tasks))

    def test_blockers_done_no_dependencies(self) -> None:
        """Task with no after should return True — nothing is blocking it."""
        no_dependency_task: dict = {"id": "free", "status": "open"}
        self.assertTrue(blockers_done(no_dependency_task, {}))

    def test_blockers_done_missing_dependency_treated_as_resolved(self) -> None:
        """If a dependency ID is not in the graph (deleted), treat it as resolved."""
        task = {"id": "orphan", "status": "open", "after": ["deleted-task"]}
        self.assertTrue(blockers_done(task, {}))

    def test_blockers_done_reads_canonical_after(self) -> None:
        """Dependencies are read from the canonical ``after`` field."""
        tasks = {
            "setup": {"id": "setup", "status": "open"},
            "work": {"id": "work", "status": "open", "after": ["setup"]},
        }
        self.assertFalse(blockers_done(tasks["work"], tasks))

    def test_detect_cycle_reads_canonical_after(self) -> None:
        """Cycle detection follows canonical ``after`` dependencies."""
        tasks = {
            "a": {"id": "a", "status": "open", "after": ["b"]},
            "b": {"id": "b", "status": "open", "after": ["a"]},
        }
        self.assertTrue(detect_cycle_from("a", tasks))

    def test_ready_queue_excludes_tasks_blocked_via_after(self) -> None:
        """Tasks whose canonical ``after`` dependencies are open stay out of the queue."""
        tasks = [
            {"id": "setup", "status": "open"},
            {
                "id": "drift-harden-setup",
                "title": "harden: setup",
                "status": "open",
                "after": ["setup"],
                "created_at": "2026-02-18T12:00:00+00:00",
            },
        ]
        ranked = rank_ready_drift_queue(tasks, limit=10)
        self.assertEqual([x["task_id"] for x in ranked], [])

    def test_ready_queue_reports_canonical_after(self) -> None:
        """Ranked entries report dependencies under the canonical field name."""
        tasks = [
            {"id": "setup", "status": "done"},
            {
                "id": "drift-harden-setup",
                "title": "harden: setup",
                "status": "open",
                "after": ["setup"],
                "created_at": "2026-02-18T12:00:00+00:00",
            },
        ]
        ranked = rank_ready_drift_queue(tasks, limit=10)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["after"], ["setup"])
        self.assertNotIn("blocked_by", ranked[0])

    def test_legacy_blocked_by_translated_only_for_explicit_legacy_source(self) -> None:
        """Legacy ``blocked_by`` is honored only when the source is identified as legacy."""
        tasks = {
            "setup": {"id": "setup", "status": "open"},
            "work": {"id": "work", "status": "open", "blocked_by": ["setup"]},
        }
        # Canonical read ignores the legacy field: the task is not blocked.
        self.assertTrue(blockers_done(tasks["work"], tasks))
        # Explicit legacy identification translates blocked_by to dependencies.
        self.assertFalse(blockers_done(tasks["work"], tasks, blocked_by_is_legacy=True))

    def test_conflicting_after_and_blocked_by_surfaces(self) -> None:
        """A task carrying both spellings with different values cannot be guessed."""
        task = {"id": "work", "status": "open", "after": ["a"], "blocked_by": ["b"]}
        with self.assertRaises(ValueError):
            blockers_done(task, {})
        with self.assertRaises(ValueError):
            blockers_done(task, {}, blocked_by_is_legacy=True)

    def test_queue_ranking_and_duplicates(self) -> None:
        tasks = [
            {"id": "parent-1", "status": "done"},
            {"id": "parent-2", "status": "done"},
            {
                "id": "coredrift-pit-parent-1",
                "title": "pit-stop: Parent",
                "status": "open",
                "after": ["parent-1"],
                "created_at": "2026-02-18T12:00:00+00:00",
            },
            {
                "id": "drift-harden-parent-2",
                "title": "harden: Parent",
                "status": "open",
                "after": ["parent-2"],
                "created_at": "2026-02-18T12:01:00+00:00",
            },
            {
                "id": "drift-scope-parent-2",
                "title": "scope: Parent",
                "status": "open",
                "after": ["parent-2"],
                "created_at": "2026-02-18T12:01:30+00:00",
                "not_before": "2099-01-01T00:00:00+00:00",
            },
            {
                "id": "redrift-build-redrift-app",
                "title": "redrift build: redrift analyze: App",
                "status": "open",
                "after": ["parent-1"],
                "created_at": "2026-02-18T12:02:00+00:00",
            },
            {
                "id": "redrift-design-redrift-app",
                "title": "redrift design: redrift analyze: App",
                "status": "open",
                "after": ["parent-1"],
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
            {"id": "redrift-build-redrift-redrift-app", "title": "drift", "status": "open", "after": ["task-1"]},
            {"id": "drift-harden-task-2", "title": "drift", "status": "open", "after": ["task-2"]},
        ]

        healthy_score = compute_scoreboard(healthy)
        risk_score = compute_scoreboard(risk)

        self.assertEqual(healthy_score["status"], "healthy")
        self.assertEqual(risk_score["status"], "risk")


if __name__ == "__main__":
    unittest.main()
