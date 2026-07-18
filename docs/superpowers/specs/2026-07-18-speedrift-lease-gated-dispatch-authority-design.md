# Speedrift Lease-Gated Dispatch Authority Design

**Status:** Approved direction; awaiting written-spec review before implementation.

## Purpose

Close the remaining Driftdriver control-plane gap in which a repository can report that dispatch is disabled after a lease expires while an already-running Workgraph coordinator may continue dispatching. This phase makes one lease-aware authority decision the admission boundary for every Driftdriver-owned dispatch and service-start path.

This is a current-main rebaseline. The `speedrift-maintenance-20260716` worktree and its five dirty planning documents are historical input only. Its old Task 2D/2E contracts, absent test names, stale Workgraph SHA, and Phase 3 smoke assumptions are not implementation instructions.

## Current baseline

- Driftdriver: commit `4232313` on `main`, aligned with `origin/main`.
- Current behavior already includes observe-first defaults, lease requirements for elevated modes, session-start gating, provider-qualified Pi compatibility, Agency/Pi fallback receipt helpers, modelrift/surfacedrift, and Workgraph-before-Speedrift initialization ordering.
- Workgraph adopted integration baseline: `e2418e4423262442ba6712b7a2eee80dd577269b`.
- The live State System agent owns `contracts.schema-export` across `paia-contracts` and `state-system`. This phase must not touch those repositories or their schema/validator files.

## Problem statement

`speedriftd_state.py` derives `dispatch_enabled` from the current control state, but the runtime loop does not reliably enforce the transition from an active lease to an expired lease. In particular:

1. Autopilot and factory paths can reach task-claim or worker-launch behavior without a shared admission predicate.
2. Service-start surfaces do not all use the same lease authority.
3. An already-running coordinator is not necessarily stopped or revoked when its lease expires.
4. Existing tests prove the reported flag becomes false, but not that claims, worker starts, coordinator activity, or completion mutations stop.

## End-state contract

For every Driftdriver-owned dispatch entrypoint:

- `observe`, `manual`, missing-owner, malformed, and expired lease states fail closed.
- `supervise` and `autonomous` states dispatch only while their lease is active.
- Denial is side-effect-free and returns a stable reason suitable for logs and tests.
- A lease-expiry transition stops or revokes an active coordinator exactly once and records terminal evidence.
- Dry-run behavior remains side-effect-free.
- Existing Pi provider-qualified and fallback receipt behavior remains unchanged.
- No State System, schema, contract, PlanForge, or Persona behavior changes in this phase.

## Architecture

### Shared authority predicate

Add one small, side-effect-free authority result in `driftdriver/speedriftd_state.py`. It must require both an elevated mode (`supervise` or `autonomous`) and an active lease, preserve readable expired state, and provide a deterministic denial reason. It must not acquire or release leases, change modes, or stop processes.

All callers must consume this result rather than independently checking mode or `dispatch_enabled`.

### Admission gates

Gate the following before any claim, spawn, completion mutation, or service start:

- `driftdriver/project_autopilot.py` autopilot loop and task dispatch.
- `driftdriver/factorydrift.py` ready-worker selection and dispatch.
- Direct service-start routes in `driftdriver/ecosystem_hub/api.py`.
- Supervisor/service command construction in `driftdriver/ecosystem_hub/snapshot.py`.

Denied paths must not invoke `wg claim`, start a worker/session, or mark work complete.

### Expiry transition

In `driftdriver/speedriftd.py`, detect the transition from an active lease to an expired/denied state. Stop or revoke the running coordinator once, persist terminal evidence, and remain idempotent across subsequent cycles. An expiry stop must not silently acquire a new lease or alter the operator's mode beyond the existing control-state contract.

## Implementation touch set

```text
driftdriver/speedriftd_state.py
driftdriver/project_autopilot.py
driftdriver/factorydrift.py
driftdriver/ecosystem_hub/api.py
driftdriver/ecosystem_hub/snapshot.py
driftdriver/speedriftd.py
tests/test_speedriftd_runtime_gate.py
tests/test_speedriftd_state.py
tests/test_project_autopilot.py
tests/test_autopilot_federation.py
tests/test_speedriftd.py
tests/test_ecosystem_hub.py
tests/test_factorydrift.py
```

If implementation discovers a required file outside this set, stop and record a follow-up or request a scope decision. Do not silently expand the phase.

## Task sequence

1. Define and test the shared authority result.
2. Gate autopilot and factory claims/worker launches.
3. Gate all Driftdriver-owned service-start surfaces.
4. Implement idempotent coordinator stop/revocation on lease expiry.
5. Run focused validation, raw-call-site audit, drift checks, review, and the supported full suite.

Tasks 2 and 3 may proceed in parallel only after Task 1 is complete. Task 4 depends on the authority predicate and persisted runtime semantics. The review gate depends on all implementation tasks.

## Acceptance criteria

- Unit coverage includes observe, manual, active supervise, active autonomous, expired lease, missing owner, and malformed control data.
- Observe and expired runs produce no `wg claim`, no session/CLI worker, no coordinator dispatch, and no completion mutation.
- Active leases preserve current behavior.
- Expiry stops/revokes an active coordinator exactly once, with terminal evidence.
- Repeated runtime cycles do not duplicate stop/revoke events.
- Direct service-start APIs and factory/autopilot paths return deterministic denial reasons.
- A repository search identifies every raw `wg service start`, `wg spawn`, worker-launch, and executor-admission call site; each is gated or recorded as a bounded follow-up.
- Existing Pi compatibility, runtime-gate, reaper, and lane tests remain green.
- No files in State System, `paia-contracts`, `paia-agent-runtime`, `paia-os`, PlanForge, or Pi persona repositories change.

## Validation

Before implementation:

```bash
driftdriver --dir /Users/braydon/projects/experiments/driftdriver --json speedriftd status --refresh
./.workgraph/drifts check --task speedrift.lease-gated-dispatch-authority --write-log
```

Focused validation:

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest -q \
  tests/test_speedriftd_runtime_gate.py \
  tests/test_speedriftd_state.py \
  tests/test_project_autopilot.py \
  tests/test_autopilot_federation.py \
  tests/test_speedriftd.py \
  tests/test_ecosystem_hub.py \
  tests/test_factorydrift.py
```

The exact file list may be trimmed only when a named file or relevant fixture does not exist; that discrepancy must be recorded. Also run a targeted search audit for raw service starts, `wg spawn`, worker launches, and executor-admission paths.

Completion validation:

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest
./.workgraph/drifts check --task speedrift.lease-gated-dispatch-authority --write-log --create-followups
```

If the full suite cannot collect because of the known undeclared `anthropic` dependency, record the exact collection failure as a baseline exception. Focused green tests must not be reported as a full-suite pass.

A fresh read-only adversarial review and RoboRev/review are required. They must specifically probe expiry races, duplicate stop events, denial status, raw bypass paths, and accidental State System/schema changes. No live credentials, autonomous arming, external sends, or external Workgraph branch operations are required.

## Rollback and safety boundaries

Keep the authority predicate, each admission gate, expiry-stop behavior, and tests in separable commits. If expiry-stop behavior proves unsafe, revert only that task while retaining fail-closed admission gates and the evidence that identified the problem.

Do not reset the historical maintenance worktree, modify `.driftdriver/upstream-pins.toml`, change the adopted Workgraph checkout, or touch State System branches. Return any supervised repository to observe mode after validation.

## Deferred decisions

The following are explicitly outside this phase and require separate decisions:

- Whether handler-first Pi dispatch replaces or wraps the current first-class Pi executor.
- Whether Agency remains preferred or becomes default-off.
- Which Workgraph SHA is the next integration pin.
- The canonical resolver contract for `.workgraph`, `.wg`, explicit paths, conflicts, and partial state.
- Whether a failed verification blocks parent completion or creates a held validation child task.
- Any external directive-interception or lane-wide task-creation redesign.

## Non-goals

This phase does not implement State System contracts, JSON Schema exports, validator replacement, PlanForge behavior, Workgraph upstream changes, new drift lanes, messaging actions, external effects, or persona/runtime policy changes.
