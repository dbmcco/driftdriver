# Speedrift Runtime WorkGraph Backlog

## Purpose

Define the implementation backlog in WorkGraph-ready form for the runtime/driver overhaul.

## Root Epic

| id | title | blocked_by |
| --- | --- | --- |
| `speedrift-runtime-epic` | Build WorkGraph-first runtime supervision with Claude/Codex driver parity | - |

## Phase 0: Contracts

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-contract-speedriftd` | Finalize `speedriftd` runtime contract | `speedrift-runtime-epic` | `docs/plans/speedriftd-runtime-contract.md` |
| `runtime-contract-driver` | Finalize worker-driver contract | `speedrift-runtime-epic` | `docs/plans/worker-driver-contract.md` |
| `runtime-contract-events` | Finalize runtime event schema | `speedrift-runtime-epic` | `docs/plans/runtime-event-schema.md` |
| `runtime-contract-codexdriver` | Finalize `codexdriver` MVP design | `runtime-contract-driver` | `docs/plans/codexdriver-mvp-design.md` |

## Phase 1: Repo Runtime MVP

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-build-speedriftd-shell` | Create repo-local daemon skeleton and config loader | `runtime-contract-speedriftd`,`runtime-contract-events` | daemon entrypoint + local state bootstrap |
| `runtime-build-lease-ledger` | Implement task lease and worker registry files | `runtime-build-speedriftd-shell` | `.workgraph/service/runtime/{leases,workers,current}.json*` |
| `runtime-build-heartbeat-loop` | Implement worker heartbeat and output freshness monitor | `runtime-build-speedriftd-shell`,`runtime-contract-events` | local heartbeat loop |
| `runtime-build-stall-classifier` | Implement deterministic stall classification | `runtime-build-heartbeat-loop` | `stalls.jsonl` + daemon state transitions |

## Phase 2: Claude Reference Driver

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-wrap-claudedriver` | Wrap current `claude-session-driver` path behind the new driver contract | `runtime-contract-driver`,`runtime-build-speedriftd-shell` | `claudedriver` adapter |
| `runtime-test-driver-conformance-claude` | Run contract conformance suite against Claude adapter | `runtime-wrap-claudedriver` | driver conformance tests |

## Phase 3: Codex Driver MVP

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-build-codexdriver-launch` | Launch detached Codex workers with persistent runtime artifacts | `runtime-contract-codexdriver`,`runtime-build-speedriftd-shell` | `codexdriver launch_worker` |
| `runtime-build-codexdriver-output` | Normalize `codex exec --json` output into runtime events | `runtime-build-codexdriver-launch`,`runtime-contract-events` | output/event adapter |
| `runtime-build-codexdriver-result` | Implement Codex terminal-state classification and result normalization | `runtime-build-codexdriver-output` | `collect_result` |
| `runtime-build-codexdriver-continuation` | Implement continuation via relaunch context pack | `runtime-build-codexdriver-result` | `resume_worker` MVP |
| `runtime-test-driver-conformance-codex` | Run contract conformance suite against Codex adapter | `runtime-build-codexdriver-continuation` | driver conformance tests |

## Phase 4: Dashboard Truth

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-hub-active-workers` | Expose active worker state in status API | `runtime-build-heartbeat-loop`,`runtime-wrap-claudedriver` | API payload updates |
| `runtime-hub-stall-visibility` | Expose stale heartbeat, stale output, and stall reason in UI | `runtime-hub-active-workers`,`runtime-build-stall-classifier` | dashboard visibility |
| `runtime-hub-runtime-mix` | Show runtime mix and driver status per repo | `runtime-hub-active-workers`,`runtime-test-driver-conformance-codex` | repo cards + overview |

## Phase 5: Graph Integrity

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-plandrift-verification-edges` | Enforce integration/e2e/test-edge insertion in active plans | `runtime-contract-speedriftd` | `plandrift` updates |
| `runtime-plandrift-continuation-edges` | Enforce continuation and failure loopback edges | `runtime-plandrift-verification-edges` | graph-integrity checks |
| `runtime-plandrift-followups` | Emit corrective WorkGraph tasks for missing graph structure | `runtime-plandrift-continuation-edges` | repo-local follow-up emission |

## Phase 6: Ecosystem Supervision

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-central-daemon-supervision` | Let central control plane monitor repo daemons | `runtime-hub-stall-visibility` | central repo-daemon view |
| `runtime-central-restart-policy` | Add bounded restart and cooldown policy for repo daemons | `runtime-central-daemon-supervision` | restart policy + ledgers |
| `runtime-central-dependency-pressure` | Surface inter-repo runtime bottlenecks and blocked chains | `runtime-central-daemon-supervision` | inter-repo actionability |

## Phase 7: Rust Kernel Migration

| id | title | blocked_by | deliverable |
| --- | --- | --- | --- |
| `runtime-rust-daemon-skeleton` | Create Rust `speedriftd` skeleton that matches the Python contract | `runtime-build-stall-classifier`,`runtime-test-driver-conformance-codex` | Rust daemon shell |
| `runtime-rust-event-ledger` | Move runtime event appenders and heartbeat bookkeeping to Rust | `runtime-rust-daemon-skeleton` | Rust ledger layer |
| `runtime-rust-scheduler` | Move bounded worker scheduling and lease management to Rust | `runtime-rust-event-ledger` | Rust scheduler |
| `runtime-rust-cutover` | Cut daemon runtime core from Python to Rust while preserving policy APIs | `runtime-rust-scheduler` | production runtime cutover |

## Acceptance Gates

The epic is complete only when:

- Claude and Codex both pass the same driver conformance suite
- no foreground chat must remain open for work to continue
- the dashboard can prove whether a repo is active, stalled, or idle
- missing verification/continuation structure becomes explicit WorkGraph follow-up work
- the runtime kernel boundary is stable enough to migrate from Python to Rust without policy churn
