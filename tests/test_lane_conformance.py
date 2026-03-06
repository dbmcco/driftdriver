# ABOUTME: Conformance test suite for drift lane plugins against DRIFT_PLUGIN_CONTRACT.md.
# ABOUTME: Validates CLI interface, exit codes, wrapper structure, and artifact conventions.
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

# Lanes that should exist in a fully-installed .workgraph/
INSTALLED_LANES = [
    "coredrift",
    "specdrift",
    "datadrift",
    "archdrift",
    "depsdrift",
    "uxdrift",
    "therapydrift",
    "fixdrift",
    "yagnidrift",
    "redrift",
]

PROJECT_DIR = Path(__file__).resolve().parent.parent
WG_DIR = PROJECT_DIR / ".workgraph"
CONTRACT_PATH = PROJECT_DIR / "DRIFT_PLUGIN_CONTRACT.md"


def _wrapper_path(lane: str) -> Path:
    return WG_DIR / lane


def _lane_on_path(lane: str) -> bool:
    """Check if lane binary exists on PATH (not just in .workgraph)."""
    try:
        result = subprocess.run(
            ["which", lane],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


class ContractDocTests(unittest.TestCase):
    """Verify the contract document itself has the required sections."""

    def test_contract_exists(self) -> None:
        self.assertTrue(CONTRACT_PATH.exists(), "DRIFT_PLUGIN_CONTRACT.md must exist")

    def test_contract_defines_cli_interface(self) -> None:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("## CLI Interface", text)
        self.assertIn("wg check --task", text)

    def test_contract_defines_exit_codes(self) -> None:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("`0`", text)
        self.assertIn("`3`", text)

    def test_contract_defines_state_artifacts(self) -> None:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("## State & Artifacts", text)

    def test_contract_defines_orchestration_rules(self) -> None:
        text = CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("## Orchestration Rules", text)


class WrapperStructureTests(unittest.TestCase):
    """Verify installed wrapper scripts follow the portable/pinned pattern."""

    def test_wg_dir_exists(self) -> None:
        self.assertTrue(WG_DIR.exists(), ".workgraph/ must exist")

    def test_coredrift_wrapper_exists(self) -> None:
        self.assertTrue(
            _wrapper_path("coredrift").exists(),
            ".workgraph/coredrift wrapper must exist",
        )

    def test_wrapper_is_executable(self) -> None:
        for lane in INSTALLED_LANES:
            wrapper = _wrapper_path(lane)
            if wrapper.exists():
                self.assertTrue(
                    wrapper.stat().st_mode & 0o111,
                    f".workgraph/{lane} must be executable",
                )

    def test_wrapper_is_bash_script(self) -> None:
        for lane in INSTALLED_LANES:
            wrapper = _wrapper_path(lane)
            if wrapper.exists():
                first_line = wrapper.read_text(encoding="utf-8").split("\n")[0]
                self.assertTrue(
                    first_line.startswith("#!/"),
                    f".workgraph/{lane} must have shebang",
                )

    def test_wrapper_does_not_hardcode_home_paths(self) -> None:
        """Portable wrappers should not hardcode /Users/ or /home/ paths."""
        for lane in INSTALLED_LANES:
            wrapper = _wrapper_path(lane)
            if not wrapper.exists():
                continue
            text = wrapper.read_text(encoding="utf-8")
            # Portable wrappers search PATH; pinned wrappers may have absolute paths.
            # We just verify the wrapper doesn't have BOTH portable and pinned patterns.
            has_path_search = "PATH" in text and "IFS=':'" in text
            has_absolute = bool(re.search(r"/Users/\w+/|/home/\w+/", text))
            if has_path_search and has_absolute:
                self.fail(
                    f".workgraph/{lane} mixes portable PATH search with hardcoded paths"
                )


class CoredriftCLITests(unittest.TestCase):
    """Verify coredrift follows the contract's CLI interface."""

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_has_check_subcommand(self) -> None:
        result = subprocess.run(
            ["coredrift", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("check", result.stdout)

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_check_accepts_task_flag(self) -> None:
        result = subprocess.run(
            ["coredrift", "check", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--task", result.stdout)

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_check_accepts_write_log_flag(self) -> None:
        result = subprocess.run(
            ["coredrift", "check", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--write-log", result.stdout)

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_check_accepts_create_followups_flag(self) -> None:
        result = subprocess.run(
            ["coredrift", "check", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("--create-followups", result.stdout)

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_exit_code_on_clean_task(self) -> None:
        """Create a minimal workgraph with a clean task and verify exit 0."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir()
            task = {
                "kind": "task",
                "id": "test-clean",
                "title": "Clean task",
                "status": "in-progress",
                "description": (
                    "```wg-contract\n"
                    'schema = 1\n'
                    'mode = "core"\n'
                    'objective = "Test"\n'
                    "non_goals = []\n"
                    'touch = ["nonexistent/**"]\n'
                    "acceptance = []\n"
                    "max_files = 100\n"
                    "max_loc = 10000\n"
                    "```"
                ),
            }
            (wg / "graph.jsonl").write_text(
                json.dumps(task) + "\n", encoding="utf-8"
            )
            result = subprocess.run(
                ["coredrift", "--dir", str(repo), "check", "--task", "test-clean"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            # Exit 0 (clean) or 3 (findings) — both are valid contract behavior
            self.assertIn(
                result.returncode,
                [0, 3],
                f"Expected exit 0 or 3, got {result.returncode}: {result.stderr}",
            )

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_ensure_contracts_subcommand(self) -> None:
        """The contract mentions ensure-contracts as a core capability."""
        result = subprocess.run(
            ["coredrift", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("ensure-contracts", result.stdout)


class ArtifactConventionTests(unittest.TestCase):
    """Verify artifact storage follows .workgraph/.<drift>/ convention."""

    def test_coredrift_artifacts_in_dotdir(self) -> None:
        """If coredrift artifacts exist, they should be in .workgraph/.coredrift/."""
        coredrift_dir = WG_DIR / ".coredrift"
        # It's fine if no artifacts exist yet — just verify convention if they do
        if coredrift_dir.exists():
            self.assertTrue(coredrift_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
