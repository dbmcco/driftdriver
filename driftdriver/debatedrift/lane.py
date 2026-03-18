# ABOUTME: debatedrift lane interface — run_as_lane() and run_as_lane_check() for driftdriver integration.
# ABOUTME: Checks for debatedrift fence and session state; does not launch sessions directly.
from __future__ import annotations

from pathlib import Path

from driftdriver.debatedrift.config import parse_debatedrift_config
from driftdriver.lane_contract import LaneFinding, LaneResult


def _session_running(*, project_dir: Path, task_id: str) -> bool:
    """Return True if a debate session directory already exists for this task."""
    debate_dir = project_dir / ".workgraph" / ".debatedrift" / task_id
    return debate_dir.exists()


def run_as_lane_check(
    *,
    project_dir: Path,
    task_description: str = "",
    task_id: str = "",
) -> LaneResult:
    """Check lane — inspects task description and returns LaneResult.

    Does NOT launch a session. Advisory finding if fence present but no session running.
    """

    cfg = parse_debatedrift_config(task_description)
    if cfg is None:
        return LaneResult(
            lane="debatedrift",
            findings=[],
            exit_code=0,
            summary="no debatedrift fence — skipping",
        )

    if task_id and _session_running(project_dir=project_dir, task_id=task_id):
        return LaneResult(
            lane="debatedrift",
            findings=[],
            exit_code=0,
            summary=f"debate session active for task {task_id}",
        )

    return LaneResult(
        lane="debatedrift",
        findings=[
            LaneFinding(
                message=(
                    f"debatedrift fence detected (type={cfg.type}) — "
                    "run `driftdriver debate start --task <id>` to launch"
                ),
                severity="warning",
                tags=["debatedrift", cfg.type],
            )
        ],
        exit_code=3,
        summary=f"debatedrift fence present, session not started (type={cfg.type})",
    )


def run_as_lane(project_dir: Path) -> LaneResult:
    """Standard internal lane entrypoint — called by driftdriver check.

    Without task_id context, always returns clean (no advisory). Full
    activation requires the `driftdriver debate start` subcommand.
    This registration exists so `check.py` can detect the fence via
    `_task_has_fence` without needing a separate plugin binary.
    """
    return run_as_lane_check(
        project_dir=project_dir,
        task_description="",
        task_id="",
    )
