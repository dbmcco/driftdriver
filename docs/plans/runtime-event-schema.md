# Runtime Event Schema

## Purpose

Define the repo-local runtime ledger and the summarized projection mirrored into the central ecosystem register.

WorkGraph remains the task authority.
Runtime events describe execution, not planning.

## Event Envelope

Every runtime event must conform to this outer shape:

```json
{
  "event_id": "evt_01HV...",
  "ts": "2026-03-06T13:00:00Z",
  "repo": "meridian",
  "cycle_id": "cycle_2026_03_06_130000",
  "worker_id": "meridian-impl-runtime-ledger-001",
  "task_id": "impl-runtime-ledger",
  "runtime": "codex",
  "event_type": "heartbeat",
  "state": "running",
  "payload": {}
}
```

## Required Top-Level Fields

| Field | Meaning |
| --- | --- |
| `event_id` | Unique id for replayability |
| `ts` | Event timestamp in UTC |
| `repo` | Repo identifier |
| `cycle_id` | Daemon cycle that emitted or observed the event |
| `worker_id` | Runtime worker identity |
| `task_id` | WorkGraph task id |
| `runtime` | `claude`, `codex`, `tmux`, etc. |
| `event_type` | One of the event types below |
| `state` | Normalized runtime state at event time |
| `payload` | Event-specific detail |

## Event Types

- `worker_launched`
- `prompt_delivered`
- `heartbeat`
- `output`
- `continuation_requested`
- `continuation_launched`
- `verification_started`
- `verification_finished`
- `worker_result`
- `stall_detected`
- `retry_scheduled`
- `worker_stopped`
- `repo_service_state`

## Event Payloads

### `worker_launched`

```json
{
  "pid": 12345,
  "attempt": 1,
  "cwd": "/repo",
  "driver": "codexdriver"
}
```

### `heartbeat`

```json
{
  "alive": true,
  "last_output_at": "2026-03-06T12:59:41Z",
  "output_seq": 42
}
```

### `output`

```json
{
  "seq": 42,
  "kind": "text",
  "stream": "stdout",
  "text": "Running integration tests"
}
```

### `stall_detected`

```json
{
  "reason": "no_output_freshness",
  "seconds_since_output": 960,
  "retry_allowed": true
}
```

### `worker_result`

```json
{
  "terminal_state": "done",
  "summary": "Implemented runtime events and tests",
  "evidence": [
    "tests/test_runtime_events.py"
  ]
}
```

## Repo-Local Storage Layout

- `.workgraph/service/runtime/events/<timestamp>.jsonl`
- `.workgraph/service/runtime/workers.jsonl`
- `.workgraph/service/runtime/heartbeats/<worker_id>.json`
- `.workgraph/service/runtime/results/<worker_id>.json`
- `.workgraph/service/runtime/current.json`
- `.workgraph/service/runtime/stalls.jsonl`

## `current.json` Snapshot

`current.json` is the daemon-friendly repo snapshot:

```json
{
  "repo": "meridian",
  "daemon_state": "running",
  "updated_at": "2026-03-06T13:00:00Z",
  "active_workers": [
    {
      "worker_id": "meridian-impl-runtime-ledger-001",
      "task_id": "impl-runtime-ledger",
      "runtime": "codex",
      "state": "running",
      "last_heartbeat_at": "2026-03-06T13:00:00Z",
      "last_output_at": "2026-03-06T12:59:41Z"
    }
  ],
  "stalled_tasks": [],
  "next_action": "continue supervision"
}
```

## Central Projection

The hub should mirror only summarized state:

```json
{
  "repo": "meridian",
  "daemon_state": "running",
  "active_worker_count": 1,
  "active_task_ids": ["impl-runtime-ledger"],
  "stalled_task_ids": [],
  "runtime_mix": ["codex"],
  "last_heartbeat_age_seconds": 5,
  "last_output_age_seconds": 24,
  "next_action": "continue supervision"
}
```

## Freshness Semantics

Suggested config-controlled thresholds:

- `heartbeat_stale_after_seconds`
- `output_stale_after_seconds`
- `worker_timeout_seconds`
- `retry_cooldown_seconds`

Classification:

- heartbeat fresh + output fresh -> `running`
- heartbeat fresh + output stale -> `watch`
- heartbeat stale + process alive unknown -> `stalled`
- timeout exceeded -> `timed_out`

## Dashboard Requirements

The dashboard should be able to derive from this schema:

- is work really active?
- which task is active?
- when did the worker last say anything?
- is it stalled or merely quiet?
- what will the daemon do next?

## Schema Evolution Rule

Additive changes are preferred.
If an event type needs breaking changes, version the payload under `payload.schema_version`.
