# ABOUTME: Test that the quality planner parser handles nested backticks in description fields.
# ABOUTME: This is the real failure mode: Haiku wraps JSON in ```json...``` and descriptions contain ```wg-contract...```.

import json
from unittest import TestCase
from driftdriver.quality_planner import _parse_plan_output


class NestedBacktickParserTests(TestCase):
    def test_json_with_backticks_in_description_string(self):
        """The LLM wraps JSON in ```json...``` and descriptions contain ```wg-contract```."""
        inner = {
            "tasks": [
                {
                    "id": "schema-update",
                    "title": "Update schema",
                    "type": "code",
                    "risk": "medium",
                    "description": '```wg-contract\nschema = 1\nmode = "core"\nobjective = "Update schema"\n```\nAdd columns.',
                    "verify": "npm run typecheck",
                },
                {
                    "id": "impl-healer",
                    "title": "Implement healer",
                    "type": "code",
                    "risk": "medium",
                    "description": '```wg-contract\nschema = 1\nmode = "core"\nobjective = "Implement feed healer"\nnon_goals = ["No alerting"]\n```\nBuild healFeeds function.',
                    "after": ["schema-update"],
                },
            ]
        }
        # Simulate what Haiku returns: JSON inside a code block
        raw = "```json\n" + json.dumps(inner, indent=2) + "\n```"

        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(result.tasks[0].id, "schema-update")
        self.assertEqual(result.tasks[0].verify, "npm run typecheck")
        self.assertIn("wg-contract", result.tasks[0].description)
        self.assertEqual(result.tasks[1].id, "impl-healer")
        self.assertEqual(result.tasks[1].after, ["schema-update"])

    def test_json_with_multiple_backtick_blocks_in_descriptions(self):
        """Multiple tasks each with wg-contract blocks."""
        inner = {
            "tasks": [
                {
                    "id": f"task-{i}",
                    "title": f"Task {i}",
                    "type": "code",
                    "risk": "low",
                    "description": f'```wg-contract\nschema = 1\nobjective = "Task {i}"\n```\nDo task {i}.',
                }
                for i in range(5)
            ]
        }
        raw = "```json\n" + json.dumps(inner) + "\n```"
        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 5)

    def test_clean_json_still_works(self):
        """Plain JSON without code block wrapper still parses."""
        raw = json.dumps({"tasks": [{"id": "x", "title": "Y", "type": "code", "risk": "low"}]})
        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 1)

    def test_json_with_quadruple_backtick_wrapper(self):
        """Some LLMs use ```` to wrap when content has ```."""
        inner = {"tasks": [{"id": "a", "title": "A", "type": "code", "risk": "low", "description": "```wg-contract\nschema=1\n```\nstuff"}]}
        raw = "````json\n" + json.dumps(inner) + "\n````"
        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 1)
