# Workgraph Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adopt the highest-value upstream `workgraph` changes into our fork in a staged, testable way, starting with graph-level recovery, then the worktree substrate, then the narrow lifecycle cleanup sweep, then the narrow provider-model execution-routing slice extended with endpoint-aware native execution, rather than a blind repo-wide convergence move.

**Architecture:** Treat the current adopted `workgraph` line as canonical until upstream replacements are proven better under stronger contracts. Land changes by tranche: first graph-level recovery, then minimal worktree-isolation substrate, then the dependent coordinator cleanup sweep, then the narrow execution-routing slice, then the endpoint-aware native execution extension of that slice, then broader session/runtime adoption. Use upstream commit lift or cherry-pick where clean, preserve local fork behavior by default, and strengthen `driftdriver` compatibility checks after each landed tranche.

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
| `workgraph/src/commands/spawn/worktree.rs` | Create | Minimal per-agent worktree substrate for isolated spawn execution |
| `workgraph/src/commands/spawn/execution.rs` | Modify | Opt-in worktree-aware spawn path, metadata, and env propagation |
| `workgraph/src/config.rs` | Modify | Coordinator-level `worktree_isolation` flag |
| `workgraph/tests/integration_spawn_worktrees.rs` | Create | End-to-end contract for isolated worktree spawn behavior |
| `workgraph/src/commands/service/worktree.rs` | Create | Minimal coordinator-side marked-worktree cleanup sweep |
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

## Task 4: Implement the minimal worktree-isolation substrate

**Files:**
- Create: `workgraph/src/commands/spawn/worktree.rs`
- Modify: `workgraph/src/commands/spawn/mod.rs`
- Modify: `workgraph/src/commands/spawn/execution.rs`
- Modify: `workgraph/src/config.rs`
- Modify: `workgraph/src/commands/config_cmd.rs`
- Create: `workgraph/tests/integration_spawn_worktrees.rs`

- [ ] **Step 1: Add an opt-in coordinator worktree flag**

Add `coordinator.worktree_isolation = false` by default, parse it from config, and surface it in config display.

- [ ] **Step 2: Add the minimal spawn-time worktree helper**

Adopt only the narrow upstream helper needed to:
- create `.wg-worktrees/<agent-id>`
- branch `wg/<agent-id>/<task-id>`
- symlink `.workgraph`
- return worktree metadata

- [ ] **Step 3: Route spawned code-writing agents through the worktree when enabled**

Keep the default shared-root behavior intact. Only opt-in code-writing tasks should:
- execute from the worktree as `cwd`
- receive `WG_WORKTREE_PATH`, `WG_BRANCH`, `WG_PROJECT_ROOT`, and `WG_WORKTREE_ACTIVE`
- persist worktree metadata for later cleanup

- [ ] **Step 4: Add the end-to-end worktree spawn contract**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_spawn_worktrees -- --nocapture
```

## Task 5: Land the narrow worktree lifecycle cleanup tranche

**Files:**
- Create: `workgraph/src/commands/service/worktree.rs`
- Modify: `workgraph/src/commands/service/coordinator.rs`
- Modify: `workgraph/src/commands/service/mod.rs`
- Modify: `workgraph/tests/integration_spawn_worktrees.rs`

- [ ] **Step 1: Add a failing end-to-end cleanup contract**

Target behavior:
- wrapper-created `.wg-cleanup-pending` marker is reaped by `wg service tick`
- the isolated worktree directory is removed
- the generated `wg/<agent>/<task>` branch is deleted

- [ ] **Step 2: Implement the minimal coordinator-side sweep**

Adopt only the narrow lifecycle helper needed to:
- scan `.wg-worktrees/*/.wg-cleanup-pending`
- resolve the owning agent/task from the existing registry
- require `agent not live AND task terminal`
- force-remove the worktree and delete its branch

- [ ] **Step 3: Re-run the workgraph tranche tests**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_spawn_worktrees -- --nocapture
cargo test --test integration_recovery_commands -- --nocapture
```

## Task 6: Strengthen Speedrift’s Workgraph contract after landed tranches

**Files:**
- Modify: `driftdriver/.driftdriver/upstream-config.toml`

- [ ] **Step 1: Add stronger Workgraph compatibility checks**

Extend the existing Workgraph compatibility section with checks that exercise the landed recovery, worktree-spawn, and worktree-lifecycle behavior, not just wrapper contracts.

- [ ] **Step 2: Add endpoint-routing checks once the Tranche 4 extension lands**

Target behavior:
- spawn-time endpoint routing picks the intended provider/endpoint metadata
- native execution prefers spawn-resolved env over legacy `[native_executor]`
- native execution still respects configured endpoint url/key without spawn env

- [ ] **Step 3: Run the targeted `driftdriver` tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_upstream_tracker.py -q
```

## Task 7: Land, verify, and hand off the next tranche boundary

**Files:**
- Modify: adoption docs if needed for factual updates after implementation

- [ ] **Step 1: Run final focused verification**

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_recovery_commands -- --nocapture
cargo test --test integration_spawn_worktrees -- --nocapture
cargo test native_client_config_ -- --nocapture
cargo test --test integration_native_executor test_native_executor_config_from_toml -- --nocapture

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

- [ ] **Step 4: Record the remaining tranche boundary**

Update the adoption plan or create a follow-up issue/task for the remaining execution-routing residuals and the later session-runtime tranche.
