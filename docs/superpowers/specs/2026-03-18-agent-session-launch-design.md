# Agent Session Launch — Design Spec
**Date:** 2026-03-18
**Status:** Draft

---

## Overview

The ecosystem hub detail page (Sub-project 2) currently shows everything *about* a repo but provides no way to act on it. The most common follow-on action after reviewing a repo's state is starting a development session in it. This spec adds an **Agent Session Launch** section to the detail page: a compact UI that opens a Freshell browser terminal in the repo's directory, optionally seeded with context derived from signals already available in the hub.

Freshell (port 3550) is a browser-based terminal that can host Claude Code, Codex, or plain shell sessions. The hub's role is minimal: collect context, POST to Freshell's session API, and hand the user a URL. Session management stays entirely inside Freshell.

Four launch modes cover the common patterns from cold-start exploration to seamless session resumption. All four use the same API endpoint; the `mode` field selects the behavior.

---

## Launch Modes

### Mode 1: Fresh

Open Freshell in the repo's working directory. No context is injected. The agent starts with a clean slate — no prompt, no pre-loaded history.

**When to use:** Exploring a repo you haven't touched recently. Starting a greenfield task. Avoiding contamination from stale prior-session context.

**Context injected:** None. Only `working_directory` is passed to Freshell.

---

### Mode 2: Context-Seeded

Open Freshell with a pre-built orientation prompt injected as the initial message. The agent reads this prompt at session start and orients itself without having to ask "what are we working on?"

**When to use:** Picking up a repo you know but haven't visited in a few days. Starting a new task where prior commits and in-progress workgraph tasks are directly relevant.

**Context injected:** Full context package (see [Context Package](#context-package) section). The prompt is formatted as a structured markdown block and delivered via Freshell's `initial_prompt` session field.

**Prompt format:**

```
You are beginning a session in the <repo_name> repository.

## Repo
- Path: <repo_path>
- Role: <role>
- Tags: <tags>

## Recent Activity
<last 5 commit subjects with short SHA and relative timestamp>
<AI prose summary from activity scanner, if present>

## In-Progress Tasks
<workgraph tasks with status "in_progress", title + description>

## Last Agent Session
<most recent entry from agent_history: agent type, date, duration, tasks completed>

Orient yourself from this context and wait for instructions.
```

---

### Mode 3: Continuation

Populate the repo's `continuation_intent` field with a structured summary of the last session's state, then open Freshell with a prompt that references that intent. The intent is persisted to the snapshot so future hub loads can surface it.

**When to use:** Explicitly resuming an interrupted session. The user knows exactly what was in progress and wants the agent to pick up mid-thought rather than re-orient from scratch.

**Difference from Context-Seeded:** Mode 2 is read-only (no writes to the snapshot). Mode 3 *writes* `continuation_intent` before opening Freshell, so the intent survives page reloads and future sessions.

**`continuation_intent` schema (written to `RepoSnapshot`):**

```json
{
  "set_at": "2026-03-18T14:22:00Z",
  "agent_type": "claude-code",
  "summary": "Implementing POST /api/repo/:name/launch endpoint. Tests written, handler stub in place. Next: wire Freshell API call and return session URL.",
  "in_progress_tasks": ["task-42", "task-43"],
  "last_commit": "a1b2c3d"
}
```

The `summary` field is assembled by the hub backend from available signals (same sources as the context package). It is not LLM-generated in v1 — it is a structured template render.

**Freshell prompt** for Continuation mode prepends:

```
CONTINUATION INTENT (set <relative time>):
<continuation_intent.summary>

In-progress tasks at time of suspension: <task titles>
Last commit: <sha>

Resume from this point.
```

followed by the full context package.

---

### Mode 4: Resume

Before opening a new session, check whether an agent session is already active in this repo. If one is found, surface a link to it instead of spawning a duplicate.

**When to use:** The user may have left a session open in another browser tab or window. Resuming avoids orphaning a live agent.

**Detection (best-effort, v1):**

1. Check `presence_actors` in the current repo snapshot. If any actor has `kind: "session"` and a heartbeat age under 10 minutes, treat it as live.
2. Optionally: query Freshell's `GET /api/sessions?repo=<path>` for an active session (if Freshell exposes such an endpoint; see [Freshell Integration](#freshell-integration)).
3. If no live session is found, fall back to Mode 2 (Context-Seeded) automatically, with a note in the UI: "No active session found — launching context-seeded session instead."

**Return value differs:** Instead of `{session_url: "...new session..."}`, the API returns `{session_url: "...existing session...", resumed: true}`. The frontend shows "Resuming existing session" in the launch button feedback before opening the tab.

---

## Context Package

The context package is built entirely from data already available to the hub backend. No new network calls are made to external services at launch time.

| Field | Source |
|---|---|
| `repo_name` | `RepoSnapshot.name` |
| `repo_path` | `RepoSnapshot.path` |
| `role` | `RepoSnapshot.role` |
| `tags` | `RepoSnapshot.tags` |
| `recent_commits` | Last 5 entries from `activity-digests.json` for this repo (commit SHA, subject, relative timestamp) |
| `activity_summary` | AI prose summary from activity scanner, if present in the digest |
| `in_progress_tasks` | `RepoSnapshot.workgraph_snapshot` — tasks with `status == "in_progress"`, title + description |
| `last_agent_session` | Most recent session from the agent history computation (Sub-project 3): agent type, start time, duration, task count |
| `continuation_intent` | `RepoSnapshot.continuation_intent` if set and not stale (set within last 7 days) |

**Staleness:** The context package is assembled at request time from the latest cached snapshot. If the snapshot is stale (last updated > 5 minutes ago), the backend optionally triggers a refresh before assembling the package, but does not block the response for it. Stale context is still useful.

**Absence handling:** Each field gracefully degrades. Missing activity digest → omit recent commits section. No in-progress tasks → omit that section. No agent history → omit last session line. The prompt renders cleanly with whatever is available.

---

## Freshell Integration

Freshell runs at `http://localhost:3550`. It exposes an HTTP API for programmatic session creation.

### Session creation (assumed API contract)

```
POST http://localhost:3550/api/sessions
Content-Type: application/json

{
  "working_directory": "/absolute/path/to/repo",
  "agent_type": "claude-code",        // "claude-code" | "codex" | "shell"
  "initial_prompt": "...",            // optional; omitted for Fresh mode
  "title": "lodestar — continuation"  // optional display label
}

→ 201 Created
{
  "session_id": "abc123",
  "url": "http://localhost:3550/session/abc123"
}
```

The hub backend POSTs to this endpoint. If Freshell returns a non-2xx status or the connection is refused, the hub returns an error response to the frontend (see [Error Handling](#error-handling)).

### Live session query (Resume mode, optional)

```
GET http://localhost:3550/api/sessions?repo=/absolute/path/to/repo&active=true

→ 200 OK
{
  "sessions": [
    {
      "session_id": "xyz789",
      "url": "http://localhost:3550/session/xyz789",
      "started_at": "2026-03-18T13:45:00Z",
      "agent_type": "claude-code"
    }
  ]
}
```

If Freshell does not implement this endpoint, Resume mode falls back gracefully: `presence_actors` detection is used alone, and if that also comes up empty, the mode falls back to Context-Seeded with a UI note.

### Error handling

- **Connection refused (Freshell not running):** Return `{error: "freshell_unavailable", message: "Freshell is not running at port 3550. Start it with: npm start in the freshell directory, or check the launchd service."}`. Frontend displays this as an inline error with the instructions — not a browser alert.
- **Freshell 4xx/5xx:** Proxy the error message to the frontend. Log the full Freshell response to hub stderr.
- **Timeout:** Hub backend times out the Freshell call at 5 seconds and returns a timeout error.

---

## API

### `POST /api/repo/:name/launch`

Assemble the context package, call Freshell, return the session URL.

**Request body:**

```json
{
  "mode": "fresh" | "seeded" | "continuation" | "resume",
  "agent_type": "claude-code" | "codex" | "shell"
}
```

**Success response:**

```json
{
  "session_url": "http://localhost:3550/session/abc123",
  "resumed": false
}
```

`resumed: true` when Mode 4 found and returned an existing live session.

**Error response:**

```json
{
  "error": "freshell_unavailable",
  "message": "Freshell is not running at port 3550. Start it with: npm start in the freshell directory."
}
```

**Side effects:**

- Mode 3 (Continuation) only: writes `continuation_intent` to the repo's snapshot cache on disk before calling Freshell.
- No other side effects. No new records, no writes to workgraph.

**Backend implementation sketch:**

```python
@app.route("/api/repo/<name>/launch", methods=["POST"])
def launch_agent_session(name):
    body = request.get_json()
    mode = body.get("mode", "fresh")
    agent_type = body.get("agent_type", "claude-code")

    snapshot = get_repo_snapshot(name)
    if not snapshot:
        return jsonify({"error": "repo_not_found"}), 404

    # Mode 4: check for live session first
    if mode == "resume":
        live = detect_live_session(snapshot)
        if live:
            return jsonify({"session_url": live["url"], "resumed": True})
        # fall back to seeded
        mode = "seeded"

    # Build context package
    context_pkg = None
    if mode in ("seeded", "continuation"):
        context_pkg = build_context_package(snapshot)

    # Mode 3: persist continuation_intent
    if mode == "continuation":
        intent = build_continuation_intent(snapshot, context_pkg, agent_type)
        persist_continuation_intent(snapshot.path, intent)

    # Build Freshell payload
    payload = {
        "working_directory": snapshot.path,
        "agent_type": agent_type,
        "title": f"{name} — {mode}",
    }
    if context_pkg:
        payload["initial_prompt"] = render_context_prompt(context_pkg, mode)

    # Call Freshell
    try:
        resp = requests.post(
            "http://localhost:3550/api/sessions",
            json=payload,
            timeout=5
        )
        resp.raise_for_status()
        return jsonify({"session_url": resp.json()["url"], "resumed": False})
    except requests.ConnectionError:
        return jsonify({
            "error": "freshell_unavailable",
            "message": "Freshell is not running at port 3550. Start it with: npm start in the freshell directory."
        }), 503
    except Exception as e:
        return jsonify({"error": "freshell_error", "message": str(e)}), 502
```

---

## UI

The **Launch Agent** section appears at the bottom of the repo detail page, below the Services section (Sub-project 4).

### Layout

```
┌─────────────────────────────────────────────────────────────┐
│ Launch Agent                                                │
│                                                             │
│ Agent type:  [Claude Code ▾]                               │
│                                                             │
│ Mode:                                                       │
│   ○ Fresh          Open a clean terminal in this repo.     │
│   ○ Context-seeded  Load recent commits + tasks as a       │
│                     prompt so the agent orients quickly.   │
│   ● Continuation   Resume the last session's thread via    │
│                     continuation_intent.                   │
│   ○ Resume          Re-join a live session if one exists.  │
│                                                             │
│                         [ Launch → ]                       │
│                                                             │
│   ⚠ Freshell is not running. Start it with: npm start     │
│     in the freshell directory.        ← (shown on error)   │
└─────────────────────────────────────────────────────────────┘
```

### Behavior

- **Agent type selector:** A `<select>` with options `Claude Code`, `Codex`, `Shell`. Maps to `claude-code`, `codex`, `shell` in the API payload.
- **Mode selector:** Radio buttons. Default selection is `seeded` on first load; remembered in `localStorage` per repo (`hub_launch_mode_<repoName>`) for subsequent visits.
- **Mode descriptions:** One sentence per option, rendered inline next to each radio. Static text, not dynamic.
- **Launch button:** On click, set button to disabled + "Launching…" text. POST to `/api/repo/:name/launch`. On success, call `window.open(session_url, '_blank')`. Restore button. On error, display the error message inline below the button (not a browser alert).
- **Resume note:** If the API returns `resumed: true`, the button feedback reads "Resuming existing session…" instead of "Launching…".
- **Freshell unavailable:** Inline error below the button, dark amber background, monospace text for the start command. No other hub functionality is affected.

### No loading spinner

The Freshell call is fast (< 1 second normally). The button disabled state with "Launching…" text is sufficient feedback. No spinner is needed.

---

## Non-Goals

- **Session management inside the hub.** The hub does not list, kill, or manage Freshell sessions. Freshell owns all of that.
- **LLM-generated continuation summaries.** The `continuation_intent.summary` is assembled from structured data via template. No LLM call is made during launch. This avoids latency and cost for what is a mechanical operation.
- **tmux detection.** tmux process detection is unreliable across environments and is deferred. Resume mode uses `presence_actors` heartbeat and Freshell's session API only.
- **Multi-agent launch.** One agent session per launch. No support for spawning parallel agents from the hub.
- **Session history in the hub.** Sessions that were launched from the hub are not tracked separately. The agent history section (Sub-project 3) already covers this from the events.jsonl perspective.
- **Automatic session cleanup.** If a Freshell session is orphaned, that is Freshell's problem to solve, not the hub's.
- **Custom prompt editing.** The context-seeded and continuation prompts are not editable in the UI. If the user wants a custom prompt, they open Fresh mode and type it themselves.
- **Non-localhost Freshell.** All Freshell calls assume `http://localhost:3550`. Remote Freshell support is out of scope.
