# speedriftd Runtime Contract

## Purpose

Define the repo-local supervisor that keeps Speedrift moving when no foreground Claude or Codex chat is active.

`speedriftd` is the runtime control loop for a single repo.
It is not the task graph authority and it is not the policy brain.

## Stack Position

| Layer | Owner | Responsibility |
| --- | --- | --- |
| Work planning + task truth | WorkGraph | Tasks, dependencies, ready/blocked/in-progress/done, continuation edges, verification edges |
| Runtime supervision | `speedriftd` | Worker lifecycle, heartbeats, stall detection, bounded retries, local ledgers |
| Model-mediated policy | Python drift modules | Priority, diagnosis, follow-up emissions, narrated state, escalation choices |
| Ecosystem awareness | ecosystem hub | Multi-repo register, websocket/dashboard, inter-repo pressure |

## System Invariants

- WorkGraph remains the only task/state source of truth.
- `speedriftd` never invents task state outside WorkGraph.
- Runtime telemetry lives under `.workgraph/service/`, not inside graph task bodies.
- Every worker action produces a local event trail.
- Every daemon action is replayable from repo-local artifacts.

## Non-Goals

- Not a replacement for WorkGraph.
- Not a tmux orchestration layer.
- Not a policy engine with hidden heuristics.
- Not a direct writer to protected branches or destructive git operations.

## Responsibilities

`speedriftd` must:

1. Observe repo state.
2. Read ready/in-progress work from WorkGraph.
3. Acquire a bounded execution lease for one or more tasks.
4. Dispatch workers through a driver contract.
5. Monitor worker heartbeat and output freshness.
6. Detect `stalled`, `timed_out`, `failed`, `done`, and `needs_human` states.
7. Trigger continuation or retry when policy allows.
8. Run deterministic post-run verification steps.
9. Write local runtime ledgers.
10. Mirror a summarized snapshot into the central register.

## Authority Boundary With WorkGraph

WorkGraph owns:

- task ids
- dependencies
- ready/in-progress/done/fail semantics
- plan topology
- verification topology
- follow-up tasks

`speedriftd` owns:

- worker ids
- worker processes/sessions
- runtime heartbeats
- last output timestamps
- retry counters
- worker log locations
- stall classification
- local runtime snapshots

`speedriftd` may update WorkGraph only through explicit, deterministic transitions:

- claim/start task
- mark done
- mark failed / needs human
- emit follow-up task requests via approved drift modules
- add runtime-linked logs/evidence references

## Runtime Loop

1. Observe
- read WorkGraph ready/in-progress state
- read prior runtime ledger
- read repo service health

2. Select
- choose bounded dispatch set based on current lease budget
- never exceed configured concurrency

3. Dispatch
- call driver `launch_worker(...)`
- persist worker metadata immediately

4. Supervise
- poll or stream heartbeats
- tail output
- refresh `last_output_at`
- classify inactivity against policy thresholds

5. Classify
- `done`
- `failed`
- `needs_human`
- `stalled`
- `timed_out`
- `continuing`

6. Verify
- run deterministic checks required by policy
- attach evidence paths

7. Record
- append runtime events
- update current snapshot
- mirror repo summary centrally

8. Continue
- if policy allows, resume or relaunch a continuation worker
- otherwise emit a follow-up or escalation task into WorkGraph

## Repo-Local Filesystem Contract

All runtime artifacts live under `.workgraph/service/runtime/`.

Required files:

- `current.json`
  - latest daemon snapshot for the repo
- `control.json`
  - repo control mode and current supervisor lease
- `workers.jsonl`
  - append-only worker lifecycle ledger
- `events/<timestamp>.jsonl`
  - high-volume runtime events
- `heartbeats/<worker_id>.json`
  - latest heartbeat per worker
- `stalls.jsonl`
  - append-only stall classifications
- `results/<worker_id>.json`
  - normalized terminal result
- `leases.json`
  - active task execution leases

## Repo Control Modes

`speedriftd` must distinguish repo intent from repo health.

Required control modes:

- `manual`
  - no automatic dispatch
- `observe`
  - refresh/report only; interactive sessions must not auto-start execution services
- `supervise`
  - services may start under an explicit supervisor lease
- `autonomous`
  - repo is armed for daemon-led dispatch and supervision

Interactive hooks must respect `control.json` rather than silently starting competing loops.

## Central Mirror Contract

The daemon must write a summarized projection to the ecosystem register, not raw full logs.

Central summary should include:

- repo
- daemon state
- active worker count
- active task ids
- last heartbeat age
- stalled task ids
- last successful dispatch
- current runtime mix (`claude`, `codex`, etc.)
- next planned action

## Safety Contract

`speedriftd` must enforce:

- max concurrent workers per repo
- max retries per task
- max continuation depth
- cooldown between restarts
- hard stop after repeated verification failures
- no hidden fallback that changes task semantics

## Python / Rust Boundary

Initial implementation may be Python for speed, but the contract is written so the runtime core can migrate to Rust without changing policy modules.

Python should keep:

- policy prompts
- scoring
- narration
- follow-up generation
- drift module composition

Rust should eventually own:

- daemon main loop
- worker supervision
- event appenders
- heartbeat bookkeeping
- lease management
- bounded scheduling

## Acceptance Criteria

`speedriftd` is acceptable when:

- a foreground chat can exit and the repo still progresses
- active workers remain visible via heartbeat and output freshness
- dead or silent workers become `stalled` without human polling
- all runtime actions are reconstructable from `.workgraph/service/runtime/`
- central status always reflects whether the repo is truly active, stalled, or idle
