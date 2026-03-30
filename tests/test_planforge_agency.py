# ABOUTME: Tests for planforge Agency composition — role dispatch, speedrift wrapping, fallback.
# ABOUTME: Verifies Agency-composed prompts get wrapped and built-in prompts used when Agency unavailable.
from __future__ import annotations

import json

from driftdriver.planforge_agency import (
    PLANFORGE_ROLES,
    AgencyResult,
    builtin_prompt,
    compose_debate_prompt,
    wrap_with_speedrift,
)


# ── Built-in prompt fallback ──


class TestBuiltinPrompt:
    def test_returns_string(self) -> None:
        prompt = builtin_prompt(
            role="synthesis",
            context={"problem": "test problem"},
            desired_outcomes=["validate problem statement"],
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_includes_role(self) -> None:
        prompt = builtin_prompt(
            role="synthesis",
            context={"problem": "test problem"},
            desired_outcomes=["validate"],
        )
        assert "synthesis" in prompt.lower() or "Synthesis" in prompt

    def test_includes_context(self) -> None:
        prompt = builtin_prompt(
            role="yagni",
            context={"problem": "caching strategy"},
            desired_outcomes=["cut list"],
        )
        assert "caching strategy" in prompt

    def test_includes_desired_outcomes(self) -> None:
        prompt = builtin_prompt(
            role="ideation",
            context={"problem": "x"},
            desired_outcomes=["propose 2-3 approaches", "tradeoff analysis"],
        )
        assert "propose 2-3 approaches" in prompt

    def test_all_roles_produce_output(self) -> None:
        for role in PLANFORGE_ROLES:
            prompt = builtin_prompt(
                role=role,
                context={"problem": "test"},
                desired_outcomes=["test outcome"],
            )
            assert len(prompt) > 50, f"Role {role} produced too-short prompt"


# ── Speedrift protocol wrapping ──


class TestWrapWithSpeedrift:
    def test_wraps_agency_output(self) -> None:
        agency_prompt = "You are a synthesis agent with strong analytical skills."
        original_prompt = "## Task\nValidate the problem.\n\n```wg-contract\nschema = 1\n```"
        result = wrap_with_speedrift(agency_prompt, original_prompt)
        # Agency identity should appear
        assert "Agency-Composed Agent Identity" in result
        assert "analytical skills" in result
        # Original speedrift prompt preserved
        assert "wg-contract" in result

    def test_empty_agency_prompt_returns_original(self) -> None:
        original = "## Task\nDo the thing."
        result = wrap_with_speedrift("", original)
        assert result == original

    def test_whitespace_agency_prompt_returns_original(self) -> None:
        original = "## Task\nDo the thing."
        result = wrap_with_speedrift("   \n  ", original)
        assert result == original


# ── Agency result model ──


class TestAgencyResult:
    def test_from_json_success(self) -> None:
        data = json.dumps([{
            "task_id": "t1",
            "system_prompt": "You are a careful analyst.",
        }])
        result = AgencyResult.from_json(data)
        assert result is not None
        assert result.prompt == "You are a careful analyst."

    def test_from_json_dict_format(self) -> None:
        data = json.dumps({
            "assignments": [{
                "task_id": "t1",
                "composed_prompt": "Role: challenger",
            }],
        })
        result = AgencyResult.from_json(data)
        assert result is not None
        assert result.prompt == "Role: challenger"

    def test_from_json_empty_list(self) -> None:
        data = json.dumps([])
        assert AgencyResult.from_json(data) is None

    def test_from_json_no_prompt_fields(self) -> None:
        data = json.dumps([{"task_id": "t1", "other": "data"}])
        assert AgencyResult.from_json(data) is None

    def test_from_json_malformed(self) -> None:
        assert AgencyResult.from_json("not json") is None


# ── Full compose_debate_prompt ──


class TestComposeDebatePrompt:
    def test_fallback_when_agency_unavailable(self) -> None:
        """When Agency is not reachable, compose_debate_prompt returns built-in."""
        result = compose_debate_prompt(
            role="synthesis",
            context={"problem": "design a cache"},
            desired_outcomes=["validate problem"],
            session_dir="/tmp/fake-session",
            agency_host="127.0.0.1",
            agency_port=1,  # port 1 — guaranteed unreachable
        )
        assert result.used_agency is False
        assert len(result.prompt) > 50
        assert "design a cache" in result.prompt

    def test_fallback_has_desired_outcomes(self) -> None:
        result = compose_debate_prompt(
            role="technical",
            context={"problem": "x"},
            desired_outcomes=["evaluate feasibility"],
            session_dir="/tmp/fake",
            agency_host="127.0.0.1",
            agency_port=1,
        )
        assert "evaluate feasibility" in result.prompt

    def test_all_roles_valid(self) -> None:
        for role in PLANFORGE_ROLES:
            result = compose_debate_prompt(
                role=role,
                context={"problem": "test"},
                desired_outcomes=["test"],
                session_dir="/tmp/fake",
                agency_host="127.0.0.1",
                agency_port=1,
            )
            assert result.used_agency is False
            assert len(result.prompt) > 0
