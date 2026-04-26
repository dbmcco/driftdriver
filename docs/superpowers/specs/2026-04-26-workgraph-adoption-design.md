# Workgraph Adoption Design

## Why This Needs To Happen Now

`workgraph` is the execution spine for Speedrift and PAIA. We now have explicit upstream tracking in `driftdriver`, but the current state is still only observational: upstream `graphwork/workgraph` is being tracked, compatibility is being sampled, and the hub correctly reports an adopted-vs-upstream split, but the adopted line has not actually absorbed the highest-value upstream work.

That split is now large enough that "stay aware" is no longer sufficient. The highest-leverage upstream changes from Erik are concentrated in the coordinator, spawn, recovery, session, and worktree lifecycle surfaces that directly determine whether Speedrift can run reliably and autonomously.

At the same time, a blind merge would be wrong. Our adopted line still carries meaningful local behavior around coordinator locking, model fallback resolution, verify validation, spawn-loop protection, and coordinator/TUI streaming. Those deltas should not be overwritten just because upstream moved faster.

So the correct move is a staged adoption program: preserve current fork behavior by default, adopt upstream value tranche by tranche, and raise the Speedrift-side contracts as each tranche lands.

## Goals

- Absorb the best upstream `workgraph` changes that materially improve Speedrift and PAIA.
- Keep our adopted line operationally stable while upstream value is being pulled in.
- Increase the parts of `workgraph` that Speedrift can trust for autonomous operation.
- Make each tranche independently reviewable, testable, and reversible.

## Non-Goals

- Converging the fork to upstream in one move.
- Adopting TUI-heavy upstream changes as a priority.
- Treating docs, benchmarks, or screencast churn as first-class adoption work.
- Retiring local fork behavior before upstream replacements are proven under stronger contracts.

## Design Principle

Preserve our current fork behavior by default in early adoption phases. Upstream changes are introduced when they are clearly better, compatible with our architecture, and verified by contracts stronger than the current wrapper-level checks.

The fork should become thinner over time, but thinning is an outcome of verified adoption, not a starting assumption.

## Tranche Model

### Tranche 1: Graph-Level Recovery

This is the first adoption tranche because it composes with the current fork directly and improves operator recovery immediately.

Scope:
- graph-surgery primitive for inserting first-class follow-up work (`wg insert`)
- bulk subgraph reset for retry/recovery (`wg reset`)
- first-class replacement-task recovery (`wg rescue`)
- provenance coverage and end-to-end recovery contract tests

Why first:
- improves recovery without forcing the fork onto upstream worktree assumptions it does not yet implement
- lands real user/operator leverage quickly
- gives Speedrift a concrete new contract it can run against the adopted line

Primary upstream surfaces:
- `src/commands/insert.rs`
- `src/commands/reset.rs`
- `src/commands/rescue.rs`
- related CLI and command wiring

### Tranche 2: Worktree Isolation Substrate

This tranche introduces the minimum viable worktree execution substrate without yet
pulling in the full upstream cleanup sweep or liveness semantics.

Scope:
- coordinator-level `worktree_isolation` flag
- per-agent spawn-time worktree creation under `.wg-worktrees/<agent-id>/`
- `.workgraph` symlink into the spawned worktree
- worktree metadata and env propagation for later cleanup
- end-to-end spawn contract showing writes land in the isolated worktree

Why now:
- it creates the substrate the later cleanup tranche actually depends on
- it is narrow enough to land without dragging in the broader session/runtime fork delta
- it gives Speedrift a concrete second adoption contract beyond recovery

### Tranche 3: Worktree Lifecycle Hygiene

Scope:
- agent liveness invariant (`status AND process alive AND heartbeat fresh`)
- worktree lifecycle module
- atomic worktree cleanup sweep from the coordinator tick
- fallback operator-facing worktree garbage collection

Why not first:
- the current adopted line does not yet have the upstream worktree-lifecycle substrate
- forcing cleanup semantics in early would couple this tranche to a larger execution-model migration

### Tranche 4: Execution Routing

Scope:
- model endpoint support
- `wg service set-executor`
- executor selection and routing
- spawn-task unification and handler routing
- tag/model/provider routing behavior

Why second:
- high Speedrift leverage
- overlaps directly with our forked coordinator/spawn behavior
- should only move after the recovery surface is stronger and easier to debug

### Tranche 5: Session Runtime

Scope:
- session identity and repair
- session check / doctor behavior
- resume and handler recovery
- PTY/session runtime improvements that affect non-TUI execution behavior

Why third:
- highest behavioral coupling to current agent execution flows
- should only move once recovery and routing are more stable

### Tranche 6: Optional Residual Upstream Convergence

Scope:
- remaining nonessential churn after the three high-value tranches
- targeted adoption only if it still matters

This tranche is explicitly optional.

## Validation Model

Current `driftdriver` upstream compatibility checks are sufficient for tracking, not for adoption. Each tranche needs a stronger contract before and after landing.

### Tranche 1 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- `workgraph` recovery contract passes end to end:
  - `insert` rewires edges correctly
  - `rescue` creates first-class replacement work
  - `reset` resets closure state and strips attached meta tasks
  - all three record provenance correctly

### Tranche 2 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- `workgraph` worktree spawn contract passes end to end:
  - enabled code-writing tasks spawn inside `.wg-worktrees/<agent-id>/`
  - worktree metadata is persisted for the spawned agent
  - repo-root writes stay inside the isolated worktree

### Tranche 3 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- `workgraph` unit/integration tests for:
  - worktree cleanup safety
  - dead-agent recovery
- one spawn/executor routing smoke
- one service executor swap / coordinator path smoke

### Tranche 4 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- one session resume / reconnect smoke
- one session repair/doctor smoke

## Operational Rules

- Do not adopt an upstream tranche by raw branch merge.
- Prefer cherry-picking or manual lift of the relevant upstream commits and tests.
- Keep changes grouped by tranche, not by author-date.
- Preserve or restate local fork behavior when upstream code would otherwise overwrite it.
- If a tranche is only partially adoptable, land the coherent subset and record the remainder explicitly.

## Success Criteria

- Tranche 1 lands on our adopted `workgraph` line with tests and Speedrift contracts passing.
- `driftdriver` reflects the strengthened adoption checks for the landed tranche.
- The adopted line has first-class graph-level recovery commands instead of only manual cleanup.
- The adopted line has opt-in worktree isolation substrate that later lifecycle cleanup can build on.
- Worktree lifecycle adoption is explicitly recorded as the next dependent tranche, not blurred into recovery.

## Recommended Execution Order

1. Land Tranche 1 in `workgraph`.
2. Land Tranche 2 in `workgraph`.
3. Strengthen `driftdriver` compatibility gates to include both landed tranche contracts.
4. Re-run upstream tracking and confirm the adopted line remains intentionally diverged but materially improved.
5. Start the dependent Tranche 3 worktree-lifecycle design only after the substrate is proven stable.
