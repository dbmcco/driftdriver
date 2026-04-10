# ABOUTME: Tests for canonicalizing the legacy agent-health queue into PAIA-aware pending decisions.
# ABOUTME: Verifies topology-based filtering, member canonicalization, and answer/archive behavior.
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.paia_agent_health.queue import (
    answer_agent_health_decision,
    load_pending_agent_health_decisions,
)


def _write_paia_topology(root: Path) -> None:
    config = root / "paia-program" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"""
[repos]
paia-agents = "{root / 'paia-agents'}"
samantha = "{root / 'paia-agents' / 'samantha'}"
caroline = "{root / 'paia-agents' / 'caroline'}"
derek = "{root / 'paia-agents' / 'derek'}"
ingrid = "{root / 'paia-agents' / 'ingrid'}"

[topology.canonical]
target_repos = ["paia-agents"]

[topology.agent_family]
target_repo = "paia-agents"
members = ["samantha", "caroline", "derek", "ingrid"]
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _write_pending(config_dir: Path, payload: dict) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    pending = config_dir / "agent_health_pending.json"
    pending.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pending


def test_load_pending_agent_health_decisions_canonicalizes_agent_family_entries() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        _write_paia_topology(root)
        config_dir = root / ".config" / "workgraph"
        _write_pending(
            config_dir,
            {
                "dec-20260410-abc123": {
                    "agent": "derek",
                    "pattern": "toolinvocationfailure",
                    "component": "workgraph_cli_integration",
                    "risk": "medium",
                    "change_summary": "Teach Derek the real wg flags.",
                    "diff": "--- a/experiments/derek/CLAUDE.md\n+++ b/experiments/derek/CLAUDE.md\n",
                }
            },
        )

        decisions = load_pending_agent_health_decisions(config_dir=config_dir, workspace_root=root)

        assert len(decisions) == 1
        dec = decisions[0]
        assert dec.id == "dec-20260410-abc123"
        assert dec.repo == "paia-agents"
        assert dec.category == "agent_health"
        assert dec.context["agent_member"] == "derek"
        assert dec.context["source_queue"] == "agent_health_pending"
        assert dec.context["component"] == "workgraph_cli_integration"
        assert "derek" in dec.question


def test_load_pending_agent_health_decisions_skips_unmapped_noise() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        _write_paia_topology(root)
        config_dir = root / ".config" / "workgraph"
        _write_pending(
            config_dir,
            {
                "dec-20260410-abc123": {
                    "agent": "random-agent",
                    "pattern": "toolinvocationfailure",
                    "component": "unknown",
                    "risk": "medium",
                    "change_summary": "Noise.",
                    "diff": "--- a/other-repo/README.md\n+++ b/other-repo/README.md\n",
                }
            },
        )

        decisions = load_pending_agent_health_decisions(config_dir=config_dir, workspace_root=root)

        assert decisions == []


def test_answer_agent_health_decision_removes_pending_and_archives_answer() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        _write_paia_topology(root)
        config_dir = root / ".config" / "workgraph"
        pending_path = _write_pending(
            config_dir,
            {
                "dec-20260410-abc123": {
                    "agent": "caroline",
                    "pattern": "canned_response_loop",
                    "component": "conversation_state",
                    "risk": "low",
                    "change_summary": "Prevent repeated ping echoes.",
                    "diff": "--- a/src/caroline/agent.py\n+++ b/src/caroline/agent.py\n",
                }
            },
        )

        answered = answer_agent_health_decision(
            decision_id="dec-20260410-abc123",
            answer="no",
            answered_via="telegram",
            config_dir=config_dir,
            workspace_root=root,
        )

        assert answered is not None
        assert answered.id == "dec-20260410-abc123"
        pending_payload = json.loads(pending_path.read_text(encoding="utf-8"))
        assert "dec-20260410-abc123" not in pending_payload

        archive_path = config_dir / "agent_health_answered.jsonl"
        rows = [json.loads(line) for line in archive_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows[-1]["id"] == "dec-20260410-abc123"
        assert rows[-1]["answer"] == "no"
        assert rows[-1]["answered_via"] == "telegram"
