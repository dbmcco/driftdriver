# ABOUTME: Tests for model-mediated routing decision module
# ABOUTME: Covers RoutingDecision dataclass, prompt formatting, response parsing, and auto-fencing

import json
import pytest
from driftdriver.smart_routing import EvidencePackage
from driftdriver.routing_models import (
    RoutingDecision,
    detect_fenced_lanes,
    format_routing_prompt,
    parse_routing_response,
)


def make_evidence(
    task_description: str = "Fix the auth bug",
    installed_lanes: list[str] | None = None,
) -> EvidencePackage:
    return EvidencePackage(
        changed_files={"src/auth.py": "modified", "tests/test_auth.py": "added"},
        file_classifications={"src/auth.py": ["coredrift"], "tests/test_auth.py": ["coredrift"]},
        task_description=task_description,
        task_contract={"lanes": [], "verify": "tests pass"},
        project_context=[],
        prior_drift_findings=[],
        installed_lanes=installed_lanes or ["coredrift", "specdrift", "datadrift"],
        pattern_hints={"coredrift": ["*.py"]},
    )


class TestRoutingDecision:
    def test_routing_decision_creation(self):
        """Basic dataclass should hold all required fields."""
        decision = RoutingDecision(
            selected_lanes=["coredrift", "specdrift"],
            reasoning={"coredrift": "python files changed", "specdrift": "not selected"},
            confidence=0.85,
            auto_fenced=["specdrift"],
            model_suggested=["coredrift"],
            evidence_summary="2 python files modified",
        )
        assert decision.selected_lanes == ["coredrift", "specdrift"]
        assert decision.confidence == 0.85
        assert decision.auto_fenced == ["specdrift"]
        assert decision.model_suggested == ["coredrift"]
        assert "coredrift" in decision.reasoning
        assert decision.evidence_summary == "2 python files modified"


class TestFormatRoutingPrompt:
    def test_format_routing_prompt_includes_evidence(self):
        """Prompt should contain the evidence context."""
        evidence = make_evidence()
        prompt = format_routing_prompt(evidence)
        # Evidence context includes task description
        assert "Fix the auth bug" in prompt
        # Evidence context includes changed files
        assert "src/auth.py" in prompt

    def test_format_routing_prompt_includes_installed_lanes(self):
        """Prompt should list all available/installed lanes."""
        evidence = make_evidence(installed_lanes=["coredrift", "specdrift", "datadrift"])
        prompt = format_routing_prompt(evidence)
        assert "coredrift" in prompt
        assert "specdrift" in prompt
        assert "datadrift" in prompt

    def test_format_routing_prompt_requests_json_output(self):
        """Prompt should ask for JSON-structured output."""
        evidence = make_evidence()
        prompt = format_routing_prompt(evidence)
        assert "json" in prompt.lower() or "JSON" in prompt


class TestDetectFencedLanes:
    def test_detect_fenced_lanes(self):
        """Should detect lane names from fenced code blocks."""
        description = """
Fix the authentication flow.

```specdrift
verify: spec matches implementation
```

Some more text.

```coredrift
verify: tests pass
```
"""
        fenced = detect_fenced_lanes(description)
        assert "specdrift" in fenced
        assert "coredrift" in fenced

    def test_detect_fenced_lanes_no_fences(self):
        """Should return empty list when no lane fences are present."""
        description = "Just a plain task description with no fenced blocks."
        fenced = detect_fenced_lanes(description)
        assert fenced == []

    def test_detect_fenced_lanes_ignores_non_lane_fences(self):
        """Should ignore generic code fences like ```python or ```bash."""
        description = """
Do something.

```python
x = 1
```

```bash
echo hello
```
"""
        fenced = detect_fenced_lanes(description)
        assert "python" not in fenced
        assert "bash" not in fenced


class TestParseRoutingResponse:
    def test_parse_valid_json_response(self):
        """Happy path: valid JSON response is parsed into a RoutingDecision."""
        evidence = make_evidence()
        response_json = {
            "selected_lanes": ["coredrift"],
            "reasoning": {"coredrift": "python files changed", "specdrift": "no spec files"},
            "confidence": 0.9,
            "evidence_summary": "1 python file modified",
        }
        response = json.dumps(response_json)
        decision = parse_routing_response(response, evidence)
        assert "coredrift" in decision.selected_lanes
        assert decision.confidence == 0.9
        assert decision.evidence_summary == "1 python file modified"

    def test_parse_response_with_markdown_fences(self):
        """Should extract JSON from markdown code-fenced responses."""
        evidence = make_evidence()
        response_json = {
            "selected_lanes": ["coredrift"],
            "reasoning": {"coredrift": "matches"},
            "confidence": 0.8,
            "evidence_summary": "python changes",
        }
        response = f"```json\n{json.dumps(response_json)}\n```"
        decision = parse_routing_response(response, evidence)
        assert "coredrift" in decision.selected_lanes
        assert decision.confidence == 0.8

    def test_parse_invalid_response_falls_back(self):
        """Unparseable response falls back to pattern-based suggestions with lower confidence."""
        evidence = make_evidence()
        decision = parse_routing_response("this is not json at all", evidence)
        # Fallback: confidence should be lower than a successful parse
        assert decision.confidence < 0.5
        # Fallback should still include pattern-suggested lanes
        # (coredrift from classify_files via pattern_hints)
        assert "coredrift" in decision.selected_lanes

    def test_fenced_lanes_always_included(self):
        """Auto-fenced lanes must appear in selected_lanes even if model omits them."""
        task = """
Update the API.

```specdrift
verify: spec is current
```
"""
        evidence = make_evidence(
            task_description=task,
            installed_lanes=["coredrift", "specdrift"],
        )
        # Model response that omits specdrift
        response_json = {
            "selected_lanes": ["coredrift"],
            "reasoning": {"coredrift": "python files"},
            "confidence": 0.85,
            "evidence_summary": "python changes",
        }
        decision = parse_routing_response(json.dumps(response_json), evidence)
        assert "specdrift" in decision.selected_lanes
        assert "specdrift" in decision.auto_fenced

    def test_parse_filters_uninstalled_lanes(self):
        """Model-suggested lanes that are not installed should be excluded."""
        evidence = make_evidence(installed_lanes=["coredrift"])
        response_json = {
            "selected_lanes": ["coredrift", "fakelane", "notreal"],
            "reasoning": {"coredrift": "yes", "fakelane": "yes"},
            "confidence": 0.9,
            "evidence_summary": "test",
        }
        decision = parse_routing_response(json.dumps(response_json), evidence)
        assert "fakelane" not in decision.selected_lanes
        assert "notreal" not in decision.selected_lanes
        assert "coredrift" in decision.selected_lanes


class TestKnownLanes:
    def test_known_lanes_includes_new_lanes(self):
        """KNOWN_LANES must include contrariandrift and qadrift."""
        from driftdriver.routing_models import KNOWN_LANES

        assert "contrariandrift" in KNOWN_LANES
        assert "qadrift" in KNOWN_LANES
