# ABOUTME: Real Postgres tests for the ecosystem intelligence schema bootstrap
# ABOUTME: Verifies fresh-database creation and idempotent reruns for the first intelligence task

from __future__ import annotations

import unittest
from uuid import uuid4

psycopg = __import__("pytest").importorskip("psycopg")
from psycopg import connect, sql

from driftdriver.intelligence.db import (
    MIGRATIONS_TABLE_NAME,
    PostgresConfig,
    ensure_database_and_apply_migrations,
)


def _test_config(database: str) -> PostgresConfig:
    return PostgresConfig(database=database)


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


def _column_metadata(config: PostgresConfig, table_name: str) -> list[tuple[str, str, str]]:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type, udt_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = %s
                 ORDER BY ordinal_position
                """,
                (table_name,),
            )
            return [(str(name), str(data_type), str(udt_name)) for name, data_type, udt_name in cur.fetchall()]


def _constraint_columns(config: PostgresConfig, table_name: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
                  FROM information_schema.table_constraints tc
                  LEFT JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
                   AND tc.table_schema = kcu.table_schema
                   AND tc.table_name = kcu.table_name
                 WHERE tc.table_schema = 'public'
                   AND tc.table_name = %s
                 ORDER BY tc.constraint_name, kcu.ordinal_position
                """,
                (table_name,),
            )
            rows = cur.fetchall()

    constraints: dict[str, tuple[str, list[str]]] = {}
    for name, constraint_type, column_name in rows:
        entry = constraints.get(str(name))
        if entry is None:
            entry = (str(constraint_type), [])
            constraints[str(name)] = entry
        if column_name:
            entry[1].append(str(column_name))
    return {name: (constraint_type, tuple(columns)) for name, (constraint_type, columns) in constraints.items()}


def _index_names(config: PostgresConfig, table_name: str) -> set[str]:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND tablename = %s
                """,
                (table_name,),
            )
            return {str(row[0]) for row in cur.fetchall()}


def _migration_row_count(config: PostgresConfig) -> int:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(
                    sql.Identifier(MIGRATIONS_TABLE_NAME),
                )
            )
            return int(cur.fetchone()[0])


class TestEcosystemIntelligencePostgres(unittest.TestCase):
    def test_bootstrap_creates_fresh_database_and_schema(self) -> None:
        database = f"ecosystem_intelligence_test_{uuid4().hex[:12]}"
        config = _test_config(database)
        _drop_database(config)

        try:
            result = ensure_database_and_apply_migrations(config)
            self.assertTrue(result.database_created)
            self.assertEqual(result.migrations_applied, ("001_initial.sql",))
            self.assertEqual(result.migrations_skipped, ())

            self.assertEqual(
                _column_metadata(config, "signals"),
                [
                    ("id", "uuid", "uuid"),
                    ("source_type", "text", "text"),
                    ("source_id", "text", "text"),
                    ("signal_type", "text", "text"),
                    ("title", "text", "text"),
                    ("raw_payload", "jsonb", "jsonb"),
                    ("detected_at", "timestamp with time zone", "timestamptz"),
                    ("evaluated_at", "timestamp with time zone", "timestamptz"),
                    ("decision", "text", "text"),
                    ("decision_reason", "text", "text"),
                    ("decision_confidence", "double precision", "float8"),
                    ("decided_by", "text", "text"),
                    ("acted_on", "boolean", "bool"),
                    ("action_log", "jsonb", "jsonb"),
                    ("vetoed_at", "timestamp with time zone", "timestamptz"),
                    ("veto_reason", "text", "text"),
                    ("created_at", "timestamp with time zone", "timestamptz"),
                    ("updated_at", "timestamp with time zone", "timestamptz"),
                ],
            )
            self.assertEqual(
                _column_metadata(config, "evaluation_runs"),
                [
                    ("id", "uuid", "uuid"),
                    ("run_at", "timestamp with time zone", "timestamptz"),
                    ("source_types", "ARRAY", "_text"),
                    ("signals_created", "integer", "int4"),
                    ("signals_evaluated", "integer", "int4"),
                    ("auto_decisions", "jsonb", "jsonb"),
                    ("escalated", "integer", "int4"),
                    ("llm_model", "text", "text"),
                    ("llm_tokens_used", "integer", "int4"),
                    ("duration_ms", "integer", "int4"),
                ],
            )
            self.assertEqual(
                _column_metadata(config, "source_configs"),
                [
                    ("id", "uuid", "uuid"),
                    ("source_type", "text", "text"),
                    ("config", "jsonb", "jsonb"),
                    ("enabled", "boolean", "bool"),
                    ("last_synced_at", "timestamp with time zone", "timestamptz"),
                    ("sync_interval_minutes", "integer", "int4"),
                ],
            )

            signal_constraints = _constraint_columns(config, "signals")
            self.assertIn(("PRIMARY KEY", ("id",)), signal_constraints.values())
            self.assertIn(
                ("UNIQUE", ("source_type", "source_id", "signal_type")),
                signal_constraints.values(),
            )

            source_config_constraints = _constraint_columns(config, "source_configs")
            self.assertIn(("PRIMARY KEY", ("id",)), source_config_constraints.values())
            self.assertIn(("UNIQUE", ("source_type",)), source_config_constraints.values())

            signal_indexes = _index_names(config, "signals")
            self.assertIn("idx_signals_source_type_evaluated_at", signal_indexes)
            self.assertIn("idx_signals_decision_acted_on", signal_indexes)
            self.assertEqual(_migration_row_count(config), 1)
        finally:
            _drop_database(config)

    def test_bootstrap_is_idempotent_on_existing_database(self) -> None:
        database = f"ecosystem_intelligence_test_{uuid4().hex[:12]}"
        config = _test_config(database)
        _drop_database(config)

        try:
            first = ensure_database_and_apply_migrations(config)
            second = ensure_database_and_apply_migrations(config)

            self.assertTrue(first.database_created)
            self.assertEqual(first.migrations_applied, ("001_initial.sql",))
            self.assertFalse(second.database_created)
            self.assertEqual(second.migrations_applied, ())
            self.assertEqual(second.migrations_skipped, ("001_initial.sql",))
            self.assertEqual(_migration_row_count(config), 1)
            self.assertIn("idx_signals_source_type_evaluated_at", _index_names(config, "signals"))
        finally:
            _drop_database(config)
