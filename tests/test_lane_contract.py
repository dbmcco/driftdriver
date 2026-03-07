# ABOUTME: Tests for the standard lane plugin contract
# ABOUTME: All drift lanes (internal and external) must conform to this interface

import json
from driftdriver.lane_contract import LaneFinding, LaneResult, validate_lane_output


def test_lane_finding_defaults():
    """LaneFinding has sensible defaults."""
    f = LaneFinding(message="test issue")
    assert f.severity == "info"
    assert f.file == ""
    assert f.line == 0
    assert f.tags == []


def test_lane_result_has_required_fields():
    """LaneResult captures all required output."""
    result = LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean")
    assert result.lane == "coredrift"
    assert result.exit_code == 0
    assert result.findings == []


def test_validate_lane_output_accepts_valid():
    """Valid JSON with required fields produces a LaneResult."""
    raw = json.dumps({
        "lane": "qadrift",
        "findings": [{"message": "missing test", "severity": "warning"}],
        "exit_code": 1,
        "summary": "1 issue found",
    })
    result = validate_lane_output(raw)
    assert result is not None
    assert result.lane == "qadrift"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "warning"


def test_validate_lane_output_rejects_missing_lane():
    """JSON without 'lane' field returns None."""
    raw = json.dumps({"findings": [], "exit_code": 0})
    result = validate_lane_output(raw)
    assert result is None


def test_validate_lane_output_rejects_invalid_json():
    """Non-JSON input returns None."""
    result = validate_lane_output("not json at all")
    assert result is None


def test_validate_lane_output_handles_empty_findings():
    """Empty findings list is valid."""
    raw = json.dumps({"lane": "secdrift", "findings": [], "exit_code": 0, "summary": "clean"})
    result = validate_lane_output(raw)
    assert result is not None
    assert result.findings == []


def test_validate_lane_output_defaults_missing_fields():
    """Optional fields get defaults."""
    raw = json.dumps({"lane": "test", "findings": [{"message": "x"}]})
    result = validate_lane_output(raw)
    assert result is not None
    assert result.exit_code == 0
    assert result.summary == ""
    assert result.findings[0].severity == "info"
