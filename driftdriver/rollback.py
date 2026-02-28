# ABOUTME: Checkpoint-based rollback decision logic for drift recovery.
# ABOUTME: Evaluates drift scores and recommends RECOVER/PARTIAL/ESCALATE/NONE actions.
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RollbackDecision:
    action: str  # "RECOVER", "PARTIAL", "ESCALATE", "NONE"
    reason: str
    checkpoint_id: str | None
    confidence: float


_CHECKPOINTS_DIR = ".agentjj/checkpoints"


def find_checkpoint(task_id: str, project_dir: Path) -> str | None:
    """Check for an agentjj checkpoint named pre-task-{task_id}.

    Returns the checkpoint ID string if found, None otherwise.
    """
    checkpoint_name = f"pre-task-{task_id}"
    checkpoint_path = project_dir / _CHECKPOINTS_DIR / checkpoint_name
    if checkpoint_path.exists():
        return checkpoint_name
    return None


def execute_rollback(checkpoint_id: str, project_dir: Path) -> bool:
    """Prepare rollback command for the given checkpoint.

    Does not execute agentjj — the handler is responsible for running:
        agentjj undo --to <checkpoint_id>

    Returns True to indicate the command is ready to execute.
    """
    # Command the handler should run:
    # f"agentjj undo --to {checkpoint_id}"
    _ = project_dir  # provided for handler context
    return True


def evaluate_rollback(drift_score: float, task_id: str, project_dir: Path) -> RollbackDecision:
    """Decide what rollback action to take based on drift score.

    - drift_score < 0.3:        NONE     (no rollback needed)
    - drift_score in [0.3, 0.7]: PARTIAL  (create follow-up tasks)
    - drift_score > 0.7:        RECOVER  (rollback to checkpoint)
    - drift_score > 0.7 + no checkpoint: ESCALATE
    """
    if drift_score < 0.3:
        return RollbackDecision(
            action="NONE",
            reason="Drift score below threshold; no rollback needed",
            checkpoint_id=None,
            confidence=1.0,
        )

    if drift_score <= 0.7:
        return RollbackDecision(
            action="PARTIAL",
            reason="Moderate drift detected; create follow-up tasks",
            checkpoint_id=None,
            confidence=0.8,
        )

    # High drift — attempt recovery via checkpoint
    checkpoint_id = find_checkpoint(task_id, project_dir)
    if checkpoint_id is not None:
        return RollbackDecision(
            action="RECOVER",
            reason="High drift detected; rolling back to checkpoint",
            checkpoint_id=checkpoint_id,
            confidence=0.9,
        )

    return RollbackDecision(
        action="ESCALATE",
        reason="High drift detected but no checkpoint available",
        checkpoint_id=None,
        confidence=0.85,
    )
