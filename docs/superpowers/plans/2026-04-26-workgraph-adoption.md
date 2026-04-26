# Workgraph Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt the highest-value upstream `workgraph` changes into our fork in a staged, testable way, starting with graph-level recovery rather than a blind repo-wide convergence move.

**Architecture:** Treat the current adopted `workgraph` line as canonical until upstream replacements are proven better under stronger contracts. Land changes by tranche: first graph-level recovery, then dependent worktree hygiene once the substrate exists locally, then execution routing, then session runtime. Use upstream commit lift or cherry-pick where clean, preserve local fork behavior by default, and strengthen `driftdriver` compatibility checks after each landed tranche.

**Tech Stack:** Rust (`workgraph` CLI/service), Python (`driftdriver` upstream tracker), pytest for Speedrift contracts, cargo test for `workgraph`, git fork/upstream remotes.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `driftdriver/docs/superpowers/specs/2026-04-26-workgraph-adoption-design.md` | Create | Adoption design and tranche boundaries |
| `driftdriver/docs/superpowers/plans/2026-04-26-workgraph-adoption.md` | Create | Execution plan for staged adoption |
| `workgraph/src/commands/mod.rs` | Modify | Export newly adopted command modules |
| `workgraph/src/cli.rs` | Modify | Wire recovery command surfaces |
| `workgraph/src/main.rs` | Modify | Route new command handlers |
| `workgraph/src/commands/insert.rs` | Create | Graph-surgery primitive for recovery and follow-up work |
| `workgraph/src/commands/reset.rs` | Create | Bulk reset recovery command from upstream, if Tranche 1 subset includes it |
| `workgraph/src/commands/rescue.rs` | Create | First-class rescue command from upstream, if Tranche 1 subset includes it |
| `workgraph/tests/integration_recovery_commands.rs` | Create | End-to-end recovery contract for insert/reset/rescue + provenance |
| `driftdriver/.driftdriver/upstream-config.toml` | Modify | Strengthen Workgraph compatibility checks after Tranche 1 lands |

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

## Task 2: Raise the Workgraph-side tests for Tranche 1

**Files:**
- Create: `workgraph/tests/integration_recovery_commands.rs`

- [ ] **Step 1: Add end-to-end recovery contract tests**

Target behaviors:
- `insert` rewires a target slot and records an insert operation
- `rescue` creates a first-class replacement task and records a rescue operation
- `reset` clears closure state, strips attached meta tasks, and records a reset operation

- [ ] **Step 2: Run the targeted Workgraph tests and confirm they fail for the missing behavior**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_recovery_commands -- --nocapture
```

Expected: failures or missing command/behavior coverage for the recovery surface before implementation.

## Task 3: Implement Tranche 1 graph-level recovery

**Files:**
- Modify: `workgraph/src/commands/mod.rs`
- Modify: `workgraph/src/cli.rs`
- Modify: `workgraph/src/main.rs`
- Create: `workgraph/src/commands/insert.rs`
- Create: `workgraph/src/commands/reset.rs`
- Create: `workgraph/src/commands/rescue.rs`

- [ ] **Step 1: Lift `wg insert` as the graph-surgery primitive**

Preserve local graph semantics while adopting the upstream insertion behaviors needed for recovery and follow-up work.

- [ ] **Step 2: Lift `wg reset` with local-state adaptation**

Adapt upstream reset semantics to the current fork's task model:
- clear closure status back to `Open`
- clear stale assignment/failure/wait state
- preserve history in task logs
- strip attached system tasks when requested

- [ ] **Step 3: Lift `wg rescue` on top of `insert`**

Bring in first-class rescue work while keeping local provenance and log conventions.

- [ ] **Step 4: Run focused `cargo test` slices until Tranche 1 passes**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_recovery_commands -- --nocapture
```

## Task 4: Strengthen Speedrift’s Workgraph contract after Tranche 1 lands

**Files:**
- Modify: `driftdriver/.driftdriver/upstream-config.toml`

- [ ] **Step 1: Add a stronger Workgraph compatibility check**

Extend the existing Workgraph compatibility section with a check that exercises the landed Tranche 1 recovery behavior, not just wrapper contracts.

- [ ] **Step 2: Run the targeted `driftdriver` tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_upstream_tracker.py -q
```

## Task 5: Land, verify, and hand off the next tranche boundary

**Files:**
- Modify: adoption docs if needed for factual updates after implementation

- [ ] **Step 1: Run final focused verification**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_recovery_commands -- --nocapture

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

Update the adoption plan or create a follow-up issue/task for the dependent worktree-lifecycle tranche and the later execution-routing tranche.
