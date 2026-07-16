# ABOUTME: Tests for repeat-ignoring escalation — closes the dead-end where advisory
# ABOUTME: drift findings get flagged and ignored repeatedly without becoming work.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.drift_task_guard import escalate_repeated_findings


def _write_outcomes(path: Path, rows: list[dict]) -> None:
    """Write a drift-outcomes.jsonl ledger from plain dicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            line = {
                "task_id": r.get("task_id", "t"),
                "lane": r["lane"],
                "finding_key": r["finding_key"],
                "recommendation": r.get("recommendation", ""),
                "action_taken": r.get("action_taken", ""),
                "outcome": r["outcome"],
                "evidence": r.get("evidence", []),
                "timestamp": r.get("timestamp", "2026-07-16T00:00:00+00:00"),
                "actor_id": r.get("actor_id", ""),
                "bundle_id": r.get("bundle_id", ""),
            }
            f.write(json.dumps(line) + "\n")


class _CallRecorder:
    """Records guarded_add_drift_task calls; returns 'created' by default."""

    def __init__(self, returns: list[str] | None = None) -> None:
        self.calls: list[dict] = []
        self._returns = list(returns) if returns else []

    def __call__(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self._returns.pop(0) if self._returns else "created"


class TestEscalateRepeatedFindings(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)
        self.ledger = self.wg_dir / "drift-outcomes.jsonl"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _cfg(self, **kw) -> dict:
        base = {"escalate_after_ignores": 3, "escalate_task_prefix": "escalate"}
        base.update(kw)
        return base

    def test_below_threshold_creates_nothing(self) -> None:
        """2 ignores with threshold 3 -> no escalation task."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
        ])
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        self.assertEqual(created, [])
        self.assertEqual(len(rec.calls), 0)

    def test_at_threshold_creates_one_task(self) -> None:
        """3 ignores with threshold 3 -> exactly one escalation task."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
        ])
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        self.assertEqual(len(rec.calls), 1)
        call = rec.calls[0]
        self.assertEqual(call["task_id"], "escalate-coredrift-churn_loc")
        self.assertIn("churn_loc", call["title"])
        self.assertIn("ignored 3", call["title"])
        self.assertEqual(call["lane_tag"], "coredrift")
        self.assertIn("escalation", call["extra_tags"])
        self.assertEqual(created, ["escalate-coredrift-churn_loc"])

    def test_threshold_zero_disables(self) -> None:
        """escalate_after_ignores=0 -> escalation disabled, no tasks."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"}
        ] * 10)
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg(escalate_after_ignores=0)
            )
        self.assertEqual(created, [])
        self.assertEqual(len(rec.calls), 0)

    def test_resolved_outcomes_do_not_trigger(self) -> None:
        """Only 'ignored' counts; resolved/worsened/deferred are ignored for escalation."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "hardening_in_core", "outcome": "resolved"},
            {"lane": "coredrift", "finding_key": "hardening_in_core", "outcome": "resolved"},
            {"lane": "coredrift", "finding_key": "hardening_in_core", "outcome": "resolved"},
            {"lane": "coredrift", "finding_key": "hardening_in_core", "outcome": "deferred"},
        ])
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        self.assertEqual(created, [])

    def test_groups_by_lane_and_finding_key(self) -> None:
        """Distinct (lane, finding_key) pairs escalate independently."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"},
            {"lane": "specdrift", "finding_key": "spec_not_updated", "outcome": "ignored"},
            {"lane": "specdrift", "finding_key": "spec_not_updated", "outcome": "ignored"},
            {"lane": "specdrift", "finding_key": "spec_not_updated", "outcome": "ignored"},
        ])
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        ids = sorted(call["task_id"] for call in rec.calls)
        self.assertEqual(ids, [
            "escalate-coredrift-churn_loc",
            "escalate-specdrift-spec_not_updated",
        ])
        self.assertEqual(len(created), 2)

    def test_dedup_via_guard_no_duplicate(self) -> None:
        """If guarded_add_drift_task returns 'existing', it is not reported as created."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "churn_loc", "outcome": "ignored"}
        ] * 3)
        rec = _CallRecorder(returns=["existing"])
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        self.assertEqual(len(rec.calls), 1)  # still attempted
        self.assertEqual(created, [])  # but not newly created

    def test_no_ledger_returns_empty(self) -> None:
        """Missing drift-outcomes.jsonl -> empty list, no crash."""
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            created = escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        self.assertEqual(created, [])
        self.assertEqual(len(rec.calls), 0)

    def test_finding_key_sanitized_in_task_id(self) -> None:
        """finding_key with ':' or whitespace is sanitized for a valid task id."""
        _write_outcomes(self.ledger, [
            {"lane": "coredrift", "finding_key": "post-completion-drift:abc 123", "outcome": "ignored"}
        ] * 3)
        rec = _CallRecorder()
        with patch("driftdriver.drift_task_guard.guarded_add_drift_task", side_effect=rec):
            escalate_repeated_findings(
                wg_dir=self.wg_dir, enforcement_cfg=self._cfg()
            )
        self.assertEqual(
            rec.calls[0]["task_id"],
            "escalate-coredrift-post-completion-drift-abc-123",
        )


if __name__ == "__main__":
    unittest.main()
