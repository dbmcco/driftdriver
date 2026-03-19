# ABOUTME: Postgres helpers for ecosystem intelligence source configs, signals, and run summaries
# ABOUTME: Persists adapter registry rows plus the deduped signal/evaluation state used by the sync orchestrator

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from uuid import uuid4

from psycopg import connect
from psycopg.types.json import Jsonb

from driftdriver.intelligence.db import PostgresConfig, ensure_database_and_apply_migrations
from driftdriver.intelligence.models import Signal, SourceConfigRecord


def _coerce_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("source_configs.config must decode to a JSON object")


def _row_to_source_config(row: tuple[Any, ...]) -> SourceConfigRecord:
    record_id, source_type, config, enabled, last_synced_at, sync_interval_minutes = row
    return SourceConfigRecord(
        id=record_id,
        source_type=str(source_type),
        config=_coerce_config(config),
        enabled=bool(enabled),
        last_synced_at=last_synced_at,
        sync_interval_minutes=int(sync_interval_minutes),
    )


def _row_to_signal(row: tuple[Any, ...]) -> Signal:
    (
        signal_id,
        source_type,
        source_id,
        signal_type,
        title,
        raw_payload,
        detected_at,
        evaluated_at,
        decision,
        decision_reason,
        decision_confidence,
        decided_by,
        acted_on,
        action_log,
        vetoed_at,
        veto_reason,
        created_at,
        updated_at,
    ) = row
    return Signal(
        id=signal_id,
        source_type=str(source_type),
        source_id=str(source_id),
        signal_type=str(signal_type),
        title=str(title),
        raw_payload=_coerce_config(raw_payload),
        detected_at=detected_at,
        evaluated_at=evaluated_at,
        decision=str(decision) if decision is not None else None,
        decision_reason=str(decision_reason) if decision_reason is not None else None,
        decision_confidence=float(decision_confidence) if decision_confidence is not None else None,
        decided_by=str(decided_by) if decided_by is not None else None,
        acted_on=bool(acted_on),
        action_log=list(action_log) if isinstance(action_log, list) else [],
        vetoed_at=vetoed_at,
        veto_reason=str(veto_reason) if veto_reason is not None else None,
        created_at=created_at,
        updated_at=updated_at,
    )


def upsert_source_config(
    postgres_config: PostgresConfig,
    *,
    source_type: str,
    config: dict[str, Any],
    enabled: bool = True,
    last_synced_at: Any = None,
    sync_interval_minutes: int = 360,
) -> SourceConfigRecord:
    clean_source_type = str(source_type).strip()
    if not clean_source_type:
        raise ValueError("source_type must not be empty")
    if int(sync_interval_minutes) <= 0:
        raise ValueError("sync_interval_minutes must be positive")

    ensure_database_and_apply_migrations(postgres_config)
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO source_configs (
                    id,
                    source_type,
                    config,
                    enabled,
                    last_synced_at,
                    sync_interval_minutes
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_type) DO UPDATE SET
                    config = EXCLUDED.config,
                    enabled = EXCLUDED.enabled,
                    last_synced_at = COALESCE(EXCLUDED.last_synced_at, source_configs.last_synced_at),
                    sync_interval_minutes = EXCLUDED.sync_interval_minutes
                RETURNING id, source_type, config, enabled, last_synced_at, sync_interval_minutes
                """,
                (
                    uuid4(),
                    clean_source_type,
                    Jsonb(config),
                    enabled,
                    last_synced_at,
                    int(sync_interval_minutes),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    if row is None:
        raise RuntimeError(f"failed to persist source config for {clean_source_type}")
    return _row_to_source_config(row)


def load_source_config(
    postgres_config: PostgresConfig,
    source_type: str,
) -> SourceConfigRecord | None:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type, config, enabled, last_synced_at, sync_interval_minutes
                  FROM source_configs
                 WHERE source_type = %s
                """,
                (str(source_type).strip(),),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return _row_to_source_config(row)


def list_enabled_source_configs(postgres_config: PostgresConfig) -> list[SourceConfigRecord]:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type, config, enabled, last_synced_at, sync_interval_minutes
                  FROM source_configs
                 WHERE enabled = true
                 ORDER BY source_type
                """
            )
            rows = cur.fetchall()
    return [_row_to_source_config(row) for row in rows]


def list_unevaluated_signals(
    postgres_config: PostgresConfig,
    *,
    limit: int | None = None,
) -> list[Signal]:
    query = """
        SELECT id, source_type, source_id, signal_type, title, raw_payload, detected_at,
               evaluated_at, decision, decision_reason, decision_confidence, decided_by,
               acted_on, action_log, vetoed_at, veto_reason, created_at, updated_at
          FROM signals
         WHERE evaluated_at IS NULL
         ORDER BY detected_at ASC, source_type ASC, signal_type ASC, source_id ASC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT %s"
        params = (int(limit),)
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
    return [_row_to_signal(row) for row in rows]


def list_recent_decisions(
    postgres_config: PostgresConfig,
    *,
    limit: int = 30,
) -> list[Signal]:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type, source_id, signal_type, title, raw_payload, detected_at,
                       evaluated_at, decision, decision_reason, decision_confidence, decided_by,
                       acted_on, action_log, vetoed_at, veto_reason, created_at, updated_at
                  FROM signals
                 WHERE evaluated_at IS NOT NULL
                 ORDER BY evaluated_at DESC, updated_at DESC
                 LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
    return [_row_to_signal(row) for row in rows]


def list_vetoed_signals(
    postgres_config: PostgresConfig,
    *,
    limit: int = 30,
) -> list[Signal]:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type, source_id, signal_type, title, raw_payload, detected_at,
                       evaluated_at, decision, decision_reason, decision_confidence, decided_by,
                       acted_on, action_log, vetoed_at, veto_reason, created_at, updated_at
                  FROM signals
                 WHERE vetoed_at IS NOT NULL
                 ORDER BY vetoed_at DESC, updated_at DESC
                 LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
    return [_row_to_signal(row) for row in rows]


def list_acted_signals(
    postgres_config: PostgresConfig,
    *,
    limit: int = 200,
) -> list[Signal]:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_type, source_id, signal_type, title, raw_payload, detected_at,
                       evaluated_at, decision, decision_reason, decision_confidence, decided_by,
                       acted_on, action_log, vetoed_at, veto_reason, created_at, updated_at
                  FROM signals
                 WHERE acted_on = true
                   AND evaluated_at IS NOT NULL
                 ORDER BY updated_at DESC
                 LIMIT %s
                """,
                (int(limit),),
            )
            rows = cur.fetchall()
    return [_row_to_signal(row) for row in rows]


def insert_signals(
    postgres_config: PostgresConfig,
    signals: list[Signal],
) -> dict[str, int]:
    if not signals:
        return {}

    inserted_by_source: dict[str, int] = {}
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            for signal in signals:
                cur.execute(
                    """
                    INSERT INTO signals (
                        id,
                        source_type,
                        source_id,
                        signal_type,
                        title,
                        raw_payload,
                        detected_at,
                        evaluated_at,
                        decision,
                        decision_reason,
                        decision_confidence,
                        decided_by,
                        acted_on,
                        action_log,
                        vetoed_at,
                        veto_reason,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_type, source_id, signal_type) DO NOTHING
                    RETURNING source_type
                    """,
                    (
                        signal.id,
                        signal.source_type,
                        signal.source_id,
                        signal.signal_type,
                        signal.title,
                        Jsonb(signal.raw_payload),
                        signal.detected_at,
                        signal.evaluated_at,
                        signal.decision,
                        signal.decision_reason,
                        signal.decision_confidence,
                        signal.decided_by,
                        signal.acted_on,
                        Jsonb(signal.action_log),
                        signal.vetoed_at,
                        signal.veto_reason,
                        signal.created_at,
                        signal.updated_at,
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    continue
                source_type = str(row[0])
                inserted_by_source[source_type] = inserted_by_source.get(source_type, 0) + 1
        conn.commit()
    return inserted_by_source


def update_source_config_sync_time(
    postgres_config: PostgresConfig,
    *,
    source_type: str,
    last_synced_at: datetime,
) -> None:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE source_configs
                   SET last_synced_at = %s
                 WHERE source_type = %s
                """,
                (last_synced_at, str(source_type).strip()),
            )
        conn.commit()


def update_signal_evaluation(
    postgres_config: PostgresConfig,
    *,
    signal_id: UUID,
    evaluated_at: datetime,
    decision: str,
    decision_reason: str,
    decision_confidence: float,
    decided_by: str,
    acted_on: bool,
    action_log: list[dict[str, Any]],
) -> None:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE signals
                   SET evaluated_at = %s,
                       decision = %s,
                       decision_reason = %s,
                       decision_confidence = %s,
                       decided_by = %s,
                       acted_on = %s,
                       action_log = %s,
                       updated_at = %s
                 WHERE id = %s
                """,
                (
                    evaluated_at,
                    decision,
                    decision_reason,
                    float(decision_confidence),
                    decided_by,
                    acted_on,
                    Jsonb(action_log),
                    evaluated_at,
                    signal_id,
                ),
            )
        conn.commit()


def append_signal_action_log(
    postgres_config: PostgresConfig,
    *,
    signal_id: UUID,
    action_entry: dict[str, Any],
    acted_on: bool | None = None,
) -> None:
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT action_log, acted_on
                  FROM signals
                 WHERE id = %s
                """,
                (signal_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"signal {signal_id} not found")
            current_action_log, current_acted_on = row
            action_log = list(current_action_log) if isinstance(current_action_log, list) else []
            action_log.append(action_entry)
            cur.execute(
                """
                UPDATE signals
                   SET action_log = %s,
                       acted_on = %s,
                       updated_at = %s
                 WHERE id = %s
                """,
                (
                    Jsonb(action_log),
                    current_acted_on if acted_on is None else acted_on,
                    datetime.now(timezone.utc),
                    signal_id,
                ),
            )
        conn.commit()


def record_evaluation_run(
    postgres_config: PostgresConfig,
    *,
    source_types: list[str],
    signals_created: int,
    signals_evaluated: int = 0,
    auto_decisions: dict[str, Any] | None = None,
    escalated: int = 0,
    llm_model: str | None = None,
    llm_tokens_used: int = 0,
    duration_ms: int = 0,
) -> str:
    ensure_database_and_apply_migrations(postgres_config)
    run_id = str(uuid4())
    with connect(**postgres_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO evaluation_runs (
                    id,
                    source_types,
                    signals_created,
                    signals_evaluated,
                    auto_decisions,
                    escalated,
                    llm_model,
                    llm_tokens_used,
                    duration_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    list(source_types),
                    int(signals_created),
                    int(signals_evaluated),
                    Jsonb(auto_decisions or {}),
                    int(escalated),
                    llm_model,
                    int(llm_tokens_used),
                    int(duration_ms),
                ),
            )
        conn.commit()
    return run_id
