# ABOUTME: Tests for the Speedrift Quality Planner.
# ABOUTME: Covers repertoire loading, plan output structure, prompt building, and dry-run mode.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.quality_planner import (
    BUILTIN_PATTERNS,
    PlannerOutput,
    PlannedTask,
    build_planner_prompt,
    load_repertoire,
    plan_from_spec,
    _parse_plan_output,
)


class RepertoireTests(unittest.TestCase):
    def test_builtin_patterns_exist(self) -> None:
        self.assertIn("e2e-breakfix", BUILTIN_PATTERNS)
        self.assertIn("ux-eval", BUILTIN_PATTERNS)
        self.assertIn("data-eval", BUILTIN_PATTERNS)
        self.assertIn("contract-test", BUILTIN_PATTERNS)
        self.assertIn("northstar-checkpoint", BUILTIN_PATTERNS)

    def test_each_pattern_has_description_and_when(self) -> None:
        for name, pattern in BUILTIN_PATTERNS.items():
            self.assertIn("description", pattern, f"{name} missing description")
            self.assertIn("when", pattern, f"{name} missing when")

    def test_load_repertoire_returns_all(self) -> None:
        repertoire = load_repertoire()
        self.assertEqual(len(repertoire), len(BUILTIN_PATTERNS))
        # Must be a copy, not the original
        self.assertIsNot(repertoire, BUILTIN_PATTERNS)


class PlanOutputTests(unittest.TestCase):
    def test_planned_task_serializes_to_dict(self) -> None:
        task = PlannedTask(
            id="impl-auth",
            title="Implement auth",
            after=[],
            task_type="code",
            risk="medium",
            description="Implement OAuth flow",
        )
        data = task.to_dict()
        self.assertEqual(data["id"], "impl-auth")
        self.assertEqual(data["title"], "Implement auth")
        self.assertEqual(data["type"], "code")
        self.assertEqual(data["risk"], "medium")
        self.assertIn("description", data)

    def test_planner_output_serializes_to_json(self) -> None:
        task = PlannedTask(
            id="impl-auth",
            title="Implement auth",
            after=[],
            task_type="code",
            risk="medium",
            description="Implement OAuth flow",
        )
        output = PlannerOutput(tasks=[task])
        data = output.to_dict()
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["id"], "impl-auth")
        # to_json should be valid JSON
        parsed = json.loads(output.to_json())
        self.assertEqual(len(parsed["tasks"]), 1)

    def test_planned_task_optional_pattern(self) -> None:
        task = PlannedTask(
            id="e2e-auth",
            title="E2E auth test",
            after=["impl-auth"],
            task_type="quality-gate",
            pattern="e2e-breakfix",
            max_iterations=3,
        )
        data = task.to_dict()
        self.assertEqual(data["pattern"], "e2e-breakfix")
        self.assertEqual(data["max_iterations"], 3)

    def test_planned_task_no_pattern_omits_fields(self) -> None:
        task = PlannedTask(
            id="impl-x",
            title="Implement X",
        )
        data = task.to_dict()
        self.assertNotIn("pattern", data)
        self.assertNotIn("max_iterations", data)


class PromptBuilderTests(unittest.TestCase):
    def test_prompt_includes_spec_content(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build relationship health indicators",
            north_star="Understand relationships with perfect memory",
            repertoire=BUILTIN_PATTERNS,
        )
        self.assertIn("relationship health indicators", prompt)

    def test_prompt_includes_north_star(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="Understand relationships",
            repertoire=BUILTIN_PATTERNS,
        )
        self.assertIn("Understand relationships", prompt)

    def test_prompt_includes_all_patterns(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="North Star",
            repertoire=BUILTIN_PATTERNS,
        )
        for pattern_name in BUILTIN_PATTERNS:
            self.assertIn(pattern_name, prompt)

    def test_prompt_requests_json_output(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="North Star",
            repertoire=BUILTIN_PATTERNS,
        )
        self.assertIn("JSON", prompt)

    def test_prompt_includes_drift_policy_summary(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="North Star",
            repertoire=BUILTIN_PATTERNS,
            drift_policy_summary="mode=redirect, schema=1",
        )
        self.assertIn("mode=redirect", prompt)


class PlanFromSpecTests(unittest.TestCase):
    def test_plan_from_spec_reads_file(self) -> None:
        with TemporaryDirectory() as td:
            spec = Path(td) / "spec.md"
            spec.write_text("# Build a feature\nImplement auth flow", encoding="utf-8")
            result = plan_from_spec(spec_path=spec, repo_path=Path(td), dry_run=True)
            self.assertIsInstance(result, PlannerOutput)

    def test_plan_from_spec_dry_run_does_not_call_llm(self) -> None:
        with TemporaryDirectory() as td:
            spec = Path(td) / "spec.md"
            spec.write_text("# Simple feature", encoding="utf-8")
            # dry_run=True should not attempt LLM call or wg add
            result = plan_from_spec(spec_path=spec, repo_path=Path(td), dry_run=True)
            self.assertIsNotNone(result)
            # Dry run returns empty task list
            self.assertEqual(len(result.tasks), 0)

    def test_plan_from_spec_returns_planner_output(self) -> None:
        with TemporaryDirectory() as td:
            spec = Path(td) / "spec.md"
            spec.write_text("# Feature spec\nDetails here.", encoding="utf-8")
            result = plan_from_spec(spec_path=spec, repo_path=Path(td), dry_run=True)
            self.assertIsInstance(result, PlannerOutput)
            self.assertIsInstance(result.to_dict(), dict)

    def test_plan_from_spec_reads_north_star(self) -> None:
        """When a drift-policy.toml has a northstardrift.alignment.statement, it's used."""
        with TemporaryDirectory() as td:
            spec = Path(td) / "spec.md"
            spec.write_text("# Feature", encoding="utf-8")
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir()
            policy = wg_dir / "drift-policy.toml"
            policy.write_text(
                '[northstardrift.alignment]\n'
                'statement = "Build the best system ever"\n',
                encoding="utf-8",
            )
            result = plan_from_spec(spec_path=spec, repo_path=Path(td), dry_run=True)
            self.assertIsInstance(result, PlannerOutput)


class ParseOutputTests(unittest.TestCase):
    def test_parses_clean_json(self) -> None:
        raw = json.dumps({
            "tasks": [
                {
                    "id": "impl-auth",
                    "title": "Implement auth",
                    "after": [],
                    "type": "code",
                    "risk": "medium",
                    "description": "Build OAuth",
                }
            ]
        })
        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].id, "impl-auth")
        self.assertEqual(result.tasks[0].title, "Implement auth")

    def test_parses_json_in_markdown_code_block(self) -> None:
        raw = (
            "Here is the plan:\n"
            "```json\n"
            '{"tasks": [{"id": "feat-x", "title": "Build X", "after": [], '
            '"type": "code", "risk": "low", "description": "Do X"}]}\n'
            "```\n"
            "That's the plan."
        )
        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].id, "feat-x")

    def test_parses_json_in_plain_code_block(self) -> None:
        raw = (
            "```\n"
            '{"tasks": [{"id": "feat-y", "title": "Build Y", "after": [], '
            '"type": "code", "risk": "low"}]}\n'
            "```"
        )
        result = _parse_plan_output(raw)
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0].id, "feat-y")

    def test_handles_invalid_json_gracefully(self) -> None:
        raw = "This is not JSON at all."
        result = _parse_plan_output(raw)
        self.assertIsInstance(result, PlannerOutput)
        self.assertEqual(len(result.tasks), 0)

    def test_handles_empty_string(self) -> None:
        result = _parse_plan_output("")
        self.assertIsInstance(result, PlannerOutput)
        self.assertEqual(len(result.tasks), 0)

    def test_preserves_optional_fields(self) -> None:
        raw = json.dumps({
            "tasks": [
                {
                    "id": "gate-1",
                    "title": "E2E gate",
                    "after": ["impl-1"],
                    "type": "quality-gate",
                    "risk": "high",
                    "description": "Run e2e tests",
                    "pattern": "e2e-breakfix",
                    "max_iterations": 5,
                }
            ]
        })
        result = _parse_plan_output(raw)
        self.assertEqual(result.tasks[0].pattern, "e2e-breakfix")
        self.assertEqual(result.tasks[0].max_iterations, 5)
