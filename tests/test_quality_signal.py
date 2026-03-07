# ABOUTME: Tests for per-actor quality signal computation from drift outcomes.
# ABOUTME: Covers rate calculation, scoring, trends, budget modifiers, and briefing format.

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from driftdriver.outcome import DriftOutcome, write_outcome
from driftdriver.quality_signal import (
    ActorQuality,
    compute_actor_quality,
    compute_all_actor_qualities,
    format_quality_briefing,
    quality_budget_modifier,
)


def _make_outcome(
    *,
    actor_id: str = "agent-1",
    task_id: str = "task-1",
    lane: str = "coredrift",
    outcome: str = "resolved",
    timestamp: datetime | None = None,
) -> DriftOutcome:
    return DriftOutcome(
        task_id=task_id,
        lane=lane,
        finding_key="scope-creep",
        recommendation="Reduce scope",
        action_taken="Split task",
        outcome=outcome,
        evidence=["commit abc"],
        timestamp=timestamp or datetime.now(timezone.utc),
        actor_id=actor_id,
    )


def _write_outcomes(
    ledger: Path,
    outcomes: list[DriftOutcome],
) -> None:
    for o in outcomes:
        write_outcome(ledger, o)


class TestComputeActorQualityMixed:
    """compute_actor_quality with mixed outcomes returns correct rates and score."""

    def test_mixed_outcomes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        _write_outcomes(
            ledger,
            [
                _make_outcome(outcome="resolved", timestamp=now),
                _make_outcome(outcome="resolved", timestamp=now),
                _make_outcome(outcome="resolved", timestamp=now),
                _make_outcome(outcome="ignored", timestamp=now),
                _make_outcome(outcome="worsened", timestamp=now),
            ],
        )

        quality = compute_actor_quality(ledger, "agent-1")

        assert quality.total_outcomes == 5
        assert quality.resolved_rate == pytest.approx(0.6)
        assert quality.ignored_rate == pytest.approx(0.2)
        assert quality.worsened_rate == pytest.approx(0.2)
        assert quality.deferred_rate == pytest.approx(0.0)
        # score = 0.6 - (2 * 0.2) - (0.5 * 0.2) = 0.6 - 0.4 - 0.1 = 0.1
        assert quality.quality_score == pytest.approx(0.1)


class TestComputeActorQualityNoOutcomes:
    """compute_actor_quality with no outcomes returns zero-score."""

    def test_no_outcomes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        ledger.write_text("")

        quality = compute_actor_quality(ledger, "agent-1")

        assert quality.total_outcomes == 0
        assert quality.resolved_rate == pytest.approx(0.0)
        assert quality.ignored_rate == pytest.approx(0.0)
        assert quality.worsened_rate == pytest.approx(0.0)
        assert quality.deferred_rate == pytest.approx(0.0)
        assert quality.quality_score == pytest.approx(0.0)
        assert quality.trend == "stable"


class TestComputeActorQualityAllResolved:
    """compute_actor_quality with all resolved returns score near 1.0."""

    def test_all_resolved(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        _write_outcomes(
            ledger,
            [_make_outcome(outcome="resolved", timestamp=now) for _ in range(10)],
        )

        quality = compute_actor_quality(ledger, "agent-1")

        assert quality.total_outcomes == 10
        assert quality.resolved_rate == pytest.approx(1.0)
        # score = 1.0 - 0 - 0 = 1.0
        assert quality.quality_score == pytest.approx(1.0)


class TestComputeActorQualityAllWorsened:
    """compute_actor_quality with all worsened returns score 0.0."""

    def test_all_worsened(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        _write_outcomes(
            ledger,
            [_make_outcome(outcome="worsened", timestamp=now) for _ in range(10)],
        )

        quality = compute_actor_quality(ledger, "agent-1")

        assert quality.total_outcomes == 10
        assert quality.worsened_rate == pytest.approx(1.0)
        # score = 0 - (2 * 1.0) - 0 = -2.0 -> clamped to 0.0
        assert quality.quality_score == pytest.approx(0.0)


class TestQualityBudgetModifier:
    """quality_budget_modifier returns correct multiplier tiers."""

    def test_high_quality(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.9, ignored_rate=0.1, worsened_rate=0.0,
            deferred_rate=0.0, quality_score=0.85, trend="stable",
        )
        assert quality_budget_modifier(q) == 1.5

    def test_normal_quality(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.7, ignored_rate=0.2, worsened_rate=0.1,
            deferred_rate=0.0, quality_score=0.65, trend="stable",
        )
        assert quality_budget_modifier(q) == 1.0

    def test_reduced_quality(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.5, ignored_rate=0.3, worsened_rate=0.2,
            deferred_rate=0.0, quality_score=0.45, trend="declining",
        )
        assert quality_budget_modifier(q) == 0.75

    def test_low_quality(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.2, ignored_rate=0.3, worsened_rate=0.5,
            deferred_rate=0.0, quality_score=0.1, trend="declining",
        )
        assert quality_budget_modifier(q) == 0.5

    def test_boundary_08(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.8, ignored_rate=0.2, worsened_rate=0.0,
            deferred_rate=0.0, quality_score=0.8, trend="stable",
        )
        assert quality_budget_modifier(q) == 1.5

    def test_boundary_06(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.6, ignored_rate=0.2, worsened_rate=0.1,
            deferred_rate=0.1, quality_score=0.6, trend="stable",
        )
        assert quality_budget_modifier(q) == 1.0

    def test_boundary_04(self) -> None:
        q = ActorQuality(
            actor_id="a", actor_class="", total_outcomes=10,
            resolved_rate=0.4, ignored_rate=0.3, worsened_rate=0.3,
            deferred_rate=0.0, quality_score=0.4, trend="stable",
        )
        assert quality_budget_modifier(q) == 0.75


class TestFormatQualityBriefing:
    """format_quality_briefing includes score, rates, and trend."""

    def test_briefing_contains_key_info(self) -> None:
        q = ActorQuality(
            actor_id="agent-1", actor_class="claude", total_outcomes=85,
            resolved_rate=0.7, ignored_rate=0.2, worsened_rate=0.1,
            deferred_rate=0.0, quality_score=0.72, trend="stable",
        )
        briefing = format_quality_briefing(q)

        assert "0.72" in briefing
        assert "stable" in briefing
        assert "85 outcomes" in briefing
        assert "70% resolved" in briefing
        assert "20% ignored" in briefing
        assert "10% worsened" in briefing

    def test_briefing_high_resolved_commentary(self) -> None:
        q = ActorQuality(
            actor_id="agent-1", actor_class="", total_outcomes=20,
            resolved_rate=0.8, ignored_rate=0.1, worsened_rate=0.1,
            deferred_rate=0.0, quality_score=0.85, trend="improving",
        )
        briefing = format_quality_briefing(q)
        assert "frequently resolved" in briefing

    def test_briefing_high_ignored_commentary(self) -> None:
        q = ActorQuality(
            actor_id="agent-1", actor_class="", total_outcomes=20,
            resolved_rate=0.3, ignored_rate=0.5, worsened_rate=0.1,
            deferred_rate=0.1, quality_score=0.3, trend="declining",
        )
        briefing = format_quality_briefing(q)
        assert "ignored" in briefing.lower()


class TestComputeAllActorQualities:
    """compute_all_actor_qualities groups by actor_id."""

    def test_groups_by_actor(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        _write_outcomes(
            ledger,
            [
                _make_outcome(actor_id="agent-1", outcome="resolved", timestamp=now),
                _make_outcome(actor_id="agent-1", outcome="resolved", timestamp=now),
                _make_outcome(actor_id="agent-2", outcome="worsened", timestamp=now),
                _make_outcome(actor_id="agent-2", outcome="ignored", timestamp=now),
            ],
        )

        qualities = compute_all_actor_qualities(ledger)

        assert len(qualities) == 2
        by_actor = {q.actor_id: q for q in qualities}

        assert by_actor["agent-1"].total_outcomes == 2
        assert by_actor["agent-1"].resolved_rate == pytest.approx(1.0)
        assert by_actor["agent-1"].quality_score == pytest.approx(1.0)

        assert by_actor["agent-2"].total_outcomes == 2
        assert by_actor["agent-2"].worsened_rate == pytest.approx(0.5)
        assert by_actor["agent-2"].ignored_rate == pytest.approx(0.5)
        # score = 0 - (2*0.5) - (0.5*0.5) = -1.25 -> clamped to 0.0
        assert by_actor["agent-2"].quality_score == pytest.approx(0.0)


class TestTrendDetection:
    """Trend detection: recent improvement shows 'improving'."""

    def test_improving_trend(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)

        # Older outcomes (8-20 days ago): mostly worsened
        older_outcomes = []
        for i in range(10):
            older_outcomes.append(
                _make_outcome(
                    outcome="worsened",
                    timestamp=now - timedelta(days=20 - i),
                )
            )

        # Recent outcomes (last 5 days): all resolved
        recent_outcomes = []
        for i in range(5):
            recent_outcomes.append(
                _make_outcome(
                    outcome="resolved",
                    timestamp=now - timedelta(days=5 - i),
                )
            )

        _write_outcomes(ledger, older_outcomes + recent_outcomes)

        quality = compute_actor_quality(ledger, "agent-1")
        assert quality.trend == "improving"

    def test_declining_trend(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)

        # Older outcomes (8-20 days ago): all resolved
        older_outcomes = []
        for i in range(10):
            older_outcomes.append(
                _make_outcome(
                    outcome="resolved",
                    timestamp=now - timedelta(days=20 - i),
                )
            )

        # Recent outcomes (last 5 days): all worsened
        recent_outcomes = []
        for i in range(5):
            recent_outcomes.append(
                _make_outcome(
                    outcome="worsened",
                    timestamp=now - timedelta(days=5 - i),
                )
            )

        _write_outcomes(ledger, older_outcomes + recent_outcomes)

        quality = compute_actor_quality(ledger, "agent-1")
        assert quality.trend == "declining"

    def test_stable_trend_with_insufficient_data(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)

        # Only one recent outcome — not enough for trend
        _write_outcomes(
            ledger,
            [_make_outcome(outcome="resolved", timestamp=now)],
        )

        quality = compute_actor_quality(ledger, "agent-1")
        assert quality.trend == "stable"


class TestBackwardCompatibility:
    """Outcomes without actor_id group as 'unknown'."""

    def test_missing_actor_id_groups_as_unknown(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)

        # Write outcomes without actor_id (simulating old format)
        _write_outcomes(
            ledger,
            [
                _make_outcome(actor_id="", outcome="resolved", timestamp=now),
                _make_outcome(actor_id="", outcome="ignored", timestamp=now),
                _make_outcome(actor_id="agent-1", outcome="resolved", timestamp=now),
            ],
        )

        qualities = compute_all_actor_qualities(ledger)

        by_actor = {q.actor_id: q for q in qualities}
        assert "unknown" in by_actor
        assert by_actor["unknown"].total_outcomes == 2
        assert by_actor["agent-1"].total_outcomes == 1

    def test_old_format_without_actor_id_field(self, tmp_path: Path) -> None:
        """Old JSONL entries missing actor_id key entirely still parse."""
        ledger = tmp_path / "outcomes.jsonl"
        import json

        old_entry = {
            "task_id": "task-1",
            "lane": "coredrift",
            "finding_key": "scope-creep",
            "recommendation": "Reduce scope",
            "action_taken": "Split task",
            "outcome": "resolved",
            "evidence": ["commit abc"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        ledger.write_text(json.dumps(old_entry) + "\n")

        from driftdriver.outcome import read_outcomes

        outcomes = read_outcomes(ledger)
        assert len(outcomes) == 1
        assert outcomes[0].actor_id == ""

        # And it groups as unknown in all-actors
        qualities = compute_all_actor_qualities(ledger)
        assert len(qualities) == 1
        assert qualities[0].actor_id == "unknown"
