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

    def test_ordered_optional_plugins(self) -> None:
        ordered = _ordered_optional_plugins(["yagnidrift", "specdrift", "unknown", "specdrift", "redrift"])
        self.assertEqual(ordered[0], "yagnidrift")
        self.assertEqual(ordered[1], "specdrift")
        self.assertEqual(len(ordered), 7)
        self.assertIn("uxdrift", ordered)
        self.assertIn("redrift", ordered)


if __name__ == "__main__":
    unittest.main()
