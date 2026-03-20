# ABOUTME: Integration test — evaluations written by wg_eval_bridge are read by agency_score_reader.
# ABOUTME: Confirms the full inbound pipeline works end-to-end with real files.
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from driftdriver.agency_score_reader import read_agency_eval_score
from driftdriver.wg_eval_bridge import write_evaluation


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    """Evaluations written by wg_eval_bridge are readable by agency_score_reader."""
    evaluation = {
        "id": "eval-test-001",
        "task_id": "task-1",
        "role_id": "role-a",
        "tradeoff_id": "unknown",
        "score": 0.75,
        "dimensions": {"correctness": 0.75},
        "notes": "test",
        "evaluator": "speedrift:coredrift",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "drift",
    }
    write_evaluation(tmp_path, evaluation)

    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(75.0)


def test_multiple_evals_averaged(tmp_path: Path) -> None:
    """Multiple evaluations are averaged into a single score."""
    ts = datetime.now(timezone.utc).isoformat()
    for i, (eval_id, score) in enumerate([("a", 0.5), ("b", 1.0), ("c", 0.75)]):
        write_evaluation(tmp_path, {
            "id": eval_id,
            "task_id": f"task-{i}",
            "role_id": "role-a",
            "tradeoff_id": "unknown",
            "score": score,
            "dimensions": {},
            "notes": "",
            "evaluator": "speedrift:coredrift",
            "timestamp": ts,
            "source": "drift",
        })

    result = read_agency_eval_score(tmp_path)
    assert result == pytest.approx((0.5 + 1.0 + 0.75) / 3 * 100, abs=0.1)
