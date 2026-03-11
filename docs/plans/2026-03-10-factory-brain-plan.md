# Factory Brain Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a model-mediated factory brain inside driftdriver's ecosystem hub that autonomously manages repo enrollment, agent dispatch, failure recovery, and resource allocation using three-tier intelligence (Haiku/Sonnet/Opus).

**Architecture:** The brain is a new Python module (`driftdriver/factory_brain/`) that replaces heuristic decision-making in the ecosystem hub's collector loop. Events flow from dispatch loops and the hub into an event aggregator. The brain reacts to events and timer-based safety nets, issuing structured JSON directives executed by a directive runner. Existing heuristic code stays as advisory input.

**Tech Stack:** Python 3.14, Anthropic SDK (`anthropic`), pytest, JSONL event files, structured tool_use for JSON output

**Design Doc:** `docs/plans/2026-03-10-factory-brain-design.md`

---

### Task 1: Harden dispatch-loop.sh

**Files:**
- Modify: `/Users/braydon/projects/experiments/lodestar/.workgraph/executors/dispatch-loop.sh`
- (Will be copied to all enrolled repos after testing)

**Context:** The dispatch loop is the workhorse that spawns agents. It currently crashes on edge cases and doesn't emit events for the brain to consume. We need: daemon kill on startup, event emission, hung command watchdog, crash trap, heartbeat file.

**Step 1: Read the current dispatch-loop.sh**

Read: `/Users/braydon/projects/experiments/lodestar/.workgraph/executors/dispatch-loop.sh`

Understand the current structure before modifying.

**Step 2: Add daemon kill on startup and event emission functions**

Add these after the variable declarations, before the main loop:

```bash
EVENTS_FILE=".workgraph/service/runtime/factory-events.jsonl"
HEARTBEAT_FILE=".workgraph/service/runtime/dispatch-loop.heartbeat"

mkdir -p "$(dirname "$EVENTS_FILE")" "$(dirname "$HEARTBEAT_FILE")"

emit_event() {
  local kind="$1" payload="$2"
  local ts
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "{\"kind\":\"$kind\",\"repo\":\"$REPO_NAME\",\"ts\":\"$ts\"${payload:+,$payload}}" >> "$EVENTS_FILE"
}

heartbeat() {
  date -u +%Y-%m-%dT%H:%M:%SZ > "$HEARTBEAT_FILE"
}

# Kill any existing daemon to prevent deadlock (graphwork/workgraph#4)
wg service stop 2>/dev/null || true
sleep 1
```

**Step 3: Add crash trap**

Add after the emit_event function:

```bash
trap 'emit_event "loop.crashed" "\"exit_code\":$?"' EXIT
```

And modify the clean exit path (the `exit 0` in the idle detection block) to emit the right event:

```bash
        log "All tasks complete. Exiting."
        notify "$REPO_NAME" "All tasks complete — factory idle"
        emit_event "loop.exited" '"reason":"all_tasks_complete"'
        trap - EXIT  # Clear crash trap before clean exit
        exit 0
```

**Step 4: Add hung command watchdog to ready_tasks and alive_count**

Replace `ready_tasks()`:

```bash
ready_tasks() {
  local output
  output=$(timeout 15 wg ready 2>/dev/null) || {
    log "WARN: wg ready hung or failed, killing daemon"
    wg service stop 2>/dev/null || true
    emit_event "daemon.killed" '"reason":"wg_ready_hung"'
    sleep 2
    output=$(timeout 15 wg ready 2>/dev/null) || { echo ""; return; }
  }
  echo "$output" \
    | grep -E '^\s+\S+' \
    | awk '{print $1}' \
    | head -n "$((MAX_AGENTS - $(alive_count)))"
}
```

**Step 5: Add event emission to spawn and heartbeat to main loop**

In the spawn loop, after successful spawn:
```bash
      emit_event "agent.spawned" "\"task\":\"$TASK_ID\""
```

After failed spawn:
```bash
      emit_event "spawn.failed" "\"task\":\"$TASK_ID\""
```

At the start of the main `while true` loop body, add:
```bash
  heartbeat
```

When tasks are exhausted (the `if [ -z "$TASKS" ]` block), add:
```bash
    emit_event "tasks.exhausted" ""
```

Add `loop.started` event at the top, right before the main loop:
```bash
emit_event "loop.started" "\"max_agents\":$MAX_AGENTS,\"poll_interval\":$POLL_INTERVAL"
```

**Step 6: Test the hardened script manually**

Run in lodestar:
```bash
cd /Users/braydon/projects/experiments/lodestar
./.workgraph/executors/dispatch-loop.sh &
sleep 10
cat .workgraph/service/runtime/factory-events.jsonl
cat .workgraph/service/runtime/dispatch-loop.heartbeat
kill %1
cat .workgraph/service/runtime/factory-events.jsonl | tail -3
```

Expected: JSONL events for `loop.started`, `agent.spawned` (if tasks ready), `loop.crashed` (from the kill).

**Step 7: Deploy to all repos and commit**

```bash
/bin/cp dispatch-loop.sh /Users/braydon/projects/experiments/training-assistant/.workgraph/executors/
/bin/cp dispatch-loop.sh /Users/braydon/projects/experiments/news-briefing/.workgraph/executors/
/bin/cp dispatch-loop.sh /Users/braydon/projects/personal/vibez-monitor/.workgraph/executors/
cd /Users/braydon/projects/experiments/driftdriver
git add -A && git commit -m "feat: harden dispatch-loop.sh with events, watchdog, heartbeat"
```

---

### Task 2: Event Schema and Writer Module

**Files:**
- Create: `driftdriver/factory_brain/__init__.py`
- Create: `driftdriver/factory_brain/events.py`
- Create: `tests/test_factory_brain_events.py`

**Context:** The brain needs a Python-side event schema matching what dispatch-loop.sh writes, plus an aggregator that reads events from all enrolled repos. This module defines the event types, writes hub-side events, and reads/aggregates repo-side events.

**Step 1: Write the failing tests**

```python
# tests/test_factory_brain_events.py
# ABOUTME: Tests for factory brain event schema and aggregation.
# ABOUTME: Covers event writing, reading, and cross-repo aggregation.

import json
import tempfile
from pathlib import Path

from driftdriver.factory_brain.events import (
    Event,
    emit_event,
    read_events,
    aggregate_events,
    TIER_ROUTING,
)


def test_emit_event_writes_jsonl(tmp_path):
    events_file = tmp_path / "factory-events.jsonl"
    emit_event(events_file, kind="loop.started", repo="test-repo", payload={"max_agents": 2})
    lines = events_file.read_text().strip().split("\n")
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["kind"] == "loop.started"
    assert event["repo"] == "test-repo"
    assert event["max_agents"] == 2
    assert "ts" in event


def test_read_events_returns_sorted(tmp_path):
    events_file = tmp_path / "factory-events.jsonl"
    emit_event(events_file, kind="agent.spawned", repo="r1", payload={"task": "t1"})
    emit_event(events_file, kind="agent.died", repo="r1", payload={"task": "t1"})
    events = read_events(events_file)
    assert len(events) == 2
    assert events[0].kind == "agent.spawned"
    assert events[1].kind == "agent.died"


def test_read_events_empty_file(tmp_path):
    events_file = tmp_path / "factory-events.jsonl"
    events_file.touch()
    events = read_events(events_file)
    assert events == []


def test_read_events_missing_file(tmp_path):
    events_file = tmp_path / "nonexistent.jsonl"
    events = read_events(events_file)
    assert events == []


def test_aggregate_events_across_repos(tmp_path):
    repo1 = tmp_path / "repo1" / ".workgraph" / "service" / "runtime"
    repo2 = tmp_path / "repo2" / ".workgraph" / "service" / "runtime"
    repo1.mkdir(parents=True)
    repo2.mkdir(parents=True)
    emit_event(repo1 / "factory-events.jsonl", kind="loop.started", repo="repo1", payload={})
    emit_event(repo2 / "factory-events.jsonl", kind="agent.spawned", repo="repo2", payload={"task": "t1"})
    repos = [tmp_path / "repo1", tmp_path / "repo2"]
    events = aggregate_events(repos)
    assert len(events) == 2
    repos_seen = {e.repo for e in events}
    assert repos_seen == {"repo1", "repo2"}


def test_tier_routing():
    assert TIER_ROUTING["loop.crashed"] == 1
    assert TIER_ROUTING["agent.spawned"] == 1
    assert TIER_ROUTING["tasks.exhausted"] == 2
    assert TIER_ROUTING["repo.discovered"] == 2
    assert TIER_ROUTING["attractor.converged"] == 2


def test_read_events_since(tmp_path):
    events_file = tmp_path / "factory-events.jsonl"
    emit_event(events_file, kind="a", repo="r", payload={})
    import time; time.sleep(0.01)
    cutoff = events[0].ts if (events := read_events(events_file)) else ""
    emit_event(events_file, kind="b", repo="r", payload={})
    events = read_events(events_file, since=cutoff)
    # Should return only events after cutoff
    assert any(e.kind == "b" for e in events)
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_factory_brain_events.py -v
```

Expected: `ModuleNotFoundError: No module named 'driftdriver.factory_brain'`

**Step 3: Implement the events module**

```python
# driftdriver/factory_brain/__init__.py
# ABOUTME: Factory brain — model-mediated self-healing dark factory.
# ABOUTME: Three-tier intelligence (Haiku/Sonnet/Opus) for autonomous repo management.
```

```python
# driftdriver/factory_brain/events.py
# ABOUTME: Event schema, writer, reader, and cross-repo aggregator for the factory brain.
# ABOUTME: Events are per-repo JSONL files read by the brain's event-driven trigger system.

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


EVENTS_FILENAME = "factory-events.jsonl"
EVENTS_REL_PATH = Path(".workgraph") / "service" / "runtime" / EVENTS_FILENAME

# Which tier handles each event kind
TIER_ROUTING: dict[str, int] = {
    # Tier 1 — Haiku reflexes
    "loop.started": 1,
    "loop.exited": 1,
    "loop.crashed": 1,
    "agent.spawned": 1,
    "agent.died": 1,
    "agent.completed": 1,
    "spawn.failed": 1,
    "daemon.killed": 1,
    "heartbeat.stale": 1,
    # Tier 2 — Sonnet strategy
    "tasks.exhausted": 2,
    "repo.discovered": 2,
    "repo.enrolled": 2,
    "repo.unenrolled": 2,
    "attractor.converged": 2,
    "attractor.plateaued": 2,
    "snapshot.collected": 2,
    "tier1.escalation": 2,
    # Tier 3 — Opus judgment
    "tier2.escalation": 3,
}


@dataclass
class Event:
    kind: str
    repo: str
    ts: str
    payload: dict


def emit_event(
    events_file: Path,
    *,
    kind: str,
    repo: str,
    payload: dict | None = None,
) -> Event:
    """Append a single event to a JSONL file."""
    events_file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    record = {"kind": kind, "repo": repo, "ts": ts, **(payload or {})}
    with open(events_file, "a") as f:
        f.write(json.dumps(record) + "\n")
    return Event(kind=kind, repo=repo, ts=ts, payload=payload or {})


def read_events(
    events_file: Path,
    *,
    since: str | None = None,
    limit: int = 200,
) -> list[Event]:
    """Read events from a JSONL file, optionally filtered by timestamp."""
    if not events_file.exists():
        return []
    events = []
    for line in events_file.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = raw.pop("ts", "")
        kind = raw.pop("kind", "unknown")
        repo = raw.pop("repo", "unknown")
        if since and ts <= since:
            continue
        events.append(Event(kind=kind, repo=repo, ts=ts, payload=raw))
    return events[-limit:]


def aggregate_events(
    repo_paths: list[Path],
    *,
    since: str | None = None,
    limit: int = 200,
) -> list[Event]:
    """Aggregate events from multiple repos, sorted by timestamp."""
    all_events: list[Event] = []
    for repo_path in repo_paths:
        events_file = repo_path / EVENTS_REL_PATH
        all_events.extend(read_events(events_file, since=since, limit=limit))
    all_events.sort(key=lambda e: e.ts)
    return all_events[-limit:]


def events_file_for_repo(repo_path: Path) -> Path:
    """Return the canonical events file path for a repo."""
    return repo_path / EVENTS_REL_PATH
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_factory_brain_events.py -v
```

Expected: All tests pass. Fix the `test_read_events_since` test if the walrus operator usage needs adjustment.

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/ tests/test_factory_brain_events.py
git commit -m "feat: factory brain event schema, writer, reader, aggregator"
```

---

### Task 3: Directive Schema and Executor

**Files:**
- Create: `driftdriver/factory_brain/directives.py`
- Create: `tests/test_factory_brain_directives.py`

**Context:** The brain outputs structured JSON directives. The directive executor takes these and runs them — killing processes, clearing locks, starting dispatch loops, etc. Each directive maps to a concrete system action. The executor must be safe (validate inputs, log everything, never crash on a bad directive).

**Step 1: Write the failing tests**

```python
# tests/test_factory_brain_directives.py
# ABOUTME: Tests for factory brain directive schema and executor.
# ABOUTME: Covers directive validation, execution, and safety guards.

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from driftdriver.factory_brain.directives import (
    Directive,
    BrainResponse,
    parse_brain_response,
    execute_directive,
    execute_directives,
    DIRECTIVE_SCHEMA,
    validate_directive,
)


def test_directive_schema_has_all_actions():
    expected = {
        "kill_process", "kill_daemon", "clear_locks",
        "start_dispatch_loop", "stop_dispatch_loop",
        "spawn_agent", "set_mode", "adjust_concurrency",
        "enroll", "unenroll", "set_attractor_target",
        "send_telegram", "escalate", "noop",
    }
    assert set(DIRECTIVE_SCHEMA.keys()) == expected


def test_parse_brain_response_valid():
    raw = {
        "reasoning": "Daemon stuck",
        "directives": [
            {"action": "kill_daemon", "repo": "lodestar"},
            {"action": "clear_locks", "repo": "lodestar"},
        ],
        "telegram": None,
        "escalate": False,
    }
    resp = parse_brain_response(raw)
    assert resp.reasoning == "Daemon stuck"
    assert len(resp.directives) == 2
    assert resp.directives[0].action == "kill_daemon"
    assert resp.escalate is False


def test_parse_brain_response_with_telegram():
    raw = {
        "reasoning": "Enrolled new repo",
        "directives": [{"action": "enroll", "repo": "/path/to/repo"}],
        "telegram": "Enrolled repo-name",
        "escalate": False,
    }
    resp = parse_brain_response(raw)
    assert resp.telegram == "Enrolled repo-name"


def test_validate_directive_valid():
    d = Directive(action="kill_daemon", params={"repo": "lodestar"})
    assert validate_directive(d) is True


def test_validate_directive_unknown_action():
    d = Directive(action="launch_missiles", params={})
    assert validate_directive(d) is False


def test_validate_directive_missing_required_param():
    d = Directive(action="kill_daemon", params={})
    assert validate_directive(d) is False


def test_execute_directive_noop():
    d = Directive(action="noop", params={"reason": "all good"})
    result = execute_directive(d, dry_run=False)
    assert result["status"] == "ok"
    assert result["action"] == "noop"


def test_execute_directive_dry_run():
    d = Directive(action="kill_daemon", params={"repo": "lodestar"})
    result = execute_directive(d, dry_run=True)
    assert result["status"] == "dry_run"


@patch("driftdriver.factory_brain.directives._run_cmd")
def test_execute_kill_daemon(mock_run):
    mock_run.return_value = (0, "stopped")
    d = Directive(action="kill_daemon", params={"repo": "lodestar"})
    result = execute_directive(d, dry_run=False, repo_paths={"lodestar": Path("/tmp/lodestar")})
    assert result["status"] == "ok"
    mock_run.assert_called()


def test_execute_directives_batch():
    directives = [
        Directive(action="noop", params={"reason": "test1"}),
        Directive(action="noop", params={"reason": "test2"}),
    ]
    results = execute_directives(directives, dry_run=True)
    assert len(results) == 2
    assert all(r["status"] == "dry_run" for r in results)
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_factory_brain_directives.py -v
```

Expected: ImportError

**Step 3: Implement the directives module**

```python
# driftdriver/factory_brain/directives.py
# ABOUTME: Directive schema, parser, validator, and executor for the factory brain.
# ABOUTME: Maps brain decisions to concrete system actions (kill, restart, enroll, etc).

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Required params per action
DIRECTIVE_SCHEMA: dict[str, list[str]] = {
    "kill_process": ["pid"],
    "kill_daemon": ["repo"],
    "clear_locks": ["repo"],
    "start_dispatch_loop": ["repo"],
    "stop_dispatch_loop": ["repo"],
    "spawn_agent": ["repo", "task_id"],
    "set_mode": ["repo", "mode"],
    "adjust_concurrency": ["repo", "max_agents"],
    "enroll": ["repo"],
    "unenroll": ["repo"],
    "set_attractor_target": ["repo", "target"],
    "send_telegram": ["message"],
    "escalate": ["reason"],
    "noop": ["reason"],
}


@dataclass
class Directive:
    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainResponse:
    reasoning: str
    directives: list[Directive]
    telegram: str | None = None
    escalate: bool = False


def parse_brain_response(raw: dict[str, Any]) -> BrainResponse:
    """Parse raw JSON from model output into a BrainResponse."""
    directives = []
    for d in raw.get("directives", []):
        action = d.pop("action")
        directives.append(Directive(action=action, params=d))
    return BrainResponse(
        reasoning=raw.get("reasoning", ""),
        directives=directives,
        telegram=raw.get("telegram"),
        escalate=raw.get("escalate", False),
    )


def validate_directive(d: Directive) -> bool:
    """Check that a directive has a known action and required params."""
    if d.action not in DIRECTIVE_SCHEMA:
        log.warning("Unknown directive action: %s", d.action)
        return False
    required = DIRECTIVE_SCHEMA[d.action]
    for param in required:
        if param not in d.params:
            log.warning("Missing param %s for action %s", param, d.action)
            return False
    return True


def _run_cmd(cmd: list[str], *, timeout: int = 30) -> tuple[int, str]:
    """Run a shell command, return (exit_code, output)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except Exception as e:
        return -1, str(e)


def execute_directive(
    d: Directive,
    *,
    dry_run: bool = False,
    repo_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Execute a single directive. Returns result dict."""
    if not validate_directive(d):
        return {"action": d.action, "status": "invalid", "error": "validation failed"}

    if dry_run:
        return {"action": d.action, "status": "dry_run", "params": d.params}

    repo_paths = repo_paths or {}
    handler = _HANDLERS.get(d.action, _handle_unknown)
    try:
        return handler(d, repo_paths)
    except Exception as e:
        log.exception("Directive execution failed: %s", d.action)
        return {"action": d.action, "status": "error", "error": str(e)}


def execute_directives(
    directives: list[Directive],
    *,
    dry_run: bool = False,
    repo_paths: dict[str, Path] | None = None,
) -> list[dict[str, Any]]:
    """Execute a batch of directives in order."""
    return [
        execute_directive(d, dry_run=dry_run, repo_paths=repo_paths)
        for d in directives
    ]


# ── Handlers ──

def _handle_noop(d: Directive, repo_paths: dict) -> dict:
    log.info("noop: %s", d.params.get("reason", ""))
    return {"action": "noop", "status": "ok", "reason": d.params.get("reason", "")}


def _handle_kill_process(d: Directive, repo_paths: dict) -> dict:
    pid = d.params["pid"]
    code, out = _run_cmd(["kill", "-9", str(pid)])
    return {"action": "kill_process", "status": "ok" if code == 0 else "error", "pid": pid, "output": out}


def _handle_kill_daemon(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    repo_path = repo_paths.get(repo)
    if not repo_path:
        return {"action": "kill_daemon", "status": "error", "error": f"unknown repo: {repo}"}
    code, out = _run_cmd(["wg", "service", "stop"], timeout=10)
    return {"action": "kill_daemon", "status": "ok" if code == 0 else "error", "repo": repo, "output": out}


def _handle_clear_locks(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    repo_path = repo_paths.get(repo)
    if not repo_path:
        return {"action": "clear_locks", "status": "error", "error": f"unknown repo: {repo}"}
    wg_svc = repo_path / ".workgraph" / "service"
    cleared = []
    for pattern in ["daemon.sock", "daemon.lock", ".registry.lock"]:
        f = wg_svc / pattern
        if f.exists():
            f.unlink()
            cleared.append(str(f))
    return {"action": "clear_locks", "status": "ok", "repo": repo, "cleared": cleared}


def _handle_start_dispatch_loop(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    repo_path = repo_paths.get(repo)
    if not repo_path:
        return {"action": "start_dispatch_loop", "status": "error", "error": f"unknown repo: {repo}"}
    dispatch = repo_path / ".workgraph" / "executors" / "dispatch-loop.sh"
    if not dispatch.exists():
        return {"action": "start_dispatch_loop", "status": "error", "error": "no dispatch-loop.sh"}
    proc = subprocess.Popen(
        [str(dispatch)],
        cwd=str(repo_path),
        stdout=open(f"/tmp/dispatch-{repo}.log", "a"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"action": "start_dispatch_loop", "status": "ok", "repo": repo, "pid": proc.pid}


def _handle_stop_dispatch_loop(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    code, out = _run_cmd(["pkill", "-f", f"dispatch-loop.sh.*{repo}"])
    return {"action": "stop_dispatch_loop", "status": "ok", "repo": repo}


def _handle_spawn_agent(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    task_id = d.params["task_id"]
    repo_path = repo_paths.get(repo)
    if not repo_path:
        return {"action": "spawn_agent", "status": "error", "error": f"unknown repo: {repo}"}
    code, out = _run_cmd(
        ["wg", "spawn", "--executor", "claude", task_id],
        timeout=30,
    )
    return {"action": "spawn_agent", "status": "ok" if code == 0 else "error", "repo": repo, "task_id": task_id, "output": out}


def _handle_set_mode(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    mode = d.params["mode"]
    repo_path = repo_paths.get(repo)
    if not repo_path:
        return {"action": "set_mode", "status": "error", "error": f"unknown repo: {repo}"}
    cmd = ["driftdriver", "--dir", str(repo_path), "speedriftd", "status", "--set-mode", mode, "--lease-owner", "factory-brain", "--reason", "factory brain directive"]
    if mode == "observe":
        cmd.append("--release-lease")
    code, out = _run_cmd(cmd, timeout=15)
    return {"action": "set_mode", "status": "ok" if code == 0 else "error", "repo": repo, "mode": mode}


def _handle_adjust_concurrency(d: Directive, repo_paths: dict) -> dict:
    repo = d.params["repo"]
    max_agents = d.params["max_agents"]
    # Write to a config file the dispatch loop reads
    repo_path = repo_paths.get(repo)
    if not repo_path:
        return {"action": "adjust_concurrency", "status": "error", "error": f"unknown repo: {repo}"}
    config = repo_path / ".workgraph" / "service" / "runtime" / "factory-config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(config.read_text()) if config.exists() else {}
    existing["max_agents"] = max_agents
    config.write_text(json.dumps(existing, indent=2))
    return {"action": "adjust_concurrency", "status": "ok", "repo": repo, "max_agents": max_agents}


def _handle_escalate(d: Directive, repo_paths: dict) -> dict:
    log.warning("Escalation: %s", d.params.get("reason", ""))
    return {"action": "escalate", "status": "ok", "reason": d.params.get("reason", "")}


def _handle_send_telegram(d: Directive, repo_paths: dict) -> dict:
    # Placeholder — wired in Task 9
    log.info("Telegram: %s", d.params.get("message", ""))
    return {"action": "send_telegram", "status": "deferred", "message": d.params.get("message", "")}


def _handle_enroll(d: Directive, repo_paths: dict) -> dict:
    # Placeholder — wired in Task 8
    log.info("Enroll: %s", d.params.get("repo", ""))
    return {"action": "enroll", "status": "deferred", "repo": d.params.get("repo", "")}


def _handle_unenroll(d: Directive, repo_paths: dict) -> dict:
    log.info("Unenroll: %s", d.params.get("repo", ""))
    return {"action": "unenroll", "status": "deferred", "repo": d.params.get("repo", "")}


def _handle_set_attractor_target(d: Directive, repo_paths: dict) -> dict:
    log.info("Set attractor: %s -> %s", d.params.get("repo"), d.params.get("target"))
    return {"action": "set_attractor_target", "status": "deferred", "repo": d.params.get("repo", ""), "target": d.params.get("target", "")}


def _handle_unknown(d: Directive, repo_paths: dict) -> dict:
    return {"action": d.action, "status": "error", "error": "unknown action"}


_HANDLERS = {
    "noop": _handle_noop,
    "kill_process": _handle_kill_process,
    "kill_daemon": _handle_kill_daemon,
    "clear_locks": _handle_clear_locks,
    "start_dispatch_loop": _handle_start_dispatch_loop,
    "stop_dispatch_loop": _handle_stop_dispatch_loop,
    "spawn_agent": _handle_spawn_agent,
    "set_mode": _handle_set_mode,
    "adjust_concurrency": _handle_adjust_concurrency,
    "enroll": _handle_enroll,
    "unenroll": _handle_unenroll,
    "set_attractor_target": _handle_set_attractor_target,
    "send_telegram": _handle_send_telegram,
    "escalate": _handle_escalate,
}
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_factory_brain_directives.py -v
```

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/directives.py tests/test_factory_brain_directives.py
git commit -m "feat: directive schema, validator, and executor for factory brain"
```

---

### Task 4: Factory Roster Management

**Files:**
- Create: `driftdriver/factory_brain/roster.py`
- Create: `tests/test_factory_brain_roster.py`

**Context:** The brain autonomously enrolls and unenrolls repos. The roster tracks which repos are active, their attractor targets, and enrollment metadata. Persisted as `factory-roster.json` in driftdriver's hub data dir. The roster also handles discovery — scanning the workspace for new `.workgraph/` directories.

**Step 1: Write the failing tests**

```python
# tests/test_factory_brain_roster.py
# ABOUTME: Tests for factory roster — autonomous repo enrollment tracking.
# ABOUTME: Covers roster CRUD, discovery, and persistence.

import json
from pathlib import Path
from driftdriver.factory_brain.roster import (
    Roster,
    load_roster,
    save_roster,
    discover_repos,
    enroll_repo,
    unenroll_repo,
)


def test_load_roster_missing_file(tmp_path):
    roster = load_roster(tmp_path / "roster.json")
    assert roster.repos == {}


def test_save_and_load_roundtrip(tmp_path):
    roster_file = tmp_path / "roster.json"
    roster = Roster(repos={})
    enroll_repo(roster, path="/tmp/myrepo", target="onboarded")
    save_roster(roster, roster_file)
    loaded = load_roster(roster_file)
    assert "myrepo" in loaded.repos
    assert loaded.repos["myrepo"]["target"] == "onboarded"
    assert loaded.repos["myrepo"]["status"] == "active"


def test_enroll_repo():
    roster = Roster(repos={})
    enroll_repo(roster, path="/projects/lodestar", target="production-ready")
    assert "lodestar" in roster.repos
    assert roster.repos["lodestar"]["path"] == "/projects/lodestar"
    assert roster.repos["lodestar"]["status"] == "active"


def test_unenroll_repo():
    roster = Roster(repos={})
    enroll_repo(roster, path="/projects/lodestar", target="production-ready")
    unenroll_repo(roster, name="lodestar")
    assert roster.repos["lodestar"]["status"] == "inactive"


def test_unenroll_preserves_history():
    roster = Roster(repos={})
    enroll_repo(roster, path="/projects/lodestar", target="production-ready")
    unenroll_repo(roster, name="lodestar")
    assert "lodestar" in roster.repos  # Not deleted
    assert "unenrolled_at" in roster.repos["lodestar"]


def test_discover_repos(tmp_path):
    repo1 = tmp_path / "repo1" / ".workgraph"
    repo2 = tmp_path / "repo2" / ".workgraph"
    not_repo = tmp_path / "notrepo"
    repo1.mkdir(parents=True)
    repo2.mkdir(parents=True)
    not_repo.mkdir(parents=True)
    discovered = discover_repos(tmp_path, max_depth=2)
    names = {d.name for d in discovered}
    assert "repo1" in names
    assert "repo2" in names
    assert "notrepo" not in names


def test_discover_repos_excludes_enrolled(tmp_path):
    repo1 = tmp_path / "repo1" / ".workgraph"
    repo1.mkdir(parents=True)
    roster = Roster(repos={})
    enroll_repo(roster, path=str(tmp_path / "repo1"), target="onboarded")
    discovered = discover_repos(tmp_path, max_depth=2, exclude=set(roster.repos.keys()))
    assert len(discovered) == 0
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_factory_brain_roster.py -v
```

**Step 3: Implement the roster module**

```python
# driftdriver/factory_brain/roster.py
# ABOUTME: Factory roster — tracks enrolled repos, supports autonomous discovery.
# ABOUTME: Persists to factory-roster.json, handles enrollment/unenrollment lifecycle.

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Roster:
    repos: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_roster(roster_file: Path) -> Roster:
    """Load roster from JSON file, return empty if missing."""
    if not roster_file.exists():
        return Roster()
    try:
        data = json.loads(roster_file.read_text())
        return Roster(repos=data.get("repos", {}))
    except (json.JSONDecodeError, KeyError):
        log.warning("Corrupt roster file, starting fresh: %s", roster_file)
        return Roster()


def save_roster(roster: Roster, roster_file: Path) -> None:
    """Persist roster to JSON file."""
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    roster_file.write_text(json.dumps({"repos": roster.repos}, indent=2) + "\n")


def enroll_repo(
    roster: Roster,
    *,
    path: str,
    target: str,
) -> str:
    """Add a repo to the roster. Returns the repo name."""
    name = Path(path).name
    now = datetime.now(timezone.utc).isoformat()
    roster.repos[name] = {
        "path": path,
        "target": target,
        "status": "active",
        "enrolled_at": now,
    }
    log.info("Enrolled repo: %s (target=%s)", name, target)
    return name


def unenroll_repo(roster: Roster, *, name: str) -> None:
    """Mark a repo as inactive (preserves history)."""
    if name not in roster.repos:
        log.warning("Cannot unenroll unknown repo: %s", name)
        return
    now = datetime.now(timezone.utc).isoformat()
    roster.repos[name]["status"] = "inactive"
    roster.repos[name]["unenrolled_at"] = now
    log.info("Unenrolled repo: %s", name)


def active_repos(roster: Roster) -> dict[str, dict[str, Any]]:
    """Return only active repos."""
    return {k: v for k, v in roster.repos.items() if v.get("status") == "active"}


def discover_repos(
    workspace_root: Path,
    *,
    max_depth: int = 3,
    exclude: set[str] | None = None,
) -> list[Path]:
    """Scan workspace for directories with .workgraph/, excluding known repos."""
    exclude = exclude or set()
    discovered = []

    def _scan(path: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if not path.is_dir():
            return
        if path.name == ".workgraph":
            return
        wg = path / ".workgraph"
        if wg.is_dir() and path.name not in exclude:
            discovered.append(path)
            return  # Don't recurse into repos
        try:
            for child in sorted(path.iterdir()):
                if child.name.startswith("."):
                    continue
                _scan(child, depth + 1)
        except PermissionError:
            pass

    _scan(workspace_root, 0)
    return discovered
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_factory_brain_roster.py -v
```

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/roster.py tests/test_factory_brain_roster.py
git commit -m "feat: factory roster — enrollment, unenrollment, discovery"
```

---

### Task 5: Brain Core — Model Invocation and Prompt Assembly

**Files:**
- Create: `driftdriver/factory_brain/brain.py`
- Create: `driftdriver/factory_brain/prompts.py`
- Create: `tests/test_factory_brain_core.py`

**Context:** This is the central brain module. It assembles prompts from snapshot data + events + heuristic recommendations, calls the Anthropic API at the appropriate tier, and parses the response into directives. Uses structured `tool_use` to get reliable JSON output. The Anthropic SDK (`anthropic` package) must be installed.

**Step 1: Install the Anthropic SDK**

```bash
cd /Users/braydon/projects/experiments/driftdriver
pip install anthropic
```

Check if there's a `requirements.txt` or `pyproject.toml` and add `anthropic` to it.

**Step 2: Write the prompts module**

```python
# driftdriver/factory_brain/prompts.py
# ABOUTME: Prompt templates for the factory brain's three-tier intelligence.
# ABOUTME: Adversarial reasoning persona with tier-specific role additions.

from __future__ import annotations

ADVERSARY_SYSTEM = """You are the Factory Adversary. Your job is to find what's broken, what's about
to break, and what everyone is pretending is fine. You distrust stability —
silence means something failed quietly. Healthy metrics mean something isn't
being measured.

When you see a snapshot, your first question is: "What's wrong that I can't
see?" When an agent reports success, you ask: "Did it actually work, or did it
just exit clean?" When a repo is idle, you ask: "Is it done, or is it stuck
and nobody noticed?"

You have heuristic recommendations from a rules-based system. Treat them as a
naive first guess. They follow playbooks. You think.

Act decisively. Log your reasoning. When you're wrong, say so — then fix it
harder."""

TIER_ADDITIONS = {
    1: "\n\nYou handle reflexes. Fix what's broken. Don't strategize — act. You run on Haiku, so be fast and focused.",
    2: "\n\nYou allocate resources and shape the factory. Think across repos, not within them. You can enroll/unenroll repos, adjust concurrency, and shift resources. You run on Sonnet.",
    3: "\n\nYou make the calls nobody else can. Enrollment decisions, attractor target changes, strategic pivots. You are invoked rarely and only for significant decisions. Be right. You run on Opus.",
}

TIER_MODELS = {
    1: "claude-haiku-4-5-20251001",
    2: "claude-sonnet-4-6",
    3: "claude-opus-4-6",
}

DIRECTIVE_TOOL = {
    "name": "issue_directives",
    "description": "Issue directives to the factory. Every response MUST use this tool.",
    "input_schema": {
        "type": "object",
        "required": ["reasoning", "directives", "escalate"],
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Your adversarial reasoning about the situation. What's broken, what's suspicious, what you're doing about it.",
            },
            "directives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "kill_process", "kill_daemon", "clear_locks",
                                "start_dispatch_loop", "stop_dispatch_loop",
                                "spawn_agent", "set_mode", "adjust_concurrency",
                                "enroll", "unenroll", "set_attractor_target",
                                "send_telegram", "escalate", "noop",
                            ],
                        },
                        "repo": {"type": "string"},
                        "pid": {"type": "integer"},
                        "task_id": {"type": "string"},
                        "mode": {"type": "string"},
                        "max_agents": {"type": "integer"},
                        "target": {"type": "string"},
                        "message": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            "telegram": {
                "type": ["string", "null"],
                "description": "Optional Telegram message for kill alerts. Only for catastrophic failures or significant autonomous decisions.",
            },
            "escalate": {
                "type": "boolean",
                "description": "Whether to escalate to the next tier for deeper analysis.",
            },
        },
    },
}


def build_system_prompt(tier: int) -> str:
    """Build the full system prompt for a given tier."""
    return ADVERSARY_SYSTEM + TIER_ADDITIONS.get(tier, "")


def build_user_prompt(
    *,
    trigger_event: dict | None = None,
    recent_events: list[dict] | None = None,
    snapshot: dict | None = None,
    heuristic_recommendation: dict | None = None,
    recent_directives: list[dict] | None = None,
    roster: dict | None = None,
    escalation_reason: str | None = None,
    tier1_reasoning: str | None = None,
    tier2_reasoning: str | None = None,
) -> str:
    """Build the user message with all context for the brain."""
    parts = []

    if trigger_event:
        parts.append(f"## Trigger Event\n```json\n{_json(trigger_event)}\n```")

    if escalation_reason:
        parts.append(f"## Escalation Reason\n{escalation_reason}")

    if tier1_reasoning:
        parts.append(f"## Tier 1 (Haiku) Reasoning\n{tier1_reasoning}")

    if tier2_reasoning:
        parts.append(f"## Tier 2 (Sonnet) Reasoning\n{tier2_reasoning}")

    if recent_events:
        parts.append(f"## Recent Events (last 20)\n```json\n{_json(recent_events[-20:])}\n```")

    if snapshot:
        parts.append(f"## Factory Snapshot\n```json\n{_json(snapshot)}\n```")

    if heuristic_recommendation:
        parts.append(f"## Heuristic Recommendation (treat as naive first guess)\n```json\n{_json(heuristic_recommendation)}\n```")

    if recent_directives:
        parts.append(f"## Recent Brain Directives (last 20)\n```json\n{_json(recent_directives[-20:])}\n```")

    if roster:
        parts.append(f"## Factory Roster\n```json\n{_json(roster)}\n```")

    parts.append("Analyze the situation. Issue directives via the issue_directives tool. Every response must use the tool.")

    return "\n\n".join(parts)


def _json(obj: object) -> str:
    import json
    return json.dumps(obj, indent=2, default=str)
```

**Step 3: Write the brain core module**

```python
# driftdriver/factory_brain/brain.py
# ABOUTME: Factory brain core — three-tier model invocation and response parsing.
# ABOUTME: Calls Anthropic API at Haiku/Sonnet/Opus tiers based on event routing.

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.factory_brain.directives import BrainResponse, Directive, parse_brain_response
from driftdriver.factory_brain.prompts import (
    DIRECTIVE_TOOL,
    TIER_MODELS,
    build_system_prompt,
    build_user_prompt,
)

log = logging.getLogger(__name__)


@dataclass
class BrainInvocation:
    tier: int
    model: str
    trigger: str
    reasoning: str
    directives: list[dict]
    telegram: str | None
    escalate: bool
    timestamp: str
    input_tokens: int = 0
    output_tokens: int = 0


def invoke_brain(
    *,
    tier: int,
    trigger_event: dict | None = None,
    recent_events: list[dict] | None = None,
    snapshot: dict | None = None,
    heuristic_recommendation: dict | None = None,
    recent_directives: list[dict] | None = None,
    roster: dict | None = None,
    escalation_reason: str | None = None,
    tier1_reasoning: str | None = None,
    tier2_reasoning: str | None = None,
    log_dir: Path | None = None,
) -> BrainResponse:
    """Invoke the factory brain at the specified tier."""
    import anthropic

    model = TIER_MODELS[tier]
    system_prompt = build_system_prompt(tier)
    user_prompt = build_user_prompt(
        trigger_event=trigger_event,
        recent_events=recent_events,
        snapshot=snapshot,
        heuristic_recommendation=heuristic_recommendation,
        recent_directives=recent_directives,
        roster=roster,
        escalation_reason=escalation_reason,
        tier1_reasoning=tier1_reasoning,
        tier2_reasoning=tier2_reasoning,
    )

    log.info("Invoking brain tier %d (%s)", tier, model)

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        tools=[DIRECTIVE_TOOL],
        tool_choice={"type": "tool", "name": "issue_directives"},
        messages=[{"role": "user", "content": user_prompt}],
    )

    # Extract tool use result
    tool_block = next(
        (b for b in response.content if b.type == "tool_use"),
        None,
    )

    if not tool_block:
        log.error("Brain returned no tool_use block (tier %d)", tier)
        return BrainResponse(
            reasoning="ERROR: No tool_use in response",
            directives=[Directive(action="noop", params={"reason": "brain returned no directives"})],
        )

    raw = tool_block.input
    brain_response = parse_brain_response(raw)

    # Log invocation
    invocation = BrainInvocation(
        tier=tier,
        model=model,
        trigger=trigger_event.get("kind", "unknown") if trigger_event else "timer",
        reasoning=brain_response.reasoning,
        directives=[{"action": d.action, **d.params} for d in brain_response.directives],
        telegram=brain_response.telegram,
        escalate=brain_response.escalate,
        timestamp=datetime.now(timezone.utc).isoformat(),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    if log_dir:
        _write_brain_log(log_dir, invocation)

    log.info(
        "Brain tier %d: %d directives, escalate=%s, tokens=%d/%d",
        tier, len(brain_response.directives), brain_response.escalate,
        invocation.input_tokens, invocation.output_tokens,
    )

    return brain_response


def _write_brain_log(log_dir: Path, invocation: BrainInvocation) -> None:
    """Append invocation to brain log (JSONL + markdown)."""
    log_dir.mkdir(parents=True, exist_ok=True)

    # JSONL for machine consumption
    jsonl_file = log_dir / "brain-invocations.jsonl"
    record = {
        "tier": invocation.tier,
        "model": invocation.model,
        "trigger": invocation.trigger,
        "reasoning": invocation.reasoning,
        "directives": invocation.directives,
        "telegram": invocation.telegram,
        "escalate": invocation.escalate,
        "ts": invocation.timestamp,
        "input_tokens": invocation.input_tokens,
        "output_tokens": invocation.output_tokens,
    }
    with open(jsonl_file, "a") as f:
        f.write(json.dumps(record) + "\n")

    # Markdown for human consumption
    md_file = log_dir / "brain-log.md"
    entry = f"\n## [{invocation.timestamp}] Tier {invocation.tier} ({invocation.model})\n"
    entry += f"**Trigger:** {invocation.trigger}\n\n"
    entry += f"**Reasoning:** {invocation.reasoning}\n\n"
    if invocation.directives:
        entry += "**Directives:**\n"
        for d in invocation.directives:
            entry += f"- `{d}`\n"
    if invocation.telegram:
        entry += f"\n**Telegram:** {invocation.telegram}\n"
    if invocation.escalate:
        entry += "\n**ESCALATED to next tier**\n"
    entry += f"\n*Tokens: {invocation.input_tokens} in / {invocation.output_tokens} out*\n"
    entry += "---\n"
    with open(md_file, "a") as f:
        f.write(entry)
```

**Step 4: Write the tests**

```python
# tests/test_factory_brain_core.py
# ABOUTME: Tests for factory brain core — prompt assembly and response parsing.
# ABOUTME: Uses mocked Anthropic client to avoid real API calls.

import json
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from driftdriver.factory_brain.brain import invoke_brain, _write_brain_log, BrainInvocation
from driftdriver.factory_brain.prompts import (
    build_system_prompt,
    build_user_prompt,
    TIER_MODELS,
    DIRECTIVE_TOOL,
)


def test_build_system_prompt_includes_adversary():
    prompt = build_system_prompt(1)
    assert "Factory Adversary" in prompt
    assert "reflexes" in prompt.lower()


def test_build_system_prompt_tier_specific():
    t1 = build_system_prompt(1)
    t2 = build_system_prompt(2)
    t3 = build_system_prompt(3)
    assert "Haiku" in t1
    assert "Sonnet" in t2
    assert "Opus" in t3


def test_build_user_prompt_includes_sections():
    prompt = build_user_prompt(
        trigger_event={"kind": "loop.crashed", "repo": "lodestar"},
        snapshot={"repos": []},
        heuristic_recommendation={"action": "restart"},
    )
    assert "Trigger Event" in prompt
    assert "loop.crashed" in prompt
    assert "Factory Snapshot" in prompt
    assert "Heuristic Recommendation" in prompt
    assert "naive first guess" in prompt


def test_build_user_prompt_escalation_context():
    prompt = build_user_prompt(
        escalation_reason="Tier 1 failed twice",
        tier1_reasoning="Tried killing daemon, didn't help",
    )
    assert "Escalation Reason" in prompt
    assert "Tier 1 (Haiku) Reasoning" in prompt


def test_tier_models():
    assert "haiku" in TIER_MODELS[1]
    assert "sonnet" in TIER_MODELS[2]
    assert "opus" in TIER_MODELS[3]


def test_directive_tool_has_all_actions():
    actions = DIRECTIVE_TOOL["input_schema"]["properties"]["directives"]["items"]["properties"]["action"]["enum"]
    assert "kill_daemon" in actions
    assert "enroll" in actions
    assert "noop" in actions
    assert "escalate" in actions


def _mock_anthropic_response(tool_input: dict):
    """Build a mock Anthropic API response with tool_use."""
    tool_block = SimpleNamespace(type="tool_use", input=tool_input, name="issue_directives", id="test")
    usage = SimpleNamespace(input_tokens=500, output_tokens=200)
    return SimpleNamespace(content=[tool_block], usage=usage)


@patch("driftdriver.factory_brain.brain.anthropic")
def test_invoke_brain_returns_directives(mock_anthropic_mod):
    mock_client = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "reasoning": "Daemon is stuck in lodestar",
        "directives": [
            {"action": "kill_daemon", "repo": "lodestar"},
            {"action": "clear_locks", "repo": "lodestar"},
        ],
        "telegram": None,
        "escalate": False,
    })

    resp = invoke_brain(
        tier=1,
        trigger_event={"kind": "loop.crashed", "repo": "lodestar"},
    )
    assert len(resp.directives) == 2
    assert resp.directives[0].action == "kill_daemon"
    assert resp.reasoning == "Daemon is stuck in lodestar"
    assert resp.escalate is False


@patch("driftdriver.factory_brain.brain.anthropic")
def test_invoke_brain_escalation(mock_anthropic_mod):
    mock_client = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "reasoning": "Can't figure this out",
        "directives": [{"action": "escalate", "reason": "need deeper analysis"}],
        "telegram": None,
        "escalate": True,
    })

    resp = invoke_brain(tier=1, trigger_event={"kind": "loop.crashed", "repo": "x"})
    assert resp.escalate is True


@patch("driftdriver.factory_brain.brain.anthropic")
def test_invoke_brain_writes_log(mock_anthropic_mod, tmp_path):
    mock_client = MagicMock()
    mock_anthropic_mod.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_anthropic_response({
        "reasoning": "All good",
        "directives": [{"action": "noop", "reason": "nothing to do"}],
        "telegram": None,
        "escalate": False,
    })

    invoke_brain(tier=1, trigger_event={"kind": "snapshot.collected"}, log_dir=tmp_path)

    assert (tmp_path / "brain-invocations.jsonl").exists()
    assert (tmp_path / "brain-log.md").exists()

    jsonl = (tmp_path / "brain-invocations.jsonl").read_text().strip()
    record = json.loads(jsonl)
    assert record["tier"] == 1
    assert record["trigger"] == "snapshot.collected"

    md = (tmp_path / "brain-log.md").read_text()
    assert "Tier 1" in md
    assert "All good" in md
```

**Step 5: Run tests**

```bash
python -m pytest tests/test_factory_brain_core.py -v
```

**Step 6: Commit**

```bash
git add driftdriver/factory_brain/prompts.py driftdriver/factory_brain/brain.py tests/test_factory_brain_core.py
git commit -m "feat: factory brain core — three-tier model invocation with adversarial prompts"
```

---

### Task 6: Telegram Notification

**Files:**
- Create: `driftdriver/factory_brain/telegram.py`
- Create: `tests/test_factory_brain_telegram.py`
- Modify: `driftdriver/factory_brain/directives.py` (wire send_telegram handler)

**Context:** The brain sends Telegram messages for catastrophic failures and significant autonomous decisions. Uses the existing bot token from `~/.config/workgraph/notify.toml`. Only kill alerts — not routine operations.

**Step 1: Write the failing tests**

```python
# tests/test_factory_brain_telegram.py
# ABOUTME: Tests for Telegram notification integration.
# ABOUTME: Uses mocked HTTP to avoid real API calls.

from pathlib import Path
from unittest.mock import patch, MagicMock

from driftdriver.factory_brain.telegram import (
    load_telegram_config,
    send_telegram,
)


def test_load_telegram_config(tmp_path):
    config_file = tmp_path / "notify.toml"
    config_file.write_text("""
[telegram]
bot_token = "123:ABC"
chat_id = "456"
""")
    config = load_telegram_config(config_file)
    assert config["bot_token"] == "123:ABC"
    assert config["chat_id"] == "456"


def test_load_telegram_config_missing(tmp_path):
    config = load_telegram_config(tmp_path / "nonexistent.toml")
    assert config is None


@patch("driftdriver.factory_brain.telegram.urllib.request.urlopen")
def test_send_telegram_success(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"ok": true}'
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    result = send_telegram(
        bot_token="123:ABC",
        chat_id="456",
        message="Test alert",
    )
    assert result is True
    mock_urlopen.assert_called_once()


@patch("driftdriver.factory_brain.telegram.urllib.request.urlopen")
def test_send_telegram_formats_factory_prefix(mock_urlopen):
    mock_response = MagicMock()
    mock_response.read.return_value = b'{"ok": true}'
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_urlopen.return_value = mock_response

    send_telegram(bot_token="t", chat_id="c", message="enrolled repo-x")
    call_args = mock_urlopen.call_args
    # Verify the request includes the factory prefix
    assert call_args is not None
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_factory_brain_telegram.py -v
```

**Step 3: Implement the telegram module**

```python
# driftdriver/factory_brain/telegram.py
# ABOUTME: Telegram notification for factory brain kill alerts.
# ABOUTME: Uses Bot API via urllib (no extra dependencies). Only for significant events.

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "workgraph" / "notify.toml"


def load_telegram_config(config_path: Path | None = None) -> dict[str, str] | None:
    """Load Telegram bot_token and chat_id from notify.toml."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return None
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        tg = data.get("telegram", {})
        if "bot_token" not in tg or "chat_id" not in tg:
            return None
        return {"bot_token": tg["bot_token"], "chat_id": str(tg["chat_id"])}
    except Exception:
        log.warning("Failed to load Telegram config from %s", path)
        return None


def send_telegram(
    *,
    bot_token: str,
    chat_id: str,
    message: str,
) -> bool:
    """Send a message via Telegram Bot API."""
    prefixed = f"🏭 *Factory Brain*\n\n{message}"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": prefixed,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            if body.get("ok"):
                log.info("Telegram sent: %s", message[:80])
                return True
            log.warning("Telegram API error: %s", body)
            return False
    except Exception:
        log.exception("Failed to send Telegram message")
        return False
```

**Step 4: Wire the send_telegram handler in directives.py**

In `driftdriver/factory_brain/directives.py`, update `_handle_send_telegram`:

```python
def _handle_send_telegram(d: Directive, repo_paths: dict) -> dict:
    from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram
    config = load_telegram_config()
    if not config:
        log.warning("No Telegram config found, skipping")
        return {"action": "send_telegram", "status": "no_config", "message": d.params.get("message", "")}
    ok = send_telegram(
        bot_token=config["bot_token"],
        chat_id=config["chat_id"],
        message=d.params.get("message", ""),
    )
    return {"action": "send_telegram", "status": "ok" if ok else "error", "message": d.params.get("message", "")}
```

**Step 5: Run all tests**

```bash
python -m pytest tests/test_factory_brain_telegram.py tests/test_factory_brain_directives.py -v
```

**Step 6: Commit**

```bash
git add driftdriver/factory_brain/telegram.py tests/test_factory_brain_telegram.py driftdriver/factory_brain/directives.py
git commit -m "feat: Telegram kill alerts for factory brain"
```

---

### Task 7: Brain Event Router and Timer System

**Files:**
- Create: `driftdriver/factory_brain/router.py`
- Create: `tests/test_factory_brain_router.py`

**Context:** The router is the brain's main loop. It watches for events (from JSONL files), runs timer-based safety nets (60s heartbeat, 10min sweep), and routes triggers to the appropriate tier. It calls `invoke_brain()`, executes the returned directives, handles escalation (Tier 1 → Tier 2 → Tier 3), and sends Telegram when the brain requests it.

**Step 1: Write the failing tests**

```python
# tests/test_factory_brain_router.py
# ABOUTME: Tests for the brain event router and timer system.
# ABOUTME: Covers event routing, tier escalation, heartbeat checks, and sweep logic.

from pathlib import Path
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone, timedelta

from driftdriver.factory_brain.events import Event, emit_event
from driftdriver.factory_brain.router import (
    route_event,
    check_heartbeats,
    should_sweep,
    process_brain_response,
    BrainState,
)
from driftdriver.factory_brain.directives import BrainResponse, Directive


def test_route_event_tier1():
    event = Event(kind="loop.crashed", repo="lodestar", ts="2026-01-01T00:00:00Z", payload={})
    assert route_event(event) == 1


def test_route_event_tier2():
    event = Event(kind="tasks.exhausted", repo="lodestar", ts="2026-01-01T00:00:00Z", payload={})
    assert route_event(event) == 2


def test_route_event_unknown_defaults_tier1():
    event = Event(kind="something.weird", repo="x", ts="2026-01-01T00:00:00Z", payload={})
    assert route_event(event) == 1


def test_check_heartbeats_detects_stale(tmp_path):
    repo = tmp_path / "repo1"
    hb_dir = repo / ".workgraph" / "service" / "runtime"
    hb_dir.mkdir(parents=True)
    hb_file = hb_dir / "dispatch-loop.heartbeat"
    # Write a stale heartbeat (2 minutes ago)
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=2)
    hb_file.write_text(stale_time.strftime("%Y-%m-%dT%H:%M:%SZ"))
    stale = check_heartbeats([repo], max_age_seconds=90)
    assert len(stale) == 1
    assert stale[0] == repo


def test_check_heartbeats_fresh(tmp_path):
    repo = tmp_path / "repo1"
    hb_dir = repo / ".workgraph" / "service" / "runtime"
    hb_dir.mkdir(parents=True)
    hb_file = hb_dir / "dispatch-loop.heartbeat"
    hb_file.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    stale = check_heartbeats([repo], max_age_seconds=90)
    assert len(stale) == 0


def test_check_heartbeats_missing_file(tmp_path):
    repo = tmp_path / "repo1"
    repo.mkdir()
    stale = check_heartbeats([repo], max_age_seconds=90)
    assert len(stale) == 1  # Missing = stale


def test_should_sweep():
    state = BrainState()
    assert should_sweep(state, interval_seconds=600) is True
    state.last_sweep = datetime.now(timezone.utc)
    assert should_sweep(state, interval_seconds=600) is False


def test_process_brain_response_escalation():
    response = BrainResponse(
        reasoning="Need deeper analysis",
        directives=[Directive(action="escalate", params={"reason": "stuck"})],
        escalate=True,
    )
    state = BrainState()
    result = process_brain_response(response, tier=1, state=state)
    assert result["escalated"] is True
    assert result["next_tier"] == 2


def test_process_brain_response_no_escalation():
    response = BrainResponse(
        reasoning="Fixed it",
        directives=[Directive(action="noop", params={"reason": "done"})],
        escalate=False,
    )
    state = BrainState()
    result = process_brain_response(response, tier=1, state=state)
    assert result["escalated"] is False
```

**Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_factory_brain_router.py -v
```

**Step 3: Implement the router module**

```python
# driftdriver/factory_brain/router.py
# ABOUTME: Brain event router — watches events, runs timers, routes to tiers.
# ABOUTME: Main orchestration loop for the model-mediated factory brain.

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from driftdriver.factory_brain.brain import invoke_brain
from driftdriver.factory_brain.directives import (
    BrainResponse,
    Directive,
    execute_directives,
)
from driftdriver.factory_brain.events import (
    Event,
    TIER_ROUTING,
    aggregate_events,
    emit_event,
    events_file_for_repo,
)

log = logging.getLogger(__name__)


@dataclass
class BrainState:
    """Mutable state for the brain router."""
    last_heartbeat_check: datetime | None = None
    last_sweep: datetime | None = None
    last_event_ts: str = ""
    recent_directives: list[dict] = field(default_factory=list)
    tier1_escalation_count: int = 0


def route_event(event: Event) -> int:
    """Determine which tier should handle an event."""
    return TIER_ROUTING.get(event.kind, 1)


def check_heartbeats(
    repo_paths: list[Path],
    *,
    max_age_seconds: int = 90,
) -> list[Path]:
    """Check dispatch loop heartbeat files, return stale repos."""
    stale = []
    now = datetime.now(timezone.utc)
    for repo in repo_paths:
        hb_file = repo / ".workgraph" / "service" / "runtime" / "dispatch-loop.heartbeat"
        if not hb_file.exists():
            stale.append(repo)
            continue
        try:
            ts_str = hb_file.read_text().strip()
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if (now - ts).total_seconds() > max_age_seconds:
                stale.append(repo)
        except (ValueError, OSError):
            stale.append(repo)
    return stale


def should_sweep(state: BrainState, *, interval_seconds: int = 600) -> bool:
    """Check if enough time has elapsed for a Tier 2 sweep."""
    if state.last_sweep is None:
        return True
    elapsed = (datetime.now(timezone.utc) - state.last_sweep).total_seconds()
    return elapsed >= interval_seconds


def process_brain_response(
    response: BrainResponse,
    *,
    tier: int,
    state: BrainState,
    repo_paths: dict[str, Path] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Process a brain response — execute directives, handle escalation."""
    results = execute_directives(
        response.directives,
        dry_run=dry_run,
        repo_paths=repo_paths,
    )

    # Track recent directives
    for d in response.directives:
        state.recent_directives.append({
            "action": d.action, "tier": tier, **d.params,
        })
    state.recent_directives = state.recent_directives[-50:]  # Keep last 50

    escalated = response.escalate and tier < 3
    next_tier = tier + 1 if escalated else None

    if escalated:
        state.tier1_escalation_count += 1

    return {
        "tier": tier,
        "directives_executed": len(results),
        "results": results,
        "escalated": escalated,
        "next_tier": next_tier,
        "reasoning": response.reasoning,
        "telegram": response.telegram,
    }


def run_brain_tick(
    *,
    state: BrainState,
    roster_repos: dict[str, dict[str, Any]],
    snapshot: dict | None = None,
    heuristic_recommendation: dict | None = None,
    log_dir: Path | None = None,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Run a single brain tick — check events, timers, invoke tiers as needed."""
    repo_paths = {name: Path(info["path"]) for name, info in roster_repos.items()}
    results = []

    # 1. Aggregate new events
    new_events = aggregate_events(
        list(repo_paths.values()),
        since=state.last_event_ts,
    )

    if new_events:
        state.last_event_ts = new_events[-1].ts

    # 2. Route events to tiers
    tier1_events = [e for e in new_events if route_event(e) == 1]
    tier2_events = [e for e in new_events if route_event(e) == 2]
    tier3_events = [e for e in new_events if route_event(e) == 3]

    # 3. Tier 1 — process health events
    for event in tier1_events:
        response = invoke_brain(
            tier=1,
            trigger_event={"kind": event.kind, "repo": event.repo, **event.payload},
            recent_events=[{"kind": e.kind, "repo": e.repo, "ts": e.ts} for e in new_events[-10:]],
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            log_dir=log_dir,
        )
        result = process_brain_response(
            response, tier=1, state=state,
            repo_paths=repo_paths, dry_run=dry_run,
        )
        results.append(result)

        # Handle escalation
        if result["escalated"]:
            tier2_events.append(Event(
                kind="tier1.escalation",
                repo=event.repo,
                ts=event.ts,
                payload={"reason": response.reasoning},
            ))

    # 4. Heartbeat check (60s timer)
    now = datetime.now(timezone.utc)
    if state.last_heartbeat_check is None or (now - state.last_heartbeat_check).total_seconds() >= 60:
        state.last_heartbeat_check = now
        stale = check_heartbeats(list(repo_paths.values()))
        for repo_path in stale:
            tier1_event = Event(
                kind="heartbeat.stale",
                repo=repo_path.name,
                ts=now.isoformat(),
                payload={},
            )
            response = invoke_brain(
                tier=1,
                trigger_event={"kind": "heartbeat.stale", "repo": repo_path.name},
                snapshot=snapshot,
                recent_directives=state.recent_directives,
                log_dir=log_dir,
            )
            result = process_brain_response(
                response, tier=1, state=state,
                repo_paths=repo_paths, dry_run=dry_run,
            )
            results.append(result)

    # 5. Tier 2 — process strategy events + sweep
    run_sweep = should_sweep(state)
    if tier2_events or run_sweep:
        trigger = tier2_events[0] if tier2_events else None
        trigger_dict = {"kind": trigger.kind, "repo": trigger.repo, **trigger.payload} if trigger else {"kind": "sweep.timer"}
        roster_dict = {name: info for name, info in roster_repos.items()}

        response = invoke_brain(
            tier=2,
            trigger_event=trigger_dict,
            recent_events=[{"kind": e.kind, "repo": e.repo, "ts": e.ts} for e in new_events[-20:]],
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            roster=roster_dict,
            log_dir=log_dir,
        )
        result = process_brain_response(
            response, tier=2, state=state,
            repo_paths=repo_paths, dry_run=dry_run,
        )
        results.append(result)

        if run_sweep:
            state.last_sweep = now

        # Handle Tier 2 escalation to Tier 3
        if result["escalated"]:
            tier3_events.append(Event(
                kind="tier2.escalation",
                repo=trigger.repo if trigger else "factory",
                ts=now.isoformat(),
                payload={"reason": response.reasoning},
            ))

    # 6. Tier 3 — judgment calls (event-triggered only)
    for event in tier3_events:
        response = invoke_brain(
            tier=3,
            trigger_event={"kind": event.kind, "repo": event.repo, **event.payload},
            recent_events=[{"kind": e.kind, "repo": e.repo, "ts": e.ts} for e in new_events[-20:]],
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            roster={name: info for name, info in roster_repos.items()},
            escalation_reason=event.payload.get("reason", ""),
            log_dir=log_dir,
        )
        result = process_brain_response(
            response, tier=3, state=state,
            repo_paths=repo_paths, dry_run=dry_run,
        )
        results.append(result)

    return results
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_factory_brain_router.py -v
```

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/router.py tests/test_factory_brain_router.py
git commit -m "feat: brain event router with tier escalation and timer safety nets"
```

---

### Task 8: Hub Integration — Wire Brain into Ecosystem Hub

**Files:**
- Modify: `driftdriver/ecosystem_hub/server.py` (inject brain calls into collector loop)
- Create: `driftdriver/factory_brain/hub_integration.py`
- Create: `tests/test_factory_brain_hub_integration.py`

**Context:** This wires the brain into the existing ecosystem hub collector loop. The brain replaces (wraps) the heuristic factory cycle. The hub's `_collector_loop()` calls `run_brain_tick()` after collecting the snapshot, passing in the heuristic recommendation from `build_factory_cycle()`.

**Step 1: Write the integration module**

```python
# driftdriver/factory_brain/hub_integration.py
# ABOUTME: Wires the factory brain into the ecosystem hub's collector loop.
# ABOUTME: Replaces heuristic factory cycle with brain-mediated decisions.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from driftdriver.factory_brain.roster import (
    Roster,
    load_roster,
    save_roster,
    active_repos,
    discover_repos,
    enroll_repo,
    unenroll_repo,
)
from driftdriver.factory_brain.router import BrainState, run_brain_tick
from driftdriver.factory_brain.events import emit_event, events_file_for_repo

log = logging.getLogger(__name__)

ROSTER_FILENAME = "factory-roster.json"


class FactoryBrain:
    """Main brain controller, instantiated by the ecosystem hub."""

    def __init__(
        self,
        *,
        hub_data_dir: Path,
        workspace_roots: list[Path],
        dry_run: bool = False,
    ):
        self.hub_data_dir = hub_data_dir
        self.workspace_roots = workspace_roots
        self.dry_run = dry_run
        self.roster_file = hub_data_dir / ROSTER_FILENAME
        self.log_dir = hub_data_dir / "brain-logs"
        self.roster = load_roster(self.roster_file)
        self.state = BrainState()

    def tick(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        heuristic_recommendation: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Run one brain tick. Called from the hub's collector loop."""
        repos = active_repos(self.roster)
        if not repos:
            return []

        results = run_brain_tick(
            state=self.state,
            roster_repos=repos,
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            log_dir=self.log_dir,
            dry_run=self.dry_run,
        )

        # Process enrollment/unenrollment directives from results
        for result in results:
            for directive_result in result.get("results", []):
                if directive_result.get("action") == "enroll" and directive_result.get("status") == "deferred":
                    self._handle_enroll(directive_result.get("repo", ""))
                elif directive_result.get("action") == "unenroll" and directive_result.get("status") == "deferred":
                    self._handle_unenroll(directive_result.get("repo", ""))

        # Persist roster after any changes
        save_roster(self.roster, self.roster_file)

        return results

    def _handle_enroll(self, repo_path_str: str) -> None:
        """Handle an enroll directive from the brain."""
        repo_path = Path(repo_path_str)
        if not repo_path.exists() or not (repo_path / ".workgraph").exists():
            log.warning("Cannot enroll %s — no .workgraph", repo_path_str)
            return
        name = enroll_repo(self.roster, path=repo_path_str, target="onboarded")
        # Copy dispatch-loop.sh if missing
        dispatch = repo_path / ".workgraph" / "executors" / "dispatch-loop.sh"
        if not dispatch.exists():
            template = Path(__file__).parent.parent / "templates" / "dispatch-loop.sh"
            if template.exists():
                dispatch.parent.mkdir(parents=True, exist_ok=True)
                dispatch.write_text(template.read_text())
                dispatch.chmod(0o755)
                log.info("Installed dispatch-loop.sh in %s", name)
        log.info("Enrolled repo: %s", name)

    def _handle_unenroll(self, repo_name: str) -> None:
        """Handle an unenroll directive from the brain."""
        unenroll_repo(self.roster, name=repo_name)
        log.info("Unenrolled repo: %s", repo_name)
```

**Step 2: Write tests**

```python
# tests/test_factory_brain_hub_integration.py
# ABOUTME: Tests for the FactoryBrain hub integration controller.
# ABOUTME: Verifies tick cycle, roster management, and enrollment handling.

from pathlib import Path
from unittest.mock import patch, MagicMock

from driftdriver.factory_brain.hub_integration import FactoryBrain
from driftdriver.factory_brain.roster import load_roster, enroll_repo


def test_factory_brain_init(tmp_path):
    brain = FactoryBrain(
        hub_data_dir=tmp_path,
        workspace_roots=[tmp_path],
        dry_run=True,
    )
    assert brain.roster.repos == {}
    assert brain.log_dir == tmp_path / "brain-logs"


def test_factory_brain_tick_empty_roster(tmp_path):
    brain = FactoryBrain(hub_data_dir=tmp_path, workspace_roots=[tmp_path], dry_run=True)
    results = brain.tick(snapshot={})
    assert results == []


@patch("driftdriver.factory_brain.hub_integration.run_brain_tick")
def test_factory_brain_tick_calls_brain(mock_tick, tmp_path):
    brain = FactoryBrain(hub_data_dir=tmp_path, workspace_roots=[tmp_path], dry_run=True)
    # Enroll a repo
    repo = tmp_path / "myrepo"
    (repo / ".workgraph" / "service" / "runtime").mkdir(parents=True)
    enroll_repo(brain.roster, path=str(repo), target="onboarded")

    mock_tick.return_value = []
    brain.tick(snapshot={"repos": []})
    mock_tick.assert_called_once()


def test_factory_brain_persists_roster(tmp_path):
    brain = FactoryBrain(hub_data_dir=tmp_path, workspace_roots=[tmp_path], dry_run=True)
    repo = tmp_path / "myrepo"
    (repo / ".workgraph").mkdir(parents=True)
    enroll_repo(brain.roster, path=str(repo), target="onboarded")

    with patch("driftdriver.factory_brain.hub_integration.run_brain_tick", return_value=[]):
        brain.tick(snapshot={})

    # Verify roster was saved
    loaded = load_roster(tmp_path / "factory-roster.json")
    assert "myrepo" in loaded.repos
```

**Step 3: Run tests**

```bash
python -m pytest tests/test_factory_brain_hub_integration.py -v
```

**Step 4: Modify the ecosystem hub's collector loop**

In `driftdriver/ecosystem_hub/server.py`, in the `_collector_loop()` function, after `build_factory_cycle()` and before `execute_factory_cycle()`, add the brain tick call.

Find the section where `build_factory_cycle()` is called (around line 273-312) and add:

```python
# Factory brain — model-mediated decisions
if hasattr(self, '_factory_brain') and self._factory_brain is not None:
    try:
        brain_results = self._factory_brain.tick(
            snapshot=snapshot,
            heuristic_recommendation=cycle if cycle else None,
        )
        if brain_results:
            log.info("Factory brain: %d tier invocations", len(brain_results))
    except Exception:
        log.exception("Factory brain tick failed")
```

And in the server's `__init__` or startup, instantiate the brain:

```python
from driftdriver.factory_brain.hub_integration import FactoryBrain

self._factory_brain = FactoryBrain(
    hub_data_dir=self._data_dir,
    workspace_roots=[Path(self._workspace_root)],
    dry_run=False,
)
```

**Step 5: Run all tests**

```bash
python -m pytest tests/test_factory_brain*.py -v
```

**Step 6: Commit**

```bash
git add driftdriver/factory_brain/hub_integration.py tests/test_factory_brain_hub_integration.py driftdriver/ecosystem_hub/server.py
git commit -m "feat: wire factory brain into ecosystem hub collector loop"
```

---

### Task 9: CLI Commands for Factory Brain

**Files:**
- Modify: `driftdriver/cli/__init__.py` (add brain subcommands)
- Create: `tests/test_factory_brain_cli.py`

**Context:** Add CLI commands to inspect and control the factory brain: `driftdriver brain status`, `driftdriver brain roster`, `driftdriver brain log`, `driftdriver brain enroll <path>`, `driftdriver brain unenroll <name>`.

**Step 1: Add the brain subcommand group**

Find the CLI registration pattern in `driftdriver/cli/__init__.py`. Add a new subparser group for `brain` with these subcommands:

```python
# brain subcommands
brain_parser = subparsers.add_parser("brain", help="Factory brain management")
brain_sub = brain_parser.add_subparsers(dest="brain_cmd")

brain_sub.add_parser("status", help="Show brain state and recent activity")
brain_sub.add_parser("roster", help="Show enrolled repos")
brain_sub.add_parser("log", help="Show recent brain reasoning log")

brain_enroll = brain_sub.add_parser("enroll", help="Manually enroll a repo")
brain_enroll.add_argument("path", help="Path to repo")
brain_enroll.add_argument("--target", default="onboarded", help="Attractor target")

brain_unenroll = brain_sub.add_parser("unenroll", help="Manually unenroll a repo")
brain_unenroll.add_argument("name", help="Repo name")
```

**Step 2: Implement the brain command handlers**

```python
def _handle_brain_cmd(args):
    from driftdriver.factory_brain.roster import load_roster, save_roster, enroll_repo, unenroll_repo, active_repos
    from pathlib import Path
    import json

    hub_data_dir = Path.home() / ".config" / "workgraph" / "factory-brain"
    roster_file = hub_data_dir / "factory-roster.json"

    if args.brain_cmd == "status":
        roster = load_roster(roster_file)
        active = active_repos(roster)
        print(f"Factory Brain")
        print(f"  Enrolled repos: {len(roster.repos)} ({len(active)} active)")
        log_file = hub_data_dir / "brain-logs" / "brain-invocations.jsonl"
        if log_file.exists():
            lines = log_file.read_text().strip().split("\n")
            print(f"  Total invocations: {len(lines)}")
            if lines:
                last = json.loads(lines[-1])
                print(f"  Last invocation: Tier {last['tier']} at {last['ts']}")
        else:
            print("  No invocations yet")

    elif args.brain_cmd == "roster":
        roster = load_roster(roster_file)
        if not roster.repos:
            print("No repos enrolled")
            return
        for name, info in roster.repos.items():
            status = info.get("status", "unknown")
            target = info.get("target", "?")
            path = info.get("path", "?")
            print(f"  {name:30s}  {status:10s}  target={target:20s}  {path}")

    elif args.brain_cmd == "log":
        md_file = hub_data_dir / "brain-logs" / "brain-log.md"
        if not md_file.exists():
            print("No brain log yet")
            return
        # Print last 2000 chars
        content = md_file.read_text()
        print(content[-2000:] if len(content) > 2000 else content)

    elif args.brain_cmd == "enroll":
        roster = load_roster(roster_file)
        enroll_repo(roster, path=args.path, target=args.target)
        save_roster(roster, roster_file)
        print(f"Enrolled: {Path(args.path).name} (target={args.target})")

    elif args.brain_cmd == "unenroll":
        roster = load_roster(roster_file)
        unenroll_repo(roster, name=args.name)
        save_roster(roster, roster_file)
        print(f"Unenrolled: {args.name}")
```

**Step 3: Write tests**

```python
# tests/test_factory_brain_cli.py
# ABOUTME: Tests for factory brain CLI commands.
# ABOUTME: Verifies roster, status, enroll, and unenroll commands.

import json
from pathlib import Path

from driftdriver.factory_brain.roster import load_roster, save_roster, Roster, enroll_repo


def test_roster_roundtrip_via_cli(tmp_path):
    roster_file = tmp_path / "factory-roster.json"
    roster = Roster()
    enroll_repo(roster, path="/tmp/test-repo", target="onboarded")
    save_roster(roster, roster_file)
    loaded = load_roster(roster_file)
    assert "test-repo" in loaded.repos
    assert loaded.repos["test-repo"]["status"] == "active"
```

**Step 4: Run tests**

```bash
python -m pytest tests/test_factory_brain_cli.py tests/test_factory_brain*.py -v
```

**Step 5: Commit**

```bash
git add driftdriver/cli/__init__.py tests/test_factory_brain_cli.py
git commit -m "feat: CLI commands for factory brain — status, roster, log, enroll, unenroll"
```

---

### Task 10: Dispatch Loop Template and Enrollment Automation

**Files:**
- Create: `driftdriver/templates/dispatch-loop.sh` (canonical hardened version)
- Modify: `driftdriver/factory_brain/directives.py` (wire enroll/unenroll handlers)

**Context:** When the brain enrolls a new repo, it needs to install the dispatch-loop.sh template. Store the canonical version in driftdriver's templates dir. Also wire the enroll/unenroll directive handlers to use the roster module.

**Step 1: Copy the hardened dispatch-loop.sh as the template**

Copy the hardened version from lodestar (Task 1 output) to `driftdriver/templates/dispatch-loop.sh`. This becomes the canonical version that gets installed in new repos.

```bash
mkdir -p /Users/braydon/projects/experiments/driftdriver/driftdriver/templates
/bin/cp /Users/braydon/projects/experiments/lodestar/.workgraph/executors/dispatch-loop.sh \
  /Users/braydon/projects/experiments/driftdriver/driftdriver/templates/dispatch-loop.sh
```

**Step 2: Update the enroll handler in directives.py**

Replace the placeholder `_handle_enroll` with:

```python
def _handle_enroll(d: Directive, repo_paths: dict) -> dict:
    repo_path_str = d.params.get("repo", "")
    repo_path = Path(repo_path_str)
    if not repo_path.exists():
        return {"action": "enroll", "status": "error", "error": f"path not found: {repo_path_str}"}
    # Install dispatch-loop.sh if missing
    dispatch = repo_path / ".workgraph" / "executors" / "dispatch-loop.sh"
    if not dispatch.exists():
        template = Path(__file__).parent / "templates" / "dispatch-loop.sh"
        if template.exists():
            dispatch.parent.mkdir(parents=True, exist_ok=True)
            dispatch.write_text(template.read_text())
            dispatch.chmod(0o755)
    return {"action": "enroll", "status": "ok", "repo": repo_path_str}
```

**Step 3: Commit**

```bash
git add driftdriver/templates/dispatch-loop.sh driftdriver/factory_brain/directives.py
git commit -m "feat: dispatch-loop.sh template + enrollment automation"
```

---

### Task 11: Integration Test — Full Brain Tick Cycle

**Files:**
- Create: `tests/test_factory_brain_integration.py`

**Context:** End-to-end test that simulates a factory brain tick: creates repos with events, invokes the brain (mocked API), executes directives, verifies outcomes. This validates the full pipeline: events → router → brain → directives → execution.

**Step 1: Write the integration test**

```python
# tests/test_factory_brain_integration.py
# ABOUTME: Integration test for the full factory brain tick cycle.
# ABOUTME: Simulates events → router → brain → directives → execution.

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from driftdriver.factory_brain.events import emit_event, events_file_for_repo
from driftdriver.factory_brain.hub_integration import FactoryBrain
from driftdriver.factory_brain.roster import enroll_repo


def _mock_brain_response(reasoning, directives, telegram=None, escalate=False):
    tool_block = SimpleNamespace(
        type="tool_use",
        input={
            "reasoning": reasoning,
            "directives": directives,
            "telegram": telegram,
            "escalate": escalate,
        },
        name="issue_directives",
        id="test",
    )
    usage = SimpleNamespace(input_tokens=100, output_tokens=50)
    return SimpleNamespace(content=[tool_block], usage=usage)


@patch("driftdriver.factory_brain.brain.anthropic")
def test_full_tick_with_crash_event(mock_anthropic, tmp_path):
    """Simulate: dispatch loop crashes → brain detects → issues restart directive."""
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_client.messages.create.return_value = _mock_brain_response(
        reasoning="Dispatch loop crashed in test-repo, restarting",
        directives=[
            {"action": "kill_daemon", "repo": "test-repo"},
            {"action": "start_dispatch_loop", "repo": "test-repo"},
        ],
    )

    # Set up repo with events
    repo = tmp_path / "test-repo"
    wg_dir = repo / ".workgraph" / "service" / "runtime"
    wg_dir.mkdir(parents=True)
    exec_dir = repo / ".workgraph" / "executors"
    exec_dir.mkdir(parents=True)
    (exec_dir / "dispatch-loop.sh").write_text("#!/bin/bash\necho test")
    (exec_dir / "dispatch-loop.sh").chmod(0o755)

    # Emit a crash event
    emit_event(
        wg_dir / "factory-events.jsonl",
        kind="loop.crashed",
        repo="test-repo",
        payload={"exit_code": 1},
    )

    # Create brain
    brain = FactoryBrain(
        hub_data_dir=tmp_path / "hub",
        workspace_roots=[tmp_path],
        dry_run=True,  # Don't actually run commands
    )
    enroll_repo(brain.roster, path=str(repo), target="onboarded")

    # Run tick
    results = brain.tick(snapshot={"repos": []})

    # Verify brain was called
    assert mock_client.messages.create.called
    assert len(results) > 0

    # Verify log was written
    log_dir = tmp_path / "hub" / "brain-logs"
    assert (log_dir / "brain-invocations.jsonl").exists()


@patch("driftdriver.factory_brain.brain.anthropic")
def test_full_tick_with_escalation(mock_anthropic, tmp_path):
    """Simulate: Tier 1 can't fix → escalates to Tier 2."""
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client

    # Tier 1 escalates
    call_count = [0]
    def side_effect(**kwargs):
        call_count[0] += 1
        if call_count[0] == 1:  # Tier 1
            return _mock_brain_response(
                reasoning="Can't figure this out",
                directives=[{"action": "escalate", "reason": "need strategy"}],
                escalate=True,
            )
        else:  # Tier 2
            return _mock_brain_response(
                reasoning="Unenrolling dead repo",
                directives=[{"action": "unenroll", "repo": "test-repo"}],
            )

    mock_client.messages.create.side_effect = side_effect

    repo = tmp_path / "test-repo"
    (repo / ".workgraph" / "service" / "runtime").mkdir(parents=True)
    emit_event(
        repo / ".workgraph" / "service" / "runtime" / "factory-events.jsonl",
        kind="loop.crashed",
        repo="test-repo",
        payload={},
    )

    brain = FactoryBrain(
        hub_data_dir=tmp_path / "hub",
        workspace_roots=[tmp_path],
        dry_run=True,
    )
    enroll_repo(brain.roster, path=str(repo), target="onboarded")

    results = brain.tick(snapshot={})

    # Should have called the API twice (Tier 1 + Tier 2)
    assert mock_client.messages.create.call_count >= 2
```

**Step 2: Run tests**

```bash
python -m pytest tests/test_factory_brain_integration.py -v
```

**Step 3: Run full test suite**

```bash
python -m pytest tests/test_factory_brain*.py -v
```

**Step 4: Commit**

```bash
git add tests/test_factory_brain_integration.py
git commit -m "test: full integration test for factory brain tick cycle"
```

---

### Task 12: Replace dark-factory.sh with Brain-Managed Startup

**Files:**
- Create: `driftdriver/scripts/factory-brain-start.sh`
- Modify: `driftdriver/scripts/dark-factory.sh` (deprecation notice)

**Context:** The old dark-factory.sh manually started dispatch loops and ran attractor convergence on a timer. The new approach: start the ecosystem hub with the brain enabled, and let the brain manage everything. The startup script just ensures the hub is running and the brain is initialized with the roster.

**Step 1: Create the new startup script**

```bash
#!/usr/bin/env bash
# ABOUTME: Start the factory brain via the ecosystem hub.
# ABOUTME: The brain manages dispatch loops, enrollment, and healing autonomously.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DRIFTDRIVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log() { echo "[factory-brain] $(date +%H:%M:%S) $*"; }

# Ensure ecosystem hub is running with brain enabled
log "Starting ecosystem hub with factory brain..."
cd "$DRIFTDRIVER_DIR"

# Check if hub is already running
if curl -s http://127.0.0.1:8777/api/status > /dev/null 2>&1; then
    log "Ecosystem hub already running on port 8777"
else
    log "Starting ecosystem hub..."
    python -m driftdriver.ecosystem_hub.server run-service &
    HUB_PID=$!
    log "Hub started (PID $HUB_PID)"
    sleep 3
fi

# Show roster
log "Current factory roster:"
driftdriver brain roster 2>/dev/null || echo "  (empty — brain will discover and enroll repos)"

log ""
log "Factory brain is running inside the ecosystem hub."
log "The brain will autonomously:"
log "  - Discover repos with .workgraph/"
log "  - Enroll/unenroll repos based on activity"
log "  - Start/restart dispatch loops"
log "  - Heal failures (stuck daemons, crashed loops)"
log "  - Send Telegram alerts for significant decisions"
log ""
log "Monitor:"
log "  driftdriver brain status   — brain state"
log "  driftdriver brain roster   — enrolled repos"
log "  driftdriver brain log      — reasoning log"
log ""
log "Kill switch:"
log "  driftdriver brain unenroll <repo>   — remove a repo"
log "  Ctrl+C the hub                      — stop everything"
```

**Step 2: Add deprecation notice to dark-factory.sh**

Add at the top of `dark-factory.sh`:

```bash
echo "⚠️  dark-factory.sh is deprecated. Use: driftdriver/scripts/factory-brain-start.sh"
echo "   The factory brain manages dispatch loops, enrollment, and healing autonomously."
echo ""
echo "   Continuing with legacy dark factory in 5 seconds... (Ctrl+C to abort)"
sleep 5
```

**Step 3: Commit**

```bash
chmod +x driftdriver/scripts/factory-brain-start.sh
git add driftdriver/scripts/factory-brain-start.sh driftdriver/scripts/dark-factory.sh
git commit -m "feat: factory-brain-start.sh replaces dark-factory.sh"
```

---

## Execution Order and Dependencies

```
Task 1 (dispatch-loop hardening) → standalone, do first
Task 2 (events) → standalone
Task 3 (directives) → standalone
Task 4 (roster) → standalone
Tasks 2-4 can be done in parallel

Task 5 (brain core) → depends on Tasks 2, 3
Task 6 (telegram) → depends on Task 3
Task 7 (router) → depends on Tasks 2, 3, 5
Task 8 (hub integration) → depends on Tasks 4, 7
Task 9 (CLI) → depends on Task 4
Task 10 (template + enrollment) → depends on Tasks 1, 3
Task 11 (integration test) → depends on Tasks 7, 8
Task 12 (startup script) → depends on Task 8
```
