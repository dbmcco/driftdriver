# ABOUTME: Tests for driftdriver.wire - the CLI pipeline integration bridge
# ABOUTME: Verifies each wire command wraps its module and returns the correct dict shape

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_DIR = Path(__file__).parent.parent


def test_cmd_verify_returns_result():
    from driftdriver.wire import cmd_verify

    result = cmd_verify(PROJECT_DIR)
    assert "passed" in result
    assert "checks" in result
    assert "warnings" in result
    assert "blockers" in result
    assert isinstance(result["passed"], bool)
    assert isinstance(result["checks"], dict)
    assert isinstance(result["warnings"], list)
    assert isinstance(result["blockers"], list)


def test_cmd_loop_check_records_and_detects():
    from driftdriver.wire import cmd_loop_check

    with TemporaryDirectory() as tmp:
        state_dir = Path(tmp)
        # Run same action 3 times to trigger loop detection
        for _ in range(3):
            result = cmd_loop_check(state_dir, "Bash", "ls -la")
        assert "detected" in result
        assert "pattern" in result
        assert "count" in result
        assert result["detected"] is True
        assert result["count"] == 3


def test_cmd_enrich_with_matching_knowledge():
    from driftdriver.wire import cmd_enrich

    knowledge = [
        {"category": "testing", "content": "always run pytest before committing", "confidence": 0.9},
        {"category": "style", "content": "use black for formatting code", "confidence": 0.8},
    ]
    result = cmd_enrich(
        task_id="task-1",
        task_description="run pytest to verify test coverage before committing code",
        project="myproject",
        knowledge=knowledge,
    )
    assert "learnings_added" in result
    assert "contract_updated" in result
    assert result["learnings_added"] > 0
    assert result["contract_updated"] is True


def test_cmd_bridge_processes_events_file():
    from driftdriver.wire import cmd_bridge

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"event": "pre_tool_use", "ts": "2026-01-15T10:30:00Z", "tool": "Read", "tool_input": {}}\n')
        f.write('{"event": "stop", "ts": "2026-01-15T10:30:01Z"}\n')
        events_file = Path(f.name)

    result = cmd_bridge(events_file, session_id="sess-1", project="testproject")
    assert isinstance(result, list)
    assert len(result) == 2
    for item in result:
        assert "session_id" in item
        assert "event_type" in item
        assert "project" in item
        assert "payload" in item


def test_cmd_distill_processes_events():
    from driftdriver.wire import cmd_distill

    events = [
        {"event_type": "observation", "content": "test passed successfully"},
        {"event_type": "observation", "content": "test coverage good"},
        {"event_type": "observation", "content": "tests verified clean"},
    ]
    knowledge = [
        {"category": "observation", "content": "prior knowledge entry", "confidence": 0.8},
    ]
    result = cmd_distill(events, knowledge)
    assert "events_processed" in result
    assert "knowledge_created" in result
    assert "entries_pruned" in result
    assert result["events_processed"] == 3


def test_cmd_rollback_eval_returns_decision():
    from driftdriver.wire import cmd_rollback_eval

    with TemporaryDirectory() as tmp:
        project_dir = Path(tmp)
        # High drift score with no checkpoint → ESCALATE
        result = cmd_rollback_eval(drift_score=0.85, task_id="task-42", project_dir=project_dir)
    assert "action" in result
    assert "reason" in result
    assert "confidence" in result
    assert result["action"] in ("RECOVER", "ESCALATE", "PARTIAL", "NONE")
    assert isinstance(result["confidence"], float)
