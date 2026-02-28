# ABOUTME: Execution state persistence for agent context recovery
# ABOUTME: Saves task phase, retry count, and plan to .workgraph/recovery/

from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import time


@dataclass
class ExecutionState:
    """Persisted state for an in-progress task."""
    task_id: str
    phase: str = "pending"  # pending, implement, validate, review, commit, done
    retry_count: int = 0
    max_retries: int = 3
    started_at: float = 0.0
    last_updated: float = 0.0
    plan_summary: str = ""
    completed_steps: list[str] = field(default_factory=list)
    error_log: list[str] = field(default_factory=list)


def recovery_dir(wg_dir: Path) -> Path:
    """Get the recovery directory path."""
    return wg_dir / "recovery"


def state_file(wg_dir: Path, task_id: str) -> Path:
    """Get the state file path for a task."""
    safe_id = task_id.replace("/", "_").replace("..", "_").replace("\\", "_")
    return recovery_dir(wg_dir) / f"{safe_id}.json"


def save_state(wg_dir: Path, state: ExecutionState) -> None:
    """Save execution state to disk."""
    state.last_updated = time.time()
    rd = recovery_dir(wg_dir)
    rd.mkdir(parents=True, exist_ok=True)
    sf = state_file(wg_dir, state.task_id)
    sf.write_text(json.dumps(asdict(state), indent=2))


def load_state(wg_dir: Path, task_id: str) -> ExecutionState | None:
    """Load execution state from disk, or None if not found."""
    sf = state_file(wg_dir, task_id)
    if not sf.exists():
        return None
    try:
        data = json.loads(sf.read_text())
        return ExecutionState(**{
            k: v for k, v in data.items()
            if k in ExecutionState.__dataclass_fields__
        })
    except (json.JSONDecodeError, TypeError):
        return None


def clear_state(wg_dir: Path, task_id: str) -> bool:
    """Remove execution state for a completed task."""
    sf = state_file(wg_dir, task_id)
    if sf.exists():
        sf.unlink()
        return True
    return False


def advance_phase(wg_dir: Path, task_id: str, new_phase: str) -> ExecutionState:
    """Advance a task to the next phase."""
    state = load_state(wg_dir, task_id)
    if state is None:
        state = ExecutionState(task_id=task_id, started_at=time.time())
    old_phase = state.phase
    state.phase = new_phase
    state.completed_steps.append(old_phase)
    save_state(wg_dir, state)
    return state


def record_error(wg_dir: Path, task_id: str, error: str) -> ExecutionState:
    """Record an error and increment retry count."""
    state = load_state(wg_dir, task_id)
    if state is None:
        state = ExecutionState(task_id=task_id, started_at=time.time())
    state.error_log.append(error)
    state.retry_count += 1
    save_state(wg_dir, state)
    return state


def should_escalate(state: ExecutionState) -> bool:
    """Check if a task has exceeded max retries and needs human escalation."""
    return state.retry_count >= state.max_retries


def list_interrupted(wg_dir: Path) -> list[ExecutionState]:
    """Find all tasks with saved state (potentially interrupted)."""
    rd = recovery_dir(wg_dir)
    if not rd.exists():
        return []
    states = []
    for sf in rd.glob("*.json"):
        task_id = sf.stem
        state = load_state(wg_dir, task_id)
        if state and state.phase != "done":
            states.append(state)
    return states
