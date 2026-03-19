# ABOUTME: Tests the ecosystem intelligence source adapter and source-config seeding path
# ABOUTME: Uses real Postgres storage plus mocked GitHub fetchers to verify the first adapter contract

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

psycopg = __import__("pytest").importorskip("psycopg")
from psycopg import connect, sql

from driftdriver.intelligence.adapters.github import GitHubAdapter
from driftdriver.intelligence.db import PostgresConfig
from driftdriver.intelligence.seed import seed_github_source_config
from driftdriver.intelligence.store import load_source_config


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


class TestEcosystemIntelligenceSource(unittest.TestCase):
    def test_seed_github_source_config_migrates_review_json_into_source_configs(self) -> None:
        database = f"ecosystem_intelligence_source_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            config_path = wg_dir / ".driftdriver" / "ecosystem-review.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "repos": {"workgraph": "graphwork/workgraph"},
                        "github_users": ["jesse"],
                        "reports": [{"name": "bibez", "url": "https://example.com/bibez"}],
                        "report_keywords": ["workgraph", "speedrift"],
                        "user_repo_limit": 25,
                    }
                ),
                encoding="utf-8",
            )

            try:
                seeded = seed_github_source_config(
                    postgres_config,
                    wg_dir=wg_dir,
                    sync_interval_minutes=180,
                )
                loaded = load_source_config(postgres_config, "github")

                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(seeded.id, loaded.id)
                self.assertEqual(loaded.source_type, "github")
                self.assertEqual(loaded.sync_interval_minutes, 180)
                self.assertEqual(loaded.config["wg_dir"], str(wg_dir))
                self.assertEqual(loaded.config["repos"], {"workgraph": "graphwork/workgraph"})
                self.assertEqual(loaded.config["github_users"], ["jesse"])
                self.assertEqual(loaded.config["report_keywords"], ["workgraph", "speedrift"])
                self.assertEqual(loaded.config["reports"][0]["name"], "bibez")
            finally:
                _drop_database(postgres_config)

    def test_github_adapter_sync_emits_repo_update_new_repo_and_activity_signals(self) -> None:
        database = f"ecosystem_intelligence_source_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            config_path = wg_dir / ".driftdriver" / "ecosystem-review.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "repos": {"coredrift": "dbmcco/coredrift"},
                        "github_users": ["jesse"],
                        "user_repo_limit": 10,
                    }
                ),
                encoding="utf-8",
            )

            calls = {"repo": 0, "user": 0}

            def fetcher(_repo: str) -> tuple[str, str]:
                calls["repo"] += 1
                if calls["repo"] == 1:
                    return ("abc123", "2026-03-17T12:00:00Z")
                return ("def456", "2026-03-17T13:00:00Z")

            def user_fetcher(_user: str, _limit: int) -> list[dict[str, str]]:
                calls["user"] += 1
                if calls["user"] == 1:
                    return [
                        {
                            "full_name": "jesse/alpha",
                            "html_url": "https://github.com/jesse/alpha",
                            "description": "alpha",
                            "pushed_at": "2026-03-17T10:00:00Z",
                            "updated_at": "2026-03-17T10:00:00Z",
                        }
                    ]
                return [
                    {
                        "full_name": "jesse/alpha",
                        "html_url": "https://github.com/jesse/alpha",
                        "description": "alpha",
                        "pushed_at": "2026-03-17T14:00:00Z",
                        "updated_at": "2026-03-17T14:00:00Z",
                    },
                    {
                        "full_name": "jesse/beta",
                        "html_url": "https://github.com/jesse/beta",
                        "description": "beta",
                        "pushed_at": "2026-03-17T14:30:00Z",
                        "updated_at": "2026-03-17T14:30:00Z",
                    },
                ]

            try:
                seed_github_source_config(postgres_config, wg_dir=wg_dir)
                stored = load_source_config(postgres_config, "github")
                self.assertIsNotNone(stored)
                assert stored is not None

                adapter = GitHubAdapter(fetcher=fetcher, user_fetcher=user_fetcher)
                baseline = adapter.sync(stored.config, stored.last_synced_at)
                second = adapter.sync(stored.config, stored.last_synced_at)

                self.assertEqual(baseline, [])
                self.assertEqual(
                    {signal.signal_type for signal in second},
                    {"repo_update", "new_repo", "activity"},
                )
                self.assertTrue(all(signal.source_type == "github" for signal in second))
                titles = {signal.signal_type: signal.title for signal in second}
                self.assertIn("coredrift", titles["repo_update"])
                self.assertIn("jesse/beta", titles["new_repo"])
                self.assertIn("jesse/alpha", titles["activity"])

                health = adapter.health_check()
                self.assertTrue(health["ok"])
                self.assertEqual(health["source_type"], "github")
                self.assertEqual(health["emits"], ["repo_update", "new_repo", "activity"])
            finally:
                _drop_database(postgres_config)
