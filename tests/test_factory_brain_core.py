# ABOUTME: Tests for the factory brain core — prompt assembly, model invocation, and logging.
# ABOUTME: Uses mocked Anthropic API to avoid real API calls during testing.
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA
from driftdriver.factory_brain.prompts import (
    ADVERSARY_SYSTEM,
    DIRECTIVE_TOOL,
    TIER_ADDITIONS,
    TIER_MODELS,
    build_system_prompt,
    build_user_prompt,
)


def _mock_anthropic_response(tool_input: dict) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking an Anthropic messages.create() response."""
    tool_block = SimpleNamespace(
        type="tool_use",
        name="issue_directives",
        input=tool_input,
    )
    usage = SimpleNamespace(input_tokens=150, output_tokens=80)
    return SimpleNamespace(content=[tool_block], usage=usage)


# --- Prompt tests ---


def test_build_system_prompt_includes_adversary():
    prompt = build_system_prompt(1)
    assert ADVERSARY_SYSTEM in prompt
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
    assert "issue_directives tool" in prompt


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


def test_directive_tool_has_all_actions():
    tool_actions = DIRECTIVE_TOOL["input_schema"]["properties"]["directives"]["items"]["properties"]["action"]["enum"]
    schema_actions = sorted(DIRECTIVE_SCHEMA.keys())
    assert tool_actions == schema_actions


# --- Brain invocation tests ---


def test_invoke_brain_returns_directives(tmp_path: Path):
    tool_input = {
        "reasoning": "Agent died in paia-os. Restarting.",
        "directives": [
            {"action": "spawn_agent", "params": {"repo": "paia-os", "task_id": "t-42"}},
        ],
        "telegram": None,
        "escalate": False,
    }
    mock_response = _mock_anthropic_response(tool_input)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
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
    tool_input = {
        "reasoning": "Repeated failures across repos. Need higher-tier analysis.",
        "directives": [
            {"action": "noop", "params": {"reason": "deferring to tier 2"}},
        ],
        "telegram": "Multiple repos failing — escalating to Sonnet.",
        "escalate": True,
    }
    mock_response = _mock_anthropic_response(tool_input)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
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
    tool_input = {
        "reasoning": "All clear. No action needed.",
        "directives": [
            {"action": "noop", "params": {"reason": "steady state"}},
        ],
        "telegram": None,
        "escalate": False,
    }
    mock_response = _mock_anthropic_response(tool_input)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
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
    assert records[0]["input_tokens"] == 150
    assert records[0]["output_tokens"] == 80

    # Check markdown log
    md_path = tmp_path / "brain-log.md"
    assert md_path.exists()
    md_content = md_path.read_text()
    assert "Tier 2" in md_content
    assert "claude-sonnet-4-6" in md_content
    assert "All clear. No action needed." in md_content
    assert "150 in / 80 out" in md_content
    assert "noop" in md_content


def test_invoke_brain_no_tool_use_returns_noop():
    """When the model doesn't return a tool_use block, we get a noop."""
    # Response with only a text block, no tool_use
    text_block = SimpleNamespace(type="text", text="I'm confused")
    usage = SimpleNamespace(input_tokens=100, output_tokens=50)
    mock_response = SimpleNamespace(content=[text_block], usage=usage)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    mock_anthropic_mod = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic_mod}):
        from driftdriver.factory_brain.brain import invoke_brain

        result = invoke_brain(tier=1, trigger_event={"kind": "loop.started", "repo": "test"})

    assert "did not return a tool_use block" in result.reasoning
    assert len(result.directives) == 1
    assert result.directives[0].action == "noop"
