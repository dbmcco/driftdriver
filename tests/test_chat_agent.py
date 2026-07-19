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
    assert result.get("binary") == "echo"


def _repo_snap(tmp_path, name: str = "myrepo"):
    snap = {"repos": [{"name": name, "drift_score": 0, "path": str(tmp_path)}]}
    snap_path = tmp_path / "snapshot.json"
    snap_path.write_text(json.dumps(snap))
    return snap_path


def test_run_command_rejects_dangerous_binary(tmp_path, monkeypatch):
    """Dangerous commands are denied and never reach the subprocess layer."""
    executed: list[list[str]] = []

    def _boom(cmd, cwd, timeout=30):
        executed.append(list(cmd))
        return {"ok": True, "output": "", "returncode": 0}

    monkeypatch.setattr("driftdriver.ecosystem_hub.chat_agent._run_subprocess", _boom)
    agent = EcosystemAgent(snapshot_path=_repo_snap(tmp_path), history_path=None)
    result = agent.execute_tool("run_command", {"repo_name": "myrepo", "command": "rm -rf /tmp/x"})
    assert result.get("denied") is True
    assert "allowlist" in result["reason"].lower() or "binary" in result["reason"].lower()
    assert executed == []  # nothing executed


def test_run_command_rejects_shell_metacharacter(tmp_path, monkeypatch):
    executed: list[list[str]] = []
    monkeypatch.setattr(
        "driftdriver.ecosystem_hub.chat_agent._run_subprocess",
        lambda cmd, cwd, timeout=30: executed.append(list(cmd)) or {"ok": True, "output": "", "returncode": 0},
    )
    agent = EcosystemAgent(snapshot_path=_repo_snap(tmp_path), history_path=None)
    result = agent.execute_tool("run_command", {"repo_name": "myrepo", "command": "wg list; rm -rf /"})
    assert result.get("denied") is True
    assert "metacharacter" in result["reason"].lower()
    assert executed == []


def test_run_command_dispatch_denied_without_authority(tmp_path, monkeypatch):
    """wg service start is denied (and not executed) without an active lease."""
    executed: list[list[str]] = []
    monkeypatch.setattr(
        "driftdriver.ecosystem_hub.chat_agent._run_subprocess",
        lambda cmd, cwd, timeout=30: executed.append(list(cmd)) or {"ok": True, "output": "", "returncode": 0},
    )
    agent = EcosystemAgent(snapshot_path=_repo_snap(tmp_path), history_path=None)
    agent._repo_dispatch_authority = lambda path: {
        "enabled": False, "mode": "observe", "lease_active": False, "reason": "lease is not active",
    }
    result = agent.execute_tool("run_command", {"repo_name": "myrepo", "command": "wg service start"})
    assert result.get("denied") is True
    assert result.get("dispatch_denied") is True
    assert "dispatch authority denied" in result["reason"]
    assert result["authority"]["enabled"] is False
    assert executed == []  # dispatch never ran


def test_run_command_dispatch_allowed_with_authority(tmp_path, monkeypatch):
    """wg service start runs (as argv, not bash -c) when authority is enabled."""
    captured: dict = {}

    def _fake_run(cmd, cwd, timeout=30):
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        return {"ok": True, "output": "service started", "returncode": 0}

    monkeypatch.setattr("driftdriver.ecosystem_hub.chat_agent._run_subprocess", _fake_run)
    agent = EcosystemAgent(snapshot_path=_repo_snap(tmp_path), history_path=None)
    agent._repo_dispatch_authority = lambda path: {
        "enabled": True, "mode": "supervise", "lease_active": True, "reason": "active lease permits dispatch",
    }
    result = agent.execute_tool("run_command", {"repo_name": "myrepo", "command": "wg --dir .wg service start"})
    assert result.get("ok") is True
    # Executed as a parsed argv list — never via bash -c.
    assert captured["cmd"] == ["wg", "--dir", ".wg", "service", "start"]
    assert "bash" not in captured["cmd"]
    assert "-c" not in captured["cmd"]


def test_run_command_diagnostic_runs_without_authority_check(tmp_path, monkeypatch):
    """Diagnostics run even when authority is denied (no authority lookup needed)."""
    authority_calls: list[str] = []
    monkeypatch.setattr(
        "driftdriver.ecosystem_hub.chat_agent._run_subprocess",
        lambda cmd, cwd, timeout=30: {"ok": True, "output": "1\topen", "returncode": 0},
    )
    agent = EcosystemAgent(snapshot_path=_repo_snap(tmp_path), history_path=None)
    agent._repo_dispatch_authority = lambda path: authority_calls.append(path) or {
        "enabled": False, "mode": "observe", "lease_active": False, "reason": "denied",
    }
    result = agent.execute_tool("run_command", {"repo_name": "myrepo", "command": "wg list"})
    assert result.get("ok") is True
    assert authority_calls == []  # diagnostics never consult authority


def test_run_command_routes_speedriftd_mutation_to_dedicated_tool(tmp_path, monkeypatch):
    """Mutating speedriftd --set-mode is rejected so the model uses arm_repo."""
    executed: list[list[str]] = []
    monkeypatch.setattr(
        "driftdriver.ecosystem_hub.chat_agent._run_subprocess",
        lambda cmd, cwd, timeout=30: executed.append(list(cmd)) or {"ok": True, "output": "", "returncode": 0},
    )
    agent = EcosystemAgent(snapshot_path=_repo_snap(tmp_path), history_path=None)
    result = agent.execute_tool(
        "run_command",
        {"repo_name": "myrepo", "command": "driftdriver --dir /x speedriftd status --set-mode autonomous --lease-owner a --reason b"},
    )
    assert result.get("denied") is True
    assert executed == []


def test_launch_freshell_unavailable():
    agent = EcosystemAgent(snapshot_path=None, history_path=None, freshell_base="http://localhost:19999")
    result = agent.execute_tool("launch_freshell_session", {"repo_name": "myrepo"})
    assert result["ok"] is False
    assert "error" in result
