# ABOUTME: Tests for driftdriver.wire - the CLI pipeline integration bridge
# ABOUTME: Verifies each wire command wraps its module and returns the correct dict shape

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory


def _init_test_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with a test file."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
    test_file = tmp_path / "hello.py"
    test_file.write_text("print('hello')\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)
    return tmp_path


def test_cmd_verify_controlled(tmp_path: Path) -> None:
    from driftdriver.wire import cmd_verify

    project = _init_test_repo(tmp_path)
    result = cmd_verify(project)
    assert "checks" in result


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
    assert result["action"] == "ESCALATE"  # high drift + no checkpoint → ESCALATE
    assert isinstance(result["confidence"], float)
    assert result["confidence"] > 0.0


def test_wire_new_subcommands_exist():
    from driftdriver.cli import _build_parser

    p = _build_parser()
    # Extract subcommand names from the parser
    subparsers_action = next(
        a for a in p._actions if hasattr(a, "_name_parser_map")
    )
    cmds = set(subparsers_action._name_parser_map.keys())
    assert "prime" in cmds
    assert "recover" in cmds
    assert "scope-check" in cmds
    assert "reflect" in cmds


def test_cmd_prime_returns_string():
    from driftdriver.wire import cmd_prime

    with TemporaryDirectory() as tmp:
        project_dir = Path(tmp)
        kb_dir = project_dir / ".workgraph"
        kb_dir.mkdir(parents=True)
        kb_file = kb_dir / "knowledge.jsonl"
        kb_file.write_text('{"fact_id":"f1","fact_type":"gotcha","content":"Watch state carefully","confidence":"high"}\n')
        result = cmd_prime(project_dir)
    assert isinstance(result, str)
    assert "Watch state carefully" in result


def test_cmd_recover_empty():
    from driftdriver.wire import cmd_recover

    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        result = cmd_recover(Path(tmp))
    assert result == []


def test_state_file_sanitizes_path_traversal():
    from driftdriver.execution_state import state_file

    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        path = state_file(wg_dir, "../../etc/passwd")
    # Must not escape the recovery directory
    assert ".." not in str(path.name)
    assert path.parent == wg_dir / "recovery"
