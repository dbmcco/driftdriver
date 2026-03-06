# Worker Driver Contract

## Purpose

Define the runtime-neutral interface between `speedriftd` and worker runtimes.

Speedrift must depend on this contract, not on `claude-session-driver` specifically.

## Design Goals

- functional parity across Claude and Codex
- deterministic addressing of workers
- durable supervision independent of a foreground chat
- runtime-specific implementation freedom

## Interface

Every driver must implement the following operations:

```text
launch_worker(task_id, cwd, runtime_config) -> worker_id
send_prompt(worker_id, prompt, context_pack?) -> delivery_ack
tail_output(worker_id, after_seq?) -> output_events[]
heartbeat(worker_id) -> heartbeat_snapshot
wait_for_terminal_state(worker_id, timeout_seconds) -> terminal_state
resume_worker(worker_id, prompt, context_pack?) -> delivery_ack
stop_worker(worker_id, reason?) -> stop_ack
collect_result(worker_id) -> structured_result
```

## Required Worker Identity

Every worker must expose:

- `worker_id`
- `runtime`
- `task_id`
- `cwd`
- `started_at`
- `attempt`
- `driver_metadata`

`worker_id` must be stable for the lifetime of the worker and unique within repo runtime history.

## Required Heartbeat Snapshot

```json
{
  "worker_id": "repo-task-0001",
  "alive": true,
  "state": "running",
  "last_heartbeat_at": "2026-03-06T13:00:00Z",
  "last_output_at": "2026-03-06T12:59:41Z",
  "output_seq": 42
}
```

## Terminal States

Drivers must normalize outcomes into:

- `done`
- `failed`
- `needs_human`
- `stalled`
- `timed_out`
- `continuing`
- `stopped`

## Structured Result

```json
{
  "worker_id": "repo-task-0001",
  "task_id": "implement-runtime-ledger",
  "runtime": "codex",
  "terminal_state": "done",
  "summary": "Implemented runtime event ledger and tests.",
  "evidence": [
    ".workgraph/service/runtime/results/repo-task-0001.json",
    "tests/test_runtime_events.py"
  ],
  "needs_followup": false
}
```

## Output Event Requirements

Drivers do not need identical raw output formats, but `tail_output` must normalize to:

```json
{
  "seq": 42,
  "ts": "2026-03-06T13:00:00Z",
  "stream": "stdout",
  "kind": "text",
  "text": "Running integration tests"
}
```

Allowed `kind` values:

- `text`
- `tool_call`
- `tool_result`
- `status`
- `warning`
- `error`

## Driver Responsibilities

A driver must:

- create and own the worker process/session
- provide a stable `worker_id`
- expose incremental output
- expose liveness and freshness
- stop the worker on demand
- return normalized results

## Driver Non-Responsibilities

A driver must not:

- decide whether a task should continue
- modify WorkGraph state directly
- suppress or reinterpret policy decisions
- bypass verification gates

## Claude / Codex Parity Requirement

`claudedriver` is the current reference implementation.

`codexdriver` must achieve functional parity at the contract layer:

- same callable operations
- same terminal states
- same heartbeat semantics
- same ability to support continuation

Implementation details may differ.

## Continuation Semantics

`resume_worker(...)` may be implemented as either:

- true session resume, or
- fresh worker launch with a daemon-built continuation context pack

Speedrift cares about semantic parity, not transport identity.

## Conformance Tests

Every driver must pass the same suite:

- launches worker and returns a stable id
- accepts prompt delivery
- emits output events
- emits heartbeat snapshots
- reaches a terminal state
- supports continuation path
- supports forced stop
- returns normalized structured result
