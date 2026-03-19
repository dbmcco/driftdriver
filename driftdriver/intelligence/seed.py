# ABOUTME: Seeds source_configs rows for ecosystem intelligence from the legacy review JSON
# ABOUTME: Moves GitHub ecosystem review config into Postgres so adapters can load it from the new store

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from driftdriver.intelligence.db import PostgresConfig
from driftdriver.intelligence.models import SourceConfigRecord
from driftdriver.intelligence.store import upsert_source_config
from driftdriver.updates import load_review_config


def build_github_source_config(
    *,
    wg_dir: Path,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    cfg = load_review_config(wg_dir, config_path)
    return {
        "wg_dir": str(wg_dir),
        "source_path": str(cfg.get("source_path") or ""),
        "repos": cfg.get("repos"),
        "extra_repos": cfg.get("extra_repos") if isinstance(cfg.get("extra_repos"), dict) else {},
        "github_users": cfg.get("github_users") if isinstance(cfg.get("github_users"), list) else [],
        "reports": cfg.get("reports") if isinstance(cfg.get("reports"), list) else [],
        "report_keywords": cfg.get("report_keywords") if isinstance(cfg.get("report_keywords"), list) else [],
        "user_repo_limit": int(cfg.get("user_repo_limit") or 10),
        "config_exists": bool(cfg.get("exists")),
    }


def build_vibez_source_config(
    *,
    api_endpoint: str = "http://localhost:3100",
    keyword_filter: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "api_endpoint": str(api_endpoint).rstrip("/"),
        "keyword_filter": list(keyword_filter or ["workgraph", "amplifier", "speedrift", "freshell"]),
        "briefing_limit": 8,
        "contributions_days": 7,
        "contributions_limit": 50,
        "messages_limit": 50,
        "messages_min_relevance": 6,
    }


def seed_github_source_config(
    postgres_config: PostgresConfig | None = None,
    *,
    wg_dir: Path | None = None,
    config_path: str | Path | None = None,
    enabled: bool = True,
    sync_interval_minutes: int = 360,
) -> SourceConfigRecord:
    active_postgres = postgres_config or PostgresConfig()
    active_wg_dir = wg_dir or (Path.cwd() / ".workgraph")
    config = build_github_source_config(wg_dir=active_wg_dir, config_path=config_path)
    return upsert_source_config(
        active_postgres,
        source_type="github",
        config=config,
        enabled=enabled,
        sync_interval_minutes=sync_interval_minutes,
    )


def seed_vibez_source_config(
    postgres_config: PostgresConfig | None = None,
    *,
    api_endpoint: str = "http://localhost:3100",
    keyword_filter: list[str] | None = None,
    enabled: bool = True,
    sync_interval_minutes: int = 180,
) -> SourceConfigRecord:
    active_postgres = postgres_config or PostgresConfig()
    config = build_vibez_source_config(
        api_endpoint=api_endpoint,
        keyword_filter=keyword_filter,
    )
    return upsert_source_config(
        active_postgres,
        source_type="vibez",
        config=config,
        enabled=enabled,
        sync_interval_minutes=sync_interval_minutes,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed the ecosystem intelligence github source config from ecosystem-review.json.",
    )
    parser.add_argument("--wg-dir", default=str(Path.cwd() / ".workgraph"), help="Workgraph directory to read config from")
    parser.add_argument("--config-path", help="Optional override for ecosystem-review.json")
    parser.add_argument("--sync-interval-minutes", type=int, default=360, help="Sync interval to persist into source_configs")
    parser.add_argument("--disabled", action="store_true", help="Seed the source as disabled")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    record = seed_github_source_config(
        wg_dir=Path(args.wg_dir),
        config_path=args.config_path,
        enabled=not args.disabled,
        sync_interval_minutes=int(args.sync_interval_minutes),
    )
    print(
        f"source_type={record.source_type} enabled={str(record.enabled).lower()} "
        f"sync_interval_minutes={record.sync_interval_minutes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
