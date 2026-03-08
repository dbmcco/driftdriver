# ABOUTME: Tests for ecosystem hub service supervision routed through directives.
# ABOUTME: Verifies supervise_repo_services emits start_service directives via ExecutorShim.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.directives import DirectiveLog


class TestSupervisionEmitsDirectives(unittest.TestCase):
    @patch("driftdriver.executor_shim.subprocess.run")
    def test_supervise_emits_start_service_directive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            repo_path = Path(tmp) / "test-repo"
            repo_path.mkdir()
            wg_dir = repo_path / ".workgraph"
            wg_dir.mkdir()
            directive_dir = Path(tmp) / "directives"
            log = DirectiveLog(directive_dir)

            from driftdriver.ecosystem_hub.snapshot import supervise_repo_services

            repos_payload = [
                {
                    "name": "test-repo",
                    "path": str(repo_path),
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": False,
                    "in_progress": ["task-1"],
                    "ready": ["task-2", "task-3"],
                },
            ]

            result = supervise_repo_services(
                repos_payload=repos_payload,
                cooldown_seconds=0,
                max_starts=5,
                directive_log=log,
            )

            self.assertEqual(result["attempted"], 1)
            self.assertEqual(result["started"], 1)

            completed = log.read_completed()
            self.assertEqual(len(completed), 1)

    def test_supervise_without_directive_log_still_works(self) -> None:
        """Backward compatibility: no directive_log uses legacy path."""
        from driftdriver.ecosystem_hub.snapshot import supervise_repo_services

        result = supervise_repo_services(
            repos_payload=[],
            cooldown_seconds=60,
            max_starts=3,
        )
        self.assertEqual(result["attempted"], 0)
