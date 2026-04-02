# ABOUTME: Tests for tier 1 brain routing to gpt-4.1-nano with Haiku fallback.
# ABOUTME: Validates OpenAI-first dispatch, fallback on exception, token recording, and backend logging.
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import driftdriver.factory_brain.brain as brain_mod
from driftdriver.factory_brain.brain import invoke_brain
from driftdriver.factory_brain.prompts import TIER_MODELS, TIER_MODELS_OPENAI


_GOOD_OPENAI_RESULT = (
    {
        "reasoning": "nano says noop",
        "directives": [{"action": "noop", "params": {"reason": "all good"}}],
        "telegram": None,
        "escalate": False,
    },
    "gpt-4.1-nano",
    (100, 20),
)

_GOOD_CLAUDE_RESULT = (
    {
        "reasoning": "haiku fallback",
        "directives": [{"action": "noop", "params": {"reason": "fallback"}}],
        "telegram": None,
        "escalate": False,
    },
    TIER_MODELS[1],
    (80, 15),
)


def test_tier1_calls_openai_not_claude():
    """Tier 1 invoke_brain should try _try_invoke_openai before _try_invoke."""
    with (
        patch.object(brain_mod, "_try_invoke_openai", return_value=_GOOD_OPENAI_RESULT) as mock_oai,
        patch.object(brain_mod, "_try_invoke") as mock_claude,
    ):
        invoke_brain(tier=1)

    mock_oai.assert_called_once()
    mock_claude.assert_not_called()


def test_tier1_fallback_to_haiku_on_openai_exception():
    """When _try_invoke_openai raises, tier 1 should fall back to _try_invoke (Haiku)."""
    with (
        patch.object(brain_mod, "_try_invoke_openai", side_effect=RuntimeError("API error")) as mock_oai,
        patch.object(brain_mod, "_try_invoke", return_value=_GOOD_CLAUDE_RESULT) as mock_claude,
    ):
        result = invoke_brain(tier=1)

    mock_oai.assert_called_once()  # OpenAI was attempted first
    mock_claude.assert_called_once()  # then Claude as fallback
    assert result.reasoning == "haiku fallback"


def test_tier2_does_not_use_openai():
    """Tier 2 should not touch _try_invoke_openai."""
    with (
        patch.object(brain_mod, "_try_invoke_openai") as mock_oai,
        patch.object(brain_mod, "_try_invoke", return_value=_GOOD_CLAUDE_RESULT),
    ):
        invoke_brain(tier=2)

    mock_oai.assert_not_called()


def test_tier1_openai_token_counts_recorded_in_log():
    """Token counts from OpenAI response should appear in brain-invocations.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        with (
            patch.object(brain_mod, "_try_invoke_openai", return_value=_GOOD_OPENAI_RESULT),
            patch.object(brain_mod, "_try_invoke"),
            patch.object(brain_mod, "record_spend"),
        ):
            invoke_brain(tier=1, log_dir=log_dir)

        log = json.loads((log_dir / "brain-invocations.jsonl").read_text().strip())
        assert log["input_tokens"] == 100
        assert log["output_tokens"] == 20


def test_tier1_backend_logged_as_openai_on_success():
    """Successful nano call should log backend='openai' in brain-invocations.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        with (
            patch.object(brain_mod, "_try_invoke_openai", return_value=_GOOD_OPENAI_RESULT),
            patch.object(brain_mod, "_try_invoke"),
            patch.object(brain_mod, "record_spend"),
        ):
            invoke_brain(tier=1, log_dir=log_dir)

        log = json.loads((log_dir / "brain-invocations.jsonl").read_text().strip())
        assert log["backend"] == "openai"


def test_tier1_backend_logged_as_claude_on_fallback():
    """Fallback to Haiku should log backend='claude' in brain-invocations.jsonl."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_dir = Path(tmpdir)
        with (
            patch.object(brain_mod, "_try_invoke_openai", side_effect=RuntimeError("no key")),
            patch.object(brain_mod, "_try_invoke", return_value=_GOOD_CLAUDE_RESULT),
            patch.object(brain_mod, "record_spend"),
        ):
            invoke_brain(tier=1, log_dir=log_dir)

        log = json.loads((log_dir / "brain-invocations.jsonl").read_text().strip())
        assert log["backend"] == "claude"


def test_tier1_openai_model_is_nano():
    """TIER_MODELS_OPENAI tier 1 should be gpt-4.1-nano (or env override)."""
    assert "gpt-4.1-nano" in TIER_MODELS_OPENAI[1] or TIER_MODELS_OPENAI[1].startswith("gpt-4.1")
