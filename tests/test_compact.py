from __future__ import annotations

import unittest

from driftdriver.cli import _compact_plan


class CompactPlanTests(unittest.TestCase):
    def test_compact_plan_abandons_duplicate_groups(self) -> None:
        tasks = [
            {"id": "parent-a", "status": "done"},
            {"id": "redrift-design-redrift-app", "title": "redrift design: redrift analyze: App", "status": "open", "blocked_by": ["parent-a"], "created_at": "2026-02-18T12:02:00+00:00"},
            {"id": "redrift-build-redrift-app", "title": "redrift build: redrift analyze: App", "status": "open", "blocked_by": ["parent-a"], "created_at": "2026-02-18T12:03:00+00:00"},
            {"id": "redrift-analyze-redrift-app", "title": "redrift analyze: App", "status": "open", "blocked_by": ["parent-a"], "created_at": "2026-02-18T12:01:00+00:00"},
        ]
        plan = _compact_plan(tasks=tasks, max_ready=10, max_redrift_depth=4)
        self.assertEqual(len(plan["duplicate_groups"]), 1)
        self.assertEqual(plan["duplicate_groups"][0]["keep_task_id"], "redrift-analyze-redrift-app")
        self.assertIn("redrift-design-redrift-app", plan["abandon_task_ids"])
        self.assertIn("redrift-build-redrift-app", plan["abandon_task_ids"])

    def test_compact_plan_defers_overflow_ready_drift(self) -> None:
        tasks = [
            {"id": "root", "status": "done"},
            {"id": "coredrift-pit-a", "title": "pit-stop: A", "status": "open", "blocked_by": ["root"], "created_at": "2026-02-18T12:00:00+00:00"},
            {"id": "drift-harden-a", "title": "harden: A", "status": "open", "blocked_by": ["root"], "created_at": "2026-02-18T12:01:00+00:00"},
            {"id": "drift-scope-a", "title": "scope: A", "status": "open", "blocked_by": ["root"], "created_at": "2026-02-18T12:02:00+00:00"},
        ]
        plan = _compact_plan(tasks=tasks, max_ready=2, max_redrift_depth=4)
        self.assertEqual(plan["ready_drift_before"], 3)
        self.assertEqual(plan["max_ready_drift"], 2)
        self.assertEqual(len(plan["defer_task_ids"]), 1)

    def test_compact_plan_does_not_defer_items_selected_for_abandon(self) -> None:
        tasks = [
            {"id": "root", "status": "done"},
            {"id": "redrift-design-redrift-app", "title": "redrift design: redrift analyze: App", "status": "open", "blocked_by": ["root"], "created_at": "2026-02-18T12:02:00+00:00"},
            {"id": "redrift-build-redrift-app", "title": "redrift build: redrift analyze: App", "status": "open", "blocked_by": ["root"], "created_at": "2026-02-18T12:03:00+00:00"},
        ]
        plan = _compact_plan(tasks=tasks, max_ready=0, max_redrift_depth=4)
        # One is kept, one is abandoned. Overflow may include the kept one, but never the abandoned id.
        self.assertEqual(len(plan["abandon_task_ids"]), 1)
        self.assertEqual(len(plan["defer_task_ids"]), 1)
        self.assertNotIn(plan["abandon_task_ids"][0], plan["defer_task_ids"])

    def test_compact_plan_abandons_depth_exceeded_redrift(self) -> None:
        tasks = [
            {"id": "root", "status": "done"},
            {"id": "redrift-analyze-redrift-analyze-redrift-app", "title": "redrift analyze: nested", "status": "open", "blocked_by": ["root"]},
            {"id": "redrift-build-redrift-app", "title": "redrift build", "status": "open", "blocked_by": ["root"]},
        ]
        plan = _compact_plan(tasks=tasks, max_ready=10, max_redrift_depth=2)
        self.assertIn("redrift-analyze-redrift-analyze-redrift-app", plan["depth_exceeded_redrift_task_ids"])
        self.assertIn("redrift-analyze-redrift-analyze-redrift-app", plan["abandon_task_ids"])


if __name__ == "__main__":
    unittest.main()
