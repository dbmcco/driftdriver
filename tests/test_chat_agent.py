# ABOUTME: Tests for EcosystemAgent — tool definitions, tool execution, subprocess helper.
# ABOUTME: Does not make live Anthropic API calls; tests tool dispatch and subprocess helper.

import json
import pathlib

import pytest

from driftdriver.ecosystem_hub.chat_agent import EcosystemAgent, _run_subprocess


def test_run_subprocess_success(tmp_path):
    result = _run_subprocess(["echo", "hello"], cwd=str(tmp_path))
    assert result["ok"] is True
    assert "hello" in result["output"]


def test_run_subprocess_nonzero(tmp_path):
    result = _run_subprocess(["false"], cwd=str(tmp_path))
    assert result["ok"] is False


def test_run_subprocess_missing_dir():
    result = _run_subprocess(["echo", "x"], cwd="/nonexistent/path/abc123")
    assert result["ok"] is False


def test_run_subprocess_timeout(tmp_path):
    result = _run_subprocess(["sleep", "10"], cwd=str(tmp_path), timeout=1)
    assert result["ok"] is False
    assert "timed out" in result["output"]


def test_agent_tool_list():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    tools = agent.get_tool_definitions()
    names = [t["name"] for t in tools]
    assert "get_ecosystem_status" in names
    assert "get_repo_detail" in names
    assert "arm_repo" in names
    assert "disarm_repo" in names
    assert "run_attractor" in names
    assert "list_tasks" in names
    assert "create_task" in names
    assert "launch_freshell_session" in names
    assert "run_command" in names


def test_agent_prefers_driftdriver_anthropic_key(monkeypatch):
    captured_keys: list[str] = []

    class _FakeAnthropic:
        def __init__(self, api_key: str) -> None:
            captured_keys.append(api_key)

    monkeypatch.setenv("DRIFTDRIVER_ANTHROPIC_API_KEY", "driftdriver-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "legacy-anthropic")
    monkeypatch.setattr("driftdriver.ecosystem_hub.chat_agent.anthropic.Anthropic", _FakeAnthropic)

    EcosystemAgent(snapshot_path=None, history_path=None)

    assert captured_keys == ["driftdriver-key"]


def test_tool_definitions_have_required_fields():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    for tool in agent.get_tool_definitions():
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool


def test_execute_get_ecosystem_status_no_snapshot():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    result = agent.execute_tool("get_ecosystem_status", {})
    assert isinstance(result, dict)


def test_execute_get_ecosystem_status_with_snapshot(tmp_path):
    snap = {
        "generated_at": "2026-03-20T10:00:00+00:00",
        "repos": [
            {"name": "paia-os", "drift_score": 75, "trend": "worsening", "path": str(tmp_path)},
            {"name": "folio", "drift_score": 20, "trend": "stable", "path": str(tmp_path)},
        ],
    }
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps(snap))
    agent = EcosystemAgent(snapshot_path=snap_path, history_path=None)
    result = agent.execute_tool("get_ecosystem_status", {})
    assert result["total_repos"] == 2
    assert result["needs_attention"] == 1
    assert result["top_attention"][0]["name"] == "paia-os"


def test_execute_get_repo_detail_found(tmp_path):
    snap = {
        "repos": [{"name": "myrepo", "drift_score": 50, "path": str(tmp_path)}]
    }
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps(snap))
    agent = EcosystemAgent(snapshot_path=snap_path, history_path=None)
    result = agent.execute_tool("get_repo_detail", {"repo_name": "myrepo"})
    assert result["name"] == "myrepo"


def test_execute_get_repo_detail_not_found(tmp_path):
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps({"repos": []}))
    agent = EcosystemAgent(snapshot_path=snap_path, history_path=None)
    result = agent.execute_tool("get_repo_detail", {"repo_name": "ghost"})
    assert "error" in result


def test_execute_unknown_tool():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    result = agent.execute_tool("nonexistent_tool", {})
    assert "error" in result


def test_execute_arm_repo_no_path():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    result = agent.execute_tool("arm_repo", {"repo_name": "ghost", "mode": "supervise", "reason": "test"})
    assert "error" in result


def test_execute_run_command_no_path():
    agent = EcosystemAgent(snapshot_path=None, history_path=None)
    result = agent.execute_tool("run_command", {"repo_name": "ghost", "command": "echo hi"})
    assert "error" in result


def test_execute_run_command_with_path(tmp_path):
    snap = {
        "repos": [{"name": "myrepo", "drift_score": 0, "path": str(tmp_path)}]
    }
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps(snap))
    agent = EcosystemAgent(snapshot_path=snap_path, history_path=None)
    result = agent.execute_tool("run_command", {"repo_name": "myrepo", "command": "echo testoutput"})
    assert result["ok"] is True
    assert "testoutput" in result["output"]


def test_launch_freshell_unavailable():
    agent = EcosystemAgent(snapshot_path=None, history_path=None, freshell_base="http://localhost:19999")
    result = agent.execute_tool("launch_freshell_session", {"repo_name": "myrepo"})
    assert result["ok"] is False
    assert "error" in result
