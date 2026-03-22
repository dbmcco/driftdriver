# ABOUTME: Tests for the Drift-to-Evaluation Bridge module.
# ABOUTME: Verifies severity mapping, lane dimensions, attribution, evaluation building, and bridging.

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from driftdriver.lane_contract import LaneFinding, LaneResult
from driftdriver.wg_eval_bridge import (
    ALL_DIMENSIONS,
    LANE_DIMENSION_MAP,
    SEVERITY_SCORES,
    BridgeReport,
    attribute_finding,
    bridge_findings_to_evaluations,
    build_evaluation,
    severity_to_score,
    write_evaluation,
)


# ---------------------------------------------------------------------------
# SeverityMappingTests
# ---------------------------------------------------------------------------
class TestSeverityMapping:
    def test_critical_maps_to_zero(self):
        assert severity_to_score("critical") == 0.0

    def test_error_maps_to_point_two(self):
        assert severity_to_score("error") == 0.2

    def test_warning_maps_to_point_five(self):
        assert severity_to_score("warning") == 0.5

    def test_info_maps_to_point_eight(self):
        assert severity_to_score("info") == 0.8

    def test_unknown_defaults_to_point_five(self):
        assert severity_to_score("banana") == 0.5

    def test_severity_scores_dict_has_four_entries(self):
        assert len(SEVERITY_SCORES) == 4
        assert set(SEVERITY_SCORES.keys()) == {"critical", "error", "warning", "info"}


# ---------------------------------------------------------------------------
# LaneDimensionMapTests
# ---------------------------------------------------------------------------
class TestLaneDimensionMap:
    EXPECTED_LANES = [
        "coredrift",
        "qadrift",
        "plandrift",
        "secdrift",
        "northstardrift",
        "factorydrift",
    ]

    def test_all_six_lanes_present(self):
        for lane in self.EXPECTED_LANES:
            assert lane in LANE_DIMENSION_MAP, f"Missing lane: {lane}"

    def test_each_lane_has_primary(self):
        for lane, dims in LANE_DIMENSION_MAP.items():
            assert "primary" in dims, f"Lane {lane} missing 'primary'"
            assert isinstance(dims["primary"], str)

    def test_each_lane_has_secondary(self):
        for lane, dims in LANE_DIMENSION_MAP.items():
            assert "secondary" in dims, f"Lane {lane} missing 'secondary'"
            assert isinstance(dims["secondary"], list)

    def test_all_mapped_dimensions_are_valid(self):
        for lane, dims in LANE_DIMENSION_MAP.items():
            assert dims["primary"] in ALL_DIMENSIONS, (
                f"Lane {lane} primary '{dims['primary']}' not in ALL_DIMENSIONS"
            )
            for sec in dims["secondary"]:
                assert sec in ALL_DIMENSIONS, (
                    f"Lane {lane} secondary '{sec}' not in ALL_DIMENSIONS"
                )


# ---------------------------------------------------------------------------
# BridgeReportTests
# ---------------------------------------------------------------------------
class TestBridgeReport:
    def test_fields_populated(self):
        report = BridgeReport(
            evaluations_written=3,
            unattributable_findings=1,
            attribution_failures=["task:missing"],
            evaluation_ids=["eval-1", "eval-2", "eval-3"],
        )
        assert report.evaluations_written == 3
        assert report.unattributable_findings == 1
        assert report.attribution_failures == ["task:missing"]
        assert report.evaluation_ids == ["eval-1", "eval-2", "eval-3"]

    def test_defaults(self):
        report = BridgeReport()
        assert report.evaluations_written == 0
        assert report.unattributable_findings == 0
        assert report.attribution_failures == []
        assert report.evaluation_ids == []


# ---------------------------------------------------------------------------
# AttributionTests
# ---------------------------------------------------------------------------
class AttributionTests:
    def _make_assignment(self, tmp_path, task_id, *, agent_id="agent-1",
                         composition_id="role-abc"):
        assignments_dir = tmp_path / ".workgraph" / "agency" / "assignments"
        assignments_dir.mkdir(parents=True, exist_ok=True)
        content = (
            f"task_id: {task_id}\n"
            f"agent_id: {agent_id}\n"
            f"composition_id: {composition_id}\n"
            f"timestamp: 2026-03-15T00:00:00Z\n"
        )
        (assignments_dir / f"{task_id}.yaml").write_text(content)

    def test_tag_based_attribution(self, tmp_path):
        self._make_assignment(tmp_path, "fix-auth")
        finding = LaneFinding(
            message="scope drift detected",
            severity="warning",
            tags=["task:fix-auth"],
        )
        result = attribute_finding(tmp_path, finding)
        assert result is not None
        assert result["task_id"] == "fix-auth"
        assert result["agent_id"] == "agent-1"
        assert result["role_id"] == "role-abc"

    def test_no_tags_returns_none(self, tmp_path):
        finding = LaneFinding(message="no tags here", severity="info", tags=[])
        result = attribute_finding(tmp_path, finding)
        assert result is None

    def test_no_task_tag_returns_none(self, tmp_path):
        finding = LaneFinding(
            message="has tags but no task",
            severity="info",
            tags=["scope:narrow", "lane:coredrift"],
        )
        result = attribute_finding(tmp_path, finding)
        assert result is None

    def test_missing_assignment_file_returns_none(self, tmp_path):
        # task tag present but no assignment file
        finding = LaneFinding(
            message="ghost task",
            severity="warning",
            tags=["task:nonexistent"],
        )
        result = attribute_finding(tmp_path, finding)
        assert result is None

    def test_no_role_id_uses_unknown(self, tmp_path):
        """Assignment without composition_id should default role_id to 'unknown'."""
        assignments_dir = tmp_path / ".workgraph" / "agency" / "assignments"
        assignments_dir.mkdir(parents=True, exist_ok=True)
        content = (
            "task_id: fix-auth\n"
            "agent_id: agent-1\n"
            "timestamp: 2026-03-15T00:00:00Z\n"
        )
        (assignments_dir / "fix-auth.yaml").write_text(content)

        finding = LaneFinding(
            message="scope drift", severity="warning", tags=["task:fix-auth"]
        )
        result = attribute_finding(tmp_path, finding)
        assert result is not None
        assert result["role_id"] == "unknown"



# ---------------------------------------------------------------------------
# BuildEvaluationTests
# ---------------------------------------------------------------------------
class TestBuildEvaluation:
    def _make_finding(self, severity="warning", message="test finding"):
        return LaneFinding(message=message, severity=severity, tags=["task:t1"])

    def _make_attribution(self):
        return {"task_id": "t1", "role_id": "role-abc", "agent_id": "agent-1"}

    def test_evaluator_field(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="coredrift"
        )
        assert ev["evaluator"] == "speedrift:coredrift"

    def test_source_field(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="qadrift"
        )
        assert ev["source"] == "drift"

    def test_dimension_mapping(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="coredrift"
        )
        dims = ev["dimensions"]
        primary = LANE_DIMENSION_MAP["coredrift"]["primary"]
        assert primary in dims

    def test_score_is_average_of_dimensions(self):
        ev = build_evaluation(
            self._make_finding(severity="critical"),
            self._make_attribution(),
            lane="coredrift",
        )
        dims = ev["dimensions"]
        expected_avg = round(sum(dims.values()) / len(dims), 4)
        assert abs(ev["score"] - expected_avg) < 1e-9

    def test_notes_contain_lane_and_message(self):
        ev = build_evaluation(
            self._make_finding(message="scope creep detected"),
            self._make_attribution(),
            lane="plandrift",
        )
        assert "plandrift" in ev["notes"]
        assert "scope creep detected" in ev["notes"]

    def test_id_format(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="secdrift"
        )
        assert ev["id"].startswith("eval-drift-secdrift-t1-")

    def test_task_and_role_ids(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="coredrift"
        )
        assert ev["task_id"] == "t1"
        assert ev["role_id"] == "role-abc"

    def test_tradeoff_id_is_unknown(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="coredrift"
        )
        assert ev["tradeoff_id"] == "unknown"

    def test_has_timestamp(self):
        ev = build_evaluation(
            self._make_finding(), self._make_attribution(), lane="coredrift"
        )
        assert "timestamp" in ev
        assert isinstance(ev["timestamp"], str)


# ---------------------------------------------------------------------------
# WriteEvaluationTests
# ---------------------------------------------------------------------------
def _mock_wg_submit(ev: dict) -> MagicMock:
    """Return a mock subprocess.CompletedProcess for wg evaluate --submit."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = f"Saved evaluation {ev.get('id', 'unknown')} to ..."
    mock.stderr = ""
    return mock


class TestWriteEvaluation:
    def test_creates_json_file(self, tmp_path):
        ev = {
            "id": "eval-drift-coredrift-t1-12345",
            "task_id": "t1",
            "role_id": "role-abc",
            "tradeoff_id": "unknown",
            "score": 0.5,
            "dimensions": {"correctness": 0.5},
            "notes": "coredrift: test",
            "evaluator": "speedrift:coredrift",
            "timestamp": "2026-03-15T00:00:00+00:00",
            "source": "drift",
        }
        with patch("driftdriver.wg_eval_bridge.subprocess.run", return_value=_mock_wg_submit(ev)):
            path = write_evaluation(tmp_path, ev)
        assert path.suffix == ".json"
        assert path.parent.name == "evaluations"
        assert path.name == f"{ev['id']}.json"

    def test_content_matches(self, tmp_path):
        ev = {
            "id": "eval-drift-qadrift-t2-99999",
            "task_id": "t2",
            "role_id": "role-xyz",
            "tradeoff_id": "unknown",
            "score": 0.3,
            "dimensions": {"completeness": 0.3},
            "notes": "qadrift: missing tests",
            "evaluator": "speedrift:qadrift",
            "timestamp": "2026-03-15T01:00:00+00:00",
            "source": "drift",
        }
        with patch("driftdriver.wg_eval_bridge.subprocess.run", return_value=_mock_wg_submit(ev)) as mock_run:
            path = write_evaluation(tmp_path, ev)
        # Verify wg was called with correct args
        call_args = mock_run.call_args
        assert call_args[0][0][0] == "wg"
        assert call_args[0][0][3] == "--submit"
        submitted = json.loads(call_args[1]["input"])
        assert submitted == ev
        assert path.name == f"{ev['id']}.json"

    def test_file_name_contains_eval_id(self, tmp_path):
        ev = {"id": "eval-drift-secdrift-t3-55555", "task_id": "t3"}
        with patch("driftdriver.wg_eval_bridge.subprocess.run", return_value=_mock_wg_submit(ev)):
            path = write_evaluation(tmp_path, ev)
        assert "eval-drift-secdrift-t3-55555" in path.name


# ---------------------------------------------------------------------------
# BridgeFunctionTests
# ---------------------------------------------------------------------------
class TestBridgeFunction:
    def _make_assignment(self, tmp_path, task_id):
        assignments_dir = tmp_path / ".workgraph" / "agency" / "assignments"
        assignments_dir.mkdir(parents=True, exist_ok=True)
        content = (
            f"task_id: {task_id}\n"
            f"agent_id: agent-1\n"
            f"composition_id: role-abc\n"
            f"timestamp: 2026-03-15T00:00:00Z\n"
        )
        (assignments_dir / f"{task_id}.yaml").write_text(content)

    def test_attributed_findings_write_evals(self, tmp_path):
        self._make_assignment(tmp_path, "fix-auth")
        lane_results = [
            LaneResult(
                lane="coredrift",
                findings=[
                    LaneFinding(
                        message="scope issue",
                        severity="warning",
                        tags=["task:fix-auth"],
                    )
                ],
                exit_code=1,
                summary="1 issue",
            )
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("driftdriver.wg_eval_bridge.subprocess.run", return_value=mock_result):
            report = bridge_findings_to_evaluations(tmp_path, lane_results)
        assert report.evaluations_written == 1
        assert report.unattributable_findings == 0
        assert len(report.evaluation_ids) == 1

    def test_unattributable_findings_skipped(self, tmp_path):
        lane_results = [
            LaneResult(
                lane="coredrift",
                findings=[
                    LaneFinding(message="no task tag", severity="warning", tags=[])
                ],
                exit_code=1,
                summary="1 issue",
            )
        ]
        report = bridge_findings_to_evaluations(tmp_path, lane_results)
        assert report.evaluations_written == 0
        assert report.unattributable_findings == 1

    def test_min_severity_filter(self, tmp_path):
        self._make_assignment(tmp_path, "fix-auth")
        lane_results = [
            LaneResult(
                lane="coredrift",
                findings=[
                    LaneFinding(
                        message="info only",
                        severity="info",
                        tags=["task:fix-auth"],
                    ),
                    LaneFinding(
                        message="real problem",
                        severity="error",
                        tags=["task:fix-auth"],
                    ),
                ],
                exit_code=1,
                summary="2 issues",
            )
        ]
        # min_severity="warning" should exclude info findings
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("driftdriver.wg_eval_bridge.subprocess.run", return_value=mock_result):
            report = bridge_findings_to_evaluations(
                tmp_path, lane_results, min_severity="warning"
            )
        assert report.evaluations_written == 1

    def test_empty_results(self, tmp_path):
        report = bridge_findings_to_evaluations(tmp_path, [])
        assert report.evaluations_written == 0
        assert report.unattributable_findings == 0
        assert report.attribution_failures == []
        assert report.evaluation_ids == []

    def test_attribution_failures_tracked(self, tmp_path):
        """Findings with task tags but no assignment file should be tracked."""
        lane_results = [
            LaneResult(
                lane="coredrift",
                findings=[
                    LaneFinding(
                        message="ghost ref",
                        severity="warning",
                        tags=["task:nonexistent"],
                    )
                ],
                exit_code=1,
                summary="1 issue",
            )
        ]
        report = bridge_findings_to_evaluations(tmp_path, lane_results)
        assert report.evaluations_written == 0
        assert report.unattributable_findings == 0  # has a tag, just can't resolve
        assert len(report.attribution_failures) == 1
        assert "nonexistent" in report.attribution_failures[0]

    def test_multiple_lanes_multiple_findings(self, tmp_path):
        self._make_assignment(tmp_path, "t1")
        self._make_assignment(tmp_path, "t2")
        lane_results = [
            LaneResult(
                lane="coredrift",
                findings=[
                    LaneFinding(
                        message="issue1", severity="warning", tags=["task:t1"]
                    ),
                    LaneFinding(
                        message="issue2", severity="error", tags=["task:t2"]
                    ),
                ],
                exit_code=1,
                summary="2 issues",
            ),
            LaneResult(
                lane="qadrift",
                findings=[
                    LaneFinding(
                        message="missing test", severity="warning", tags=["task:t1"]
                    ),
                ],
                exit_code=1,
                summary="1 issue",
            ),
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("driftdriver.wg_eval_bridge.subprocess.run", return_value=mock_result):
            report = bridge_findings_to_evaluations(tmp_path, lane_results)
        assert report.evaluations_written == 3
        assert len(report.evaluation_ids) == 3


# ---------------------------------------------------------------------------
# ContractTests — live wg evaluate --submit CLI (skipped if wg absent)
# ---------------------------------------------------------------------------
WG_AVAILABLE = shutil.which("wg") is not None


@pytest.mark.skipif(not WG_AVAILABLE, reason="wg CLI not in PATH")
class TestWgEvaluateSubmitContract:
    """Integration tests that exercise the real wg evaluate --submit CLI."""

    def _init_wg(self, tmp_path: Path) -> None:
        """Run wg init in tmp_path so .workgraph/agency/ structure exists."""
        subprocess.run(["wg", "init"], cwd=str(tmp_path), check=True, capture_output=True)

    def _build_eval(self, eval_id: str) -> dict:
        return {
            "id": eval_id,
            "task_id": "contract-task",
            "role_id": "role-contract",
            "score": 0.75,
            "dimensions": {"correctness": 0.75},
            "notes": "contract test evaluation",
            "evaluator": "speedrift:coredrift",
            "timestamp": "2026-03-15T00:00:00+00:00",
        }

    def test_submit_exits_zero(self, tmp_path):
        self._init_wg(tmp_path)
        ev = self._build_eval("eval-contract-001")
        result = subprocess.run(
            ["wg", "evaluate", ev["id"], "--submit"],
            input=json.dumps(ev),
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
        )
        assert result.returncode == 0, f"wg evaluate --submit failed: {result.stderr}"

    def test_submit_writes_file(self, tmp_path):
        self._init_wg(tmp_path)
        ev = self._build_eval("eval-contract-002")
        subprocess.run(
            ["wg", "evaluate", ev["id"], "--submit"],
            input=json.dumps(ev),
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=True,
        )
        eval_path = (
            tmp_path / ".workgraph" / "agency" / "evaluations" / f"{ev['id']}.json"
        )
        assert eval_path.exists(), f"Evaluation file not written: {eval_path}"

    def test_submit_stdout_mentions_eval_id(self, tmp_path):
        self._init_wg(tmp_path)
        ev = self._build_eval("eval-contract-003")
        result = subprocess.run(
            ["wg", "evaluate", ev["id"], "--submit"],
            input=json.dumps(ev),
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=True,
        )
        assert ev["id"] in result.stdout, (
            f"Expected eval id in stdout, got: {result.stdout!r}"
        )

    def test_submit_json_flag_returns_id_and_path(self, tmp_path):
        self._init_wg(tmp_path)
        ev = self._build_eval("eval-contract-004")
        result = subprocess.run(
            ["wg", "--json", "evaluate", ev["id"], "--submit"],
            input=json.dumps(ev),
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            check=True,
        )
        data = json.loads(result.stdout)
        assert data["id"] == ev["id"]
        assert "path" in data
