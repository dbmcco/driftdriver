# ABOUTME: Tests for debatedrift lane interface (run_as_lane).
# ABOUTME: Verifies LaneResult output, fence detection, and no-fence pass-through.
from __future__ import annotations

import tempfile
from pathlib import Path

from driftdriver.debatedrift.lane import run_as_lane_check


class TestRunAsLaneCheck:
    """run_as_lane_check inspects the task description for a debatedrift fence
    and returns a LaneResult indicating whether a session should be launched.
    It does NOT launch the session itself (that's the CLI's job).
    """

    def test_returns_lane_result_with_no_fence(self) -> None:
        from driftdriver.lane_contract import LaneResult
        with tempfile.TemporaryDirectory() as td:
            result = run_as_lane_check(
                project_dir=Path(td),
                task_description="just a normal task",
            )
        assert isinstance(result, LaneResult)
        assert result.lane == "debatedrift"
        assert len(result.findings) == 0
        assert result.exit_code == 0

    def test_returns_finding_when_fence_present_no_session(self) -> None:
        from driftdriver.lane_contract import LaneResult
        desc = (
            "Do the thing.\n\n"
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"planning\"\n"
            "```\n"
        )
        with tempfile.TemporaryDirectory() as td:
            result = run_as_lane_check(
                project_dir=Path(td),
                task_description=desc,
            )
        assert isinstance(result, LaneResult)
        assert result.lane == "debatedrift"
        # A fence with no running session → advisory finding
        assert len(result.findings) == 1
        assert result.findings[0].severity == "warning"
        assert result.exit_code == 3

    def test_returns_info_when_session_already_running(self) -> None:
        from driftdriver.lane_contract import LaneResult
        desc = (
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"troubleshoot\"\n"
            "```\n"
        )
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            # Simulate a running session by creating the debate dir
            (td_path / ".workgraph" / ".debatedrift" / "task-123").mkdir(parents=True)
            result = run_as_lane_check(
                project_dir=td_path,
                task_description=desc,
                task_id="task-123",
            )
        assert isinstance(result, LaneResult)
        assert result.exit_code == 0  # session running — no advisory needed
