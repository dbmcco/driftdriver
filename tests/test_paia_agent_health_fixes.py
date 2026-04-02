# tests/test_paia_agent_health_fixes.py
# ABOUTME: Tests for fix application and proposal Telegram messaging.

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
from driftdriver.paia_agent_health.fix_history import FixRecord, load_history
from driftdriver.paia_agent_health.fixes import apply_fix, build_proposal_message, handle_agent_fix_decision


def _make_proposal(auto_apply: bool = True) -> FixProposal:
    finding = Finding(
        agent="caroline",
        pattern_type="tool_failure",
        evidence=["user: that didn't work", "user: try again"],
        evidence_count=4,
        affected_component="skills/outreach_templates.md",
        severity="medium",
        confidence=0.85,
    )
    return FixProposal(
        finding=finding,
        change_summary="Add fallback search protocol",
        diff="--- a/outreach_templates.md\n+++ b/outreach_templates.md\n@@ -10 +10,2 @@\n+## Fallback\n+retry",
        auto_apply=auto_apply,
        risk="low",
    )


def test_build_proposal_message_contains_key_elements():
    proposal = _make_proposal(auto_apply=False)
    dec_id = "dec-20260401-abc123"
    msg = build_proposal_message(proposal, dec_id)
    assert "🤖 Agent Health" in msg
    assert "caroline" in msg
    assert dec_id in msg
    assert "approve" in msg.lower() or "yes" in msg.lower()
    assert "Fallback" in msg


def test_apply_fix_writes_skill_file():
    proposal = _make_proposal(auto_apply=True)
    with tempfile.TemporaryDirectory() as td:
        skill_path = Path(td) / "outreach_templates.md"
        skill_path.write_text("line1\nline2\nIf search returns no results, try with just last name before proceeding.\n")
        history_path = Path(td) / "fixes.json"

        with patch("driftdriver.paia_agent_health.fixes._resolve_skill_path", return_value=skill_path):
            with patch("driftdriver.paia_agent_health.fixes._publish_event") as mock_publish:
                apply_fix(proposal, history_path=history_path)
                mock_publish.assert_called_once()

        history = load_history(history_path)
        assert len(history) == 1
        assert history[0].agent == "caroline"
        assert history[0].auto_applied is True


def test_handle_agent_fix_decision_approve():
    with tempfile.TemporaryDirectory() as td:
        pending_path = Path(td) / "pending.json"
        history_path = Path(td) / "fixes.json"
        dec_id = "dec-20260401-abc123"

        import json
        from dataclasses import asdict
        finding = Finding("caroline", "tool_failure", ["x"], 4,
                          "skills/outreach_templates.md", "medium", 0.85)
        proposal = FixProposal(finding, "Add fallback", "+fallback\n", False, "low")
        pending = {
            dec_id: {
                "agent": proposal.finding.agent,
                "component": proposal.finding.affected_component,
                "pattern": proposal.finding.pattern_type,
                "change_summary": proposal.change_summary,
                "diff": proposal.diff,
                "risk": proposal.risk,
            }
        }
        pending_path.write_text(json.dumps(pending))

        skill_path = Path(td) / "outreach_templates.md"
        skill_path.write_text("existing content\n")

        with patch("driftdriver.paia_agent_health.fixes._resolve_skill_path", return_value=skill_path):
            with patch("driftdriver.paia_agent_health.fixes._publish_event"):
                result = handle_agent_fix_decision(
                    dec_id, "yes",
                    pending_path=pending_path, history_path=history_path
                )
        assert result == "applied"
        history = load_history(history_path)
        assert len(history) == 1


def test_handle_agent_fix_decision_skip():
    with tempfile.TemporaryDirectory() as td:
        pending_path = Path(td) / "pending.json"
        import json
        dec_id = "dec-20260401-skip001"
        pending_path.write_text(json.dumps({dec_id: {"agent": "derek", "component": "x",
                                                       "pattern": "y", "change_summary": "z",
                                                       "diff": "", "risk": "low"}}))
        history_path = Path(td) / "fixes.json"
        result = handle_agent_fix_decision(dec_id, "no", pending_path=pending_path, history_path=history_path)
        assert result == "skipped"
