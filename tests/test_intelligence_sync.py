# ABOUTME: Tests the ecosystem intelligence sync orchestrator against a real Postgres database
# ABOUTME: Verifies parallel adapter execution, signal dedupe, run metadata, and isolated adapter failures

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from uuid import uuid4

psycopg = __import__("pytest").importorskip("psycopg")
from psycopg import connect, sql

from driftdriver.intelligence.db import PostgresConfig
from driftdriver.intelligence.models import Signal
from driftdriver.intelligence.store import load_source_config, upsert_source_config
from driftdriver.intelligence.sync import EvaluationHandoffResult, run_sync


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


def _signal(*, source_type: str, source_id: str, signal_type: str, title: str) -> Signal:
    return Signal(
        source_type=source_type,
        source_id=source_id,
        signal_type=signal_type,
        title=title,
        raw_payload={"source_id": source_id, "signal_type": signal_type},
        detected_at=datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc),
    )


def _signal_count(config: PostgresConfig) -> int:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM signals")
            return int(cur.fetchone()[0])


def _latest_evaluation_run(config: PostgresConfig) -> tuple[list[str], int, int, int, str | None, int, dict]:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source_types, signals_created, signals_evaluated, escalated, llm_model, llm_tokens_used, auto_decisions
                  FROM evaluation_runs
                 ORDER BY run_at DESC
                 LIMIT 1
                """
            )
            row = cur.fetchone()
    assert row is not None
    source_types, signals_created, signals_evaluated, escalated, llm_model, llm_tokens_used, auto_decisions = row
    return (
        list(source_types),
        int(signals_created),
        int(signals_evaluated),
        int(escalated),
        str(llm_model) if llm_model else None,
        int(llm_tokens_used),
        dict(auto_decisions),
    )


class _StaticAdapter:
    def __init__(self, signals: list[Signal]) -> None:
        self._signals = list(signals)

    def sync(self, _config: dict, _last_synced_at):
        return list(self._signals)

    def health_check(self) -> dict[str, object]:
        return {"ok": True}


class _FailingAdapter:
    def __init__(self, message: str) -> None:
        self._message = message

    def sync(self, _config: dict, _last_synced_at):
        raise RuntimeError(self._message)

    def health_check(self) -> dict[str, object]:
        return {"ok": False, "error": self._message}


class TestIntelligenceSync(unittest.TestCase):
    def test_run_sync_persists_signals_updates_source_state_and_records_run(self) -> None:
        database = f"ecosystem_intelligence_sync_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        try:
            upsert_source_config(postgres_config, source_type="github", config={"wg_dir": "/tmp/wg"})
            upsert_source_config(postgres_config, source_type="vibez", config={"api_endpoint": "http://localhost:3100"})

            adapter_factories = {
                "github": lambda: _StaticAdapter(
                    [_signal(source_type="github", source_id="repo@abc123", signal_type="repo_update", title="Repo update")]
                ),
                "vibez": lambda: _StaticAdapter(
                    [_signal(source_type="vibez", source_id="msg-1", signal_type="hot_alert", title="Hot alert")]
                ),
            }

            def evaluator(_config: PostgresConfig, signals: list[Signal]) -> EvaluationHandoffResult:
                return EvaluationHandoffResult(
                    signals_evaluated=len(signals),
                    escalated=1,
                    auto_decisions={"watch": 1, "adopt": 1},
                    llm_model="fake-evaluator",
                    llm_tokens_used=42,
                    metadata={"status": "ok"},
                )

            summary = asyncio.run(
                run_sync(
                    postgres_config,
                    adapter_factories=adapter_factories,
                    evaluator=evaluator,
                )
            )

            self.assertEqual(summary.source_types, ("github", "vibez"))
            self.assertEqual(summary.signals_seen, 2)
            self.assertEqual(summary.signals_created, 2)
            self.assertEqual(summary.signals_evaluated, 2)
            self.assertEqual(summary.escalated, 1)
            self.assertEqual(summary.llm_model, "fake-evaluator")
            self.assertEqual(summary.llm_tokens_used, 42)
            self.assertEqual(_signal_count(postgres_config), 2)

            github_config = load_source_config(postgres_config, "github")
            vibez_config = load_source_config(postgres_config, "vibez")
            self.assertIsNotNone(github_config)
            self.assertIsNotNone(vibez_config)
            assert github_config is not None
            assert vibez_config is not None
            self.assertIsNotNone(github_config.last_synced_at)
            self.assertIsNotNone(vibez_config.last_synced_at)

            run = _latest_evaluation_run(postgres_config)
            self.assertEqual(run[0], ["github", "vibez"])
            self.assertEqual(run[1], 2)
            self.assertEqual(run[2], 2)
            self.assertEqual(run[3], 1)
            self.assertEqual(run[4], "fake-evaluator")
            self.assertEqual(run[5], 42)
            self.assertEqual(run[6]["sync"]["adapter_results"]["github"]["signals_created"], 1)
            self.assertEqual(run[6]["sync"]["adapter_results"]["vibez"]["signals_created"], 1)
            self.assertEqual(run[6]["evaluation"]["auto_decisions"], {"watch": 1, "adopt": 1})
        finally:
            _drop_database(postgres_config)

    def test_run_sync_dedupes_existing_signals(self) -> None:
        database = f"ecosystem_intelligence_sync_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        try:
            upsert_source_config(postgres_config, source_type="github", config={"wg_dir": "/tmp/wg"})
            adapter_factories = {
                "github": lambda: _StaticAdapter(
                    [_signal(source_type="github", source_id="repo@abc123", signal_type="repo_update", title="Repo update")]
                )
            }

            def evaluator(_config: PostgresConfig, _signals: list[Signal]) -> EvaluationHandoffResult:
                return EvaluationHandoffResult(metadata={"status": "noop"})

            first = asyncio.run(run_sync(postgres_config, adapter_factories=adapter_factories, evaluator=evaluator))
            second = asyncio.run(run_sync(postgres_config, adapter_factories=adapter_factories, evaluator=evaluator))

            self.assertEqual(first.signals_created, 1)
            self.assertEqual(second.signals_created, 0)
            self.assertEqual(_signal_count(postgres_config), 1)
        finally:
            _drop_database(postgres_config)

    def test_run_sync_isolates_adapter_failures(self) -> None:
        database = f"ecosystem_intelligence_sync_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        try:
            upsert_source_config(postgres_config, source_type="github", config={"wg_dir": "/tmp/wg"})
            upsert_source_config(postgres_config, source_type="vibez", config={"api_endpoint": "http://localhost:3100"})
            adapter_factories = {
                "github": lambda: _StaticAdapter(
                    [_signal(source_type="github", source_id="repo@abc123", signal_type="repo_update", title="Repo update")]
                ),
                "vibez": lambda: _FailingAdapter("vibez offline"),
            }

            def evaluator(_config: PostgresConfig, _signals: list[Signal]) -> EvaluationHandoffResult:
                return EvaluationHandoffResult(metadata={"status": "noop"})

            summary = asyncio.run(run_sync(postgres_config, adapter_factories=adapter_factories, evaluator=evaluator))

            self.assertEqual(summary.signals_created, 1)
            self.assertIn("vibez", summary.adapter_results)
            self.assertEqual(summary.adapter_results["vibez"]["error"], "vibez offline")
            self.assertEqual(_signal_count(postgres_config), 1)

            github_config = load_source_config(postgres_config, "github")
            vibez_config = load_source_config(postgres_config, "vibez")
            self.assertIsNotNone(github_config)
            self.assertIsNotNone(vibez_config)
            assert github_config is not None
            assert vibez_config is not None
            self.assertIsNotNone(github_config.last_synced_at)
            self.assertIsNone(vibez_config.last_synced_at)

            run = _latest_evaluation_run(postgres_config)
            self.assertEqual(run[1], 1)
            self.assertEqual(run[6]["sync"]["adapter_results"]["vibez"]["error"], "vibez offline")
            self.assertEqual(run[6]["sync"]["adapter_results"]["github"]["signals_created"], 1)
        finally:
            _drop_database(postgres_config)
