# ABOUTME: Tests for the factory brain core — prompt assembly, CLI invocation, and logging.
# ABOUTME: Uses mocked subprocess.run to avoid real claude CLI calls during testing.
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA
from driftdriver.factory_brain.prompts import (
    ADVERSARY_SYSTEM,
    SELF_HEAL_ADDENDUM,
    TIER_ADDITIONS,
    TIER_MODELS,
    build_system_prompt,
    build_user_prompt,
)


def _mock_cli_result(directive_data: dict, *, returncode: int = 0) -> object:
    """Build a mock subprocess.CompletedProcess mimicking claude CLI --output-format json."""
    cli_output = {
        "type": "result",
        "subtype": "success",
        "result": "Done.",
        "structured_output": directive_data,
        "cost_usd": 0.003,
        "is_error": False,
        "duration_ms": 1200,
        "num_turns": 1,
        "session_id": "test-session",
    }

    class FakeResult:
        pass

    r = FakeResult()
    r.returncode = returncode
    r.stdout = json.dumps(cli_output)
    r.stderr = ""
    return r


# --- Prompt tests ---


def test_build_system_prompt_includes_adversary():
    prompt = build_system_prompt(1)
    assert "Factory Adversary" in prompt


def test_build_system_prompt_tier_specific():
    p1 = build_system_prompt(1)
    p2 = build_system_prompt(2)
    p3 = build_system_prompt(3)

    assert "Haiku" in p1
    assert "reflexes" in p1

    assert "Sonnet" in p2
    assert "strategy" in p2

    assert "Opus" in p3
    assert "judgment" in p3


def test_build_system_prompt_includes_action_vocab():
    prompt = build_system_prompt(1)
    assert "noop" in prompt
    assert "kill_daemon" in prompt
    assert "spawn_agent" in prompt


def test_system_prompt_includes_self_heal():
    prompt = build_system_prompt(tier=2)
    assert "self-heal" in prompt.lower()
    assert "create_decision" in prompt


def test_system_prompt_includes_compliance():
    prompt = build_system_prompt(tier=2)
    assert "enforce_compliance" in prompt


def test_system_prompt_self_heal_scenarios():
    """Verify all four self-heal scenarios are mentioned."""
    prompt = build_system_prompt(tier=1)
    for scenario in ["blocked cascade", "agent failure", "task loop", "drift plateau"]:
        assert scenario.lower() in prompt.lower(), f"Missing scenario: {scenario}"


def test_system_prompt_escalation_criteria():
    """Brain must try self-heal before escalating."""
    prompt = build_system_prompt(tier=2)
    assert "before" in prompt.lower() or "first" in prompt.lower()
    assert "escalat" in prompt.lower()


def test_build_user_prompt_includes_sections():
    prompt = build_user_prompt(
        trigger_event={"kind": "agent.died", "repo": "paia-os"},
        snapshot={"repos": 5, "agents": 3},
        heuristic_recommendation="restart agent",
    )
    assert "## Trigger Event" in prompt
    assert "agent.died" in prompt
    assert "## Factory Snapshot" in prompt
    assert "## Heuristic Recommendation" in prompt
    assert "restart agent" in prompt


def test_build_user_prompt_escalation_context():
    prompt = build_user_prompt(
        escalation_reason="Tier 1 could not resolve repeated agent deaths",
        tier1_reasoning="Saw 3 agent.died events in 60 seconds",
        tier2_reasoning="Cross-repo pattern suggests systemic memory issue",
    )
    assert "## Escalation Context" in prompt
    assert "Tier 1 could not resolve" in prompt
    assert "## Tier 1 Reasoning" in prompt
    assert "## Tier 2 Reasoning" in prompt


# --- Schema tests ---


def test_tier_models():
    assert TIER_MODELS[1] == "claude-haiku-4-5-20251001"
    assert TIER_MODELS[2] == "claude-sonnet-4-6"
    assert TIER_MODELS[3] == "claude-opus-4-6"


# --- Brain invocation tests ---


def test_invoke_brain_returns_directives(tmp_path: Path):
    directive_data = {
        "reasoning": "Agent died in paia-os. Restarting.",
        "directives": [
            {"action": "spawn_agent", "params": {"repo": "paia-os", "task_id": "t-42"}},
        ],
        "telegram": None,
        "escalate": False,
    }

    with patch("driftdriver.factory_brain.brain.subprocess.run", return_value=_mock_cli_result(directive_data)):
        from driftdriver.factory_brain.brain import invoke_brain

        result = invoke_brain(
            tier=1,
            trigger_event={"kind": "agent.died", "repo": "paia-os"},
            log_dir=tmp_path,
        )

    assert result.reasoning == "Agent died in paia-os. Restarting."
    assert len(result.directives) == 1
    assert result.directives[0].action == "spawn_agent"
    assert result.directives[0].params["repo"] == "paia-os"
    assert result.escalate is False


def test_invoke_brain_escalation(tmp_path: Path):
    directive_data = {
        "reasoning": "Repeated failures across repos. Need higher-tier analysis.",
        "directives": [
            {"action": "noop", "params": {"reason": "deferring to tier 2"}},
        ],
        "telegram": "Multiple repos failing — escalating to Sonnet.",
        "escalate": True,
    }

    with patch("driftdriver.factory_brain.brain.subprocess.run", return_value=_mock_cli_result(directive_data)):
        from driftdriver.factory_brain.brain import invoke_brain

        result = invoke_brain(
            tier=1,
            trigger_event={"kind": "loop.crashed", "repo": "paia-os"},
            escalation_reason="too many crashes",
        )

    assert result.escalate is True
    assert result.telegram == "Multiple repos failing — escalating to Sonnet."
    assert result.directives[0].action == "noop"


def test_invoke_brain_writes_log(tmp_path: Path):
    directive_data = {
        "reasoning": "All clear. No action needed.",
        "directives": [
            {"action": "noop", "params": {"reason": "steady state"}},
        ],
        "telegram": None,
        "escalate": False,
    }

    with patch("driftdriver.factory_brain.brain.subprocess.run", return_value=_mock_cli_result(directive_data)):
        from driftdriver.factory_brain.brain import invoke_brain

        invoke_brain(
            tier=2,
            trigger_event={"kind": "snapshot.collected", "repo": "lodestar"},
            snapshot={"repos": 3},
            log_dir=tmp_path,
        )

    # Check JSONL log
    jsonl_path = tmp_path / "brain-invocations.jsonl"
    assert jsonl_path.exists()
    records = [json.loads(line) for line in jsonl_path.read_text().strip().splitlines()]
    assert len(records) == 1
    assert records[0]["tier"] == 2
    assert records[0]["model"] == "claude-sonnet-4-6"
    assert records[0]["reasoning"] == "All clear. No action needed."

    # Check markdown log
    md_path = tmp_path / "brain-log.md"
    assert md_path.exists()
    md_content = md_path.read_text()
    assert "Tier 2" in md_content
    assert "claude-sonnet-4-6" in md_content
    assert "All clear. No action needed." in md_content
    assert "noop" in md_content


def test_invoke_brain_cli_error_returns_noop():
    """When claude CLI exits non-zero, we get a noop."""

    class FailResult:
        returncode = 1
        stdout = ""
        stderr = "error: model overloaded"

    with patch("driftdriver.factory_brain.brain.subprocess.run", return_value=FailResult()):
        from driftdriver.factory_brain.brain import invoke_brain

        result = invoke_brain(tier=1, trigger_event={"kind": "loop.started", "repo": "test"})

    assert "exit 1" in result.reasoning
    assert len(result.directives) == 1
    assert result.directives[0].action == "noop"


def test_invoke_brain_cli_timeout_returns_noop():
    """When claude CLI times out, we get a noop."""
    import subprocess as sp

    with patch("driftdriver.factory_brain.brain.subprocess.run", side_effect=sp.TimeoutExpired(cmd="claude", timeout=120)):
        from driftdriver.factory_brain.brain import invoke_brain

        result = invoke_brain(tier=1, trigger_event={"kind": "loop.started", "repo": "test"})

    assert "timed out" in result.reasoning
    assert result.directives[0].action == "noop"


def test_invoke_brain_cli_not_found_returns_noop():
    """When claude CLI is not installed, we get a noop."""
    with patch("driftdriver.factory_brain.brain.subprocess.run", side_effect=FileNotFoundError("claude not found")):
        from driftdriver.factory_brain.brain import invoke_brain

        result = invoke_brain(tier=1, trigger_event={"kind": "loop.started", "repo": "test"})

    assert "found" in result.reasoning
    assert result.directives[0].action == "noop"
