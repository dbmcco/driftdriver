# ABOUTME: Tests for agency_score_reader — rolling evaluation score aggregation.
# ABOUTME: Uses real fixture files; no mocks.

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from driftdriver.agency_score_reader import read_agency_eval_score


def _write_eval(evals_dir: Path, eval_id: str, score: float, age_days: float = 0.0) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    data = {"id": eval_id, "score": score, "timestamp": ts}
    (evals_dir / f"{eval_id}.json").write_text(json.dumps(data))


def test_no_evaluations_dir_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert read_agency_eval_score(repo) is None


def test_empty_evaluations_dir_returns_none(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    assert read_agency_eval_score(tmp_path) is None


def test_single_perfect_score(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-1", 1.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(100.0)


def test_single_zero_score(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-1", 0.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(0.0)


def test_average_of_multiple_scores(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-1", 0.8)
    _write_eval(evals_dir, "eval-2", 0.6)
    _write_eval(evals_dir, "eval-3", 1.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx((0.8 + 0.6 + 1.0) / 3 * 100, abs=0.1)


def test_old_evaluations_excluded(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-fresh", 1.0, age_days=1.0)
    _write_eval(evals_dir, "eval-old", 0.0, age_days=10.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(100.0)


def test_malformed_json_skipped(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    (evals_dir / "bad.json").write_text("not json{{{")
    _write_eval(evals_dir, "eval-good", 0.5)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(50.0)


def test_custom_window_days(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-2day", 1.0, age_days=2.0)
    _write_eval(evals_dir, "eval-4day", 0.0, age_days=4.0)
    score = read_agency_eval_score(tmp_path, window_days=3)
    assert score == pytest.approx(100.0)
