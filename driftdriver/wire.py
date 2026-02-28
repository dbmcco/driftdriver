# ABOUTME: Wires standalone modules into the CLI pipeline
# ABOUTME: Provides CLI subcommand entry points for all dead-code modules

from __future__ import annotations

import hashlib
from pathlib import Path


def cmd_verify(project_dir: Path, task_contract: dict | None = None) -> dict:
    """Run verification checks and return a result dict."""
    from driftdriver.verification import verify_task_completion

    result = verify_task_completion(project_dir, task_contract or {})
    return {
        "passed": result.passed,
        "checks": result.checks,
        "warnings": result.warnings,
        "blockers": result.blockers,
    }


def cmd_loop_check(project_dir: Path, tool_name: str, tool_input: str) -> dict:
    """Record a tool action and detect if a loop is forming."""
    from driftdriver.loop_detection import detect_loop, fingerprint_action, record_action

    tool_input_hash = hashlib.sha256(tool_input.encode()).hexdigest()[:16]
    fingerprint = fingerprint_action(tool_name, tool_input_hash)
    record_action(project_dir, fingerprint)
    result = detect_loop(project_dir)
    return {
        "detected": result.detected,
        "pattern": result.pattern,
        "count": result.occurrences,
    }


def cmd_enrich(task_id: str, task_description: str, project: str, knowledge: list[dict]) -> dict:
    """Enrich a task contract with relevant prior learnings."""
    from driftdriver.contract_enrichment import enrich_contract

    result = enrich_contract(task_id, task_description, project, knowledge)
    return {
        "learnings_added": result.learnings_added,
        "contract_updated": result.contract_updated,
    }


def cmd_bridge(events_file: Path, session_id: str, project: str) -> list[dict]:
    """Parse a JSONL events file and return Lessons MCP call dicts."""
    from driftdriver.event_bridge import bridge_events, format_mcp_call

    events = bridge_events(events_file, session_id, project)
    return [format_mcp_call(e) for e in events]


def cmd_distill(events: list[dict], knowledge: list[dict], prune_threshold: float = 0.2) -> dict:
    """Distill events into knowledge and prune low-confidence entries."""
    from driftdriver.cold_distillation import distill

    result = distill(events, knowledge, prune_threshold)
    return {
        "events_processed": result.events_processed,
        "knowledge_created": result.knowledge_created,
        "entries_pruned": result.entries_pruned,
    }


def cmd_rollback_eval(drift_score: float, task_id: str, project_dir: Path) -> dict:
    """Evaluate drift score and return a rollback decision."""
    from driftdriver.rollback import evaluate_rollback

    result = evaluate_rollback(drift_score, task_id, project_dir)
    return {
        "action": result.action,
        "reason": result.reason,
        "confidence": result.confidence,
    }
