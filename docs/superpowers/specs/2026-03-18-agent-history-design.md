# Agent History Tracking — Design Spec
**Date:** 2026-03-18
**Status:** Draft

---

## Overview

The ecosystem hub's `presence_actors` field shows who is in a repo *right now*, but the data is real-time and not persisted. There is no record of which agents worked in a repo, when they started, how long they ran, or what they accomplished. This spec adds a per-repo agent history system: a bounded timeline of past sessions computed on demand from data that already exists, surfaced in the repo detail page as a scrollable session feed.

The design has a strict YAGNI constraint: no new write paths, no new daemon, no new persistence layer. All data is read from sources already on disk.

---

## Data Sources

### Primary: `.workgraph/service/runtime/events.jsonl`

Every repo with driftdriver installed emits a flat JSONL file at this path. Observed event types from real repos:

```
session.started  — emitted on every `driftdriver install` or session hook
session.ended    — emitted on clean session exit (may be absent for crashes)
agent.spawned    — emitted when a workgraph agent process is launched
agent.died       — emitted on unclean agent exit
agent.completed  — emitted on clean agent task completion
loop.crashed     — emitted when the speedriftd dispatch loop crashes
heartbeat.stale  — emitted when a session heartbeat goes silent
tasks.exhausted  — emitted when the task queue drains
repo.discovered  — emitted on driftdriver-install (not session-related, filtered out)
```

Real event schema (from live repos):

```json
{"kind": "session.started", "repo": "lodestar", "ts": 1773249515.76, "payload": {"cli": "claude-code", "actor_id": "session-55884"}}
{"kind": "session.started", "repo": "driftdriver", "ts": 1773764456.37, "payload": {"cli": "codex", "actor_id": "session-45987"}}
```

Key observations:
- `ts` is a Unix epoch float, not ISO
- `payload.cli` is the agent type: `"claude-code"`, `"codex"`, or `"freshell"` (from `driftdriver install --cli <type>`)
- `payload.actor_id` is a stable session identifier for the run: `"session-<random>"`
- `session.ended` may be absent — a session that crashed or was killed has only `session.started`
- History goes back only as far as the file exists (no rotation/archival mechanism in place)

### Secondary: `.workgraph/graph.jsonl`

Completed tasks carry `started_at` and `completed_at` ISO timestamps and a `log` array. Tasks do **not** carry an `agent_id` field in the observed data — correlation to a session requires time-window overlap.

Done task schema (observed):

```json
{
  "id": "qadrift-e9fe1830c98abf",
  "title": "qadrift: high work-stalled",
  "status": "done",
  "started_at": "2026-03-07T04:37:10Z",
  "completed_at": "2026-03-07T15:55:56Z",
  "log": [{"timestamp": "...", "message": "Task claimed"}, ...]
}
```

### Tertiary: Git log

The git activity scanner (from the git-activity-tracking spec) already collects `git log --since="8 days ago"` per repo. The history builder re-uses the same git call or reads from the `activity-digests.json` cache. Git commits are correlated to a session by timestamp overlap: commits whose author timestamp falls within `[session.started_ts, session.ended_ts]` are attributed to that session.

---

## Session Model

A **session** is a bounded unit: one agent working in one repo from `session.started` to the next `session.ended` or an inferred end.

### Session fields

```python
@dataclass
class AgentSession:
    session_id: str           # payload.actor_id from session.started
    agent_type: str           # "claude-code" | "codex" | "freshell" | "unknown"
    started_at: float         # Unix epoch from session.started ts
    ended_at: float | None    # Unix epoch from session.ended ts; None if inferred
    duration_seconds: int | None  # ended_at - started_at; None if no end
    tasks_completed: list[str]    # task IDs completed within this session's time window
    tasks_claimed: list[str]      # task IDs claimed (started_at in window) but not necessarily done
    commits_in_window: int        # git commits whose timestamp falls in [started_at, ended_at]
    outcome: str              # "clean_exit" | "crashed" | "stalled" | "still_running" | "unknown"
    task_titles: list[str]    # titles of tasks_completed (for display, capped at 3)
```

### Outcome classification

| Condition | Outcome |
|-----------|---------|
| `session.ended` event follows `session.started` for the same `actor_id` | `clean_exit` |
| `agent.died` or `loop.crashed` falls within the session window | `crashed` |
| `heartbeat.stale` falls within the session window | `stalled` |
| `session.started` is the most recent event and ts < 10 minutes ago | `still_running` |
| None of the above (session.started with no matching end, ts > 10 min ago) | `unknown` |

### What counts as "same session"

Sessions are keyed by `actor_id`. Each `session.started` with a unique `actor_id` opens a new session. A `session.ended` closes it if the `actor_id` matches an open session. If multiple `session.started` events share the same `actor_id` (re-install without clean exit), the later one overwrites — treat the earlier one as `unknown` outcome.

If `actor_id` is absent from the payload (older events), group by `cli` + 30-second proximity to the next `session.started` from the same `cli`. This is a best-effort fallback for legacy events.

---

## History Builder

The history builder is a pure function: `events.jsonl` + `graph.jsonl` (optional) + git activity cache (optional) → `list[AgentSession]`.

### Algorithm

```python
def build_agent_history(
    repo_path: Path,
    *,
    limit: int = 20,
    activity_digest: dict | None = None,
) -> list[AgentSession]:
```

**Step 1 — Load events**

Read `.workgraph/service/runtime/events.jsonl`. Parse each line. Filter to session-relevant kinds:
`session.started`, `session.ended`, `agent.died`, `agent.completed`, `loop.crashed`, `heartbeat.stale`.

Discard `repo.discovered`, `repo_service_state`, and any other kinds.

**Step 2 — Build session spans**

Walk events sorted by `ts`. For each `session.started`:
- Open a new `AgentSession` with `session_id = payload.actor_id`, `agent_type = payload.cli`, `started_at = ts`
- Mark it `still_running` if it's the most recent and fresh

For each `session.ended`:
- Find the open session with matching `actor_id`, set `ended_at = ts`, `outcome = clean_exit`

For `agent.died` / `loop.crashed`:
- If an open session exists whose window contains the event `ts`, mark `outcome = crashed`

For `heartbeat.stale`:
- Same as above, mark `outcome = stalled`

If no matching open session for a lifecycle event, attach it to the most recently opened session from the same repo.

**Step 3 — Infer end for sessions without session.ended**

For open sessions (no `session.ended` received): set `ended_at` to the timestamp of the next `session.started` from a different `actor_id`, minus 1 second. This is a heuristic — the prior session likely ended when the new one opened. Cap inferred `ended_at` at `now`.

**Step 4 — Correlate tasks**

Load `graph.jsonl`. For each done task with `completed_at` in `[session.started_at, session.ended_at]`, add its `id` to `tasks_completed` and its `title` (truncated to 80 chars) to `task_titles` (cap at 3 titles for display). For tasks with `started_at` in the window but `status != done`, add to `tasks_claimed`.

**Step 5 — Correlate commits**

If `activity_digest` is provided and contains a `commits` list for this repo, count commits whose `timestamp` falls in the session window. Otherwise, skip — commit count is advisory.

**Step 6 — Sort and cap**

Sort sessions by `started_at` descending (most recent first). Return the first `limit` (default 20).

### Error handling

- `events.jsonl` missing → return empty list (repo not yet instrumented)
- Malformed JSON lines → skip silently
- `graph.jsonl` missing or unreadable → skip task correlation (sessions still returned)
- Git data unavailable → `commits_in_window = 0`
- No sessions found → return empty list

### Performance

`events.jsonl` is a small append-only file. In the largest observed repos it contains a few hundred lines. A full linear scan is fast enough for an on-request read — no caching required. If `graph.jsonl` grows large (>10,000 tasks), limit the task scan to records where `completed_at >= earliest_session_start` to bound the work.

---

## API Integration

Agent history is surfaced through the existing `GET /api/repo/:name` endpoint defined in the repo-detail-page spec. No new route is needed.

### Addition to `GET /api/repo/:name` response

```json
{
  "name": "lodestar",
  ...,
  "agent_history": {
    "sessions": [
      {
        "session_id": "session-55884",
        "agent_type": "codex",
        "started_at": "2026-03-18T13:00:00Z",
        "ended_at": "2026-03-18T15:23:00Z",
        "duration_seconds": 8580,
        "tasks_completed": ["t-42", "t-43"],
        "task_titles": ["Add regret scoring to scenario engine", "Wire briefing history to UI"],
        "commits_in_window": 3,
        "outcome": "clean_exit"
      },
      {
        "session_id": "session-91751",
        "agent_type": "codex",
        "started_at": "2026-03-17T22:10:00Z",
        "ended_at": "2026-03-17T22:14:00Z",
        "duration_seconds": 240,
        "tasks_completed": [],
        "task_titles": [],
        "commits_in_window": 0,
        "outcome": "unknown"
      }
    ],
    "total_sessions_in_file": 8,
    "history_since": "2026-02-01T00:00:00Z"
  }
}
```

`history_since` is the timestamp of the oldest event in `events.jsonl` — tells the caller how far back the data goes.
`total_sessions_in_file` is the count before the `limit=20` cap, so the caller can show "Showing 20 of 47 sessions."

Timestamps in the response are ISO 8601 UTC strings (converted from Unix epoch floats).

### Implementation location

Add a `_build_agent_history(repo_path: Path, *, activity_digest: dict | None) -> dict` helper in a new file:

```
driftdriver/ecosystem_hub/agent_history.py
```

Call it from `_HubHandler.do_GET` when handling `/api/repo/:name`, after the existing snapshot and activity-digest reads:

```python
from .agent_history import build_agent_history

history = build_agent_history(Path(repo_path), activity_digest=activity_entry)
payload["agent_history"] = history
```

The call is synchronous and fast. No threading or caching needed for v1.

---

## Detail Page Display

The agent history appears as a new section in the repo detail page (defined in the repo-detail-page spec), slotted between **Active Agents (Presence)** and **Repo Dependencies**.

### Section: Agent History

**Header:** "Agent History" with a muted count badge: "20 sessions"

**Empty state:** "No session history recorded. Sessions appear here after an agent runs `driftdriver install` in this repo."

**Session feed** — a compact vertical list, most recent first:

```
● codex      · 2h ago · 2h 23m · clean exit    · 2 tasks · 3 commits
  feat: add regret scoring, Wire briefing history to UI

● codex      · 8h ago · 4m     · unknown        · 0 tasks · 0 commits

● claude-code · 2 days ago · 45m · clean exit   · 1 task  · 7 commits
  Implement scenario engine core
```

**Row structure:**
- Color-coded dot: green (`clean_exit`), red (`crashed`), yellow (`stalled`), grey (`unknown`), pulsing green (`still_running`)
- Agent type badge: `codex` / `claude-code` / `freshell` in mono, same pill style as other badges in the hub
- Relative time of `started_at` ("2h ago", "3 days ago") using the existing `relTime()` helper
- Duration: `ended_at - started_at` as a human string ("2h 23m", "45m", "4m"). Omitted if `ended_at` is null.
- Outcome: one of `clean exit` / `crashed` / `stalled` / `still running` / `unknown` in muted text
- Task count: "N tasks" (0 if none)
- Commit count: "N commits" (0 if none; omit if git data unavailable)
- Second line (indented, only if `task_titles` is non-empty): comma-separated task titles, truncated at 120 chars with ellipsis

**Expand behavior:** clicking a session row expands it to show the full task list (IDs + titles) and the raw outcome detail. No new API call on expand — all data is in the initial `GET /api/repo/:name` response.

**Cap display:** show at most 20 sessions. If `total_sessions_in_file > 20`, show a muted footer: "Showing 20 of N sessions · History since \<date\>". No pagination in v1.

**Loading state:** "Loading agent history…" placeholder while `GET /api/repo/:name` is in flight (same pattern as the Git Activity section).

---

## Non-Goals

- **No cross-repo agent performance analytics** — this is per-repo history only. No aggregate "which agent is most productive" view.
- **No agent identity beyond CLI type** — all `codex` sessions look the same. There is no way to distinguish Braydon's local Codex from a remote Codex unless the `actor_id` payload grows richer. No work here to add that.
- **No alerting on agent patterns** — if a repo has 20 consecutive `unknown` outcome sessions, no notification is generated. This is a read-only display.
- **No backfill** — history only goes back as far as `events.jsonl` exists. Repos that had driftdriver installed before `session.started` events were added have no history. No synthetic reconstruction.
- **No archival or rotation** — `events.jsonl` is read as-is. If it grows very large over months, that is a separate concern. No rotation logic here.
- **No write-back to workgraph** — agent history is computed from existing data and displayed. Nothing is written to the workgraph task graph.
- **No per-task attribution fidelity guarantee** — task-to-session correlation is time-window based. If two sessions overlap (unlikely but possible with daemon + interactive session simultaneously), task attribution may be ambiguous. The display shows counts without claiming precision.
- **No UI for filtering/searching history** — the 20-session cap is the only view. Filtering by outcome or agent type is a future convenience.
