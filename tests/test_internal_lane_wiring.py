# ABOUTME: Tests for internal lane wiring in check.py via run_as_lane().
# ABOUTME: Verifies internal lanes produce same format as external, graceful degradation.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from driftdriver.cli.check import (
    INTERNAL_LANES,
    ExitCode,
    _count_contract_compliance,
    _run_internal_lane,
)
from driftdriver.lane_contract import LaneFinding, LaneResult


class TestInternalLanesConstant:
    """INTERNAL_LANES maps all 5 internal lanes to their module paths."""

    def test_contains_all_five_lanes(self) -> None:
        expected = {"qadrift", "secdrift", "plandrift", "factorydrift", "northstardrift"}
        assert set(INTERNAL_LANES.keys()) == expected

    def test_module_paths_are_importable_strings(self) -> None:
        for lane, path in INTERNAL_LANES.items():
            assert path.startswith("driftdriver."), f"{lane} module path should start with driftdriver."
            assert lane in path, f"{lane} should appear in its module path {path}"


class TestRunInternalLane:
    """_run_internal_lane() produces the same dict shape as external plugins."""

    def test_unknown_lane_returns_not_ran(self) -> None:
        result = _run_internal_lane(lane="nonexistent", project_dir=Path("/tmp"))
        assert result["ran"] is False
        assert result["exit_code"] == 0
        assert result["report"] is None

    def test_qadrift_runs_and_produces_report(self, tmp_path: Path) -> None:
        """qadrift runs on a real temp directory and produces valid report format."""
        # Create minimal project structure so qadrift has something to scan
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("def main(): pass\n")

        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path)
        assert result["ran"] is True
        report = result["report"]
        assert isinstance(report, dict)
        assert report["lane"] == "qadrift"
        assert isinstance(report["findings"], list)
        assert report["_contract_valid"] is True
        assert report["_lane_result"]["lane"] == "qadrift"
        assert isinstance(report["_lane_result"]["findings_count"], int)

    def test_secdrift_runs_and_produces_report(self, tmp_path: Path) -> None:
        """secdrift runs on a real temp directory and produces valid report format."""
        result = _run_internal_lane(lane="secdrift", project_dir=tmp_path)
        assert result["ran"] is True
        report = result["report"]
        assert isinstance(report, dict)
        assert report["lane"] == "secdrift"
        assert report["_contract_valid"] is True

    def test_plandrift_runs_and_produces_report(self, tmp_path: Path) -> None:
        """plandrift runs on a real temp directory and produces valid report format."""
        result = _run_internal_lane(lane="plandrift", project_dir=tmp_path)
        assert result["ran"] is True
        report = result["report"]
        assert isinstance(report, dict)
        assert report["lane"] == "plandrift"
        assert report["_contract_valid"] is True

    def test_report_matches_external_plugin_shape(self, tmp_path: Path) -> None:
        """Internal lane report has the same keys as external plugin contract validation."""
        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path)
        report = result["report"]
        assert isinstance(report, dict)
        # Same keys that _run_optional_plugin_json adds after contract validation
        assert "lane" in report
        assert "findings" in report
        assert "exit_code" in report
        assert "summary" in report
        assert "_contract_valid" in report
        assert "_lane_result" in report
        # _lane_result sub-dict shape
        lr = report["_lane_result"]
        assert "lane" in lr
        assert "findings_count" in lr
        assert "exit_code" in lr
        assert "summary" in lr

    def test_findings_exit_code_mapped_to_findings_code(self, tmp_path: Path) -> None:
        """When run_as_lane returns exit_code != 0 with findings, it maps to ExitCode.findings."""
        # qadrift with src files but no tests should produce findings
        src = tmp_path / "src"
        src.mkdir()
        (src / "module_a.py").write_text("def fn(): pass\n")

        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path)
        report = result["report"]
        if report["findings"]:
            assert result["exit_code"] == ExitCode.findings
            assert report["exit_code"] == ExitCode.findings

    def test_clean_lane_returns_exit_code_zero(self, tmp_path: Path) -> None:
        """A lane with no findings returns exit_code 0."""
        # Empty project — some lanes (like secdrift) produce no findings
        result = _run_internal_lane(lane="secdrift", project_dir=tmp_path)
        if not result["report"]["findings"]:
            assert result["exit_code"] == 0

    def test_graceful_degradation_on_import_error(self, monkeypatch: Any) -> None:
        """If the module import fails, returns a non-blocking error report."""
        import importlib

        original_import = importlib.import_module

        def broken_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "driftdriver.qadrift":
                raise ImportError("simulated import failure")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", broken_import)
        result = _run_internal_lane(lane="qadrift", project_dir=Path("/tmp"))
        assert result["ran"] is True
        assert result["exit_code"] == 0  # Non-blocking
        report = result["report"]
        assert isinstance(report, dict)
        assert "error" in report
        assert "import failure" in report["detail"]

    def test_graceful_degradation_on_runtime_error(self, tmp_path: Path, monkeypatch: Any) -> None:
        """If run_as_lane() raises, returns a non-blocking error report."""
        import driftdriver.qadrift as qadrift_mod

        def exploding_lane(project_dir: Path) -> LaneResult:
            raise RuntimeError("kaboom in lane")

        monkeypatch.setattr(qadrift_mod, "run_as_lane", exploding_lane)
        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path)
        assert result["ran"] is True
        assert result["exit_code"] == 0
        assert "error" in result["report"]
        assert "kaboom" in result["report"]["detail"]


class TestInternalLaneContractCompliance:
    """Internal lane reports integrate with _count_contract_compliance."""

    def test_internal_lane_counted_as_contract_valid(self, tmp_path: Path) -> None:
        """Internal lane with _contract_valid=True is counted in compliance."""
        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path)
        plugins_json: dict[str, Any] = {
            "qadrift": {
                "ran": True,
                "exit_code": result["exit_code"],
                "report": result["report"],
            },
        }
        compliance = _count_contract_compliance(plugins_json)
        assert compliance["total_checked"] == 1
        assert compliance["contract_valid"] == 1
        assert compliance["contract_invalid"] == 0

    def test_error_report_not_counted_as_contract_valid(self) -> None:
        """Error reports (no _contract_valid) are counted as invalid."""
        plugins_json: dict[str, Any] = {
            "qadrift": {
                "ran": True,
                "exit_code": 0,
                "report": {"error": "import failed", "detail": "boom"},
            },
        }
        compliance = _count_contract_compliance(plugins_json)
        assert compliance["total_checked"] == 1
        assert compliance["contract_valid"] == 0
        assert compliance["contract_invalid"] == 1


class TestInternalLaneGating:
    """Internal lanes only run when their wrapper exists in .workgraph/."""

    def test_lane_skipped_when_wrapper_missing(self, tmp_path: Path) -> None:
        """If .workgraph/qadrift doesn't exist, _run_internal_lane returns ran=False."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path, wg_dir=wg_dir)
        assert result["ran"] is False
        assert result["exit_code"] == 0
        assert result["report"] is None

    def test_lane_runs_when_wrapper_exists(self, tmp_path: Path) -> None:
        """If .workgraph/qadrift exists, the lane runs."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        lane_wrapper = wg_dir / "qadrift"
        lane_wrapper.write_text("#!/bin/sh\n")
        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path, wg_dir=wg_dir)
        assert result["ran"] is True

    def test_lane_runs_without_wg_dir_gating(self, tmp_path: Path) -> None:
        """When wg_dir is None, gating is skipped and lane always runs."""
        result = _run_internal_lane(lane="qadrift", project_dir=tmp_path)
        assert result["ran"] is True

    def test_external_plugin_still_uses_subprocess(self, tmp_path: Path) -> None:
        """Plugins not in INTERNAL_LANES fall through to subprocess invocation."""
        assert "specdrift" not in INTERNAL_LANES
        result = _run_internal_lane(lane="specdrift", project_dir=tmp_path)
        assert result["ran"] is False


class TestAllInternalLanesRun:
    """Smoke test: all 5 internal lanes execute without crashing."""

    def test_all_lanes_run_on_empty_project(self, tmp_path: Path) -> None:
        for lane in INTERNAL_LANES:
            result = _run_internal_lane(lane=lane, project_dir=tmp_path)
            assert result["ran"] is True, f"{lane} should have ran"
            report = result["report"]
            assert isinstance(report, dict), f"{lane} report should be dict"
            assert report.get("_contract_valid") is True, f"{lane} should be contract valid"

    def test_all_lanes_produce_serializable_json(self, tmp_path: Path) -> None:
        """All internal lane reports can be serialized to JSON (needed for combined output)."""
        for lane in INTERNAL_LANES:
            result = _run_internal_lane(lane=lane, project_dir=tmp_path)
            # Must not raise
            serialized = json.dumps(result, indent=2)
            parsed = json.loads(serialized)
            assert parsed["ran"] is True
