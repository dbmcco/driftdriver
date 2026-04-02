# ABOUTME: Chat handler for the factory chatbot — LLM invocation, context assembly,
# ABOUTME: prompt building, response parsing, and conversation state management.
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA

logger = logging.getLogger(__name__)

# Actions that require user confirmation before execution
DESTRUCTIVE_ACTIONS = frozenset({"kill_daemon", "kill_process", "unenroll", "restart_paia_service"})

# Max conversation history pairs per chat_id
_MAX_HISTORY_PAIRS = 10

# In-memory conversation state: chat_id -> list of {"role": ..., "content": ...}
_conversations: dict[str, list[dict[str, str]]] = {}

# In-memory pending confirmations: chat_id -> directive dict
_pending_confirmations: dict[str, dict[str, Any]] = {}

# Structured output schema for chat responses
_CHAT_SCHEMA = {
    "type": "object",
    "required": ["reply_text", "directives", "needs_confirmation"],
    "properties": {
        "reply_text": {
            "type": "string",
            "description": "Human-readable response for Telegram",
        },
        "directives": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string"},
                    "params": {"type": "object"},
                },
            },
        },
        "needs_confirmation": {
            "type": "boolean",
            "description": "Whether the action(s) require user confirmation before execution",
        },
    },
}

# Env vars to strip for clean subprocess invocation (matches brain.py)
_STRIPPED_ENV_VARS = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}

# Decision ID pattern for pre-screening
_DECISION_ID_RE = re.compile(r"dec-\d{8}-[a-f0-9]{6}")


def _clean_env() -> dict[str, str]:
    """Return env dict safe for spawning CLI subprocesses."""
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_VARS}
    # Ensure claude CLI is on PATH (launchd doesn't inherit shell profile)
    path = env.get("PATH", "")
    for extra in [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin"]:
        if extra not in path:
            path = f"{extra}:{path}"
    env["PATH"] = path
    return env


def needs_confirmation(directives: list[dict[str, Any]]) -> bool:
    """Return True if any directive in the list requires user confirmation."""
    for d in directives:
        action = d.get("action", "")
        if action in DESTRUCTIVE_ACTIONS:
            return True
        if action == "set_mode" and isinstance(d.get("params"), dict):
            if d["params"].get("mode") == "autonomous":
                return True
    return False


def build_chat_context(
    *,
    snapshot: dict[str, Any] | None = None,
    roster: dict[str, Any] | None = None,
    events_history: list[dict[str, Any]] | None = None,
    brain_log: list[dict[str, Any]] | None = None,
    pending_decisions: list[dict[str, Any]] | None = None,
    speedriftd_states: dict[str, dict[str, Any]] | None = None,
    velocity_data: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> str:
    """Assemble full factory state context for the chat prompt."""
    sections: list[str] = []

    if roster is not None:
        sections.append(f"## Enrolled Repos (Roster)\n```json\n{json.dumps(roster, indent=2)}\n```")

    if snapshot is not None:
        # Include repo-level summaries rather than the full multi-MB snapshot
        repos = snapshot.get("repos", [])
        repo_summaries = []
        for r in repos:
            if not isinstance(r, dict):
                continue
            repo_summaries.append({
                "name": r.get("name"),
                "activity_state": r.get("activity_state"),
                "narrative": r.get("narrative"),
                "task_counts": r.get("task_counts"),
                "in_progress": r.get("in_progress"),
                "ready": r.get("ready"),
                "stalled": r.get("stalled"),
                "stall_reasons": r.get("stall_reasons"),
                "service_running": r.get("service_running"),
                "runtime": r.get("runtime"),
                "presence_actors": r.get("presence_actors"),
            })
        sections.append(f"## Repo Snapshots\n```json\n{json.dumps(repo_summaries, indent=2)}\n```")

        overview = snapshot.get("overview")
        if overview:
            sections.append(f"## Ecosystem Overview\n```json\n{json.dumps(overview, indent=2)}\n```")

        narrative = snapshot.get("narrative")
        if narrative:
            sections.append(f"## Ecosystem Narrative\n{narrative}")

    if pending_decisions:
        sections.append(f"## Pending Decisions\n```json\n{json.dumps(pending_decisions, indent=2)}\n```")

    if events_history:
        sections.append(f"## Event History\n```json\n{json.dumps(events_history, indent=2)}\n```")

    if brain_log:
        sections.append(f"## Brain Invocation Log\n```json\n{json.dumps(brain_log, indent=2)}\n```")

    if speedriftd_states:
        sections.append(f"## Speedriftd Mode/Lease per Repo\n```json\n{json.dumps(speedriftd_states, indent=2)}\n```")

    if velocity_data:
        sections.append(f"## Velocity & Convergence\n```json\n{json.dumps(velocity_data, indent=2)}\n```")

    if conversation_history:
        sections.append(f"## Conversation History\n```json\n{json.dumps(conversation_history, indent=2)}\n```")

    return "\n\n".join(sections)


def build_chat_prompt() -> str:
    """Build the system prompt defining the chatbot persona."""
    actions_list = ", ".join(sorted(DIRECTIVE_SCHEMA.keys()))
    destructive_list = ", ".join(sorted(DESTRUCTIVE_ACTIONS))

    return (
        "You are the Factory Operator's interface — a conversational bot for the "
        "speedrift dark factory ecosystem. You help the operator (Braydon) monitor, "
        "query, and control all enrolled repos.\n\n"
        "## What you can do\n"
        "- Answer questions about repo status, active agents, task progress, events, decisions\n"
        "- Issue directives to control the factory (start/stop loops, kill daemons, adjust concurrency, etc.)\n"
        "- Advise on strategy (attractor targets, enrollment, convergence)\n\n"
        "## Directive Vocabulary\n"
        f"Available actions: {actions_list}\n"
        "Each action takes params as defined in the directive schema. "
        "Use action names exactly as listed.\n"
        "Note: There is no restart_dispatch_loop — use stop_dispatch_loop followed by start_dispatch_loop.\n\n"
        "## Confirmation Rules\n"
        f"Destructive actions that ALWAYS require confirmation: {destructive_list}\n"
        "Additionally, set_mode requires confirmation ONLY when params.mode == 'autonomous'.\n"
        "For these, set needs_confirmation: true and explain what will happen.\n"
        "All other actions can execute immediately (needs_confirmation: false).\n\n"
        "## Response Format\n"
        "For read-only queries: reply with information, empty directives list, needs_confirmation: false.\n"
        "For action requests: include the directive(s) and set needs_confirmation appropriately.\n"
        "For conversational/advisory queries: reason over state and give advice without directives "
        "unless the operator explicitly asks for action.\n\n"
        "## Telegram Formatting\n"
        "Keep replies concise. Use limited Markdown (bold, code, lists). "
        "Avoid long code blocks. Prefer bullet points for multi-item responses.\n"
    )


def invoke_chat(system_prompt: str, context: str, user_message: str, timeout: int = 120) -> dict[str, Any]:
    """Invoke Claude Sonnet via CLI with the chat-specific JSON schema.

    Returns parsed structured output dict with keys: reply_text, directives, needs_confirmation.
    Raises RuntimeError on CLI failure.
    """
    full_prompt = f"{system_prompt}\n\n---\n\n{context}\n\n---\n\n## User Message\n{user_message}"

    cmd = [
        "claude",
        "-p",
        "--model", "sonnet",
        "--output-format", "json",
        "--json-schema", json.dumps(_CHAT_SCHEMA),
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--max-budget-usd", "2.00",
    ]

    result = subprocess.run(
        cmd,
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"claude exit {result.returncode}: {result.stderr[:300]}")

    cli_output = json.loads(result.stdout)

    # --json-schema puts structured data in "structured_output"
    data = cli_output.get("structured_output") if isinstance(cli_output, dict) else None
    if data is not None:
        return data

    # Fallback: parse "result" field as JSON
    raw = cli_output.get("result", "") if isinstance(cli_output, dict) else result.stdout
    return json.loads(raw) if isinstance(raw, str) else raw


def parse_chat_response(raw: Any) -> dict[str, Any]:
    """Extract reply_text, directives, needs_confirmation from LLM output.

    Handles malformed/missing fields gracefully — always returns a usable dict.
    """
    if not isinstance(raw, dict):
        return {
            "reply_text": "Sorry, I couldn't process that — brain returned an invalid response. Try again or rephrase.",
            "directives": [],
            "needs_confirmation": False,
        }

    reply_text = raw.get("reply_text")
    if not isinstance(reply_text, str) or not reply_text.strip():
        reply_text = "Sorry, I couldn't process that — brain returned an invalid response. Try again or rephrase."

    directives = raw.get("directives")
    if not isinstance(directives, list):
        directives = []
    else:
        # Filter to only valid directive dicts with an action field
        directives = [
            d for d in directives
            if isinstance(d, dict) and isinstance(d.get("action"), str)
        ]

    needs_conf = raw.get("needs_confirmation")
    if not isinstance(needs_conf, bool):
        # Infer from directives if the field is missing/wrong
        needs_conf = needs_confirmation(directives)

    return {
        "reply_text": reply_text,
        "directives": directives,
        "needs_confirmation": needs_conf,
    }


def get_conversation(chat_id: str) -> list[dict[str, str]]:
    """Get conversation history for a chat_id."""
    return list(_conversations.get(chat_id, []))


def append_conversation(chat_id: str, role: str, content: str) -> None:
    """Append a message to conversation history, evicting oldest pairs if over limit."""
    if chat_id not in _conversations:
        _conversations[chat_id] = []
    _conversations[chat_id].append({"role": role, "content": content})
    # Each pair = 2 entries, cap at _MAX_HISTORY_PAIRS * 2
    max_entries = _MAX_HISTORY_PAIRS * 2
    if len(_conversations[chat_id]) > max_entries:
        _conversations[chat_id] = _conversations[chat_id][-max_entries:]


def set_pending_confirmation(chat_id: str, confirmation: dict[str, Any]) -> None:
    """Store a pending confirmation for a chat_id."""
    _pending_confirmations[chat_id] = confirmation


def get_pending_confirmation(chat_id: str) -> dict[str, Any] | None:
    """Retrieve and clear a pending confirmation for a chat_id."""
    return _pending_confirmations.pop(chat_id, None)


def clear_pending_confirmation(chat_id: str) -> None:
    """Clear any pending confirmation for a chat_id."""
    _pending_confirmations.pop(chat_id, None)


def extract_decision_id(message: str) -> str | None:
    """Extract a decision ID from a message, if present."""
    match = _DECISION_ID_RE.search(message)
    return match.group(0) if match else None


def load_authorized_chat_ids(config_path: Path | None = None) -> set[str]:
    """Load authorized chat_ids from notify.toml [telegram_factory] section."""
    import tomllib

    path = config_path or (Path.home() / ".config" / "workgraph" / "notify.toml")
    if not path.exists():
        return set()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        # Check both [telegram_factory] and [telegram] sections
        tf = data.get("telegram_factory", {})
        chat_id = tf.get("chat_id")
        if chat_id:
            return {str(chat_id)}
        # Fallback to [telegram] section
        tg = data.get("telegram", {})
        chat_id = tg.get("chat_id")
        if chat_id:
            return {str(chat_id)}
        return set()
    except Exception:
        logger.warning("Failed to load authorized chat_ids from %s", path)
        return set()


def handle_chat_message(
    *,
    message: str,
    chat_id: str,
    user_name: str = "",
    snapshot: dict[str, Any] | None = None,
    roster: dict[str, Any] | None = None,
    events_history: list[dict[str, Any]] | None = None,
    brain_log: list[dict[str, Any]] | None = None,
    pending_decisions: list[dict[str, Any]] | None = None,
    speedriftd_states: dict[str, dict[str, Any]] | None = None,
    velocity_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Main entry point for processing a chat message.

    Returns dict with keys: reply, directives_executed, pending_confirmation.
    """
    # Auth: reject unknown chat_ids with no LLM call
    authorized = load_authorized_chat_ids()
    if authorized and chat_id not in authorized:
        return {
            "reply": "Unauthorized chat_id.",
            "directives_executed": [],
            "pending_confirmation": None,
        }

    # Check for pending confirmation — "yes"/"y" confirms, "no"/"n" cancels,
    # anything else cancels and processes normally
    pending = get_pending_confirmation(chat_id)
    if pending is not None:
        normalized = message.strip().lower()
        if normalized in ("yes", "y"):
            # Execute the pending directive(s)
            from driftdriver.factory_brain.directives import (
                Directive,
                execute_directives,
            )

            repo_paths = _build_repo_paths(snapshot)
            directives_to_exec = [
                Directive(action=d["action"], params=d.get("params", {}))
                for d in pending.get("directives", [])
            ]
            results = execute_directives(directives_to_exec, repo_paths=repo_paths)
            reply = f"Done. Executed {len(results)} directive(s)."
            append_conversation(chat_id, "user", message)
            append_conversation(chat_id, "assistant", reply)
            return {
                "reply": reply,
                "directives_executed": results,
                "pending_confirmation": None,
            }
        elif normalized in ("no", "n"):
            cancel_reply = "Cancelled."
            append_conversation(chat_id, "user", message)
            append_conversation(chat_id, "assistant", cancel_reply)
            return {
                "reply": cancel_reply,
                "directives_executed": [],
                "pending_confirmation": None,
            }
        else:
            # Any other message cancels and processes normally
            pass

    # Decision ID pre-screening
    decision_id = extract_decision_id(message)
    if decision_id:
        # Extract the answer — everything after the decision ID
        answer_text = message[message.index(decision_id) + len(decision_id):].strip()
        if not answer_text:
            answer_text = "approved"
        return {
            "reply": f"Routing decision {decision_id} with answer: {answer_text}",
            "directives_executed": [],
            "pending_confirmation": None,
            "decision_route": {
                "decision_id": decision_id,
                "answer": answer_text,
            },
        }

    # Build context and invoke LLM
    conversation_history = get_conversation(chat_id)
    system_prompt = build_chat_prompt()
    context = build_chat_context(
        snapshot=snapshot,
        roster=roster,
        events_history=events_history,
        brain_log=brain_log,
        pending_decisions=pending_decisions,
        speedriftd_states=speedriftd_states,
        velocity_data=velocity_data,
        conversation_history=conversation_history,
    )

    try:
        raw_response = invoke_chat(system_prompt, context, message)
    except Exception as exc:
        logger.warning("Chat LLM invocation failed: %s", exc)
        error_reply = "Sorry, I couldn't process that — brain returned an invalid response. Try again or rephrase."
        append_conversation(chat_id, "user", message)
        append_conversation(chat_id, "assistant", error_reply)
        return {
            "reply": error_reply,
            "directives_executed": [],
            "pending_confirmation": None,
        }

    parsed = parse_chat_response(raw_response)
    reply_text = parsed["reply_text"]
    directives = parsed["directives"]

    # Record conversation
    append_conversation(chat_id, "user", message)
    append_conversation(chat_id, "assistant", reply_text)

    # If directives need confirmation, store them and return without executing
    if parsed["needs_confirmation"] or needs_confirmation(directives):
        confirmation = {
            "directives": directives,
            "reply_text": reply_text,
        }
        set_pending_confirmation(chat_id, confirmation)
        return {
            "reply": reply_text,
            "directives_executed": [],
            "pending_confirmation": confirmation,
        }

    # Execute safe directives immediately
    executed = []
    if directives:
        from driftdriver.factory_brain.directives import (
            Directive,
            execute_directives,
        )

        repo_paths = _build_repo_paths(snapshot)
        directives_to_exec = [
            Directive(action=d["action"], params=d.get("params", {}))
            for d in directives
        ]
        executed = execute_directives(directives_to_exec, repo_paths=repo_paths)

    return {
        "reply": reply_text,
        "directives_executed": executed,
        "pending_confirmation": None,
    }


def _build_repo_paths(snapshot: dict[str, Any] | None) -> dict[str, str]:
    """Extract repo name -> path mapping from snapshot for directive execution."""
    if not snapshot:
        return {}
    paths: dict[str, str] = {}
    for r in snapshot.get("repos", []):
        if isinstance(r, dict):
            name = str(r.get("name") or "")
            path = str(r.get("path") or "")
            if name and path:
                paths[name] = path
    return paths
