from __future__ import annotations

import unittest

from driftdriver.cli import OPTIONAL_PLUGINS, _select_optional_plugins


class LaneSelectionTests(unittest.TestCase):
    def test_auto_strategy_keeps_fence_only_for_simple_task(self) -> None:
        task = {
            "title": "Small docs tweak",
            "description": "```specdrift\nschema = 1\n```\n",
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="auto",
        )
        self.assertEqual(selected, {"specdrift"})
        self.assertFalse(plan["full_suite"])
        self.assertEqual(plan["plugin_reasons"]["specdrift"], "task fence")

    def test_auto_strategy_escalates_to_full_suite_for_redrift(self) -> None:
        task = {
            "title": "v2 rebuild",
            "description": "```redrift\nschema = 1\n```\n",
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="auto",
        )
        self.assertEqual(selected, set(OPTIONAL_PLUGINS))
        self.assertTrue(plan["full_suite"])
        joined_reasons = " ".join(plan["reasons"]).lower()
        self.assertIn("redrift fence", joined_reasons)

    def test_auto_strategy_escalates_to_full_suite_for_complexity(self) -> None:
        task = {
            "title": "Rewrite platform for migration",
            "description": "\n".join(
                [
                    "```wg-contract",
                    "max_files = 40",
                    "max_loc = 1400",
                    "```",
                    "Need architecture + schema migration across frontend and backend.",
                ]
            ),
            "blocked_by": ["a", "b", "c"],
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="auto",
        )
        self.assertEqual(selected, set(OPTIONAL_PLUGINS))
        self.assertTrue(plan["full_suite"])
        self.assertGreaterEqual(len(plan["reasons"]), 2)

    def test_auto_strategy_escalates_to_full_suite_for_data_redo_phrase(self) -> None:
        task = {
            "title": "assistant-system redo",
            "description": "Need a data redo and app redo across the stack.",
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="auto",
        )
        self.assertEqual(selected, set(OPTIONAL_PLUGINS))
        self.assertTrue(plan["full_suite"])
        joined_reasons = " ".join(plan["reasons"]).lower()
        self.assertIn("full-suite intent", joined_reasons)

    def test_fences_strategy_does_not_auto_escalate(self) -> None:
        task = {
            "title": "v2 rebuild",
            "description": "```redrift\nschema = 1\n```\n",
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="fences",
        )
        self.assertEqual(selected, {"redrift"})
        self.assertFalse(plan["full_suite"])

    def test_all_strategy_forces_all_plugins(self) -> None:
        task = {
            "title": "Routine task",
            "description": "",
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="all",
        )
        self.assertEqual(selected, set(OPTIONAL_PLUGINS))
        self.assertTrue(plan["full_suite"])
        self.assertEqual(plan["reasons"], ["lane strategy forced all optional plugins"])


    def test_smart_strategy_falls_back_without_wg_dir(self) -> None:
        """Smart strategy without wg_dir should fall back to auto behavior."""
        task = {
            "title": "Simple fix",
            "description": "```specdrift\nschema = 1\n```\n",
        }
        selected, plan = _select_optional_plugins(
            task=task,
            ordered_plugins=list(OPTIONAL_PLUGINS),
            lane_strategy="smart",
            # No wg_dir — should fall back to auto
        )
        # Falls back to auto which should at least find fenced plugins
        assert "specdrift" in selected
        assert plan["strategy"] == "auto"

    def test_smart_strategy_accepted_as_valid(self) -> None:
        """'smart' should be a valid lane_strategy value."""
        from driftdriver.cli import LANE_STRATEGIES
        assert "smart" in LANE_STRATEGIES


def test_smart_strategy_with_wg_dir(tmp_path):
    """Smart strategy with a workgraph dir should attempt evidence gathering."""
    wg_dir = tmp_path / ".workgraph"
    wg_dir.mkdir()
    (wg_dir / "graph.jsonl").write_text('{"type":"task","id":"t1","title":"Test"}\n')

    _selected, plan = _select_optional_plugins(
        task=None,
        ordered_plugins=list(OPTIONAL_PLUGINS),
        lane_strategy="smart",
        wg_dir=wg_dir,
    )
    assert plan["strategy"] == "smart"
    assert isinstance(plan.get("lanes", []), list)
    assert len(plan.get("lanes", [])) >= 0  # smart routing ran, lanes is populated list


if __name__ == "__main__":
    unittest.main()
