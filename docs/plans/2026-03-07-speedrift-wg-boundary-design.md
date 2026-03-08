# Speedrift / Workgraph Architectural Boundary Design

**Date:** 2026-03-07
**Status:** Approved
**Scope:** Full ecosystem — driftdriver, all drift lanes, ecosystem hub, workgraph integration

## Goal

Speedrift is the **opinionated judgment engine**. Workgraph (wg) is the **execution layer**. Every action Speedrift wants taken flows through a formal directive interface. When Erik ships a wg portfolio coordinator, the execution shim is replaced — Speedrift's judgment code doesn't change.

## Context

### Current State

- **wg** is Erik Garrison's execution engine (496 commits). It handles task graphs, dependency resolution, coordinator tick loops, agent spawning, agency (roles/motivations/eval/evolve), cycles, federation, messaging, and multi-executor support.
- **Speedrift ecosystem** grew organically into a judgment + execution hybrid. Drift lanes are cleanly judgment. The orchestrator (driftdriver) mixes both. Ecosystem hub, autopilot, and drift_task_guard all call `wg` CLI directly.
- **Braydon's `feature/project-protocol` branch** (8 commits) added validation gates inside wg that are judgment-layer concerns.

### Boundary Violations Found

**Speedrift doing execution:**
- `project_autopilot` — spawns workers, claims tasks, marks done/fail
- `ecosystem_hub/server.py` — restarts stalled wg services
- `drift_task_guard` — calls `wg add` directly
- `discovery.py` — runs `gh pr create` directly

**Workgraph doing judgment (Braydon's additions):**
- `wg validate` — deliverable verification
- `wg validate-phase` — phase boundary checks
- Auto-create validation tasks on `done`
- Integration readiness gate in `done.rs`

### Key Distinction: Agency Is Execution

Erik built the Agency system (roles, motivations, eval, evolve) as part of wg. It answers "who works on this?" — that's agent assignment, an execution concern. "Did they do it well enough?" is Speedrift's judgment concern. These are different questions.

## Design

### 1. Directive Schema

The contract between Speedrift (judgment) and wg (execution). Every execution action flows through a directive.

```jsonc
{
  "id": "dir-20260307-a1b2c3",
  "timestamp": "2026-03-07T14:30:00Z",
  "source": "control_plane",
  "repo": "paia-shell",              // repo-relative, portable
  "action": "create_task",
  "params": {
    "task_id": "drift-harden-fix-auth",
    "title": "harden: fix-auth",
    "after": ["fix-auth"],
    "tags": ["drift", "harden"]
  },
  "reason": "Hardening signals detected in core task fix-auth",
  "authority": {
    "actor": "coredrift",
    "actor_class": "lane",
    "budget_remaining": 2
  },
  "priority": "normal"
}
```

**Action vocabulary:**

| Action | Params | Emitted by |
|--------|--------|------------|
| `create_task` | task_id, title, after, tags, description | drift_task_guard, autopilot |
| `claim_task` | task_id, agent | autopilot |
| `complete_task` | task_id, artifacts | autopilot |
| `fail_task` | task_id, reason | autopilot |
| `start_service` | repo | ecosystem_hub |
| `stop_service` | repo | ecosystem_hub |
| `log_to_task` | task_id, message | drift lanes, handlers |
| `evolve_prompt` | pattern, evidence | outcome_feedback |
| `dispatch_to_peer` | repo, task_id, prompt | autopilot |
| `block_task` | task_id, reason, until | validation gates |
| `create_validation` | parent_task_id, criteria | validation gates |
| `create_upstream_pr` | repo, branch, title, body | discovery |

**Storage:** `.workgraph/service/directives/pending.jsonl` — append-only, cursor-based.

**Constraint:** Repo-relative task IDs and portable paths. No absolute paths in directives. This ensures clean migration to a networked portfolio coordinator later.

### 2. Executor Shim

Temporary bridge: reads directives, calls wg CLI. Intentionally dumb — no judgment, no filtering, just translation.

```
.workgraph/service/directives/
├── pending.jsonl          # Speedrift appends here
├── completed.jsonl        # Shim moves processed directives here
├── failed.jsonl           # Directives that errored
└── cursor.json            # Read position
```

**Execution mode:** Inline (synchronous). Speedrift calls `shim.execute(directive)` immediately after emitting. JSONL audit trail supports async consumption later if needed.

**Location:** `driftdriver/executor_shim.py` (~150-200 lines).

**Lifecycle:** Dies when Erik ships portfolio coordination. Replaced with wg portfolio API calls. Speedrift's judgment code is untouched.

**Key rule:** The shim never makes decisions. Directives are pre-authorized by authority + budget gates before emission. The shim just translates and executes.

### 3. Module Migration

#### Stays in Speedrift (already clean judgment) — no changes

- All 15 drift lanes (coredrift, specdrift, datadrift, archdrift, depsdrift, uxdrift, therapydrift, fixdrift, yagnidrift, redrift, secdrift, qadrift, plandrift, northstardrift, factorydrift)
- `control_plane` — dependency pressure scoring
- `health` — task classification, queue ranking
- `routing_models` — lane selection
- `authority` — actor identity + budget gates
- `outcome_feedback` — pre/post check comparison
- `notifications` — alert routing

#### Stays in Speedrift, refactored to emit directives

| Module | Change |
|--------|--------|
| `drift_task_guard` | Last ~10 lines: `wg add` → `shim.execute(create_task)` |
| `project_autopilot` | Rebuild as slim judgment loop + `driftdriver decompose` command. Emits directives, doesn't spawn workers. |
| `ecosystem_hub/server.py` | Service supervision, factory, northstar, upstream automation → emit directives through shim |

#### Moves out of wg back to Speedrift

| wg addition (feature/project-protocol) | Becomes |
|-----------------------------------------|---------|
| `wg validate` | Handler in `task-completing.sh` — emits `block_task` directive on failure |
| `wg validate-phase` | Speedrift judgment module — emits `block_task` or `create_validation` directive |
| Auto-create validation tasks on `done` | Handler in `task-completing.sh` — emits `create_validation` directive |
| Integration readiness gate in `done.rs` | Handler — emits `log_to_task` directive on downstream tasks |
| `protocol.md` injection | Moves to `templates/adapters/` |

**Net effect:** `feature/project-protocol` branch can be closed.

#### Stays unchanged (visibility/wiring)

- Ecosystem hub dashboard, API, WebSocket
- Snapshot collection
- Install system + templates
- `wire.py`, `speedriftd`, `speedriftd_state`

#### New modules

| Module | Role | Size |
|--------|------|------|
| `driftdriver/directives.py` | Directive dataclass + JSONL I/O + action enum | ~100 lines |
| `driftdriver/executor_shim.py` | Maps directives → wg CLI, records outcomes | ~150-200 lines |

### 4. Autonomous Mode

The ecosystem hub's collector loop IS the autonomous engine. No separate autopilot daemon needed.

```
Each collector cycle:
  1. Snapshot all repos (read-only)
  2. Run judgment modules (control_plane, health, northstardrift, routing, factory)
  3. Emit directives based on mode:
     - observe:    no directives
     - supervise:  start_service, log_to_task only
     - autonomous: full vocabulary
  4. Shim executes directives inline
  5. Record outcomes
  6. Broadcast updated snapshot to dashboard
```

**Safety:** Authority budgets cap directive volume. Global ceiling prevents runaway. All directives logged for audit. `escalations.jsonl` captures capped directives for human review.

**Goal decomposition:** Standalone command, not a persistent loop:
```bash
driftdriver decompose --goal "Ship auth system" --repo /path/to/repo
```
Emits batch of `create_task` directives. Hub's autonomous loop monitors from there.

### 5. Future Migration (Erik's Portfolio Coordinator)

When Erik ships portfolio coordination:

| Current (shim) | Future (wg portfolio) |
|---|---|
| `shim.execute(start_service, repo=X)` | `wg portfolio service start --repo X` |
| `shim.execute(create_task, repo=X)` | `wg portfolio add --repo X --id ...` |
| `shim.execute(dispatch_to_peer, repo=X)` | `wg portfolio dispatch --repo X` |
| Directive JSONL files | wg's native intake |

**Migration:** Delete `executor_shim.py`. Replace with wg portfolio API calls. Speedrift judgment code untouched. Directive schema becomes the API contract with wg.

## Deliverables

1. `driftdriver/directives.py` — schema + I/O
2. `driftdriver/executor_shim.py` — temporary bridge
3. `drift_task_guard` refactor — emit directives
4. Ecosystem hub refactor — emit directives for service/factory/northstar/upstream
5. Autopilot rebuild — slim judgment loop + `driftdriver decompose`
6. Validation gates migration — `feature/project-protocol` → Speedrift handlers
7. Autonomous mode wiring — speedriftd mode controls directive vocabulary

## What Doesn't Get Touched

- All 15 drift lanes
- Dashboard / API / WebSocket
- Snapshot collection
- wg itself (Erik's code)
- Install system + handler templates (minor updates)
