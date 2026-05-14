# tmux-monitor — Design Spec
**Date:** 2026-05-14
**Status:** Approved for implementation

---

## Overview

A tmux-layer monitor daemon that watches all panes across all sessions, detects coding agent types, summarizes activity, and writes file-based state. The primary consumers are paia-agents and other coding agents running in tmux; secondary consumer is the human operator.

The monitor lives in driftdriver as a new module (`driftdriver tmux-monitor`) and integrates with the existing presence system, event pipeline, and ecosystem hub.

### Problem

Driftdriver's presence system knows *which* agents are alive via self-reported heartbeats, but has no visibility into *what* agents are doing inside tmux. The ecosystem hub aggregates repo-level state but cannot see tmux sessions. When agents drift between tasks or collaborate across sessions, there is no coherent narrative of activity.

### Goals

- Discover and track all tmux sessions and panes dynamically
- Detect agent type per pane (Claude Code, Codex, OpenCode, Kilocode, pi.dev, shell, idle)
- Maintain rolling pane logs with bounded size (512KB max per pane)
- Generate periodic LLM summaries capturing agent drift, trajectory, and cross-session collaboration
- Write structured file-based state that paia-agents and other agents can read directly
- Emit lifecycle events into the existing driftdriver event pipeline
- Prune history to current day only

---

## Architecture

### Core Loop

A long-running Python daemon, managed by launchd. On each heartbeat:

1. **Discover** — `tmux list-sessions -F '#{session_name}'` to enumerate all sessions; `tmux list-panes -t {session} -F '#{pane_id}'` per session to enumerate panes. Diff against previous cycle to detect new/gone sessions and new/gone panes.

2. **Attach/detach pipes** — New panes get `tmux pipe-pane -t {pane_id} 'cat >> {state_dir}/panes/{session}_{pane_id}.log'`. Gone panes get `tmux pipe-pane -t {pane_id}` (no command, stops piping) and their log is finalized.

3. **Trim logs** — Any pane log exceeding 512KB gets truncated from the head (tail preserved). Checked every heartbeat cycle.

4. **Classify** — Read the tail of each pane log and match against agent signatures (prompt patterns, process names via `ps -o comm= -t /dev/ttypN`).

5. **Write state** — Structured JSON snapshot to `{state_dir}/status.json`.

6. **Emit events** — Session appeared/disappeared, pane created/destroyed, agent started/stopped, state transitions. Appended to `{state_dir}/daily/{date}.jsonl` and forwarded to the driftdriver `events.jsonl` pipeline if one exists.

### Heartbeat Schedule

| Time window | Interval |
|-------------|----------|
| 4:00 AM – 10:00 PM | 30 seconds |
| 10:00 PM – 4:00 AM | 3600 seconds (1 hour) |

Configurable via `config.json`. The daemon checks the current hour at each cycle to select the interval.

### Agent Detection (Heuristic Layer)

Runs every heartbeat. No LLM cost. Combines two signals:

**Process detection:** `ps -o pid,comm -t {tty}` to find the foreground process group for each pane's TTY.

**Pane content patterns:**

| Agent | Process name | Pane content signals |
|-------|-------------|---------------------|
| Claude Code | `claude` | `╭─` / `╰─` markers, `>` prompt, tool-use blocks |
| Codex CLI | `codex` | `codex>` prompt, `>` prompt with codex branding |
| OpenCode | `opencode` | OpenCode UI markers |
| Kilocode | `kilocode` | Kilocode prompt patterns |
| pi.dev | `pi` / `pi.dev` | pi.dev prompt patterns |
| Generic shell | `bash` / `zsh` | `$` or `#` prompt, no agent process detected |
| Idle/unknown | — | No detectable activity or process |

Classification writes a `type` field per pane into `status.json`. Detection can be extended with additional patterns without code changes via a patterns config file.

### LLM Summarization (Periodic)

Runs on a separate configurable interval (default: every 5 minutes) for agent-type panes only. For each agent pane:

1. Read the current pane log (up to 512KB of recent context)
2. Call an LLM (route resolved via the central `cognition-presets.toml` registry) with a structured prompt requesting:
   - A one-paragraph summary of what the agent has been doing (capturing drift and trajectory)
   - A `current_task` field (best guess)
   - A `related_panes` field if cross-session activity is detected (e.g., two agents working in the same repo or referencing each other)
3. Write the summary into the pane's state entry in `status.json` and append a summary event to the daily log

Summaries are cumulative — the LLM sees the raw pane log plus the previous summary for continuity.

### Cross-Session Detection

When multiple agent panes share a working directory (detected via `tmux display-message -t {pane} -p '#{pane_current_path}'`) or reference the same files/repos in their pane output, the LLM summarization step is instructed to flag these as potentially collaborating. This is a best-effort heuristic surfaced via the `related_panes` field.

---

## Output Files

All state lives under `{driftdriver_state}/tmux-monitor/` (default: `~/.local/share/driftdriver/tmux-monitor/`).

```
tmux-monitor/
├── status.json              # Current snapshot (overwritten each heartbeat)
├── panes/                   # Per-pane pipe logs (trimmed to 512KB max)
│   ├── main_0.1.log
│   └── work_1.0.3.log
├── daily/                   # Daily event/summary log (current day only)
│   └── 2026-05-14.jsonl
├── config.json              # Heartbeat schedule, LLM interval, log limits
└── known_sessions.json      # Previous cycle state for diffing (internal)
```

### status.json Structure

```json
{
  "timestamp": "2026-05-14T14:30:00Z",
  "heartbeat_interval": 30,
  "sessions": {
    "main": {
      "windows": 3,
      "panes": {
        "main:0.1": {
          "type": "claude-code",
          "pid": 48231,
          "tty": "/dev/ttys001",
          "cwd": "/Users/braydon/projects/experiments/driftdriver",
          "active_since": "2026-05-14T13:45:00Z",
          "summary": "Implementing tmux-monitor daemon. Started with core discovery loop, then moved to pipe-pane attachment logic. Currently working on log trimming.",
          "current_task": "tmux-monitor log trimming",
          "related_panes": ["work:1.0.3"],
          "llm_summary_at": "2026-05-14T14:25:00Z"
        },
        "main:0.2": {
          "type": "shell",
          "pid": 49001,
          "tty": "/dev/ttys002",
          "cwd": "/Users/braydon/projects/experiments"
        }
      }
    }
  }
}
```

Fields:
- `type` — agent classification from heuristic detection
- `pid` — foreground process ID for the pane
- `tty` — terminal device
- `cwd` — current working directory from tmux pane metadata
- `active_since` — first detection timestamp for this agent in this pane
- `summary` — LLM-generated narrative (agent panes only, absent for shells)
- `current_task` — LLM-extracted task description (agent panes only)
- `related_panes` — other panes this agent may be collaborating with
- `llm_summary_at` — timestamp of last LLM summary generation

### daily/{date}.jsonl Structure

One JSON object per line. Event types:

- `session.appeared` — new tmux session detected
- `session.disappeared` — tmux session gone
- `pane.created` — new pane in existing session
- `pane.destroyed` — pane gone
- `agent.started` — agent process detected in a pane
- `agent.stopped` — agent process no longer detected
- `agent.summary` — LLM summary generated for a pane
- `agent.task_changed` — `current_task` field changed between summaries

Each event includes: `timestamp`, `session`, `pane_id`, `event_type`, plus event-specific fields.

Auto-pruned at midnight — only the current day's file is retained.

### config.json

```json
{
  "heartbeat_day_seconds": 30,
  "heartbeat_night_seconds": 3600,
  "night_start_hour": 22,
  "night_end_hour": 4,
  "llm_summary_interval_seconds": 300,
  "max_pane_log_bytes": 524288,
  "state_dir": "~/.local/share/driftdriver/tmux-monitor"
}
```

---

## Integration Points

### Driftdriver Presence

The monitor writes presence records for detected agent sessions into `.workgraph/presence/{actor_id}.json` using the same format as the existing presence system. This means the ecosystem hub dashboard on port 8777 automatically shows tmux agent activity without modification.

### events.jsonl

Lifecycle events (session appeared/disappeared, agent started/stopped) are appended to the repo-local `.workgraph/service/runtime/events.jsonl` if the monitor detects it is running inside a driftdriver-managed repo. This integrates with the factory brain's event pipeline.

### Ecosystem Hub

The `/api/status` endpoint on port 8777 can include tmux state alongside existing repo state. The snapshot collector in `ecosystem_hub/snapshot.py` reads `tmux-monitor/status.json` and includes it in ecosystem snapshots.

### paia-agents

Agents read `status.json` directly from the well-known path. A CLI wrapper (`driftdriver tmux-status`) provides formatted output for terminal consumption.

---

## Log Size Management

Each pane log is capped at 512KB (524,288 bytes). On each heartbeat:

1. Check file size via `os.path.getsize()`
2. If exceeding limit, read the file, truncate the front portion, keep the tail
3. Write the trimmed content back

This preserves the most recent context (which is most relevant for summarization) while bounding total disk usage. With ~20 panes, worst case is ~10MB total — negligible.

---

## Dynamic Session Discovery

On each heartbeat cycle:

1. Run `tmux list-sessions -F '#{session_name}'` to get current sessions
2. Load `known_sessions.json` from previous cycle
3. **New sessions:** For each session not in known set:
   - Enumerate panes with `tmux list-panes -t {session} -F '#{pane_id} #{pane_current_path} #{pane_tty}'`
   - Attach `pipe-pane` to each pane, logging to `panes/{session}_{pane_id}.log`
   - Emit `session.appeared` and `pane.created` events
4. **Gone sessions:** For each known session not in current set:
   - Emit `session.disappeared` event
   - Finalize and clean up pane logs (leave them on disk for the daily log to reference)
   - Remove pipe-pane attachments (no-op if session is already dead)
5. **New panes in existing sessions:** Diff pane lists, attach pipes to new ones
6. **Gone panes in existing sessions:** Finalize logs, clean up
7. Write current state to `known_sessions.json` for next cycle

No static configuration — the monitor adapts to whatever tmux topology exists.

---

## Daemon Lifecycle

Managed by launchd with `KeepAlive: true` and `RunAtLoad: true`.

- **Start:** Attach pipes to all existing panes, write initial `status.json`
- **Run:** Heartbeat loop with schedule-aware intervals
- **Stop (SIGTERM):** Detach all pipes, write final state, clean exit
- **Crash recovery:** launchd restarts; next heartbeat re-discovers and re-attaches

---

## CLI Interface

```
driftdriver tmux-monitor start        # Start daemon (or via launchd)
driftdriver tmux-monitor stop         # Graceful stop
driftdriver tmux-monitor status       # Print current status summary
driftdriver tmux-monitor sessions     # List sessions with agent types
driftdriver tmux-monitor logs <pane>  # Tail a specific pane log
```

The `status` command reads `status.json` and formats it for terminal output — human-readable summary of what's running and what each agent is doing.

---

## Error Handling

- **tmux not running:** Sleep until next heartbeat, retry. Not an error condition.
- **pipe-pane fails (permission/session gone):** Log warning, skip pane, retry next cycle.
- **LLM call fails:** Skip summary for this cycle, keep stale summary in `status.json`. Don't block heartbeat.
- **Log file corruption:** Truncate to last valid newline, continue.
- **Disk full:** Stop writing logs, emit error event, continue heartbeat with capture-pane fallback.

---

## Testing Strategy

- Unit tests for agent classification (pattern matching against sample pane output)
- Unit tests for log trimming logic
- Unit tests for session diffing (new/gone sessions and panes)
- Integration test with a mock tmux (scripted session create/destroy) to verify lifecycle
- Integration test with real tmux for pipe-pane attach/detach
- LLM summarization tested with captured pane logs (deterministic with mock LLM)
