# ABOUTME: Tests for the ecosystem intelligence dashboard API endpoints
# ABOUTME: Verifies briefing, inbox actions, decision log, and trends against a real Postgres database

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

psycopg = __import__("pytest").importorskip("psycopg")
from psycopg import connect, sql

from driftdriver.intelligence.db import PostgresConfig, ensure_database_and_apply_migrations
from driftdriver.intelligence.models import Signal
from driftdriver.intelligence.store import insert_signals
from driftdriver.ecosystem_hub.intelligence_api import (
    build_briefing,
    build_briefing_history,
    build_inbox,
    approve_signal,
    override_signal,
    snooze_signal,
    batch_approve_signals,
    build_decision_log,
    build_decision_trends,
)


TEST_DB = f"test_intelligence_dashboard_{uuid4().hex[:8]}"


def _drop_database(config: PostgresConfig) -> None:
    with connect(
        **config.connection_kwargs(database=config.maintenance_database),
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                  FROM pg_stat_activity
                 WHERE datname = %s
                   AND pid <> pg_backend_pid()
                """,
                (config.database,),
            )
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(config.database),
                )
            )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _signal(
    *,
    source_type: str = "github",
    source_id: str | None = None,
    signal_type: str = "repo_update",
    title: str = "Test signal",
    detected_at: datetime | None = None,
    evaluated_at: datetime | None = None,
    decision: str | None = None,
    decision_reason: str | None = None,
    decision_confidence: float | None = None,
    decided_by: str | None = None,
    acted_on: bool = False,
    action_log: list | None = None,
    vetoed_at: datetime | None = None,
    veto_reason: str | None = None,
) -> Signal:
    return Signal(
        id=uuid4(),
        source_type=source_type,
        source_id=source_id or uuid4().hex[:12],
        signal_type=signal_type,
        title=title,
        raw_payload={"test": True},
        detected_at=detected_at or _now(),
        evaluated_at=evaluated_at,
        decision=decision,
        decision_reason=decision_reason,
        decision_confidence=decision_confidence,
        decided_by=decided_by,
        acted_on=acted_on,
        action_log=action_log or [],
        vetoed_at=vetoed_at,
        veto_reason=veto_reason,
    )


class TestIntelligenceDashboardAPI(unittest.TestCase):
    config: PostgresConfig

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = PostgresConfig(database=TEST_DB)
        ensure_database_and_apply_migrations(cls.config)

    @classmethod
    def tearDownClass(cls) -> None:
        _drop_database(cls.config)

    def setUp(self) -> None:
        with connect(**self.config.connection_kwargs()) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM signals")
                cur.execute("DELETE FROM evaluation_runs")
                cur.execute("DELETE FROM source_configs")
            conn.commit()

    def test_briefing_empty(self) -> None:
        result = build_briefing(self.config)
        self.assertIn("stats", result)
        self.assertEqual(result["stats"]["total_signals"], 0)

    def test_briefing_with_signals(self) -> None:
        now = _now()
        signals = [
            _signal(
                title="Evaluated signal",
                detected_at=now - timedelta(hours=1),
                evaluated_at=now,
                decision="skip",
                decision_confidence=0.95,
                decided_by="gpt-4o-mini",
                acted_on=True,
                action_log=[{"action": "skip", "status": "completed"}],
            ),
            _signal(
                title="Inbox signal",
                detected_at=now - timedelta(hours=2),
                evaluated_at=now,
                decision="adopt",
                decision_confidence=0.7,
                decided_by="gpt-4o-mini",
                acted_on=False,
                action_log=[{"action": "inbox", "status": "pending_human"}],
            ),
            _signal(title="Unevaluated signal", detected_at=now),
        ]
        insert_signals(self.config, signals)
        result = build_briefing(self.config)
        self.assertEqual(result["stats"]["total_signals"], 3)
        self.assertEqual(result["stats"]["evaluated_today"], 2)
        self.assertEqual(result["stats"]["auto_decided"], 1)
        self.assertEqual(result["stats"]["escalated"], 1)

    def test_briefing_history(self) -> None:
        result = build_briefing_history(self.config)
        self.assertIn("days", result)
        self.assertIsInstance(result["days"], list)

    def test_inbox_returns_pending_signals(self) -> None:
        now = _now()
        inbox_signal = _signal(
            title="Needs review",
            evaluated_at=now,
            decision="adopt",
            decision_confidence=0.7,
            decided_by="gpt-4o-mini",
            acted_on=False,
            action_log=[{"action": "inbox", "status": "pending_human"}],
        )
        acted_signal = _signal(
            title="Already acted",
            evaluated_at=now,
            decision="skip",
            decision_confidence=0.95,
            decided_by="gpt-4o-mini",
            acted_on=True,
        )
        insert_signals(self.config, [inbox_signal, acted_signal])
        result = build_inbox(self.config)
        self.assertEqual(len(result["signals"]), 1)
        self.assertEqual(result["signals"][0]["title"], "Needs review")

    def test_approve_signal(self) -> None:
        now = _now()
        sig = _signal(
            title="To approve",
            evaluated_at=now,
            decision="adopt",
            decision_confidence=0.7,
            decided_by="gpt-4o-mini",
            acted_on=False,
            action_log=[{"action": "inbox", "status": "pending_human"}],
        )
        insert_signals(self.config, [sig])
        result = approve_signal(self.config, signal_id=sig.id)
        self.assertEqual(result["status"], "approved")
        inbox = build_inbox(self.config)
        self.assertEqual(len(inbox["signals"]), 0)

    def test_override_signal(self) -> None:
        now = _now()
        sig = _signal(
            title="To override",
            evaluated_at=now,
            decision="adopt",
            decision_confidence=0.7,
            decided_by="gpt-4o-mini",
            acted_on=False,
        )
        insert_signals(self.config, [sig])
        result = override_signal(
            self.config,
            signal_id=sig.id,
            new_decision="skip",
            reason="Not relevant to our stack",
        )
        self.assertEqual(result["status"], "overridden")
        self.assertEqual(result["decision"], "skip")

    def test_snooze_signal(self) -> None:
        now = _now()
        sig = _signal(
            title="To snooze",
            evaluated_at=now,
            decision="defer",
            decision_confidence=0.6,
            decided_by="gpt-4o-mini",
            acted_on=False,
        )
        insert_signals(self.config, [sig])
        result = snooze_signal(self.config, signal_id=sig.id)
        self.assertEqual(result["status"], "snoozed")

    def test_batch_approve(self) -> None:
        now = _now()
        signals = [
            _signal(
                title=f"Batch item {i}",
                evaluated_at=now,
                decision="skip",
                decision_confidence=0.85,
                decided_by="gpt-4o-mini",
                acted_on=False,
                action_log=[{"action": "inbox", "status": "pending_human"}],
            )
            for i in range(3)
        ]
        insert_signals(self.config, signals)
        result = batch_approve_signals(self.config)
        self.assertEqual(result["approved_count"], 3)
        inbox = build_inbox(self.config)
        self.assertEqual(len(inbox["signals"]), 0)

    def test_decision_log(self) -> None:
        now = _now()
        signals = [
            _signal(
                title="Decided signal",
                source_type="github",
                evaluated_at=now,
                decision="skip",
                decision_confidence=0.9,
                decided_by="gpt-4o-mini",
                acted_on=True,
            ),
            _signal(
                title="Vibez signal",
                source_type="vibez",
                evaluated_at=now,
                decision="watch",
                decision_confidence=0.85,
                decided_by="gpt-4o-mini",
                acted_on=True,
            ),
        ]
        insert_signals(self.config, signals)
        result = build_decision_log(self.config)
        self.assertEqual(len(result["decisions"]), 2)

    def test_decision_log_filters(self) -> None:
        now = _now()
        signals = [
            _signal(
                title="GitHub skip",
                source_type="github",
                evaluated_at=now,
                decision="skip",
                decision_confidence=0.9,
                decided_by="gpt-4o-mini",
                acted_on=True,
            ),
            _signal(
                title="Vibez adopt",
                source_type="vibez",
                evaluated_at=now,
                decision="adopt",
                decision_confidence=0.92,
                decided_by="claude-haiku",
                acted_on=True,
            ),
        ]
        insert_signals(self.config, signals)
        result = build_decision_log(self.config, source_type="github")
        self.assertEqual(len(result["decisions"]), 1)
        self.assertEqual(result["decisions"][0]["source_type"], "github")

        result = build_decision_log(self.config, decision="adopt")
        self.assertEqual(len(result["decisions"]), 1)
        self.assertEqual(result["decisions"][0]["decision"], "adopt")

    def test_decision_trends(self) -> None:
        now = _now()
        signals = [
            _signal(
                title=f"Trend signal {i}",
                evaluated_at=now - timedelta(days=i),
                decision="skip" if i % 2 == 0 else "watch",
                decision_confidence=0.9,
                decided_by="gpt-4o-mini",
                acted_on=True,
            )
            for i in range(5)
        ]
        insert_signals(self.config, signals)
        result = build_decision_trends(self.config)
        self.assertIn("daily", result)
        self.assertIsInstance(result["daily"], list)
        self.assertGreater(len(result["daily"]), 0)

    def test_approve_nonexistent_signal(self) -> None:
        result = approve_signal(self.config, signal_id=uuid4())
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
