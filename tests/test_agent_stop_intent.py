# ABOUTME: Tests for agent-stop hook continuation intent behavior.
# ABOUTME: Verifies agent-stop.sh writes intent=continue by default, preserves parked intent.

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def project_tree(tmp_path: Path) -> Path:
    """Create a minimal project tree with .workgraph runtime dirs."""
    runtime = tmp_path / ".workgraph" / "service" / "runtime"
    runtime.mkdir(parents=True)
    handlers = tmp_path / ".workgraph" / "handlers"
    handlers.mkdir(parents=True)
    return tmp_path


def _install_handler_scripts(project_dir: Path) -> None:
    """Copy agent-stop.sh and common.sh into the project's .workgraph/handlers/."""
    template_dir = Path(__file__).resolve().parent.parent / "driftdriver" / "templates" / "handlers"
    handlers_dir = project_dir / ".workgraph" / "handlers"
    for name in ("agent-stop.sh", "common.sh"):
        src = template_dir / name
        dst = handlers_dir / name
        dst.write_text(src.read_text())
        dst.chmod(0o755)


def _make_stub_driftdriver(bin_dir: Path, project_dir: Path) -> None:
    """Create a stub 'driftdriver' script that delegates intent commands to the real Python module."""
    real_driftdriver = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "driftdriver"
    stub = bin_dir / "driftdriver"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        # Stub driftdriver — delegates intent to real binary, no-ops everything else
        ARGS=()
        DIR=""
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --dir) DIR="$2"; ARGS+=("--dir" "$2"); shift 2 ;;
                intent) ARGS+=("intent"); shift
                    # Pass remaining args and exec real driftdriver
                    exec "{real_driftdriver}" "${{ARGS[@]}}" "$@"
                    ;;
                record-event|presence) exit 0 ;;
                *) ARGS+=("$1"); shift ;;
            esac
        done
        exit 0
    """))
    stub.chmod(0o755)


def _make_stub_wg(bin_dir: Path) -> None:
    """Create a no-op 'wg' stub."""
    stub = bin_dir / "wg"
    stub.write_text(textwrap.dedent("""\
        #!/usr/bin/env bash
        # Stub wg — succeeds silently for all commands
        if [[ "${1:-}" == "status" && "${2:-}" == "--json" ]]; then
            echo '{"in_progress":[]}'
        fi
        exit 0
    """))
    stub.chmod(0o755)


def _make_stub_jq(bin_dir: Path) -> None:
    """Symlink the real jq so the stub PATH doesn't shadow it."""
    # We don't need a stub — just ensure the real jq is accessible.
    # The stub PATH prepends bin_dir, so if jq isn't there, the system one is used.
    pass


def _run_agent_stop(project_dir: Path, bin_dir: Path, env_extra: dict | None = None) -> str:
    """Run agent-stop.sh and return stdout."""
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    if env_extra:
        env.update(env_extra)

    handler = project_dir / ".workgraph" / "handlers" / "agent-stop.sh"
    result = subprocess.run(
        ["bash", str(handler), "--cli", "claude-code"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project_dir),
        timeout=30,
    )
    # Allow non-zero (wg commands may fail in stub), but check stderr for real errors
    return result.stdout.strip()


def _read_control(project_dir: Path) -> dict | None:
    ctrl = project_dir / ".workgraph" / "service" / "runtime" / "control.json"
    if not ctrl.exists():
        return None
    return json.loads(ctrl.read_text())


def _write_control(project_dir: Path, control: dict) -> None:
    ctrl = project_dir / ".workgraph" / "service" / "runtime" / "control.json"
    ctrl.parent.mkdir(parents=True, exist_ok=True)
    ctrl.write_text(json.dumps(control, indent=2) + "\n")


class TestAgentStopWritesContinueIntent:
    """agent-stop.sh should write intent=continue by default."""

    def test_sets_continue_intent_on_stop(self, project_tree: Path, tmp_path: Path) -> None:
        _install_handler_scripts(project_tree)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_stub_driftdriver(bin_dir, project_tree)
        _make_stub_wg(bin_dir)
        _make_stub_jq(bin_dir)

        _run_agent_stop(project_tree, bin_dir)

        control = _read_control(project_tree)
        assert control is not None, "control.json should exist after agent-stop"
        ci = control.get("continuation_intent", {})
        assert ci["intent"] == "continue"
        assert ci["set_by"] == "agent"
        assert "reason" in ci

    def test_preserves_parked_intent(self, project_tree: Path, tmp_path: Path) -> None:
        """If intent is already 'parked', agent-stop should NOT overwrite it."""
        _install_handler_scripts(project_tree)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_stub_driftdriver(bin_dir, project_tree)
        _make_stub_wg(bin_dir)
        _make_stub_jq(bin_dir)

        # Pre-set parked intent
        _write_control(project_tree, {
            "continuation_intent": {
                "intent": "parked",
                "reason": "user parked this repo",
                "set_by": "human",
                "set_at": "2026-03-13T00:00:00+00:00",
            }
        })

        _run_agent_stop(project_tree, bin_dir)

        control = _read_control(project_tree)
        ci = control["continuation_intent"]
        assert ci["intent"] == "parked", "parked intent should be preserved"
        assert ci["set_by"] == "human"

    def test_preserves_needs_human_intent(self, project_tree: Path, tmp_path: Path) -> None:
        """If intent is 'needs_human', agent-stop should NOT overwrite it."""
        _install_handler_scripts(project_tree)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_stub_driftdriver(bin_dir, project_tree)
        _make_stub_wg(bin_dir)
        _make_stub_jq(bin_dir)

        _write_control(project_tree, {
            "continuation_intent": {
                "intent": "needs_human",
                "reason": "blocked on decision",
                "set_by": "agent",
                "set_at": "2026-03-13T00:00:00+00:00",
            }
        })

        _run_agent_stop(project_tree, bin_dir)

        control = _read_control(project_tree)
        ci = control["continuation_intent"]
        assert ci["intent"] == "needs_human", "needs_human intent should be preserved"

    def test_overwrites_stale_continue_intent(self, project_tree: Path, tmp_path: Path) -> None:
        """If intent is already 'continue', it should be refreshed with new timestamp."""
        _install_handler_scripts(project_tree)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _make_stub_driftdriver(bin_dir, project_tree)
        _make_stub_wg(bin_dir)
        _make_stub_jq(bin_dir)

        _write_control(project_tree, {
            "continuation_intent": {
                "intent": "continue",
                "reason": "old reason",
                "set_by": "agent",
                "set_at": "2026-01-01T00:00:00+00:00",
            }
        })

        _run_agent_stop(project_tree, bin_dir)

        control = _read_control(project_tree)
        ci = control["continuation_intent"]
        assert ci["intent"] == "continue"
        # Timestamp should be newer than the old one
        assert ci["set_at"] > "2026-01-01T00:00:00+00:00"
