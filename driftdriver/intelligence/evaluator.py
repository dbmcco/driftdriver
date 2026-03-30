# ABOUTME: LLM-backed evaluator for ecosystem intelligence signals
# ABOUTME: Fetches pending signals, batches by type, requests decision envelopes, and applies policy-driven auto-actions

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from driftdriver.intelligence.db import PostgresConfig, ensure_database_and_apply_migrations
from driftdriver.llm_meter import extract_usage_from_claude_json, record_spend
from driftdriver.intelligence.models import Signal
from driftdriver.intelligence.store import (
    append_signal_action_log,
    list_acted_signals,
    list_recent_decisions,
    list_unevaluated_signals,
    list_vetoed_signals,
    load_source_config,
    update_signal_evaluation,
    upsert_source_config,
)


LOG = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
ANTHROPIC_DECISION_TOOL = "record_decisions"
DEFAULT_CLASSIFICATION_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_BATCH_SIZES: dict[str, int] = {
    "repo_update": 10,
    "new_repo": 1,
    "community_mention": 5,
    "trend": 5,
    "hot_alert": 5,
    "activity": 5,
}
DEFAULT_SIGNAL_MODELS: dict[str, str] = {
    "repo_update": DEFAULT_CLASSIFICATION_MODEL,
    "new_repo": DEFAULT_CLASSIFICATION_MODEL,
    "community_mention": DEFAULT_CLASSIFICATION_MODEL,
    "trend": DEFAULT_CLASSIFICATION_MODEL,
    "hot_alert": DEFAULT_CLASSIFICATION_MODEL,
    "activity": DEFAULT_CLASSIFICATION_MODEL,
}
DEFAULT_ADOPTION_REVIEW_MODEL = "claude-haiku-4-5-20251001"
LEGACY_OPENAI_SIGNAL_MODELS: dict[str, str] = {
    "repo_update": "gpt-4o-mini",
    "new_repo": "gpt-4o-mini",
    "community_mention": "gpt-4o-mini",
    "trend": "gpt-4o-mini",
    "hot_alert": "gpt-4o-mini",
    "activity": "gpt-4o-mini",
}
DEFAULT_EVALUATOR_SOURCE_TYPE = "evaluator"
DEFAULT_WATCHLIST_LIMIT = 100
AUTO_THRESHOLDS: dict[str, float] = {
    "skip": 0.80,
    "watch": 0.80,
    "defer": 0.85,
    "adopt": 0.90,
}
COMMUNITY_SIGNAL_TYPES = {"community_mention", "trend", "hot_alert"}
DECISION_SCHEMA = {
    "type": "object",
    "required": ["decisions"],
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "signal_id",
                    "decision",
                    "confidence",
                    "rationale",
                    "recommended_actions",
                    "relevance_to_stack",
                    "urgency",
                ],
                "properties": {
                    "signal_id": {"type": "string"},
                    "decision": {"type": "string", "enum": ["skip", "watch", "defer", "adopt"]},
                    "confidence": {"type": "number"},
                    "rationale": {"type": "string"},
                    "recommended_actions": {"type": "array", "items": {"type": "string"}},
                    "relevance_to_stack": {"type": "string"},
                    "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
                },
            },
        }
    },
}
STRIPPED_ENV_VARS = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}

ModelInvoker = Callable[[str, str, str, dict[str, Any]], dict[str, Any]]
TaskCreator = Callable[[Signal, "DecisionEnvelope"], dict[str, Any]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in STRIPPED_ENV_VARS}
    path = env.get("PATH", "")
    for extra in [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin"]:
        if extra not in path:
            path = f"{extra}:{path}" if path else extra
    env["PATH"] = path
    return env


def _anthropic_api_key() -> str:
    value = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if value:
        return value
    raise RuntimeError("Anthropic API key not configured. Set ANTHROPIC_API_KEY.")


def _extract_anthropic_tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        raise RuntimeError("Anthropic response missing content blocks")
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use" or block.get("name") != ANTHROPIC_DECISION_TOOL:
            continue
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            raise RuntimeError("Anthropic tool response missing structured input")
        return tool_input
    raise RuntimeError("Anthropic response missing decision tool output")


def _invoke_anthropic_api(model: str, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": model,
        "max_tokens": 2048,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        "tools": [
            {
                "name": ANTHROPIC_DECISION_TOOL,
                "description": "Return evaluator decisions matching the provided schema exactly.",
                "input_schema": schema,
                "strict": True,
            }
        ],
        "tool_choice": {"type": "tool", "name": ANTHROPIC_DECISION_TOOL},
        "disable_parallel_tool_use": True,
    }
    request = Request(
        ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": _anthropic_api_key(),
            "anthropic-version": ANTHROPIC_API_VERSION,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as response:  # noqa: S310 - fixed Anthropic API endpoint
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Anthropic API error {exc.code}: {error_body[:300]}") from exc
    return _extract_anthropic_tool_input(json.loads(body))


def _invoke_codex(model: str, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="eval-schema-", delete=False) as schema_file:
        json.dump(schema, schema_file)
        schema_path = schema_file.name
    with tempfile.NamedTemporaryFile(suffix=".json", prefix="eval-output-", delete=False) as output_file:
        output_path = output_file.name
    try:
        cmd = [
            "codex",
            "exec",
            "-m",
            model,
            "--output-schema",
            schema_path,
            "-o",
            output_path,
            "--ephemeral",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-",
        ]
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            env=_clean_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(f"codex exit {result.returncode}: {result.stderr[:300]}")
        return json.loads(Path(output_path).read_text(encoding="utf-8"))
    finally:
        for path in (schema_path, output_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _invoke_claude_cli(model: str, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Invoke via `claude --print` CLI — uses Claude Code's own auth, no ANTHROPIC_API_KEY needed."""
    prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--model", model,
        "--no-session-persistence",
    ]
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
        env=_clean_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude cli exit {result.returncode}: {result.stderr[:300]}")
    # --output-format json + --json-schema: structured output lands in outer["structured_output"]
    outer = json.loads(result.stdout)

    # Record LLM spend
    usage = extract_usage_from_claude_json(outer)
    if usage:
        record_spend(
            agent="evaluator",
            model=model,
            input_tokens=usage[0],
            output_tokens=usage[1],
        )

    if outer.get("is_error"):
        raise RuntimeError(f"claude cli error: {outer.get('result', '')[:300]}")
    structured = outer.get("structured_output")
    if isinstance(structured, dict):
        return structured
    # Fallback: parse result text as JSON
    raw = outer.get("result", "")
    if isinstance(raw, str) and raw.strip():
        return json.loads(raw)
    raise RuntimeError(f"claude cli: no structured_output and empty result")


def default_model_invoker(model: str, system_prompt: str, user_prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    lower = model.lower()
    is_claude = lower.startswith("claude") or "haiku" in lower or "sonnet" in lower or "opus" in lower
    if not is_claude:
        return _invoke_codex(model, system_prompt, user_prompt, schema)
    # Prefer direct API when key is available; fall back to claude CLI
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY"):
        return _invoke_anthropic_api(model, system_prompt, user_prompt, schema)
    return _invoke_claude_cli(model, system_prompt, user_prompt, schema)


@dataclass(frozen=True)
class DecisionEnvelope:
    signal_id: str
    decision: str
    confidence: float
    rationale: str
    recommended_actions: list[str]
    relevance_to_stack: str
    urgency: str
    decided_by: str

    def as_context(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "decision": self.decision,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "recommended_actions": self.recommended_actions,
            "relevance_to_stack": self.relevance_to_stack,
            "urgency": self.urgency,
            "decided_by": self.decided_by,
        }


@dataclass(frozen=True)
class EvaluationSummary:
    processed: int
    evaluated: int
    escalated: int
    auto_decisions: dict[str, int]
    llm_model: str | None
    llm_tokens_used: int
    finalized: int
    errors: list[str]

    def as_sync_payload(self) -> dict[str, Any]:
        return {
            "signals_evaluated": self.evaluated,
            "escalated": self.escalated,
            "auto_decisions": self.auto_decisions,
            "llm_model": self.llm_model,
            "llm_tokens_used": self.llm_tokens_used,
            "metadata": {
                "processed": self.processed,
                "finalized": self.finalized,
                "errors": self.errors,
            },
        }


def _chunk(signals: list[Signal], size: int) -> list[list[Signal]]:
    return [signals[idx : idx + size] for idx in range(0, len(signals), max(1, size))]


def _default_evaluator_config() -> dict[str, Any]:
    return {
        "signal_models": dict(DEFAULT_SIGNAL_MODELS),
        "adoption_review_model": DEFAULT_ADOPTION_REVIEW_MODEL,
        "batch_sizes": dict(DEFAULT_BATCH_SIZES),
        "watchlist": [],
        "watchlist_limit": DEFAULT_WATCHLIST_LIMIT,
    }


def _load_evaluator_config(postgres_config: PostgresConfig) -> dict[str, Any]:
    existing = load_source_config(postgres_config, DEFAULT_EVALUATOR_SOURCE_TYPE)
    if existing is not None:
        config = dict(existing.config)
        merged = _default_evaluator_config()
        merged.update(config)
        migrated = False
        if not isinstance(merged.get("signal_models"), dict):
            merged["signal_models"] = dict(DEFAULT_SIGNAL_MODELS)
            migrated = True
        elif dict(merged["signal_models"]) == LEGACY_OPENAI_SIGNAL_MODELS:
            merged["signal_models"] = dict(DEFAULT_SIGNAL_MODELS)
            migrated = True
        if not isinstance(merged.get("batch_sizes"), dict):
            merged["batch_sizes"] = dict(DEFAULT_BATCH_SIZES)
        if not isinstance(merged.get("watchlist"), list):
            merged["watchlist"] = []
        if migrated:
            _save_evaluator_config(postgres_config, merged)
        return merged
    config = _default_evaluator_config()
    upsert_source_config(
        postgres_config,
        source_type=DEFAULT_EVALUATOR_SOURCE_TYPE,
        config=config,
        enabled=False,
        sync_interval_minutes=1440,
    )
    return config


def _save_evaluator_config(postgres_config: PostgresConfig, config: dict[str, Any]) -> None:
    upsert_source_config(
        postgres_config,
        source_type=DEFAULT_EVALUATOR_SOURCE_TYPE,
        config=config,
        enabled=False,
        sync_interval_minutes=1440,
    )


def _serialize_signal(signal: Signal) -> dict[str, Any]:
    return {
        "id": str(signal.id),
        "source_type": signal.source_type,
        "source_id": signal.source_id,
        "signal_type": signal.signal_type,
        "title": signal.title,
        "detected_at": _iso(signal.detected_at),
        "raw_payload": signal.raw_payload,
    }


def _serialize_history(signal: Signal) -> dict[str, Any]:
    return {
        "signal_id": str(signal.id),
        "source_type": signal.source_type,
        "signal_type": signal.signal_type,
        "title": signal.title,
        "decision": signal.decision,
        "confidence": signal.decision_confidence,
        "reason": signal.decision_reason,
        "decided_by": signal.decided_by,
        "evaluated_at": _iso(signal.evaluated_at) if signal.evaluated_at else None,
    }


def _serialize_veto(signal: Signal) -> dict[str, Any]:
    return {
        "signal_id": str(signal.id),
        "title": signal.title,
        "decision": signal.decision,
        "decision_reason": signal.decision_reason,
        "veto_reason": signal.veto_reason,
        "vetoed_at": _iso(signal.vetoed_at) if signal.vetoed_at else None,
    }


def _system_prompt() -> str:
    return (
        "You evaluate ecosystem intelligence signals for the Speedrift/Driftdriver development stack.\n\n"
        "ECOSYSTEM CONTEXT:\n"
        "This is a personal AI-agentic software stack. The operator builds and operates:\n"
        "- Workgraph (graphwork/workgraph): task graph/dependency spine — core scheduling backbone for all repos\n"
        "- Driftdriver (dbmcco/driftdriver): drift-check orchestrator (coredrift, specdrift, uxdrift, etc.)\n"
        "- Speedrift: per-repo development workflow built on workgraph + driftdriver\n"
        "- Paia: personal AI agent platform — paia-shell, paia-memory, paia-events, paia-identity, paia-triage, plus agents samantha/derek/ingrid/caroline\n"
        "- Lodestar: situation intelligence / decision-support platform\n"
        "- LFW (lfw-ai-graph-crm): relationship intelligence CRM\n"
        "- Freshell (danshapiro/freshell): upstream shell UX layer integrated into paia-shell\n"
        "- Amplifier (microsoft/amplifier, amplifier-core, amplifier-app-cli): agentic skill runner used in speedrift recipes\n"
        "- Beads (steveyegge/beads): alternative task-tracking system — consider for paia workflow\n"
        "- Mira-OSS (taylorsatula/mira-OSS): open-source AI assistant framework — evaluate patterns for paia agents\n"
        "- Superpowers (obra/superpowers, superpowers-chrome): Claude Code skill framework — actively deployed\n"
        "- Agency (agentbureau/agency): multi-agent orchestration — evaluate for paia-triage delegation\n"
        "- Prime Radiant (prime-radiant-inc): stockyard (Firecracker VM orchestration for Claude Code), claude-session-viewer, llm-proxy, gsuite-mcp\n"
        "- Metaswarm (dsifry/metaswarm): swarm coordination — evaluate for multi-agent paia patterns\n\n"
        "SIGNAL TYPES:\n"
        "- repo_update: a monitored dependency changed SHA — decide if the stack should incorporate the update\n"
        "- activity: a tracked person/org pushed to a repo — evaluate the repo for ecosystem adoption potential\n"
        "- new_repo: a tracked person/org created a new repo — decide if worth watching or adopting\n\n"
        "DECISIONS:\n"
        "- skip: no relevance to this stack\n"
        "- watch: potentially relevant but needs more signal before acting\n"
        "- defer: relevant but not actionable now — include specific reason (e.g., 'wait for stable release', 'revisit after M7')\n"
        "- adopt: concrete action warranted — recommended_actions MUST:\n"
        "  * Name the specific ecosystem component affected (e.g., 'workgraph', 'paia-triage', 'speedrift recipes')\n"
        "  * State the concrete action (e.g., 'bump dep to pick up fix', 'evaluate API for...', 'port pattern to...')\n"
        "  Examples: 'Bump workgraph dep to pick up task-cancellation fix in paia dispatch loop'\n"
        "            'Evaluate Agency delegation API for paia-triage routing chain'\n"
        "            'Test stockyard Firecracker sandboxing for driftdriver executor isolation'\n\n"
        "Return structured JSON only. Confidence must be between 0.0 and 1.0.\n"
        "Prefer skip/watch when evidence is weak. Use adopt only when a concrete follow-up is warranted.\n"
        "recommended_actions should be short, operator-facing, specific to named ecosystem components."
    )


def _build_user_prompt(
    *,
    signals: list[Signal],
    recent_decisions: list[Signal],
    watchlist: list[dict[str, Any]],
    veto_patterns: list[Signal],
    source_metadata: dict[str, dict[str, Any]],
    prior_decision: DecisionEnvelope | None = None,
) -> str:
    context = {
        "signals": [_serialize_signal(signal) for signal in signals],
        "recent_decisions": [_serialize_history(signal) for signal in recent_decisions],
        "watchlist": watchlist,
        "veto_patterns": [_serialize_veto(signal) for signal in veto_patterns],
        "source_metadata": source_metadata,
    }
    if prior_decision is not None:
        context["prior_decision"] = prior_decision.as_context()
    return (
        "Evaluate the batch of signals and return one decision envelope per signal.\n\n"
        "Context:\n"
        f"```json\n{json.dumps(context, indent=2, sort_keys=True)}\n```"
    )


def _coerce_decisions(raw: Any, *, model: str) -> list[DecisionEnvelope]:
    if not isinstance(raw, dict):
        raise TypeError("model response must be a dict")
    decisions = raw.get("decisions")
    if not isinstance(decisions, list):
        raise TypeError("model response is missing decisions[]")
    envelopes: list[DecisionEnvelope] = []
    for item in decisions:
        if not isinstance(item, dict):
            raise TypeError("decision item must be an object")
        recommended_actions = item.get("recommended_actions")
        if not isinstance(recommended_actions, list):
            recommended_actions = []
        envelopes.append(
            DecisionEnvelope(
                signal_id=str(item.get("signal_id") or "").strip(),
                decision=str(item.get("decision") or "").strip(),
                confidence=float(item.get("confidence") or 0.0),
                rationale=str(item.get("rationale") or "").strip(),
                recommended_actions=[str(entry).strip() for entry in recommended_actions if str(entry).strip()],
                relevance_to_stack=str(item.get("relevance_to_stack") or "").strip(),
                urgency=str(item.get("urgency") or "medium").strip(),
                decided_by=model,
            )
        )
    return envelopes


def _batch_size(signal_type: str, config: dict[str, Any]) -> int:
    raw = config.get("batch_sizes")
    if isinstance(raw, dict) and raw.get(signal_type):
        try:
            return max(1, int(raw[signal_type]))
        except (TypeError, ValueError):
            pass
    if signal_type in COMMUNITY_SIGNAL_TYPES:
        return 5
    return DEFAULT_BATCH_SIZES.get(signal_type, 5)


def _model_for_signal_type(signal_type: str, config: dict[str, Any]) -> str:
    raw = config.get("signal_models")
    if isinstance(raw, dict) and raw.get(signal_type):
        return str(raw[signal_type]).strip()
    return DEFAULT_SIGNAL_MODELS.get(signal_type, DEFAULT_CLASSIFICATION_MODEL)


def _adoption_review_model(config: dict[str, Any]) -> str:
    raw = str(config.get("adoption_review_model") or "").strip()
    return raw or DEFAULT_ADOPTION_REVIEW_MODEL


def _source_metadata(postgres_config: PostgresConfig, signal_batch: list[Signal]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for source_type in sorted({signal.source_type for signal in signal_batch}):
        record = load_source_config(postgres_config, source_type)
        metadata[source_type] = {
            "config": record.config if record is not None else {},
            "last_synced_at": _iso(record.last_synced_at) if record and record.last_synced_at else None,
        }
    return metadata


def _map_envelopes(batch: list[Signal], envelopes: list[DecisionEnvelope]) -> list[tuple[Signal, DecisionEnvelope]]:
    by_id = {envelope.signal_id: envelope for envelope in envelopes}
    mapped: list[tuple[Signal, DecisionEnvelope]] = []
    for signal in batch:
        envelope = by_id.get(str(signal.id))
        if envelope is None:
            raise RuntimeError(f"missing decision for signal {signal.id}")
        mapped.append((signal, envelope))
    return mapped


def _should_auto_act(envelope: DecisionEnvelope) -> bool:
    threshold = AUTO_THRESHOLDS.get(envelope.decision)
    if threshold is None:
        return False
    return envelope.confidence >= threshold


def _make_action_entry(
    *,
    action: str,
    status: str,
    now: datetime,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "status": status,
        "timestamp": _iso(now),
    }
    if metadata:
        payload.update(metadata)
    return payload


def _watch_key(signal: Signal) -> str:
    return f"{signal.source_type}:{signal.signal_type}:{signal.source_id}"


def _update_watchlist(config: dict[str, Any], signal: Signal, envelope: DecisionEnvelope, now: datetime) -> None:
    watchlist = list(config.get("watchlist") or [])
    key = _watch_key(signal)
    entry = {
        "key": key,
        "signal_id": str(signal.id),
        "title": signal.title,
        "signal_type": signal.signal_type,
        "source_type": signal.source_type,
        "reason": envelope.rationale,
        "confidence": envelope.confidence,
        "updated_at": _iso(now),
    }
    watchlist = [item for item in watchlist if isinstance(item, dict) and item.get("key") != key]
    watchlist.insert(0, entry)
    limit = max(1, int(config.get("watchlist_limit") or DEFAULT_WATCHLIST_LIMIT))
    config["watchlist"] = watchlist[:limit]


def default_task_creator(signal: Signal, envelope: DecisionEnvelope) -> dict[str, Any]:
    date_prefix = signal.detected_at.date().isoformat()
    signal_suffix = str(signal.id).split("-")[0]
    task_id = f"ecosystem-adopt-{date_prefix}-{signal_suffix}"
    description = (
        f"Evaluate and act on ecosystem intelligence signal `{signal.id}`.\n\n"
        "```wg-contract\n"
        "schema = 1\n"
        "mode = \"core\"\n"
        "objective = \"Act on adopted ecosystem intelligence signal\"\n"
        "non_goals = [\"No broad ecosystem audit\"]\n"
        "touch = []\n"
        "acceptance = []\n"
        "max_files = 25\n"
        "max_loc = 800\n"
        "auto_followups = true\n"
        "```\n\n"
        f"Title: {signal.title}\n"
        f"Signal type: {signal.signal_type}\n"
        f"Recommended actions: {', '.join(envelope.recommended_actions) or '-'}\n"
        f"Rationale: {envelope.rationale}\n"
        f"Urgency: {envelope.urgency}\n"
    )
    cmd = [
        "wg",
        "add",
        signal.title,
        "--id",
        task_id,
        "--immediate",
        "-d",
        description,
    ]
    last_error: str | None = None
    for attempt in range(1, 3):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
                env=_clean_env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_error = f"timeout on attempt {attempt}: {exc}"
            if attempt < 2:
                time.sleep(1)
                continue
            return {
                "action": "adopt",
                "status": "failed",
                "task_id": task_id,
                "attempts": attempt,
                "error": last_error,
            }
        except OSError as exc:
            return {
                "action": "adopt",
                "status": "failed",
                "task_id": task_id,
                "attempts": attempt,
                "error": str(exc),
            }
        if result.returncode == 0:
            return {
                "action": "adopt",
                "status": "created",
                "task_id": task_id,
                "attempts": attempt,
            }
        last_error = (result.stderr or result.stdout or f"wg exit {result.returncode}")[:300]
        if attempt < 2:
            time.sleep(1)
            continue
    return {
        "action": "adopt",
        "status": "failed",
        "task_id": task_id,
        "attempts": 2,
        "error": last_error or "wg add failed",
    }


def _execute_auto_action(
    *,
    signal: Signal,
    envelope: DecisionEnvelope,
    evaluator_config: dict[str, Any],
    task_creator: TaskCreator,
    now: datetime,
) -> tuple[bool, list[dict[str, Any]]]:
    action_log = [
        _make_action_entry(
            action="decision_envelope",
            status="recorded",
            now=now,
            metadata={
                "decision": envelope.decision,
                "confidence": envelope.confidence,
                "rationale": envelope.rationale,
                "recommended_actions": envelope.recommended_actions,
                "relevance_to_stack": envelope.relevance_to_stack,
                "urgency": envelope.urgency,
                "decided_by": envelope.decided_by,
            },
        )
    ]
    veto_expires_at = now + timedelta(hours=48)
    if envelope.decision == "skip":
        action_log.append(
            _make_action_entry(
                action="skip",
                status="completed",
                now=now,
                metadata={"veto_expires_at": _iso(veto_expires_at)},
            )
        )
        return True, action_log
    if envelope.decision == "watch":
        _update_watchlist(evaluator_config, signal, envelope, now)
        action_log.append(
            _make_action_entry(
                action="watch",
                status="completed",
                now=now,
                metadata={"veto_expires_at": _iso(veto_expires_at)},
            )
        )
        return True, action_log
    if envelope.decision == "defer":
        action_log.append(
            _make_action_entry(
                action="defer",
                status="completed",
                now=now,
                metadata={"veto_expires_at": _iso(veto_expires_at)},
            )
        )
        return True, action_log
    if envelope.decision == "adopt":
        task_result = task_creator(signal, envelope)
        action_log.append(
            _make_action_entry(
                action="adopt",
                status=str(task_result.get("status") or "failed"),
                now=now,
                metadata={
                    "task_id": task_result.get("task_id"),
                    "error": task_result.get("error"),
                    "veto_expires_at": _iso(veto_expires_at),
                },
            )
        )
        return bool(task_result.get("status") == "created"), action_log
    action_log.append(_make_action_entry(action=envelope.decision, status="unknown-decision", now=now))
    return False, action_log


def _finalize_expired_auto_decisions(postgres_config: PostgresConfig, now: datetime) -> int:
    finalized = 0
    for signal in list_acted_signals(postgres_config):
        if signal.vetoed_at is not None:
            continue
        finalized_at = False
        pending_veto = False
        for entry in signal.action_log:
            if not isinstance(entry, dict):
                continue
            if entry.get("action") == "finalize_auto_decision":
                finalized_at = True
            veto_expiry = _parse_datetime(entry.get("veto_expires_at"))
            if veto_expiry is not None and veto_expiry <= now:
                pending_veto = True
        if finalized_at or not pending_veto:
            continue
        append_signal_action_log(
            postgres_config,
            signal_id=signal.id,
            action_entry=_make_action_entry(
                action="finalize_auto_decision",
                status="completed",
                now=now,
            ),
            acted_on=True,
        )
        finalized += 1
    return finalized


def _review_for_adoption(
    *,
    signal: Signal,
    envelope: DecisionEnvelope,
    recent_decisions: list[Signal],
    watchlist: list[dict[str, Any]],
    veto_patterns: list[Signal],
    source_metadata: dict[str, dict[str, Any]],
    model: str,
    model_invoker: ModelInvoker,
) -> DecisionEnvelope:
    user_prompt = _build_user_prompt(
        signals=[signal],
        recent_decisions=recent_decisions,
        watchlist=watchlist,
        veto_patterns=veto_patterns,
        source_metadata=source_metadata,
        prior_decision=envelope,
    )
    raw = model_invoker(model, _system_prompt(), user_prompt, DECISION_SCHEMA)
    reviewed = _coerce_decisions(raw, model=model)
    mapped = _map_envelopes([signal], reviewed)
    return mapped[0][1]


def evaluate_pending_signals(
    postgres_config: PostgresConfig | None = None,
    _signals: list[Signal] | None = None,
    *,
    model_invoker: ModelInvoker | None = None,
    task_creator: TaskCreator | None = None,
) -> dict[str, Any]:
    active_postgres = postgres_config or PostgresConfig()
    active_model_invoker = model_invoker or default_model_invoker
    active_task_creator = task_creator or default_task_creator
    ensure_database_and_apply_migrations(active_postgres)

    evaluator_config = _load_evaluator_config(active_postgres)
    now = _utc_now()
    finalized = _finalize_expired_auto_decisions(active_postgres, now)
    unevaluated = list_unevaluated_signals(active_postgres)
    if not unevaluated:
        summary = EvaluationSummary(
            processed=0,
            evaluated=0,
            escalated=0,
            auto_decisions={},
            llm_model=None,
            llm_tokens_used=0,
            finalized=finalized,
            errors=[],
        )
        return summary.as_sync_payload()

    recent_decisions = list_recent_decisions(active_postgres, limit=30)
    veto_patterns = list_vetoed_signals(active_postgres, limit=30)
    watchlist = list(evaluator_config.get("watchlist") or [])
    auto_counts: dict[str, int] = {}
    processed = 0
    evaluated = 0
    escalated = 0
    llm_model: str | None = None
    errors: list[str] = []

    for signal_type in sorted({signal.signal_type for signal in unevaluated}):
        group = [signal for signal in unevaluated if signal.signal_type == signal_type]
        batch_size = _batch_size(signal_type, evaluator_config)
        model = _model_for_signal_type(signal_type, evaluator_config)
        llm_model = llm_model or model
        for batch in _chunk(group, batch_size):
            processed += len(batch)
            source_metadata = _source_metadata(active_postgres, batch)
            user_prompt = _build_user_prompt(
                signals=batch,
                recent_decisions=recent_decisions,
                watchlist=watchlist,
                veto_patterns=veto_patterns,
                source_metadata=source_metadata,
            )
            try:
                raw = active_model_invoker(model, _system_prompt(), user_prompt, DECISION_SCHEMA)
                envelopes = _coerce_decisions(raw, model=model)
                mapped = _map_envelopes(batch, envelopes)
            except Exception as exc:
                error = f"{signal_type} batch failed: {exc}"
                LOG.warning("ecosystem evaluator batch failed for %s: %s", signal_type, exc)
                errors.append(error)
                continue

            for signal, envelope in mapped:
                try:
                    if envelope.decision in {"adopt", "defer"} and "haiku" not in envelope.decided_by.lower():
                        envelope = _review_for_adoption(
                            signal=signal,
                            envelope=envelope,
                            recent_decisions=recent_decisions,
                            watchlist=watchlist,
                            veto_patterns=veto_patterns,
                            source_metadata=source_metadata,
                            model=_adoption_review_model(evaluator_config),
                            model_invoker=active_model_invoker,
                        )
                except Exception as exc:
                    errors.append(f"review failed for {signal.id}: {exc}")

                decision_time = _utc_now()
                auto_action = _should_auto_act(envelope)
                if auto_action:
                    acted_on, action_log = _execute_auto_action(
                        signal=signal,
                        envelope=envelope,
                        evaluator_config=evaluator_config,
                        task_creator=active_task_creator,
                        now=decision_time,
                    )
                    if acted_on:
                        auto_counts[envelope.decision] = auto_counts.get(envelope.decision, 0) + 1
                    else:
                        escalated += 1
                        action_log.append(
                            _make_action_entry(
                                action="inbox",
                                status="pending_human",
                                now=decision_time,
                                metadata={"reason": "auto-action failed"},
                            )
                        )
                else:
                    acted_on = False
                    escalated += 1
                    action_log = [
                        _make_action_entry(
                            action="decision_envelope",
                            status="recorded",
                            now=decision_time,
                            metadata={
                                "decision": envelope.decision,
                                "confidence": envelope.confidence,
                                "rationale": envelope.rationale,
                                "recommended_actions": envelope.recommended_actions,
                                "relevance_to_stack": envelope.relevance_to_stack,
                                "urgency": envelope.urgency,
                                "decided_by": envelope.decided_by,
                            },
                        ),
                        _make_action_entry(
                            action="inbox",
                            status="pending_human",
                            now=decision_time,
                            metadata={"reason": "below_auto_threshold"},
                        ),
                    ]

                update_signal_evaluation(
                    active_postgres,
                    signal_id=signal.id,
                    evaluated_at=decision_time,
                    decision=envelope.decision,
                    decision_reason=envelope.rationale,
                    decision_confidence=envelope.confidence,
                    decided_by=envelope.decided_by,
                    acted_on=acted_on,
                    action_log=signal.action_log + action_log,
                )
                evaluated += 1
                recent_decisions.insert(
                    0,
                    Signal(
                        id=signal.id,
                        source_type=signal.source_type,
                        source_id=signal.source_id,
                        signal_type=signal.signal_type,
                        title=signal.title,
                        raw_payload=signal.raw_payload,
                        detected_at=signal.detected_at,
                        evaluated_at=decision_time,
                        decision=envelope.decision,
                        decision_reason=envelope.rationale,
                        decision_confidence=envelope.confidence,
                        decided_by=envelope.decided_by,
                        acted_on=acted_on,
                        action_log=signal.action_log + action_log,
                        vetoed_at=signal.vetoed_at,
                        veto_reason=signal.veto_reason,
                        created_at=signal.created_at,
                        updated_at=decision_time,
                    ),
                )
                recent_decisions = recent_decisions[:30]
                watchlist = list(evaluator_config.get("watchlist") or [])

    _save_evaluator_config(active_postgres, evaluator_config)
    summary = EvaluationSummary(
        processed=processed,
        evaluated=evaluated,
        escalated=escalated,
        auto_decisions=auto_counts,
        llm_model=llm_model,
        llm_tokens_used=0,
        finalized=finalized,
        errors=errors,
    )
    return summary.as_sync_payload()


def _build_parser() -> argparse.ArgumentParser:
    defaults = PostgresConfig()
    parser = argparse.ArgumentParser(description="Run the ecosystem intelligence LLM evaluation pipeline.")
    parser.add_argument("--host", default=defaults.host, help="Postgres host")
    parser.add_argument("--port", type=int, default=defaults.port, help="Postgres port")
    parser.add_argument("--user", default=defaults.user, help="Postgres user")
    parser.add_argument("--password", default=defaults.password, help="Postgres password")
    parser.add_argument("--database", default=defaults.database, help="Target database name")
    parser.add_argument("--maintenance-database", default=defaults.maintenance_database, help="CREATE DATABASE db")
    parser.add_argument("--connect-timeout", type=int, default=defaults.connect_timeout_seconds, help="Connect timeout")
    parser.add_argument("--json", action="store_true", help="Emit the evaluation summary as JSON")
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
    summary = evaluate_pending_signals(config)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            f"processed={summary['metadata']['processed']} "
            f"evaluated={summary['signals_evaluated']} "
            f"escalated={summary['escalated']} "
            f"finalized={summary['metadata']['finalized']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
