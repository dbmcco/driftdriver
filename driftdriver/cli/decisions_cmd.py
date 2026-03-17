# ABOUTME: CLI handler for 'driftdriver decisions' subcommand.
# ABOUTME: Surfaces pending human decisions at session start and on demand.
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from driftdriver.continuation_intent import write_intent
from driftdriver.decision_queue import answer_decision, read_pending_decisions, _record_to_dict


def handle_decisions_answer(
    project_dir: Path,
    *,
    decision_id: str,
    answer: str,
    answered_via: str = "cli",
) -> dict[str, Any]:
    """Answer a pending decision and flip continuation intent back to continue."""
    record = answer_decision(
        project_dir,
        decision_id=decision_id,
        answer=answer,
        answered_via=answered_via,
    )
    if record is None:
        return {"error": "decision_not_found", "decision_id": decision_id}

    # Flip intent back to continue now that the human answered
    write_intent(
        project_dir,
        intent="continue",
        set_by="human",
        reason=f"Decision {decision_id} answered via {answered_via}",
        decision_id=decision_id,
    )
    return {
        "decision_id": record.id,
        "repo": record.repo,
        "answer": record.answer,
        "answered_via": record.answered_via,
        "answered_at": record.answered_at,
        "intent_flipped": "continue",
    }


def handle_decisions_pending(project_dir: Path) -> dict[str, Any]:
    """Return a summary dict of all pending decisions for a project."""
    pending = read_pending_decisions(project_dir)
    decisions = [
        {
            "id": d.id,
            "repo": d.repo,
            "question": d.question,
            "category": d.category,
            "created_at": d.created_at,
            "context": d.context,
        }
        for d in pending
    ]
    return {"count": len(decisions), "decisions": decisions}


def format_decisions_text(result: dict[str, Any]) -> str:
    """Format pending decisions as human-readable text."""
    if result["count"] == 0:
        return "No pending decisions."
    lines = [f"=== {result['count']} Pending Decision(s) ==="]
    for dec in result["decisions"]:
        lines.append(f"  [{dec['category']}] {dec['repo']}: {dec['question']}")
        lines.append(f"    id={dec['id']}  created={dec['created_at']}")
    return "\n".join(lines)


def cmd_decisions(args: argparse.Namespace) -> int:
    """CLI entrypoint for 'driftdriver decisions pending|answer'."""
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    action = getattr(args, "action", "pending")
    as_json = bool(getattr(args, "json", False))

    if action == "answer":
        decision_id = getattr(args, "decision_id", None)
        answer_text = getattr(args, "answer_text", None)
        answered_via = getattr(args, "answered_via", "cli")
        if not decision_id or not answer_text:
            print("error: decision_id and answer_text required", file=sys.stderr)
            return 1
        result = handle_decisions_answer(
            project_dir,
            decision_id=decision_id,
            answer=answer_text,
            answered_via=answered_via,
        )
        if "error" in result:
            if as_json:
                print(json.dumps(result))
            else:
                print(f"Error: {result['error']} ({result.get('decision_id', '')})", file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps(result, indent=2, sort_keys=False))
        else:
            print(f"Answered {result['decision_id']}: {result['answer']}")
            print(f"Intent flipped to: {result['intent_flipped']}")
        return 0

    if action == "pending":
        result = handle_decisions_pending(project_dir)
        if as_json:
            print(json.dumps(result, indent=2, sort_keys=False))
        else:
            print(format_decisions_text(result))
        return 0

    print(f"error: unknown decisions action '{action}'", file=sys.stderr)
    return 1
