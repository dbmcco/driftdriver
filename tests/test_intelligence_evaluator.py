# ABOUTME: Tests the ecosystem intelligence evaluator against a real Postgres database
# ABOUTME: Verifies batching, auto-action thresholds, watchlist updates, and veto-context injection

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

psycopg = __import__("pytest").importorskip("psycopg")
from psycopg import connect, sql

from driftdriver.intelligence import evaluator as intelligence_evaluator
from driftdriver.intelligence.db import PostgresConfig
from driftdriver.intelligence.evaluator import (
    DecisionEnvelope,
    default_model_invoker,
    default_task_creator,
    evaluate_pending_signals,
)
from driftdriver.intelligence.models import Signal
from driftdriver.intelligence.store import insert_signals, load_source_config, upsert_source_config


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


def _signal(
    *,
    source_type: str,
    source_id: str,
    signal_type: str,
    title: str,
    detected_at: datetime | None = None,
    evaluated_at: datetime | None = None,
    decision: str | None = None,
    decision_reason: str | None = None,
    decision_confidence: float | None = None,
    decided_by: str | None = None,
    acted_on: bool = False,
    action_log: list[dict] | None = None,
    vetoed_at: datetime | None = None,
    veto_reason: str | None = None,
) -> Signal:
    now = detected_at or datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
    return Signal(
        source_type=source_type,
        source_id=source_id,
        signal_type=signal_type,
        title=title,
        raw_payload={"source_id": source_id, "signal_type": signal_type},
        detected_at=now,
        evaluated_at=evaluated_at,
        decision=decision,
        decision_reason=decision_reason,
        decision_confidence=decision_confidence,
        decided_by=decided_by,
        acted_on=acted_on,
        action_log=list(action_log or []),
        vetoed_at=vetoed_at,
        veto_reason=veto_reason,
        created_at=now,
        updated_at=evaluated_at or now,
    )


def _extract_context(user_prompt: str) -> dict:
    marker = "```json\n"
    start = user_prompt.index(marker) + len(marker)
    end = user_prompt.index("\n```", start)
    return json.loads(user_prompt[start:end])


def _load_signal(config: PostgresConfig, signal_id: UUID) -> tuple[str | None, float | None, bool, list[dict]]:
    with connect(**config.connection_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT decision, decision_confidence, acted_on, action_log
                  FROM signals
                 WHERE id = %s
                """,
                (signal_id,),
            )
            row = cur.fetchone()
    assert row is not None
    decision, confidence, acted_on, action_log = row
    return (
        str(decision) if decision is not None else None,
        float(confidence) if confidence is not None else None,
        bool(acted_on),
        list(action_log) if isinstance(action_log, list) else [],
    )


class TestIntelligenceEvaluator(unittest.TestCase):
    def test_invoke_anthropic_api_uses_strict_tool_output(self) -> None:
        schema = {"type": "object", "properties": {"decisions": {"type": "array"}}}
        response_payload = {
            "content": [
                {"type": "text", "text": "Recording decisions."},
                {
                    "type": "tool_use",
                    "id": "toolu_eval",
                    "name": "record_decisions",
                    "input": {"decisions": [{"signal_id": "sig-1", "decision": "skip"}]},
                },
            ]
        }
        response = mock.MagicMock()
        response.read.return_value = json.dumps(response_payload).encode("utf-8")
        urlopen_result = mock.MagicMock()
        urlopen_result.__enter__.return_value = response
        urlopen_result.__exit__.return_value = None

        with (
            mock.patch.dict("driftdriver.intelligence.evaluator.os.environ", {"DRIFTDRIVER_ANTHROPIC_API_KEY": "test-key"}, clear=True),
            mock.patch("driftdriver.intelligence.evaluator.urlopen", return_value=urlopen_result) as urlopen_mock,
        ):
            result = intelligence_evaluator._invoke_anthropic_api(
                "claude-haiku-4-5-20251001",
                "system prompt",
                "user prompt",
                schema,
            )

        self.assertEqual(result, {"decisions": [{"signal_id": "sig-1", "decision": "skip"}]})
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, intelligence_evaluator.ANTHROPIC_API_URL)
        self.assertEqual(request.get_method(), "POST")
        headers = dict(request.header_items())
        self.assertEqual(headers["Content-type"], "application/json")
        self.assertEqual(headers["X-api-key"], "test-key")
        self.assertEqual(headers["Anthropic-version"], intelligence_evaluator.ANTHROPIC_API_VERSION)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": intelligence_evaluator.ANTHROPIC_DECISION_TOOL})
        self.assertTrue(body["disable_parallel_tool_use"])
        self.assertTrue(body["tools"][0]["strict"])
        self.assertEqual(body["tools"][0]["input_schema"], schema)

    def test_invoke_anthropic_api_requires_key(self) -> None:
        with mock.patch.dict("driftdriver.intelligence.evaluator.os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Anthropic API key not configured"):
                intelligence_evaluator._invoke_anthropic_api(
                    "claude-haiku-4-5-20251001",
                    "system prompt",
                    "user prompt",
                    {"type": "object"},
                )

    def test_default_model_invoker_routes_haiku_models_to_anthropic_api(self) -> None:
        with (
            mock.patch.dict("os.environ", {"DRIFTDRIVER_ANTHROPIC_API_KEY": "fake-key"}, clear=True),
            mock.patch(
                "driftdriver.intelligence.evaluator._invoke_anthropic_api",
                return_value={"decisions": []},
            ) as anthropic_mock,
            mock.patch("driftdriver.intelligence.evaluator._invoke_codex", return_value={"decisions": []}) as codex_mock,
        ):
            result = default_model_invoker(
                "claude-haiku-4-5-20251001",
                "system prompt",
                "user prompt",
                {"type": "object"},
            )

        self.assertEqual(result, {"decisions": []})
        anthropic_mock.assert_called_once()
        codex_mock.assert_not_called()

    def test_evaluator_batches_signals_updates_watchlist_and_creates_adopt_task(self) -> None:
        database = f"ecosystem_intelligence_eval_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        repo_signal = _signal(
            source_type="github",
            source_id="repo@aaa111",
            signal_type="repo_update",
            title="Repo update",
        )
        watch_signal = _signal(
            source_type="github",
            source_id="new/repo",
            signal_type="new_repo",
            title="New repo",
        )
        adopt_signal = _signal(
            source_type="vibez",
            source_id="msg-1",
            signal_type="hot_alert",
            title="Hot alert",
        )

        try:
            upsert_source_config(postgres_config, source_type="github", config={"wg_dir": "/tmp/wg"})
            upsert_source_config(postgres_config, source_type="vibez", config={"api_endpoint": "http://localhost:3100"})
            upsert_source_config(
                postgres_config,
                source_type="evaluator",
                enabled=False,
                sync_interval_minutes=1440,
                config={
                    "signal_models": {
                        "repo_update": "gpt-4o-mini",
                        "new_repo": "gpt-4o-mini",
                        "hot_alert": "gpt-4o-mini",
                    },
                    "adoption_review_model": "claude-haiku-4-5-20251001",
                    "batch_sizes": {"repo_update": 10, "new_repo": 1, "hot_alert": 5},
                    "watchlist": [],
                },
            )
            insert_signals(postgres_config, [repo_signal, watch_signal, adopt_signal])

            calls: list[tuple[str, list[str]]] = []
            adopted_tasks: list[tuple[str, str]] = []

            def model_invoker(model: str, _system_prompt: str, user_prompt: str, _schema: dict) -> dict:
                context = _extract_context(user_prompt)
                signal_ids = [signal["id"] for signal in context["signals"]]
                calls.append((model, signal_ids))
                if "prior_decision" in context:
                    signal_id = signal_ids[0]
                    return {
                        "decisions": [
                            {
                                "signal_id": signal_id,
                                "decision": "adopt",
                                "confidence": 0.95,
                                "rationale": "Adopt after review",
                                "recommended_actions": ["Create follow-up task"],
                                "relevance_to_stack": "Directly relevant",
                                "urgency": "high",
                            }
                        ]
                    }
                decision_map = {
                    str(repo_signal.id): ("skip", 0.91, "Skip low-value update"),
                    str(watch_signal.id): ("watch", 0.86, "Track this repo"),
                    str(adopt_signal.id): ("adopt", 0.92, "Worth a concrete follow-up"),
                }
                return {
                    "decisions": [
                        {
                            "signal_id": signal_id,
                            "decision": decision_map[signal_id][0],
                            "confidence": decision_map[signal_id][1],
                            "rationale": decision_map[signal_id][2],
                            "recommended_actions": ["Do the obvious next thing"],
                            "relevance_to_stack": "Relevant",
                            "urgency": "medium" if signal_id != str(adopt_signal.id) else "high",
                        }
                        for signal_id in signal_ids
                    ]
                }

            def task_creator(signal: Signal, envelope) -> dict:
                adopted_tasks.append((str(signal.id), envelope.decision))
                return {"action": "adopt", "status": "created", "task_id": "eval-task-1"}

            summary = evaluate_pending_signals(
                postgres_config,
                model_invoker=model_invoker,
                task_creator=task_creator,
            )

            self.assertEqual(summary["signals_evaluated"], 3)
            self.assertEqual(summary["escalated"], 0)
            self.assertEqual(summary["auto_decisions"], {"skip": 1, "watch": 1, "adopt": 1})
            self.assertEqual(adopted_tasks, [(str(adopt_signal.id), "adopt")])
            self.assertIn(("gpt-4o-mini", [str(repo_signal.id)]), calls)
            self.assertIn(("gpt-4o-mini", [str(watch_signal.id)]), calls)
            self.assertIn(("gpt-4o-mini", [str(adopt_signal.id)]), calls)
            self.assertIn(("claude-haiku-4-5-20251001", [str(adopt_signal.id)]), calls)

            repo_row = _load_signal(postgres_config, repo_signal.id)
            watch_row = _load_signal(postgres_config, watch_signal.id)
            adopt_row = _load_signal(postgres_config, adopt_signal.id)
            self.assertEqual(repo_row[0], "skip")
            self.assertTrue(repo_row[2])
            self.assertEqual(watch_row[0], "watch")
            self.assertTrue(watch_row[2])
            self.assertEqual(adopt_row[0], "adopt")
            self.assertTrue(adopt_row[2])
            self.assertTrue(any(entry.get("task_id") == "eval-task-1" for entry in adopt_row[3]))

            evaluator_config = load_source_config(postgres_config, "evaluator")
            self.assertIsNotNone(evaluator_config)
            assert evaluator_config is not None
            watchlist = evaluator_config.config["watchlist"]
            self.assertEqual(len(watchlist), 1)
            self.assertEqual(watchlist[0]["signal_id"], str(watch_signal.id))
        finally:
            _drop_database(postgres_config)

    def test_low_confidence_adopt_escalates_and_includes_veto_context(self) -> None:
        database = f"ecosystem_intelligence_eval_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        vetoed = _signal(
            source_type="github",
            source_id="repo@old",
            signal_type="repo_update",
            title="Old repo update",
            detected_at=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
            evaluated_at=datetime(2026, 3, 14, 13, 0, tzinfo=timezone.utc),
            decision="adopt",
            decision_reason="This looked promising",
            decision_confidence=0.94,
            decided_by="gpt-4o-mini",
            acted_on=True,
            action_log=[{"action": "adopt", "status": "completed"}],
            vetoed_at=datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
            veto_reason="Too noisy for the current roadmap",
        )
        pending = _signal(
            source_type="vibez",
            source_id="msg-2",
            signal_type="community_mention",
            title="Community mention",
        )

        try:
            upsert_source_config(postgres_config, source_type="vibez", config={"api_endpoint": "http://localhost:3100"})
            insert_signals(postgres_config, [vetoed, pending])
            captured_context: dict = {}

            def model_invoker(_model: str, _system_prompt: str, user_prompt: str, _schema: dict) -> dict:
                captured_context.update(_extract_context(user_prompt))
                return {
                    "decisions": [
                        {
                            "signal_id": str(pending.id),
                            "decision": "adopt",
                            "confidence": 0.60,
                            "rationale": "Maybe useful, but unclear",
                            "recommended_actions": ["Review manually"],
                            "relevance_to_stack": "Potentially relevant",
                            "urgency": "medium",
                        }
                    ]
                }

            summary = evaluate_pending_signals(
                postgres_config,
                model_invoker=model_invoker,
                task_creator=lambda *_args, **_kwargs: {"action": "adopt", "status": "created", "task_id": "unused"},
            )

            self.assertEqual(summary["signals_evaluated"], 1)
            self.assertEqual(summary["escalated"], 1)
            self.assertEqual(summary["auto_decisions"], {})
            self.assertEqual(captured_context["veto_patterns"][0]["veto_reason"], "Too noisy for the current roadmap")

            pending_row = _load_signal(postgres_config, pending.id)
            self.assertEqual(pending_row[0], "adopt")
            self.assertFalse(pending_row[2])
            self.assertTrue(any(entry.get("action") == "inbox" for entry in pending_row[3]))
        finally:
            _drop_database(postgres_config)

    def test_legacy_openai_default_models_are_migrated_to_haiku(self) -> None:
        database = f"ecosystem_intelligence_eval_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        pending = _signal(
            source_type="github",
            source_id="repo@legacy",
            signal_type="repo_update",
            title="Legacy classifier config",
        )

        try:
            upsert_source_config(postgres_config, source_type="github", config={"wg_dir": "/tmp/wg"})
            upsert_source_config(
                postgres_config,
                source_type="evaluator",
                enabled=False,
                sync_interval_minutes=1440,
                config={
                    "signal_models": {
                        "repo_update": "gpt-4o-mini",
                        "new_repo": "gpt-4o-mini",
                        "community_mention": "gpt-4o-mini",
                        "trend": "gpt-4o-mini",
                        "hot_alert": "gpt-4o-mini",
                        "activity": "gpt-4o-mini",
                    },
                    "adoption_review_model": "claude-haiku-4-5-20251001",
                    "batch_sizes": {"repo_update": 10},
                    "watchlist": [],
                },
            )
            insert_signals(postgres_config, [pending])
            models_used: list[str] = []

            def model_invoker(model: str, _system_prompt: str, _user_prompt: str, _schema: dict) -> dict:
                models_used.append(model)
                return {
                    "decisions": [
                        {
                            "signal_id": str(pending.id),
                            "decision": "skip",
                            "confidence": 0.95,
                            "rationale": "No action needed",
                            "recommended_actions": ["Log only"],
                            "relevance_to_stack": "Low",
                            "urgency": "low",
                        }
                    ]
                }

            summary = evaluate_pending_signals(
                postgres_config,
                model_invoker=model_invoker,
                task_creator=lambda *_args, **_kwargs: {"action": "adopt", "status": "created", "task_id": "unused"},
            )

            self.assertEqual(summary["signals_evaluated"], 1)
            self.assertEqual(models_used, ["claude-haiku-4-5-20251001"])
            evaluator_config = load_source_config(postgres_config, "evaluator")
            self.assertIsNotNone(evaluator_config)
            assert evaluator_config is not None
            self.assertEqual(
                evaluator_config.config["signal_models"]["repo_update"],
                "claude-haiku-4-5-20251001",
            )
        finally:
            _drop_database(postgres_config)

    def test_expired_auto_decisions_are_finalized_on_later_runs(self) -> None:
        database = f"ecosystem_intelligence_eval_{uuid4().hex[:12]}"
        postgres_config = PostgresConfig(database=database)
        _drop_database(postgres_config)

        old_time = datetime.now(timezone.utc) - timedelta(days=3)
        acted_signal = _signal(
            source_type="github",
            source_id="repo@old",
            signal_type="repo_update",
            title="Already acted repo update",
            detected_at=old_time,
            evaluated_at=old_time,
            decision="skip",
            decision_reason="Already covered",
            decision_confidence=0.95,
            decided_by="gpt-4o-mini",
            acted_on=True,
            action_log=[
                {
                    "action": "skip",
                    "status": "completed",
                    "timestamp": old_time.isoformat(),
                    "veto_expires_at": (old_time + timedelta(hours=48)).isoformat(),
                }
            ],
        )

        try:
            upsert_source_config(postgres_config, source_type="github", config={"wg_dir": "/tmp/wg"})
            insert_signals(postgres_config, [acted_signal])

            summary = evaluate_pending_signals(
                postgres_config,
                model_invoker=lambda *_args, **_kwargs: {"decisions": []},
                task_creator=lambda *_args, **_kwargs: {"action": "adopt", "status": "created", "task_id": "unused"},
            )

            self.assertEqual(summary["metadata"]["finalized"], 1)
            acted_row = _load_signal(postgres_config, acted_signal.id)
            self.assertTrue(any(entry.get("action") == "finalize_auto_decision" for entry in acted_row[3]))
        finally:
            _drop_database(postgres_config)

    def test_default_task_creator_retries_after_timeout(self) -> None:
        signal = _signal(
            source_type="vibez",
            source_id="msg-3",
            signal_type="hot_alert",
            title="Retry adopt task creation",
        )
        envelope = DecisionEnvelope(
            signal_id=str(signal.id),
            decision="adopt",
            confidence=0.95,
            rationale="Create a follow-up task",
            recommended_actions=["Create WG task"],
            relevance_to_stack="Directly relevant",
            urgency="high",
            decided_by="claude-haiku-4-5-20251001",
        )
        calls: list[int] = []

        def fake_run(*args, **kwargs):
            calls.append(int(kwargs["timeout"]))
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        with (
            mock.patch("driftdriver.intelligence.evaluator.subprocess.run", side_effect=fake_run),
            mock.patch("driftdriver.intelligence.evaluator.time.sleep", return_value=None),
        ):
            result = default_task_creator(signal, envelope)

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(calls, [10, 10])
