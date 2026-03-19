# ABOUTME: Postgres bootstrap and migration helpers for the ecosystem intelligence signal store
# ABOUTME: Creates the ecosystem_intelligence database and applies idempotent schema migrations

from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Final

def _psycopg():
    """Lazy import so the module is importable without psycopg installed."""
    import psycopg  # noqa: E402
    return psycopg


def _connect(**kwargs):
    return _psycopg().connect(**kwargs)


def _sql():
    return _psycopg().sql


DEFAULT_DATABASE: Final[str] = "ecosystem_intelligence"
DEFAULT_HOST: Final[str] = "127.0.0.1"
DEFAULT_PORT: Final[int] = 5432
DEFAULT_MAINTENANCE_DATABASE: Final[str] = "postgres"
DEFAULT_CONNECT_TIMEOUT_SECONDS: Final[int] = 5
MIGRATION_NAMES: Final[tuple[str, ...]] = ("001_initial.sql",)
MIGRATIONS_TABLE_NAME: Final[str] = "driftdriver_intelligence_migrations"


def _env_value(*names: str) -> str | None:
    for name in names:
        raw = os.getenv(name)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return None


def _env_int(default: int, *names: str) -> int:
    raw = _env_value(*names)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class PostgresConfig:
    host: str = field(default_factory=lambda: _env_value("DRIFTDRIVER_PGHOST", "PGHOST") or DEFAULT_HOST)
    port: int = field(default_factory=lambda: _env_int(DEFAULT_PORT, "DRIFTDRIVER_PGPORT", "PGPORT"))
    user: str = field(
        default_factory=lambda: _env_value("DRIFTDRIVER_PGUSER", "PGUSER", "POSTGRES_USER") or getpass.getuser()
    )
    password: str | None = field(
        default_factory=lambda: _env_value("DRIFTDRIVER_PGPASSWORD", "PGPASSWORD", "POSTGRES_PASSWORD")
    )
    database: str = field(default_factory=lambda: _env_value("DRIFTDRIVER_PGDATABASE", "ECOSYSTEM_INTELLIGENCE_DB") or DEFAULT_DATABASE)
    maintenance_database: str = field(
        default_factory=lambda: _env_value("DRIFTDRIVER_PGMAINTENANCE_DB", "PGMAINTENANCE_DB") or DEFAULT_MAINTENANCE_DATABASE
    )
    connect_timeout_seconds: int = field(
        default_factory=lambda: _env_int(
            DEFAULT_CONNECT_TIMEOUT_SECONDS,
            "DRIFTDRIVER_PGCONNECT_TIMEOUT",
            "PGCONNECT_TIMEOUT",
        )
    )

    def connection_kwargs(self, *, database: str | None = None) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "dbname": database or self.database,
            "connect_timeout": self.connect_timeout_seconds,
        }
        if self.password:
            kwargs["password"] = self.password
        return kwargs


@dataclass(frozen=True)
class MigrationResult:
    database_created: bool
    migrations_applied: tuple[str, ...]
    migrations_skipped: tuple[str, ...]


def _load_migration_sql(name: str) -> str:
    return (
        files("driftdriver.intelligence")
        .joinpath("migrations")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def database_exists(config: PostgresConfig) -> bool:
    with _connect(
        **config.connection_kwargs(database=config.maintenance_database),
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (config.database,),
            )
            return cur.fetchone() is not None


def ensure_database(config: PostgresConfig) -> bool:
    if database_exists(config):
        return False
    with _connect(
        **config.connection_kwargs(database=config.maintenance_database),
        autocommit=True,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                _sql().SQL("CREATE DATABASE {}").format(
                    _sql().Identifier(config.database),
                )
            )
    return True


def ensure_database_and_apply_migrations(config: PostgresConfig | None = None) -> MigrationResult:
    active_config = config or PostgresConfig()
    database_created = ensure_database(active_config)
    migrations_applied: list[str] = []
    migrations_skipped: list[str] = []

    with _connect(**active_config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                _sql().SQL(
                    """
                    CREATE TABLE IF NOT EXISTS {} (
                        name text PRIMARY KEY,
                        applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                ).format(_sql().Identifier(MIGRATIONS_TABLE_NAME))
            )
            cur.execute(
                _sql().SQL("SELECT name FROM {} ORDER BY name").format(
                    _sql().Identifier(MIGRATIONS_TABLE_NAME),
                )
            )
            applied_names = {row[0] for row in cur.fetchall()}

            for name in MIGRATION_NAMES:
                if name in applied_names:
                    migrations_skipped.append(name)
                    continue
                cur.execute(_load_migration_sql(name))
                cur.execute(
                    _sql().SQL("INSERT INTO {} (name) VALUES (%s)").format(
                        _sql().Identifier(MIGRATIONS_TABLE_NAME),
                    ),
                    (name,),
                )
                migrations_applied.append(name)
        conn.commit()

    return MigrationResult(
        database_created=database_created,
        migrations_applied=tuple(migrations_applied),
        migrations_skipped=tuple(migrations_skipped),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the ecosystem_intelligence Postgres database and apply schema migrations.",
    )
    parser.add_argument("--host", help="Postgres host")
    parser.add_argument("--port", type=int, help="Postgres port")
    parser.add_argument("--user", help="Postgres user")
    parser.add_argument("--password", help="Postgres password")
    parser.add_argument("--database", help="Target database name")
    parser.add_argument("--maintenance-database", help="Database used for CREATE DATABASE")
    parser.add_argument("--connect-timeout", type=int, help="Connection timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = PostgresConfig(
        host=args.host or PostgresConfig().host,
        port=args.port or PostgresConfig().port,
        user=args.user or PostgresConfig().user,
        password=args.password if args.password is not None else PostgresConfig().password,
        database=args.database or PostgresConfig().database,
        maintenance_database=args.maintenance_database or PostgresConfig().maintenance_database,
        connect_timeout_seconds=args.connect_timeout or PostgresConfig().connect_timeout_seconds,
    )
    result = ensure_database_and_apply_migrations(config)
    applied = ",".join(result.migrations_applied) or "-"
    skipped = ",".join(result.migrations_skipped) or "-"
    print(
        f"database={config.database} created={str(result.database_created).lower()} "
        f"applied={applied} skipped={skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
