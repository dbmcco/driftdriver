# Workgraph Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt the highest-value upstream `workgraph` changes into our fork in a staged, testable way, starting with recovery and worktree hygiene rather than a blind repo-wide convergence move.

**Architecture:** Treat the current adopted `workgraph` line as canonical until upstream replacements are proven better under stronger contracts. Land changes by tranche: first recovery and worktree hygiene, then execution routing, then session runtime. Use upstream commit lift or cherry-pick where clean, preserve local fork behavior by default, and strengthen `driftdriver` compatibility checks after each landed tranche.

**Tech Stack:** Rust (`workgraph` CLI/service), Python (`driftdriver` upstream tracker), pytest for Speedrift contracts, cargo test for `workgraph`, git fork/upstream remotes.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `driftdriver/docs/superpowers/specs/2026-04-26-workgraph-adoption-design.md` | Create | Adoption design and tranche boundaries |
| `driftdriver/docs/superpowers/plans/2026-04-26-workgraph-adoption.md` | Create | Execution plan for staged adoption |
| `workgraph/src/service/registry.rs` | Modify | Add stronger agent liveness invariant from upstream |
| `workgraph/src/commands/service/mod.rs` | Modify | Register worktree lifecycle module and any related service hooks |
| `workgraph/src/commands/service/coordinator.rs` | Modify | Sweep cleanup-pending worktrees and preserve local coordinator behavior |
| `workgraph/src/commands/service/worktree.rs` | Create | Upstream worktree lifecycle cleanup module |
| `workgraph/src/commands/worktree_gc.rs` | Create | Operator-facing fallback GC for orphaned worktrees |
| `workgraph/src/commands/mod.rs` | Modify | Export newly adopted command modules |
| `workgraph/src/cli.rs` | Modify | Wire GC/worktree and recovery command surfaces if adopted |
| `workgraph/src/main.rs` | Modify | Route new command handlers |
| `workgraph/src/commands/reset.rs` | Create | Bulk reset recovery command from upstream, if Tranche 1 subset includes it |
| `workgraph/src/commands/rescue.rs` | Create | First-class rescue command from upstream, if Tranche 1 subset includes it |
| `workgraph/tests/...` | Modify/Create | Add or lift upstream tests for liveness, worktree cleanup, recovery, and logging |
| `driftdriver/.driftdriver/upstream-config.toml` | Modify | Strengthen Workgraph compatibility checks after Tranche 1 lands |
| `driftdriver/tests/test_upstream_tracker.py` | Modify | Verify updated compatibility gate behavior |

---

## Task 1: Land the adoption design and plan

**Files:**
- Create: `docs/superpowers/specs/2026-04-26-workgraph-adoption-design.md`
- Create: `docs/superpowers/plans/2026-04-26-workgraph-adoption.md`

- [ ] **Step 1: Write the adoption design and implementation plan**

Write the two documents above capturing:
- preserve-local-behavior-first strategy
- tranche order
- non-TUI prioritization
- stronger contract requirement

- [ ] **Step 2: Review the docs for contradictions or placeholders**

Check:
- tranche boundaries are explicit
- Tranche 1 is independently implementable
- no implied blind merge language remains

- [ ] **Step 3: Commit the planning docs**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add docs/superpowers/specs/2026-04-26-workgraph-adoption-design.md docs/superpowers/plans/2026-04-26-workgraph-adoption.md
git commit -m "docs: add staged workgraph adoption plan"
```

## Task 2: Raise the failing Workgraph-side tests for Tranche 1

**Files:**
- Modify/Create: `workgraph/src/service/registry.rs`
- Modify/Create: `workgraph/tests/...`

- [ ] **Step 1: Add or lift failing tests for the stronger liveness invariant**

Target behaviors:
- alive status alone is insufficient
- dead process makes agent non-live
- stale heartbeat makes agent non-live
- all three conditions together make agent live

- [ ] **Step 2: Add or lift failing tests for cleanup-pending worktree sweep**

Target behaviors:
- marked worktree is removed when agent is not live and task is terminal
- live agent worktree is preserved
- non-terminal task worktree is preserved
- repeated sweep is idempotent

- [ ] **Step 3: Add or lift failing tests for recovery command logging if `reset`/`rescue` are included**

Target behaviors:
- operation log entries recorded
- rescue creates first-class replacement task
- reset updates targeted closure without silent mutation outside scope

- [ ] **Step 4: Run the targeted Workgraph tests and confirm they fail for the missing behavior**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test service::registry -- --nocapture
cargo test worktree -- --nocapture
```

Expected: failures or missing-module/command coverage for the newly introduced behavior.

## Task 3: Implement Tranche 1 Worktree hygiene

**Files:**
- Modify: `workgraph/src/service/registry.rs`
- Modify: `workgraph/src/commands/service/mod.rs`
- Modify: `workgraph/src/commands/service/coordinator.rs`
- Create: `workgraph/src/commands/service/worktree.rs`
- Create: `workgraph/src/commands/worktree_gc.rs`
- Modify: `workgraph/src/commands/mod.rs`
- Modify: `workgraph/src/cli.rs`
- Modify: `workgraph/src/main.rs`

- [ ] **Step 1: Add `AgentEntry::is_live()` and keep `is_alive()` behavior unchanged**

Adopt the stronger liveness rule from upstream without changing current callers that still need status-only checks.

- [ ] **Step 2: Lift the upstream worktree lifecycle module**

Bring in:
- cleanup-pending marker semantics
- orphaned worktree cleanup logic
- shared removal machinery
- heartbeat timeout constant

Preserve local project conventions where they differ, but do not re-invent the safety model.

- [ ] **Step 3: Integrate cleanup sweep into coordinator tick**

Add the sweep call in the coordinator path without overwriting our local coordinator-specific deltas.

- [ ] **Step 4: Add operator-facing fallback worktree GC command surface**

Expose safe fallback cleanup for orphaned worktrees. Keep dry-run/default-safe semantics.

- [ ] **Step 5: Run focused `cargo test` slices until Tranche 1 passes**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test worktree -- --nocapture
cargo test integration_service -- --nocapture
cargo test integration_service_coordinator -- --nocapture
```

## Task 4: Adopt recovery commands if they land cleanly in the same tranche

**Files:**
- Create: `workgraph/src/commands/reset.rs`
- Create: `workgraph/src/commands/rescue.rs`
- Modify: `workgraph/src/cli.rs`
- Modify: `workgraph/src/main.rs`
- Modify: `workgraph/src/commands/mod.rs`
- Modify/Create: recovery-related tests

- [ ] **Step 1: Lift `wg rescue` with its tests**

Bring in the first-class replacement-task recovery path if it composes cleanly with the current graph and logging model.

- [ ] **Step 2: Lift `wg reset` with its tests**

Bring in bounded graph reset behavior if it composes cleanly with the current graph model and logging model.

- [ ] **Step 3: Skip this subtask if the recovery commands create cross-tranche risk**

If adopting these commands starts coupling Tranche 1 to execution-routing or session-runtime changes, defer them explicitly instead of forcing them in.

## Task 5: Strengthen Speedrift’s Workgraph contract after Tranche 1 lands

**Files:**
- Modify: `driftdriver/.driftdriver/upstream-config.toml`
- Modify: `driftdriver/tests/test_upstream_tracker.py`

- [ ] **Step 1: Add a stronger Workgraph compatibility check**

Extend the existing Workgraph compatibility section with a check that exercises the landed Tranche 1 behavior, not just wrapper contracts.

- [ ] **Step 2: Verify the tracker still reports adopted-vs-upstream honestly**

Update tests so compatibility passing does not imply branch adoption when the adopted line is still intentionally diverged.

- [ ] **Step 3: Run the targeted `driftdriver` tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_upstream_tracker.py -q
```

## Task 6: Land, verify, and hand off the next tranche boundary

**Files:**
- Modify: adoption docs if needed for factual updates after implementation

- [ ] **Step 1: Run final focused verification**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test integration_service -- --nocapture
cargo test integration_service_coordinator -- --nocapture

cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_upstream_tracker.py tests/test_executor_shim.py tests/test_handlers.py tests/test_unified_install.py -q
```

- [ ] **Step 2: Commit the Tranche 1 code**

Commit `workgraph` and `driftdriver` changes separately with tranche-specific messages.

- [ ] **Step 3: Push both repos and verify clean `main`**

```bash
cd /Users/braydon/projects/experiments/workgraph
git pull --rebase
git push
git status -sb

cd /Users/braydon/projects/experiments/driftdriver
git pull --rebase
git push
git status -sb
```

- [ ] **Step 4: Record the next tranche**

Update the adoption plan or create a follow-up issue/task for Tranche 2:
- execution routing
- model endpoint
- service executor swap
- spawn-task unification
