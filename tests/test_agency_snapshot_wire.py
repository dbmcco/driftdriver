# ABOUTME: Tests for agency_eval_inputs wiring — verifies score reader produces hub-compatible values.
# ABOUTME: Snapshot integration is exercised via the end-to-end test in test_agency_adapter_integration.py.
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from driftdriver.agency_score_reader import read_agency_eval_score


def test_agency_eval_inputs_in_snapshot(tmp_path: Path) -> None:
    """agency_eval_inputs should appear in snapshot when evaluations exist."""
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    ts = datetime.now(timezone.utc).isoformat()
    (evals_dir / "eval-1.json").write_text(
        json.dumps({"id": "eval-1", "score": 0.8, "timestamp": ts})
    )

    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(80.0)
    # The score must be in 0-100 range for northstardrift blending
    assert 0.0 <= score <= 100.0


def test_no_evals_returns_none_for_hub(tmp_path: Path) -> None:
    """When no evaluations exist, score reader returns None — hub should preserve that."""
    assert read_agency_eval_score(tmp_path) is None
