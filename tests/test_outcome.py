# ABOUTME: Tests for drift outcome feedback schema and JSONL ledger.
# ABOUTME: Covers serialization, read/write, querying, and rate calculation.

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from driftdriver.outcome import (
    OUTCOME_VALUES,
    DriftOutcome,
    outcome_rates,
    query_outcomes,
    read_outcomes,
    write_outcome,
)


def _make_outcome(
    *,
    task_id: str = "task-1",
    lane: str = "coredrift",
    finding_key: str = "scope-creep",
    recommendation: str = "Reduce scope to match contract",
    action_taken: str = "Split into two tasks",
    outcome: str = "resolved",
    evidence: list[str] | None = None,
    timestamp: datetime | None = None,
) -> DriftOutcome:
    return DriftOutcome(
        task_id=task_id,
        lane=lane,
        finding_key=finding_key,
        recommendation=recommendation,
        action_taken=action_taken,
        outcome=outcome,
        evidence=evidence if evidence is not None else ["commit abc123"],
        timestamp=timestamp or datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestDriftOutcomeCreation:
    def test_create_valid_outcome(self) -> None:
        o = _make_outcome()
        assert o.task_id == "task-1"
        assert o.lane == "coredrift"
        assert o.finding_key == "scope-creep"
        assert o.recommendation == "Reduce scope to match contract"
        assert o.action_taken == "Split into two tasks"
        assert o.outcome == "resolved"
        assert o.evidence == ["commit abc123"]
        assert o.timestamp == datetime(2026, 3, 6, 12, 0, 0, tzinfo=timezone.utc)

    def test_all_valid_outcome_values(self) -> None:
        for value in OUTCOME_VALUES:
            o = _make_outcome(outcome=value)
            assert o.outcome == value

    def test_invalid_outcome_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="invalid outcome"):
            _make_outcome(outcome="banana")

    def test_empty_evidence_list(self) -> None:
        o = _make_outcome(evidence=[])
        assert o.evidence == []


class TestWriteOutcome:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        o = _make_outcome()
        write_outcome(ledger, o)
        assert ledger.exists()

    def test_write_appends(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(task_id="t1"))
        write_outcome(ledger, _make_outcome(task_id="t2"))
        lines = ledger.read_text().strip().splitlines()
        assert len(lines) == 2


class TestReadOutcomes:
    def test_round_trip(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        originals = [
            _make_outcome(task_id="t1", outcome="resolved"),
            _make_outcome(task_id="t2", outcome="ignored"),
            _make_outcome(task_id="t3", outcome="worsened"),
        ]
        for o in originals:
            write_outcome(ledger, o)

        loaded = read_outcomes(ledger)
        assert len(loaded) == 3
        for orig, got in zip(originals, loaded):
            assert got.task_id == orig.task_id
            assert got.lane == orig.lane
            assert got.finding_key == orig.finding_key
            assert got.recommendation == orig.recommendation
            assert got.action_taken == orig.action_taken
            assert got.outcome == orig.outcome
            assert got.evidence == orig.evidence
            assert got.timestamp == orig.timestamp

    def test_empty_file(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        ledger.write_text("")
        assert read_outcomes(ledger) == []

    def test_missing_file(self, tmp_path: Path) -> None:
        ledger = tmp_path / "nonexistent.jsonl"
        assert read_outcomes(ledger) == []


class TestQueryOutcomes:
    def test_filter_by_lane(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(lane="coredrift", task_id="t1"))
        write_outcome(ledger, _make_outcome(lane="specdrift", task_id="t2"))
        write_outcome(ledger, _make_outcome(lane="coredrift", task_id="t3"))

        results = query_outcomes(ledger, lane="coredrift")
        assert len(results) == 2
        assert all(r.lane == "coredrift" for r in results)

    def test_filter_by_task_id(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(task_id="t1"))
        write_outcome(ledger, _make_outcome(task_id="t2"))
        write_outcome(ledger, _make_outcome(task_id="t1"))

        results = query_outcomes(ledger, task_id="t1")
        assert len(results) == 2
        assert all(r.task_id == "t1" for r in results)

    def test_filter_by_both(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(task_id="t1", lane="coredrift"))
        write_outcome(ledger, _make_outcome(task_id="t1", lane="specdrift"))
        write_outcome(ledger, _make_outcome(task_id="t2", lane="coredrift"))

        results = query_outcomes(ledger, lane="coredrift", task_id="t1")
        assert len(results) == 1
        assert results[0].task_id == "t1"
        assert results[0].lane == "coredrift"

    def test_no_filters_returns_all(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(task_id="t1"))
        write_outcome(ledger, _make_outcome(task_id="t2"))

        results = query_outcomes(ledger)
        assert len(results) == 2


class TestOutcomeRates:
    def test_basic_rates(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(outcome="resolved"))
        write_outcome(ledger, _make_outcome(outcome="resolved"))
        write_outcome(ledger, _make_outcome(outcome="ignored"))
        write_outcome(ledger, _make_outcome(outcome="worsened"))

        rates = outcome_rates(ledger)
        assert rates["resolved"] == pytest.approx(0.5)
        assert rates["ignored"] == pytest.approx(0.25)
        assert rates["worsened"] == pytest.approx(0.25)
        assert rates["deferred"] == pytest.approx(0.0)

    def test_rates_with_lane_filter(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        write_outcome(ledger, _make_outcome(lane="coredrift", outcome="resolved"))
        write_outcome(ledger, _make_outcome(lane="coredrift", outcome="ignored"))
        write_outcome(ledger, _make_outcome(lane="specdrift", outcome="worsened"))

        rates = outcome_rates(ledger, lane="coredrift")
        assert rates["resolved"] == pytest.approx(0.5)
        assert rates["ignored"] == pytest.approx(0.5)
        assert rates["worsened"] == pytest.approx(0.0)
        assert rates["deferred"] == pytest.approx(0.0)

    def test_empty_returns_all_zeros(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        ledger.write_text("")

        rates = outcome_rates(ledger)
        for v in OUTCOME_VALUES:
            assert rates[v] == pytest.approx(0.0)
