# ABOUTME: Agent session launch logic for the ecosystem hub.
# ABOUTME: Builds context packages from snapshot data and POSTs to Freshell to open sessions.
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_FRESHELL_BASE = "http://localhost:3550"
_LIVE_SESSION_HEARTBEAT_THRESHOLD = 600  # 10 minutes in seconds
_CONTINUATION_INTENT_STALENESS_DAYS = 7
_INTENT_FILENAME = ".hub-continuation-intent.json"


class FreshellUnavailableError(Exception):
    """Raised when Freshell cannot be reached at the configured URL."""


# ---------------------------------------------------------------------------
# Low-level HTTP helpers — patched in tests
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict[str, Any], timeout: int = 5) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Continuation intent persistence
# ---------------------------------------------------------------------------

def persist_continuation_intent(repo_path: Path, intent: dict[str, Any]) -> None:
    """Atomically write continuation intent JSON to repo_path/.hub-continuation-intent.json."""
    target = repo_path / _INTENT_FILENAME
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(intent, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def _load_continuation_intent(repo_path: str) -> dict[str, Any] | None:
    """Load continuation intent if it exists and is not stale."""
    intent_file = Path(repo_path) / _INTENT_FILENAME
    if not intent_file.exists():
        return None
    try:
        intent = json.loads(intent_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    set_at_str = intent.get("set_at") or ""
    try:
        set_at = datetime.fromisoformat(set_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if datetime.now(timezone.utc) - set_at > timedelta(days=_CONTINUATION_INTENT_STALENESS_DAYS):
        return None
    return intent


# ---------------------------------------------------------------------------
# Context package
# ---------------------------------------------------------------------------

def build_context_package(
    repo: dict[str, Any],
    *,
    activity_digest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble context from snapshot data and activity digest.

    All fields degrade gracefully when data is absent.
    """
    repo_name = str(repo.get("name") or "")
    repo_path = str(repo.get("path") or "")

    # Recent commits — last 5 from activity digest for this repo
    recent_commits: list[dict[str, Any]] = []
    activity_summary: str | None = None
    if activity_digest:
        for repo_entry in (activity_digest.get("repos") or []):
            if str(repo_entry.get("name") or "") == repo_name:
                timeline = repo_entry.get("timeline") or []
                recent_commits = timeline[:5]
                activity_summary = repo_entry.get("summary") or None
                break

    # In-progress tasks from workgraph_snapshot
    wg_snapshot = repo.get("workgraph_snapshot") or {}
    tasks = wg_snapshot.get("tasks") or []
    in_progress_tasks = [
        {"id": str(t.get("id") or ""),
         "title": str(t.get("title") or ""),
         "status": str(t.get("status") or ""),
         "description": str(t.get("description") or "")}
        for t in tasks
        if str(t.get("status") or "") == "in_progress"
    ]

    # Last agent session — call build_agent_history if available
    last_agent_session: dict[str, Any] | None = None
    if repo_path and Path(repo_path).is_dir():
        try:
            from .agent_history import build_agent_history
            history = build_agent_history(Path(repo_path))
            sessions = history.get("sessions") or []
            if sessions:
                last_agent_session = sessions[0]
        except Exception:
            pass

    # Continuation intent
    continuation_intent = _load_continuation_intent(repo_path) if repo_path else None

    return {
        "repo_name": repo_name,
        "repo_path": repo_path,
        "role": str(repo.get("role") or ""),
        "tags": list(repo.get("tags") or []),
        "recent_commits": recent_commits,
        "activity_summary": activity_summary,
        "in_progress_tasks": in_progress_tasks,
        "last_agent_session": last_agent_session,
        "continuation_intent": continuation_intent,
    }


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def render_context_prompt(
    pkg: dict[str, Any],
    mode: str,
) -> str | None:
    """Render the initial_prompt string for Freshell from the context package.

    Returns None for fresh mode (no prompt injected).
    """
    if mode == "fresh":
        return None

    lines: list[str] = []

    # Continuation mode header
    if mode == "continuation":
        intent = pkg.get("continuation_intent")
        if intent:
            set_at = intent.get("set_at", "unknown time")
            summary = intent.get("summary", "")
            in_prog = intent.get("in_progress_tasks") or []
            last_commit = intent.get("last_commit", "")
            lines.append(f"CONTINUATION INTENT (set {set_at}):")
            if summary:
                lines.append(summary)
            if in_prog:
                lines.append(f"In-progress tasks at time of suspension: {', '.join(in_prog)}")
            if last_commit:
                lines.append(f"Last commit: {last_commit}")
            lines.append("")
            lines.append("Resume from this point.")
            lines.append("")
            lines.append("---")
            lines.append("")

    lines.append(f"You are beginning a session in the {pkg['repo_name']} repository.")
    lines.append("")

    # Repo section
    lines.append("## Repo")
    lines.append(f"- Path: {pkg['repo_path']}")
    if pkg.get("role"):
        lines.append(f"- Role: {pkg['role']}")
    tags = pkg.get("tags") or []
    if tags:
        lines.append(f"- Tags: {', '.join(str(t) for t in tags)}")
    lines.append("")

    # Recent activity
    commits = pkg.get("recent_commits") or []
    summary = pkg.get("activity_summary")
    if commits or summary:
        lines.append("## Recent Activity")
        for c in commits:
            sha = str(c.get("sha") or "")[:7]
            subject = str(c.get("subject") or "")
            ts = str(c.get("timestamp") or "")
            lines.append(f"- {sha} {subject} ({ts})")
        if summary:
            lines.append(summary)
        lines.append("")

    # In-progress tasks
    tasks = pkg.get("in_progress_tasks") or []
    if tasks:
        lines.append("## In-Progress Tasks")
        for t in tasks:
            title = str(t.get("title") or "")
            desc = str(t.get("description") or "")
            lines.append(f"- {title}" + (f": {desc}" if desc else ""))
        lines.append("")

    # Last agent session
    last_session = pkg.get("last_agent_session")
    if last_session:
        agent_type = str(last_session.get("agent_type") or "unknown")
        started = str(last_session.get("started_at") or "")
        duration = last_session.get("duration_seconds")
        task_count = len(last_session.get("tasks_completed") or [])
        dur_str = f"{duration // 60}m" if duration else "unknown duration"
        lines.append("## Last Agent Session")
        lines.append(f"- Agent: {agent_type}, started {started}, {dur_str}, {task_count} tasks completed")
        lines.append("")

    lines.append("Orient yourself from this context and wait for instructions.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Continuation intent builder
# ---------------------------------------------------------------------------

def build_continuation_intent(
    repo: dict[str, Any],
    context_pkg: dict[str, Any],
    *,
    agent_type: str,
) -> dict[str, Any]:
    """Build a continuation_intent dict from available context. Not LLM-generated."""
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    in_progress_ids = [t["id"] for t in (context_pkg.get("in_progress_tasks") or [])]

    commits = context_pkg.get("recent_commits") or []
    last_commit = commits[0].get("sha", "")[:7] if commits else ""

    # Summary: structured template — not LLM
    tasks_str = ", ".join(
        t["title"] for t in (context_pkg.get("in_progress_tasks") or [])
    )
    summary_parts = []
    if tasks_str:
        summary_parts.append(f"In progress: {tasks_str}.")
    if last_commit:
        summary_parts.append(f"Last commit: {last_commit}.")
    summary = " ".join(summary_parts) if summary_parts else "Session state captured."

    return {
        "set_at": now_iso,
        "agent_type": agent_type,
        "summary": summary,
        "in_progress_tasks": in_progress_ids,
        "last_commit": last_commit,
    }


# ---------------------------------------------------------------------------
# Live session detection
# ---------------------------------------------------------------------------

def detect_live_session(
    repo: dict[str, Any],
    *,
    freshell_base_url: str | None = _FRESHELL_BASE,
) -> dict[str, Any] | None:
    """Detect an active session via presence_actors or Freshell API.

    Returns a dict with at least {"url": str} if a live session is found,
    or None if no live session detected.
    """
    # Strategy 1: presence_actors heartbeat
    actors = repo.get("presence_actors") or []
    for actor in actors:
        if str(actor.get("kind") or "") == "session":
            age = actor.get("heartbeat_age_seconds") or 9999
            if int(age) < _LIVE_SESSION_HEARTBEAT_THRESHOLD:
                actor_id = str(actor.get("actor_id") or "")
                url = (f"{freshell_base_url}/session/{actor_id}"
                       if freshell_base_url and actor_id else None)
                return {"url": url, "actor_id": actor_id, "source": "presence"}

    # Strategy 2: Freshell session API
    if freshell_base_url:
        repo_path = str(repo.get("path") or "")
        try:
            encoded = urllib.parse.quote(repo_path, safe="")
            data = _http_get_json(
                f"{freshell_base_url}/api/sessions?repo={encoded}&active=true"
            )
            sessions = (data.get("sessions") or []) if isinstance(data, dict) else []
            if sessions:
                return {
                    "url": str(sessions[0].get("url") or ""),
                    "session_id": str(sessions[0].get("session_id") or ""),
                    "source": "freshell",
                }
        except Exception:
            pass

    return None


# ---------------------------------------------------------------------------
# Main launch function
# ---------------------------------------------------------------------------

def launch_session(
    repo: dict[str, Any],
    *,
    mode: str,
    agent_type: str,
    activity_digest: dict[str, Any] | None = None,
    freshell_base_url: str = _FRESHELL_BASE,
) -> dict[str, Any]:
    """Launch (or resume) an agent session in Freshell.

    Returns {"session_url": str, "resumed": bool}.
    Raises FreshellUnavailableError if Freshell cannot be reached.
    """
    repo_name = str(repo.get("name") or "")
    repo_path = str(repo.get("path") or "")

    # Mode 4: Resume — check for live session first
    if mode == "resume":
        live = detect_live_session(repo, freshell_base_url=freshell_base_url)
        if live and live.get("url"):
            return {"session_url": live["url"], "resumed": True}
        # Fall back to seeded
        mode = "seeded"

    # Build context package for seeded and continuation modes
    context_pkg: dict[str, Any] | None = None
    if mode in ("seeded", "continuation"):
        context_pkg = build_context_package(repo, activity_digest=activity_digest)

    # Mode 3: Continuation — persist intent before calling Freshell
    if mode == "continuation" and context_pkg is not None:
        intent = build_continuation_intent(repo, context_pkg, agent_type=agent_type)
        persist_continuation_intent(Path(repo_path), intent)

    # Build Freshell payload
    payload: dict[str, Any] = {
        "working_directory": repo_path,
        "agent_type": agent_type,
        "title": f"{repo_name} \u2014 {mode}",
    }
    if context_pkg is not None:
        prompt = render_context_prompt(context_pkg, mode=mode)
        if prompt:
            payload["initial_prompt"] = prompt

    # Call Freshell
    try:
        resp = _http_post_json(
            f"{freshell_base_url}/api/sessions",
            payload,
            timeout=5,
        )
        session_url = str(resp.get("url") or "")
        return {"session_url": session_url, "resumed": False}
    except ConnectionError as exc:
        raise FreshellUnavailableError(
            f"Freshell is not running at {freshell_base_url}. "
            "Start it with: npm start in the freshell directory, "
            "or check the launchd service."
        ) from exc
    except Exception as exc:
        raise FreshellUnavailableError(
            f"Freshell error: {exc!s}"
        ) from exc
