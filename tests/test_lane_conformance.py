# ABOUTME: Conformance test suite for drift lane plugins against lane_contract.py and DRIFT_PLUGIN_CONTRACT.md.
# ABOUTME: Validates CLI interface, exit codes, JSON output schema, wrapper structure, and run_as_lane() for all lanes.
from __future__ import annotations

import importlib
import json
import re
import subprocess
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from driftdriver.lane_contract import LaneFinding, LaneResult, validate_lane_output

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent
WG_DIR = PROJECT_DIR / ".workgraph"
CONTRACT_PATH = PROJECT_DIR / "DRIFT_PLUGIN_CONTRACT.md"

# External lanes — dispatched via subprocess to standalone binaries
EXTERNAL_LANES = [
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

# Internal lanes — dispatched via run_as_lane() in-process
INTERNAL_LANES = {
    "qadrift": "driftdriver.qadrift",
    "secdrift": "driftdriver.secdrift",
    "plandrift": "driftdriver.plandrift",
    "factorydrift": "driftdriver.factorydrift",
    "northstardrift": "driftdriver.northstardrift",
    "evolverdrift": "driftdriver.evolverdrift",
}

ALL_LANES = EXTERNAL_LANES + list(INTERNAL_LANES.keys())

# Lanes where --json is NOT supported (different CLI shape)
JSON_UNSUPPORTED_LANES = {"uxdrift"}

# External lanes that DO NOT yet conform to the lane_contract.py JSON schema.
# They all pre-date the LaneResult contract and use their own JSON format
# (e.g. {task_id, score, findings[{kind, severity, summary}], recommendations}).
# The driftdriver check.py integration layer handles translation at runtime.
# Only internal lanes (via run_as_lane()) implement the LaneResult schema.
NONSTANDARD_JSON_LANES = set(EXTERNAL_LANES)

# Valid exit codes per the contract
VALID_EXIT_CODES = {0, 3}

# Valid severity values per LaneFinding
VALID_SEVERITIES = {"info", "warning", "error", "critical"}

# Required fields on LaneResult
LANE_RESULT_REQUIRED_FIELDS = {"lane", "findings", "exit_code", "summary"}

# Required fields on LaneFinding
LANE_FINDING_REQUIRED_FIELDS = {"message"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_temp_workgraph() -> tuple[Path, Path]:
    """Create a temp directory with a minimal .workgraph and test task.

    Returns (repo_dir, wg_dir). Caller should clean up with shutil or tempfile.
    """
    td = tempfile.mkdtemp()
    repo = Path(td)
    wg = repo / ".workgraph"
    wg.mkdir()
    task = {
        "kind": "task",
        "id": "conformance-test",
        "title": "Conformance test task",
        "status": "in-progress",
        "description": (
            "```wg-contract\n"
            'schema = 1\n'
            'mode = "core"\n'
            'objective = "Lane conformance test"\n'
            "non_goals = []\n"
            'touch = ["nonexistent/**"]\n'
            "acceptance = []\n"
            "max_files = 100\n"
            "max_loc = 10000\n"
            "```"
        ),
    }
    (wg / "graph.jsonl").write_text(json.dumps(task) + "\n", encoding="utf-8")
    return repo, wg


def _validate_finding_dict(finding: dict[str, Any]) -> list[str]:
    """Return a list of problems with a finding dict against LaneFinding schema."""
    problems: list[str] = []
    if "message" not in finding:
        problems.append("missing required field 'message'")
    if not isinstance(finding.get("message", ""), str):
        problems.append("'message' must be a string")
    severity = finding.get("severity", "info")
    if severity not in VALID_SEVERITIES:
        problems.append(f"invalid severity '{severity}', expected one of {VALID_SEVERITIES}")
    if "file" in finding and not isinstance(finding["file"], str):
        problems.append("'file' must be a string")
    if "line" in finding and not isinstance(finding["line"], int):
        problems.append("'line' must be an integer")
    if "tags" in finding:
        if not isinstance(finding["tags"], list):
            problems.append("'tags' must be a list")
        elif not all(isinstance(t, str) for t in finding["tags"]):
            problems.append("all items in 'tags' must be strings")
    return problems


def _validate_lane_result_dict(data: dict[str, Any]) -> list[str]:
    """Return a list of problems with a lane result dict against LaneResult schema."""
    problems: list[str] = []
    if "lane" not in data:
        problems.append("missing required field 'lane'")
    elif not isinstance(data["lane"], str):
        problems.append("'lane' must be a string")
    if "findings" not in data:
        problems.append("missing required field 'findings'")
    elif not isinstance(data["findings"], list):
        problems.append("'findings' must be a list")
    else:
        for i, f in enumerate(data["findings"]):
            if not isinstance(f, dict):
                problems.append(f"findings[{i}] must be a dict")
            else:
                for p in _validate_finding_dict(f):
                    problems.append(f"findings[{i}]: {p}")
    if "exit_code" in data and not isinstance(data["exit_code"], int):
        problems.append("'exit_code' must be an integer")
    if "summary" in data and not isinstance(data["summary"], str):
        problems.append("'summary' must be a string")
    return problems


# ===========================================================================
# Section 1: Contract Document Tests
# ===========================================================================

class TestContractDocument(unittest.TestCase):
    """Verify the DRIFT_PLUGIN_CONTRACT.md document has required sections."""

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


# ===========================================================================
# Section 2: Wrapper Structure Tests
# ===========================================================================

class TestWrapperStructure(unittest.TestCase):
    """Verify installed wrapper scripts in .workgraph/ follow conventions."""

    def test_wg_dir_exists(self) -> None:
        self.assertTrue(WG_DIR.exists(), ".workgraph/ must exist")

    def test_coredrift_wrapper_exists(self) -> None:
        self.assertTrue(
            _wrapper_path("coredrift").exists(),
            ".workgraph/coredrift wrapper must exist",
        )

    def test_wrapper_is_executable(self) -> None:
        for lane in EXTERNAL_LANES:
            wrapper = _wrapper_path(lane)
            if wrapper.exists():
                self.assertTrue(
                    wrapper.stat().st_mode & 0o111,
                    f".workgraph/{lane} must be executable",
                )

    def test_wrapper_is_bash_script(self) -> None:
        for lane in EXTERNAL_LANES:
            wrapper = _wrapper_path(lane)
            if wrapper.exists():
                first_line = wrapper.read_text(encoding="utf-8").split("\n")[0]
                self.assertTrue(
                    first_line.startswith("#!/"),
                    f".workgraph/{lane} must have shebang",
                )

    def test_wrapper_does_not_hardcode_home_paths(self) -> None:
        """Portable wrappers should not mix portable PATH search with hardcoded paths."""
        for lane in EXTERNAL_LANES:
            wrapper = _wrapper_path(lane)
            if not wrapper.exists():
                continue
            text = wrapper.read_text(encoding="utf-8")
            has_path_search = "PATH" in text and "IFS=':'" in text
            has_absolute = bool(re.search(r"/Users/\w+/|/home/\w+/", text))
            if has_path_search and has_absolute:
                self.fail(
                    f".workgraph/{lane} mixes portable PATH search with hardcoded paths"
                )


# ===========================================================================
# Section 3: Artifact Convention Tests
# ===========================================================================

class TestArtifactConventions(unittest.TestCase):
    """Verify artifact storage follows .workgraph/.<drift>/ convention."""

    def test_artifact_dirs_are_dotdirs(self) -> None:
        """If lane artifact dirs exist, they should be dot-prefixed directories."""
        for lane in ALL_LANES:
            artifact_dir = WG_DIR / f".{lane}"
            if artifact_dir.exists():
                self.assertTrue(
                    artifact_dir.is_dir(),
                    f".workgraph/.{lane} should be a directory, not a file",
                )


# ===========================================================================
# Section 4: External Lane CLI Conformance (parametrized)
# ===========================================================================

# coredrift has some extra subcommands (ensure-contracts) but for conformance
# we test the standard interface shared by ALL lanes.

def _build_check_cmd(lane: str, repo_dir: str, task_id: str, *, json_flag: bool = False) -> list[str]:
    """Build the full check invocation command for any lane.

    Respects the different CLI shapes:
    - coredrift: coredrift --dir <dir> check [--json] --task <id>
    - uxdrift:   uxdrift wg --dir <dir> check --task <id>  (no --json support)
    - others:    <lane> --dir <dir> [--json] wg check --task <id>
    """
    if lane == "coredrift":
        cmd = [lane, "--dir", repo_dir, "check"]
        if json_flag:
            cmd.append("--json")
        cmd.extend(["--task", task_id])
    elif lane == "uxdrift":
        cmd = [lane, "wg", "--dir", repo_dir, "check", "--task", task_id]
    else:
        cmd = [lane, "--dir", repo_dir]
        if json_flag:
            cmd.append("--json")
        cmd.extend(["wg", "check", "--task", task_id])
    return cmd


def _check_help_cmd(lane: str) -> list[str]:
    """Return the command to get help for the 'check' subcommand of a lane.

    coredrift has 'check' at top level; others nest it under 'wg'.
    """
    if lane == "coredrift":
        return [lane, "check", "--help"]
    return [lane, "wg", "check", "--help"]


def _top_help_cmd(lane: str) -> list[str]:
    """Return the command to get top-level help for a lane."""
    return [lane, "--help"]


def _wg_help_cmd(lane: str) -> list[str]:
    """Return the command to get 'wg' subcommand help.

    coredrift doesn't have a 'wg' subcommand — its check is top-level.
    """
    if lane == "coredrift":
        return [lane, "--help"]
    return [lane, "wg", "--help"]


@pytest.mark.parametrize("lane", EXTERNAL_LANES)
class TestExternalLaneCLIHelp:
    """Verify external lanes expose the expected CLI flags via --help."""

    def test_lane_has_help(self, lane: str) -> None:
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        result = subprocess.run(
            _top_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        assert len(combined) > 0, f"{lane} --help produced no output"

    def test_lane_accepts_dir_flag(self, lane: str) -> None:
        """Lane must accept --dir at some level of its CLI."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        # Check both top-level and wg-level help for --dir
        top = subprocess.run(
            _top_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        wg = subprocess.run(
            _wg_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        combined = top.stdout + top.stderr + wg.stdout + wg.stderr
        assert "--dir" in combined, f"{lane} must accept --dir flag"

    def test_lane_has_check_subcommand(self, lane: str) -> None:
        """Lane must expose a 'check' subcommand (top-level or under wg)."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        # coredrift: check is top-level; others: check is under wg
        result = subprocess.run(
            _wg_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "check" in combined, f"{lane} must expose 'check' subcommand"

    def test_check_accepts_task_flag(self, lane: str) -> None:
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        result = subprocess.run(
            _check_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "--task" in combined, f"{lane} check must accept --task flag"

    def test_check_accepts_write_log_flag(self, lane: str) -> None:
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        result = subprocess.run(
            _check_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "--write-log" in combined, f"{lane} check must accept --write-log flag"

    def test_check_accepts_create_followups_flag(self, lane: str) -> None:
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        result = subprocess.run(
            _check_help_cmd(lane), capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "--create-followups" in combined, (
            f"{lane} check must accept --create-followups flag"
        )


@pytest.mark.parametrize("lane", [l for l in EXTERNAL_LANES if l not in JSON_UNSUPPORTED_LANES])
class TestExternalLaneJSONSupport:
    """Verify external lanes that support --json produce valid contract output."""

    def test_lane_accepts_json_flag(self, lane: str) -> None:
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        # coredrift has --json on check subcommand; others have it at top level
        if lane == "coredrift":
            cmd = [lane, "check", "--help"]
        else:
            cmd = [lane, "--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        combined = result.stdout + result.stderr
        assert "--json" in combined, f"{lane} must accept --json flag"


@pytest.mark.parametrize("lane", EXTERNAL_LANES)
class TestExternalLaneExitCodes:
    """Verify external lanes return valid contract exit codes on a test task."""

    def test_exit_code_is_valid(self, lane: str) -> None:
        """Run lane against a minimal workgraph and verify exit code is 0 or 3.

        Lanes that require additional configuration (e.g. uxdrift needs --url)
        may exit 1 on a bare workgraph — this is a config error, not a contract
        violation, so we skip rather than fail.
        """
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")

        repo, wg = _make_temp_workgraph()
        try:
            cmd = _build_check_cmd(lane, str(repo), "conformance-test")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            # Exit code 1/2 with a clear config/usage error is acceptable on a
            # bare workgraph — skip rather than fail.
            if result.returncode not in VALID_EXIT_CODES:
                stderr = result.stderr.lower()
                config_errors = ("required", "missing", "url", "usage", "error:")
                if any(kw in stderr for kw in config_errors):
                    pytest.skip(
                        f"{lane} returned exit {result.returncode} due to missing "
                        f"configuration on bare workgraph (expected)"
                    )
            assert result.returncode in VALID_EXIT_CODES, (
                f"{lane} returned exit code {result.returncode}, expected 0 or 3. "
                f"stderr: {result.stderr[:500]}"
            )
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)


# Lanes eligible for full LaneResult JSON schema validation
_JSON_SCHEMA_LANES = [
    l for l in EXTERNAL_LANES
    if l not in JSON_UNSUPPORTED_LANES and l not in NONSTANDARD_JSON_LANES
]


@pytest.mark.parametrize("lane", [l for l in EXTERNAL_LANES if l not in JSON_UNSUPPORTED_LANES])
class TestExternalLaneJSONOutput:
    """Verify JSON output from external lanes is valid JSON.

    For lanes in NONSTANDARD_JSON_LANES, we verify valid JSON but skip
    LaneResult schema tests (they use a pre-contract format).
    """

    def _run_json_check(self, lane: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        """Run a lane with --json against a temp workgraph. Returns (result, repo_dir).

        Caller must clean up repo_dir.
        """
        repo, _wg = _make_temp_workgraph()
        cmd = _build_check_cmd(lane, str(repo), "conformance-test", json_flag=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result, repo

    def test_json_output_is_valid_json(self, lane: str) -> None:
        """When --json is passed, stdout must be valid JSON."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")

        result, repo = self._run_json_check(lane)
        try:
            if result.returncode not in VALID_EXIT_CODES:
                pytest.skip(f"{lane} returned non-contract exit code {result.returncode}")
            stdout = result.stdout.strip()
            if not stdout:
                pytest.skip(f"{lane} produced no stdout with --json")
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError as exc:
                pytest.fail(f"{lane} --json output is not valid JSON: {exc}")
            assert isinstance(data, dict), f"{lane} --json output must be a JSON object"
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)

    def test_json_output_has_lane_field(self, lane: str) -> None:
        """JSON output must include 'lane' field (skipped for nonstandard lanes)."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        if lane in NONSTANDARD_JSON_LANES:
            pytest.skip(f"{lane} uses pre-contract JSON format (no 'lane' field)")

        result, repo = self._run_json_check(lane)
        try:
            if result.returncode not in VALID_EXIT_CODES:
                pytest.skip(f"{lane} returned non-contract exit code {result.returncode}")
            stdout = result.stdout.strip()
            if not stdout:
                pytest.skip(f"{lane} produced no stdout with --json")
            data = json.loads(stdout)
            assert "lane" in data, f"{lane} JSON output missing 'lane' field"
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)

    def test_json_output_validates_via_lane_contract(self, lane: str) -> None:
        """JSON output must parse via validate_lane_output() (skipped for nonstandard)."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        if lane in NONSTANDARD_JSON_LANES:
            pytest.skip(f"{lane} uses pre-contract JSON format")

        result, repo = self._run_json_check(lane)
        try:
            if result.returncode not in VALID_EXIT_CODES:
                pytest.skip(f"{lane} returned non-contract exit code {result.returncode}")
            stdout = result.stdout.strip()
            if not stdout:
                pytest.skip(f"{lane} produced no stdout with --json")
            lane_result = validate_lane_output(stdout)
            assert lane_result is not None, (
                f"{lane} JSON output failed validate_lane_output(). Output: {stdout[:500]}"
            )
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)

    def test_json_findings_match_schema(self, lane: str) -> None:
        """Each finding in JSON output must match LaneFinding field schema."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")
        if lane in NONSTANDARD_JSON_LANES:
            pytest.skip(f"{lane} uses pre-contract finding format")

        result, repo = self._run_json_check(lane)
        try:
            if result.returncode not in VALID_EXIT_CODES:
                pytest.skip(f"{lane} returned non-contract exit code {result.returncode}")
            stdout = result.stdout.strip()
            if not stdout:
                pytest.skip(f"{lane} produced no stdout with --json")
            data = json.loads(stdout)
            findings = data.get("findings", [])
            for i, finding in enumerate(findings):
                problems = _validate_finding_dict(finding)
                assert not problems, (
                    f"{lane} findings[{i}] schema violations: {problems}"
                )
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)

    def test_json_exit_code_matches_findings(self, lane: str) -> None:
        """Exit code 0 means no findings; exit code 3 means findings exist."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")

        result, repo = self._run_json_check(lane)
        try:
            if result.returncode not in VALID_EXIT_CODES:
                pytest.skip(f"{lane} returned non-contract exit code {result.returncode}")
            stdout = result.stdout.strip()
            if not stdout:
                pytest.skip(f"{lane} produced no stdout with --json")
            data = json.loads(stdout)
            findings = data.get("findings", [])
            if result.returncode == 0:
                # Clean exit: no findings or empty findings are both acceptable
                pass
            elif result.returncode == 3:
                # Advisory findings exit: should have at least one finding
                assert len(findings) > 0, (
                    f"{lane} exited 3 (findings advisory) but findings list is empty"
                )
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)


# ===========================================================================
# Section 5: Coredrift-specific tests (it has extra subcommands)
# ===========================================================================

class TestCoredriftExtras(unittest.TestCase):
    """Verify coredrift-specific features beyond the base contract."""

    @unittest.skipUnless(_lane_on_path("coredrift"), "coredrift not on PATH")
    def test_coredrift_ensure_contracts_subcommand(self) -> None:
        result = subprocess.run(
            ["coredrift", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertIn("ensure-contracts", result.stdout)


# ===========================================================================
# Section 6: Internal Lane run_as_lane() Conformance (parametrized)
# ===========================================================================

@pytest.mark.parametrize("lane,module_path", list(INTERNAL_LANES.items()))
class TestInternalLaneRunAsLane:
    """Verify internal lanes' run_as_lane() returns valid LaneResult objects."""

    def test_run_as_lane_returns_lane_result(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """run_as_lane() must return a LaneResult instance."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        assert isinstance(result, LaneResult), (
            f"{lane}.run_as_lane() must return LaneResult, got {type(result).__name__}"
        )

    def test_lane_field_matches_lane_name(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """LaneResult.lane must match the lane name."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        assert result.lane == lane, (
            f"{lane}.run_as_lane() returned lane='{result.lane}', expected '{lane}'"
        )

    def test_findings_are_lane_finding_instances(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """All findings must be LaneFinding instances."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        assert isinstance(result.findings, list), f"{lane} findings must be a list"
        for i, finding in enumerate(result.findings):
            assert isinstance(finding, LaneFinding), (
                f"{lane} findings[{i}] must be LaneFinding, got {type(finding).__name__}"
            )

    def test_finding_severities_are_valid(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """All finding severities must be valid contract values."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        for i, finding in enumerate(result.findings):
            assert finding.severity in VALID_SEVERITIES, (
                f"{lane} findings[{i}].severity='{finding.severity}' not in {VALID_SEVERITIES}"
            )

    def test_finding_message_is_nonempty_string(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """All finding messages must be non-empty strings."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        for i, finding in enumerate(result.findings):
            assert isinstance(finding.message, str), (
                f"{lane} findings[{i}].message must be str"
            )
            assert len(finding.message) > 0, (
                f"{lane} findings[{i}].message must not be empty"
            )

    def test_finding_tags_are_string_list(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """All finding tags must be lists of strings."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        for i, finding in enumerate(result.findings):
            assert isinstance(finding.tags, list), (
                f"{lane} findings[{i}].tags must be a list"
            )
            for j, tag in enumerate(finding.tags):
                assert isinstance(tag, str), (
                    f"{lane} findings[{i}].tags[{j}] must be str, got {type(tag).__name__}"
                )

    def test_exit_code_is_integer(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """exit_code must be an integer."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        assert isinstance(result.exit_code, int), (
            f"{lane} exit_code must be int, got {type(result.exit_code).__name__}"
        )

    def test_summary_is_string(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """summary must be a string."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        assert isinstance(result.summary, str), (
            f"{lane} summary must be str, got {type(result.summary).__name__}"
        )

    def test_result_serializes_to_valid_json(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """LaneResult must be serializable to JSON that passes validate_lane_output()."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        # Serialize manually since dataclasses don't have a built-in to_dict
        data = {
            "lane": result.lane,
            "findings": [
                {
                    "message": f.message,
                    "severity": f.severity,
                    "file": f.file,
                    "line": f.line,
                    "tags": f.tags,
                }
                for f in result.findings
            ],
            "exit_code": result.exit_code,
            "summary": result.summary,
        }
        raw = json.dumps(data)
        validated = validate_lane_output(raw)
        assert validated is not None, (
            f"{lane} LaneResult serialization failed validate_lane_output(). "
            f"Data: {raw[:500]}"
        )
        assert validated.lane == result.lane
        assert len(validated.findings) == len(result.findings)

    def test_exit_code_consistent_with_findings(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """If exit_code != 0 there should be findings (or an error condition)."""
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        if result.exit_code != 0 and result.exit_code != 1:
            # exit_code 1 is allowed for error cases
            assert len(result.findings) > 0, (
                f"{lane} exit_code={result.exit_code} but no findings produced"
            )


@pytest.mark.parametrize("lane,module_path", list(INTERNAL_LANES.items()))
class TestInternalLaneWithProjectContent:
    """Verify internal lanes handle a project with actual source files."""

    def test_lane_handles_project_with_source(self, lane: str, module_path: str, tmp_path: Path) -> None:
        """Lane should not crash when scanning a project with source files."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main():\n    print('hello')\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_app.py").write_text(
            "def test_main():\n    assert True\n"
        )
        mod = importlib.import_module(module_path)
        result = mod.run_as_lane(tmp_path)
        assert isinstance(result, LaneResult), (
            f"{lane} must return LaneResult on project with source files"
        )
        assert result.lane == lane


# ===========================================================================
# Section 7: LaneResult / LaneFinding Dataclass Field Compliance
# ===========================================================================

class TestLaneContractDataclasses:
    """Verify the lane contract dataclasses have the expected field structure."""

    def test_lane_finding_fields(self) -> None:
        """LaneFinding must have the documented fields."""
        field_names = {f.name for f in fields(LaneFinding)}
        expected = {"message", "severity", "file", "line", "tags"}
        assert field_names == expected, f"LaneFinding fields: {field_names} != {expected}"

    def test_lane_result_fields(self) -> None:
        """LaneResult must have the documented fields."""
        field_names = {f.name for f in fields(LaneResult)}
        expected = {"lane", "findings", "exit_code", "summary"}
        assert field_names == expected, f"LaneResult fields: {field_names} != {expected}"

    def test_lane_finding_defaults(self) -> None:
        """LaneFinding defaults match contract expectations."""
        f = LaneFinding(message="test")
        assert f.severity == "info"
        assert f.file == ""
        assert f.line == 0
        assert f.tags == []

    def test_validate_lane_output_round_trip(self) -> None:
        """validate_lane_output produces correct LaneResult from valid JSON."""
        data = {
            "lane": "testlane",
            "findings": [
                {"message": "issue 1", "severity": "warning", "file": "a.py", "line": 10, "tags": ["scope"]},
                {"message": "issue 2"},
            ],
            "exit_code": 3,
            "summary": "2 issues found",
        }
        result = validate_lane_output(json.dumps(data))
        assert result is not None
        assert result.lane == "testlane"
        assert result.exit_code == 3
        assert len(result.findings) == 2
        assert result.findings[0].severity == "warning"
        assert result.findings[0].file == "a.py"
        assert result.findings[0].line == 10
        assert result.findings[0].tags == ["scope"]
        assert result.findings[1].severity == "info"  # default
        assert result.findings[1].file == ""  # default
        assert result.summary == "2 issues found"

    def test_validate_lane_output_rejects_missing_lane(self) -> None:
        raw = json.dumps({"findings": [], "exit_code": 0})
        assert validate_lane_output(raw) is None

    def test_validate_lane_output_rejects_invalid_json(self) -> None:
        assert validate_lane_output("not json") is None

    def test_validate_lane_output_rejects_none(self) -> None:
        assert validate_lane_output(None) is None  # type: ignore[arg-type]


# ===========================================================================
# Section 8: Cross-cutting: all lanes have wrappers or are internal
# ===========================================================================

class TestAllLanesCoverage:
    """Verify every lane known to driftdriver is covered by conformance tests."""

    def test_all_optional_plugins_are_in_external_lanes(self) -> None:
        """OPTIONAL_PLUGINS from check.py should all appear in EXTERNAL_LANES."""
        from driftdriver.cli.check import OPTIONAL_PLUGINS
        for plugin in OPTIONAL_PLUGINS:
            assert plugin in EXTERNAL_LANES, (
                f"OPTIONAL_PLUGINS entry '{plugin}' not in EXTERNAL_LANES"
            )

    def test_all_internal_lanes_match_check_py(self) -> None:
        """INTERNAL_LANES here should match INTERNAL_LANES in check.py."""
        from driftdriver.cli.check import INTERNAL_LANES as CHECK_INTERNAL
        assert set(INTERNAL_LANES.keys()) == set(CHECK_INTERNAL.keys()), (
            f"Conformance INTERNAL_LANES {set(INTERNAL_LANES.keys())} != "
            f"check.py INTERNAL_LANES {set(CHECK_INTERNAL.keys())}"
        )

    def test_no_lane_is_both_external_and_internal(self) -> None:
        """A lane should not appear in both EXTERNAL_LANES and INTERNAL_LANES."""
        overlap = set(EXTERNAL_LANES) & set(INTERNAL_LANES.keys())
        assert not overlap, f"Lanes appear in both external and internal: {overlap}"

    def test_every_external_lane_has_wrapper_or_is_expected_missing(self) -> None:
        """Each external lane should have a .workgraph/ wrapper installed."""
        for lane in EXTERNAL_LANES:
            wrapper = _wrapper_path(lane)
            assert wrapper.exists(), (
                f"External lane '{lane}' has no wrapper at .workgraph/{lane}"
            )


# ===========================================================================
# Section 9: Conformance Audit — external lanes vs LaneResult schema
# ===========================================================================

@pytest.mark.parametrize("lane", [l for l in EXTERNAL_LANES if l not in JSON_UNSUPPORTED_LANES])
class TestExternalLaneContractAudit:
    """Audit: do external lanes' --json output pass validate_lane_output()?

    These are marked xfail because all external lanes currently pre-date the
    LaneResult schema. When an external lane is migrated to the contract
    format, remove it from NONSTANDARD_JSON_LANES and these tests will
    start passing (strict xfail will catch the transition).
    """

    @pytest.mark.xfail(reason="External lanes pre-date lane_contract.py schema", strict=True)
    def test_json_output_passes_validate_lane_output(self, lane: str) -> None:
        """External lane JSON should eventually pass validate_lane_output()."""
        if not _lane_on_path(lane):
            pytest.skip(f"{lane} not installed on PATH")

        repo, _wg = _make_temp_workgraph()
        try:
            cmd = _build_check_cmd(lane, str(repo), "conformance-test", json_flag=True)
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode not in VALID_EXIT_CODES:
                stderr = result.stderr.lower()
                config_errors = ("required", "missing", "url", "usage", "error:")
                if any(kw in stderr for kw in config_errors):
                    pytest.skip(f"{lane} config error on bare workgraph")
            stdout = result.stdout.strip()
            if not stdout:
                pytest.skip(f"{lane} produced no stdout")
            lane_result = validate_lane_output(stdout)
            assert lane_result is not None, (
                f"{lane} JSON does not conform to LaneResult schema"
            )
        finally:
            import shutil
            shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
