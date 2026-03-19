# ABOUTME: Async sync orchestrator for ecosystem intelligence source adapters
# ABOUTME: Loads enabled sources, runs adapters in parallel, persists deduped signals, and records run metadata

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from driftdriver.intelligence.adapters import GitHubAdapter, SourceAdapter, VibezAdapter
from driftdriver.intelligence.db import PostgresConfig, ensure_database_and_apply_migrations
from driftdriver.intelligence.models import Signal, SourceConfigRecord
from driftdriver.intelligence.store import (
    insert_signals,
    list_enabled_source_configs,
    record_evaluation_run,
    update_source_config_sync_time,
)


LOG = logging.getLogger(__name__)

AdapterFactory = Callable[[], SourceAdapter]
EvaluatorFn = Callable[[PostgresConfig, list[Signal]], "EvaluationHandoffResult"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EvaluationHandoffResult:
    signals_evaluated: int = 0
    escalated: int = 0
    auto_decisions: dict[str, Any] = field(default_factory=dict)
    llm_model: str | None = None
    llm_tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterSyncResult:
    source_type: str
    signals: tuple[Signal, ...]
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class SyncRunSummary:
    evaluation_run_id: str
    source_types: tuple[str, ...]
    signals_seen: int
    signals_created: int
    signals_evaluated: int
    escalated: int
    duration_ms: int
    adapter_results: dict[str, dict[str, Any]]
    llm_model: str | None = None
    llm_tokens_used: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluation_run_id": self.evaluation_run_id,
            "source_types": list(self.source_types),
            "signals_seen": self.signals_seen,
            "signals_created": self.signals_created,
            "signals_evaluated": self.signals_evaluated,
            "escalated": self.escalated,
            "duration_ms": self.duration_ms,
            "adapter_results": self.adapter_results,
            "llm_model": self.llm_model,
            "llm_tokens_used": self.llm_tokens_used,
        }


def build_default_adapter_registry() -> dict[str, AdapterFactory]:
    return {
        "github": GitHubAdapter,
        "vibez": VibezAdapter,
    }


def _coerce_evaluation_result(raw: Any) -> EvaluationHandoffResult:
    if isinstance(raw, EvaluationHandoffResult):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("evaluation callback must return EvaluationHandoffResult or dict")
    return EvaluationHandoffResult(
        signals_evaluated=int(raw.get("signals_evaluated") or 0),
        escalated=int(raw.get("escalated") or 0),
        auto_decisions=dict(raw.get("auto_decisions") or {}),
        llm_model=str(raw["llm_model"]) if raw.get("llm_model") else None,
        llm_tokens_used=int(raw.get("llm_tokens_used") or 0),
        metadata=dict(raw.get("metadata") or {}),
    )


def default_evaluator(_postgres_config: PostgresConfig, _signals: list[Signal]) -> EvaluationHandoffResult:
    try:
        from driftdriver.intelligence import evaluator as evaluator_module
    except ImportError:
        return EvaluationHandoffResult(
            metadata={
                "status": "skipped",
                "reason": "evaluator_unavailable",
            }
        )

    evaluate_pending = getattr(evaluator_module, "evaluate_pending_signals", None)
    if not callable(evaluate_pending):
        return EvaluationHandoffResult(
            metadata={
                "status": "skipped",
                "reason": "evaluate_pending_signals_missing",
            }
        )
    return _coerce_evaluation_result(evaluate_pending(_postgres_config, _signals))


def _normalize_signals(raw: Any) -> tuple[Signal, ...]:
    if raw is None:
        return ()
    signals = tuple(raw)
    for signal in signals:
        if not isinstance(signal, Signal):
            raise TypeError(f"adapter returned {type(signal).__name__}; expected Signal")
    return signals


async def _run_source(
    record: SourceConfigRecord,
    adapter_factories: Mapping[str, AdapterFactory],
) -> AdapterSyncResult:
    started = time.perf_counter()
    try:
        factory = adapter_factories.get(record.source_type)
        if factory is None:
            return AdapterSyncResult(
                source_type=record.source_type,
                signals=(),
                duration_ms=0,
                error=f"no adapter registered for source_type={record.source_type}",
            )
        adapter = factory()
        signals = await asyncio.to_thread(adapter.sync, record.config, record.last_synced_at)
        return AdapterSyncResult(
            source_type=record.source_type,
            signals=_normalize_signals(signals),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        LOG.exception("ecosystem intelligence adapter failed for %s", record.source_type)
        return AdapterSyncResult(
            source_type=record.source_type,
            signals=(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
        )


async def run_sync(
    postgres_config: PostgresConfig | None = None,
    *,
    adapter_factories: Mapping[str, AdapterFactory] | None = None,
    evaluator: EvaluatorFn | None = None,
) -> SyncRunSummary:
    active_postgres = postgres_config or PostgresConfig()
    active_factories = dict(adapter_factories or build_default_adapter_registry())
    active_evaluator = evaluator or default_evaluator

    ensure_database_and_apply_migrations(active_postgres)
    source_configs = list_enabled_source_configs(active_postgres)

    run_started = time.perf_counter()
    adapter_results = await asyncio.gather(
        *(_run_source(record, active_factories) for record in source_configs)
    )
    all_signals = [signal for result in adapter_results for signal in result.signals]
    inserted_by_source = insert_signals(active_postgres, all_signals)
    sync_completed_at = _utc_now()

    for record, result in zip(source_configs, adapter_results, strict=True):
        if result.error is not None:
            continue
        update_source_config_sync_time(
            active_postgres,
            source_type=record.source_type,
            last_synced_at=sync_completed_at,
        )

    evaluation_result = await asyncio.to_thread(active_evaluator, active_postgres, all_signals)
    evaluation_result = _coerce_evaluation_result(evaluation_result)

    signals_seen = len(all_signals)
    signals_created = sum(inserted_by_source.values())
    adapter_payload: dict[str, dict[str, Any]] = {}
    for result in adapter_results:
        payload: dict[str, Any] = {
            "signals_seen": len(result.signals),
            "signals_created": inserted_by_source.get(result.source_type, 0),
            "duration_ms": result.duration_ms,
        }
        if result.error is not None:
            payload["error"] = result.error
        adapter_payload[result.source_type] = payload

    duration_ms = int((time.perf_counter() - run_started) * 1000)
    run_metadata = {
        "sync": {
            "signals_seen": signals_seen,
            "signals_created": signals_created,
            "adapter_results": adapter_payload,
        },
        "evaluation": {
            "signals_evaluated": evaluation_result.signals_evaluated,
            "escalated": evaluation_result.escalated,
            "auto_decisions": evaluation_result.auto_decisions,
            "metadata": evaluation_result.metadata,
        },
    }
    evaluation_run_id = record_evaluation_run(
        active_postgres,
        source_types=[record.source_type for record in source_configs],
        signals_created=signals_created,
        signals_evaluated=evaluation_result.signals_evaluated,
        auto_decisions=run_metadata,
        escalated=evaluation_result.escalated,
        llm_model=evaluation_result.llm_model,
        llm_tokens_used=evaluation_result.llm_tokens_used,
        duration_ms=duration_ms,
    )
    return SyncRunSummary(
        evaluation_run_id=evaluation_run_id,
        source_types=tuple(record.source_type for record in source_configs),
        signals_seen=signals_seen,
        signals_created=signals_created,
        signals_evaluated=evaluation_result.signals_evaluated,
        escalated=evaluation_result.escalated,
        duration_ms=duration_ms,
        adapter_results=adapter_payload,
        llm_model=evaluation_result.llm_model,
        llm_tokens_used=evaluation_result.llm_tokens_used,
    )


def _build_parser() -> argparse.ArgumentParser:
    defaults = PostgresConfig()
    parser = argparse.ArgumentParser(description="Run the ecosystem intelligence sync orchestrator.")
    parser.add_argument("--host", default=defaults.host, help="Postgres host")
    parser.add_argument("--port", type=int, default=defaults.port, help="Postgres port")
    parser.add_argument("--user", default=defaults.user, help="Postgres user")
    parser.add_argument("--password", default=defaults.password, help="Postgres password")
    parser.add_argument("--database", default=defaults.database, help="Target database name")
    parser.add_argument(
        "--maintenance-database",
        default=defaults.maintenance_database,
        help="Database used for CREATE DATABASE",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=defaults.connect_timeout_seconds,
        help="Connection timeout in seconds",
    )
    parser.add_argument("--json", action="store_true", help="Emit the run summary as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    config = PostgresConfig(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        maintenance_database=args.maintenance_database,
        connect_timeout_seconds=args.connect_timeout,
    )
    summary = asyncio.run(run_sync(config))
    if args.json:
        print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
    else:
        print(
            f"sources={','.join(summary.source_types) or '-'} "
            f"seen={summary.signals_seen} "
            f"created={summary.signals_created} "
            f"evaluated={summary.signals_evaluated} "
            f"duration_ms={summary.duration_ms}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
