# ABOUTME: Tests for execution state persistence and agent context recovery
# ABOUTME: Covers save/load/clear, phase transitions, error recording, and recovery formatting

from driftdriver.execution_state import (
    ExecutionState, save_state, load_state, clear_state,
    advance_phase, record_error, should_escalate,
    list_interrupted,
)


def test_save_and_load_state(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    state = ExecutionState(task_id="t1", phase="implement")
    save_state(wg, state)
    loaded = load_state(wg, "t1")
    assert loaded is not None
    assert loaded.task_id == "t1"
    assert loaded.phase == "implement"


def test_load_state_missing(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    assert load_state(wg, "nonexistent") is None


def test_clear_state(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    save_state(wg, ExecutionState(task_id="t1"))
    assert clear_state(wg, "t1") is True
    assert load_state(wg, "t1") is None


def test_advance_phase(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    save_state(wg, ExecutionState(task_id="t1", phase="pending"))
    state = advance_phase(wg, "t1", "implement")
    assert state.phase == "implement"
    assert "pending" in state.completed_steps


def test_record_error(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    save_state(wg, ExecutionState(task_id="t1"))
    state = record_error(wg, "t1", "tests failed")
    assert state.retry_count == 1
    assert "tests failed" in state.error_log


def test_should_escalate():
    state = ExecutionState(task_id="t1", retry_count=3, max_retries=3)
    assert should_escalate(state) is True
    state.retry_count = 2
    assert should_escalate(state) is False


def test_list_interrupted(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    save_state(wg, ExecutionState(task_id="t1", phase="implement"))
    save_state(wg, ExecutionState(task_id="t2", phase="done"))
    interrupted = list_interrupted(wg)
    assert len(interrupted) == 1
    assert interrupted[0].task_id == "t1"
