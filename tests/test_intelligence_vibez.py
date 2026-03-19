# ABOUTME: Tests the Vibez community intelligence adapter and its source config seeding path
# ABOUTME: Verifies endpoint normalization, keyword filtering, and graceful degradation when Vibez is unavailable

from __future__ import annotations

import unittest
from uuid import uuid4

psycopg = __import__("pytest").importorskip("psycopg")
from psycopg import connect, sql

from driftdriver.intelligence.adapters.vibez import VibezAdapter
from driftdriver.intelligence.db import PostgresConfig
from driftdriver.intelligence.seed import seed_vibez_source_config
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


class TestVibezAdapter(unittest.TestCase):
    def test_seed_vibez_source_config_persists_endpoint_and_keywords(self) -> None:
        database = f"ecosystem_intelligence_vibez_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)
        try:
            seeded = seed_vibez_source_config(
                postgres_config,
                api_endpoint="http://localhost:3100",
                keyword_filter=["workgraph", "speedrift"],
                sync_interval_minutes=120,
            )
            loaded = load_source_config(postgres_config, "vibez")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(seeded.id, loaded.id)
            self.assertEqual(loaded.config["api_endpoint"], "http://localhost:3100")
            self.assertEqual(loaded.config["keyword_filter"], ["workgraph", "speedrift"])
            self.assertEqual(loaded.sync_interval_minutes, 120)
            self.assertTrue(loaded.enabled)
        finally:
            _drop_database(postgres_config)

    def test_vibez_adapter_emits_trend_community_and_hot_alert_signals(self) -> None:
        def fake_fetch_json(url: str):
            if url == "http://localhost:3100/api/briefing":
                return {
                    "report": {
                        "report_date": "2026-03-17",
                        "generated_at": "2026-03-17T12:00:00Z",
                        "daily_memo": "Workgraph and speedrift keep surfacing.",
                        "briefing_json": (
                            '[{"title":"Workgraph daemon failures","participants":["Ramon"],'
                            '"insights":"Concurrency bugs in workgraph daemon are worth tracking.","links":[]}]'
                        ),
                    }
                }
            if url == "http://localhost:3100/api/contributions?days=7&limit=50":
                return {
                    "opportunities": [
                        {
                            "id": "opp-1",
                            "room_name": "The vibez (code code code)",
                            "sender_name": "Naveen",
                            "body": "Workgraph daemon failures are showing up again",
                            "timestamp": 1773752260000,
                            "topics": ["workgraph", "reliability"],
                            "entities": ["workgraph"],
                            "contribution_themes": ["orchestration"],
                            "contribution_hint": "Speedrift has direct relevance here.",
                        },
                        {
                            "id": "opp-2",
                            "room_name": "Off-topic",
                            "sender_name": "Someone",
                            "body": "Completely unrelated",
                            "timestamp": 1773752260000,
                            "topics": ["humor"],
                            "entities": [],
                            "contribution_themes": [],
                            "contribution_hint": "",
                        },
                    ]
                }
            if url == "http://localhost:3100/api/messages?limit=50&minRelevance=6":
                return {
                    "messages": [
                        {
                            "id": "msg-1",
                            "room_name": "AI for daily use",
                            "sender_name": "Ben",
                            "body": "I will present my AI chief of staff",
                            "timestamp": 1773752260000,
                            "topics": ["workflow", "amplifier"],
                            "entities": ["Amplifier"],
                            "contribution_themes": ["business-ai-workflow-design"],
                            "contribution_hint": "Amplifier could be relevant.",
                            "alert_level": "hot",
                        },
                        {
                            "id": "msg-2",
                            "room_name": "Off-topic",
                            "sender_name": "Someone",
                            "body": "No keywords here",
                            "timestamp": 1773752260000,
                            "topics": ["humor"],
                            "entities": [],
                            "contribution_themes": [],
                            "contribution_hint": "",
                            "alert_level": "digest",
                        },
                    ]
                }
            if url == "http://localhost:3100/api/health":
                return {"ok": True}
            raise AssertionError(f"Unexpected URL: {url}")

        adapter = VibezAdapter(fetch_json=fake_fetch_json)
        config = {
            "api_endpoint": "http://localhost:3100",
            "keyword_filter": ["workgraph", "speedrift", "amplifier"],
            "contributions_days": 7,
            "contributions_limit": 50,
            "messages_limit": 50,
            "messages_min_relevance": 6,
        }

        signals = adapter.sync(config, None)
        self.assertEqual(
            {signal.signal_type for signal in signals},
            {"trend", "community_mention", "hot_alert"},
        )
        by_type = {signal.signal_type: signal for signal in signals}
        self.assertIn("Workgraph daemon failures", by_type["trend"].title)
        self.assertEqual(by_type["community_mention"].source_id, "opp-1")
        self.assertEqual(by_type["hot_alert"].source_id, "msg-1")
        health = adapter.health_check()
        self.assertTrue(health["ok"])

    def test_vibez_adapter_returns_empty_list_when_service_is_unavailable(self) -> None:
        def failing_fetch_json(_url: str):
            raise OSError("connection refused")

        adapter = VibezAdapter(fetch_json=failing_fetch_json)
        signals = adapter.sync(
            {
                "api_endpoint": "http://localhost:3100",
                "keyword_filter": ["workgraph"],
            },
            None,
        )
        self.assertEqual(signals, [])
        health = adapter.health_check()
        self.assertFalse(health["ok"])
        self.assertIn("connection refused", health["error"])
