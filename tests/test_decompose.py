# ABOUTME: Tests for decompose — goal decomposition into workgraph tasks via LLM.
# ABOUTME: Verifies directive emission, dependency preservation, and empty-response handling.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.decompose import decompose_goal
from driftdriver.directives import DirectiveLog


class TestDecomposeGoal(unittest.TestCase):
    @patch("driftdriver.decompose._call_llm")
    @patch("driftdriver.executor_shim.subprocess.run")
    def test_decompose_emits_create_task_directives(self, mock_run: MagicMock, mock_llm: MagicMock) -> None:
        mock_llm.return_value = [
            {"id": "task-1", "title": "Set up project", "after": []},
            {"id": "task-2", "title": "Write tests", "after": ["task-1"]},
            {"id": "task-3", "title": "Implement feature", "after": ["task-2"]},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()
            log = DirectiveLog(wg_dir / "service" / "directives")

            result = decompose_goal(
                goal="Build authentication system",
                wg_dir=wg_dir,
                directive_log=log,
                repo="paia-shell",
            )

            self.assertEqual(result["task_count"], 3)
            completed = log.read_completed()
            self.assertEqual(len(completed), 3)

    @patch("driftdriver.decompose._call_llm")
    @patch("driftdriver.executor_shim.subprocess.run")
    def test_decompose_preserves_dependencies(self, mock_run: MagicMock, mock_llm: MagicMock) -> None:
        mock_llm.return_value = [
            {"id": "setup", "title": "Set up", "after": []},
            {"id": "build", "title": "Build", "after": ["setup"]},
        ]
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()
            log = DirectiveLog(wg_dir / "service" / "directives")

            decompose_goal(
                goal="Test goal",
                wg_dir=wg_dir,
                directive_log=log,
                repo="test",
            )

            # Read the pending.jsonl to inspect directive params
            import json
            lines = (wg_dir / "service" / "directives" / "pending.jsonl").read_text().splitlines()
            directives = [json.loads(line) for line in lines if line.strip()]
            build_directive = [d for d in directives if d["params"]["task_id"] == "build"][0]
            self.assertEqual(build_directive["params"]["after"], ["setup"])

    @patch("driftdriver.decompose._call_llm")
    def test_decompose_empty_llm_response(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = []
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()
            log = DirectiveLog(wg_dir / "service" / "directives")

            result = decompose_goal(
                goal="Nothing",
                wg_dir=wg_dir,
                directive_log=log,
                repo="test",
            )
            self.assertEqual(result["task_count"], 0)
