# tests/test_paia_agent_health_analyzer.py
# ABOUTME: Tests for two-pass LLM analysis — monkeypatches subprocess to avoid real Claude calls.

from __future__ import annotations

import json
from unittest.mock import patch
from driftdriver.paia_agent_health.collector import AgentSignals, SignalBundle
from driftdriver.paia_agent_health.analyzer import Finding, FixProposal, run_analysis


def _make_bundle() -> SignalBundle:
    signals = AgentSignals(
        name="caroline",
        tenant_id="Caroline",
        conversation_turns=[
            {"content": "user: that search didn't work again"},
            {"content": "user: you missed the contact lookup"},
            {"content": "user: try again, same issue"},
        ],
        tool_events=[
            {"data": {"tool": "load_skill", "success": False, "error": "search failed"}} for _ in range(4)
        ],
    )
    return SignalBundle(
        agents={"caroline": signals, "samantha": AgentSignals(name="samantha", tenant_id="Samantha"),
                "derek": AgentSignals(name="derek", tenant_id="Derek"),
                "ingrid": AgentSignals(name="ingrid", tenant_id="Ingrid")},
        shell_metrics=[],
        collected_at="2026-04-01T03:00:00+00:00",
    )


def _pass1_output() -> dict:
    return {
        "findings": [{
            "agent": "caroline",
            "pattern_type": "tool_failure",
            "evidence": ["user: that search didn't work again", "user: you missed the contact lookup"],
            "evidence_count": 4,
            "affected_component": "skills/outreach_templates.md",
            "severity": "high",
            "confidence": 0.85,
        }]
    }


def _pass2_output() -> dict:
    return {
        "change_summary": "Add fallback search protocol to outreach_templates.md",
        "diff": "--- a/outreach_templates.md\n+++ b/outreach_templates.md\n@@ -10 +10,3 @@\n+## Fallback\n+Retry with last name only.",
        "auto_apply": False,
        "risk": "low",
    }


def _mock_run(pass1_out, pass2_out):
    call_count = [0]
    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        out = pass1_out if call_count[0] == 1 else pass2_out
        return type("R", (), {"returncode": 0, "stdout": json.dumps({"structured_output": out}), "stderr": ""})()
    return fake_run


def test_run_analysis_returns_proposals():
    bundle = _make_bundle()
    with patch("subprocess.run", side_effect=_mock_run(_pass1_output(), _pass2_output())):
        proposals = run_analysis(bundle)
    assert len(proposals) == 1
    p = proposals[0]
    assert isinstance(p, FixProposal)
    assert p.finding.agent == "caroline"
    assert p.auto_apply is False
    assert "Fallback" in p.diff


def test_low_confidence_findings_are_filtered():
    bundle = _make_bundle()
    low_conf = {"findings": [{"agent": "caroline", "pattern_type": "tool_failure",
                               "evidence": ["x"], "evidence_count": 1,
                               "affected_component": "skills/outreach_templates.md",
                               "severity": "low", "confidence": 0.3}]}
    with patch("subprocess.run", side_effect=_mock_run(low_conf, _pass2_output())):
        proposals = run_analysis(bundle)
    assert proposals == []


def test_empty_bundle_returns_no_proposals():
    empty = SignalBundle(
        agents={"caroline": AgentSignals(name="caroline", tenant_id="Caroline")},
        shell_metrics=[],
        collected_at="2026-04-01T00:00:00+00:00",
    )
    with patch("subprocess.run", side_effect=_mock_run({"findings": []}, {})):
        proposals = run_analysis(empty)
    assert proposals == []
