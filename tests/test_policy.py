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
            self.assertGreaterEqual(p.max_auto_depth, 1)
            self.assertTrue(p.contracts_auto_ensure)
            self.assertTrue(p.updates_enabled)
            self.assertEqual(p.updates_check_interval_seconds, 21600)
            self.assertFalse(p.updates_create_followup)
            self.assertEqual(p.loop_max_redrift_depth, 2)
            self.assertEqual(p.loop_max_ready_drift_followups, 20)
            self.assertTrue(p.loop_block_followup_creation)

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
                    ]
                ),
                encoding="utf-8",
            )

            p = load_drift_policy(wg_dir)
            self.assertEqual(p.mode, "redirect")
            self.assertEqual(p.order[0], "coredrift")
            self.assertIn("yagnidrift", p.order)
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

    def test_ordered_optional_plugins(self) -> None:
        ordered = _ordered_optional_plugins(["yagnidrift", "specdrift", "unknown", "specdrift", "redrift"])
        self.assertEqual(ordered[0], "yagnidrift")
        self.assertEqual(ordered[1], "specdrift")
        self.assertEqual(len(ordered), 8)
        self.assertIn("archdrift", ordered)
        self.assertIn("uxdrift", ordered)
        self.assertIn("redrift", ordered)


if __name__ == "__main__":
    unittest.main()
