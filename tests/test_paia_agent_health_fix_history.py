# tests/test_paia_agent_health_fix_history.py
# ABOUTME: Tests for FixRecord persistence, add, update, and due-check logic.

from __future__ import annotations
import json
import tempfile
from pathlib import Path
from driftdriver.paia_agent_health.fix_history import (
    FixRecord, add_fix, update_outcome, load_history, save_history, pending_checks
)

def test_add_and_load_roundtrip():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        record = FixRecord(
            fix_id="abc123",
            applied_at="2026-04-01T03:00:00+00:00",
            agent="samantha",
            component="skills/scheduling_tactics.md",
            finding_pattern="tool_failure",
            change_summary="Added retry logic",
            diff="--- a\n+++ b\n@@ -1 +1,2 @@\n line\n+retry",
            auto_applied=True,
            check_after="2026-04-08T03:00:00+00:00",
            outcome=None,
        )
        add_fix(path, record)
        history = load_history(path)
        assert len(history) == 1
        assert history[0].fix_id == "abc123"
        assert history[0].outcome is None

def test_update_outcome():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        record = FixRecord(
            fix_id="xyz789",
            applied_at="2026-04-01T03:00:00+00:00",
            agent="caroline",
            component="skills/outreach_templates.md",
            finding_pattern="behavioral_loop",
            change_summary="Fallback search protocol",
            diff="+fallback",
            auto_applied=False,
            check_after="2026-04-08T03:00:00+00:00",
            outcome=None,
        )
        add_fix(path, record)
        update_outcome(path, "xyz789", "resolved")
        history = load_history(path)
        assert history[0].outcome == "resolved"

def test_pending_checks_returns_due_records():
    with tempfile.NamedTemporaryFile(suffix=".json") as f:
        path = Path(f.name)
        due = FixRecord(
            fix_id="due1",
            applied_at="2026-03-01T00:00:00+00:00",
            agent="samantha",
            component="skills/scheduling_tactics.md",
            finding_pattern="tool_failure",
            change_summary="old fix",
            diff="+x",
            auto_applied=True,
            check_after="2026-03-08T00:00:00+00:00",  # past
            outcome=None,
        )
        not_due = FixRecord(
            fix_id="notdue1",
            applied_at="2026-04-01T00:00:00+00:00",
            agent="derek",
            component="skills/architecture_patterns.md",
            finding_pattern="task_stall",
            change_summary="future fix",
            diff="+y",
            auto_applied=True,
            check_after="2099-01-01T00:00:00+00:00",  # future
            outcome=None,
        )
        add_fix(path, due)
        add_fix(path, not_due)
        due_records = pending_checks(path)
        assert len(due_records) == 1
        assert due_records[0].fix_id == "due1"
