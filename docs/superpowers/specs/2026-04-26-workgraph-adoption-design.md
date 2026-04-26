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

### Tranche 1: Recovery + Worktree Hygiene

This is the first adoption tranche because it has the best risk/reward profile.

Scope:
- agent liveness invariant (`status AND process alive AND heartbeat fresh`)
- worktree lifecycle module
- atomic worktree cleanup sweep from the coordinator tick
- fallback operator-facing worktree garbage collection
- recovery-oriented commands that reduce manual cleanup work (`wg rescue`, `wg reset`) if they can be landed cleanly without destabilizing the coordinator

Why first:
- directly reduces long-running autonomy failure modes
- narrows leaked worktree / zombie agent / stranded task states
- improves operator recovery without touching the most fragile session-routing code first

Primary upstream surfaces:
- `src/commands/service/worktree.rs`
- `src/service/registry.rs`
- coordinator integration points in `src/commands/service/coordinator.rs`
- recovery commands around `src/commands/reset.rs`, `src/commands/rescue.rs`, and related CLI wiring

### Tranche 2: Execution Routing

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

### Tranche 3: Session Runtime

Scope:
- session identity and repair
- session check / doctor behavior
- resume and handler recovery
- PTY/session runtime improvements that affect non-TUI execution behavior

Why third:
- highest behavioral coupling to current agent execution flows
- should only move once recovery and routing are more stable

### Tranche 4: Optional Residual Upstream Convergence

Scope:
- remaining nonessential churn after the three high-value tranches
- targeted adoption only if it still matters

This tranche is explicitly optional.

## Validation Model

Current `driftdriver` upstream compatibility checks are sufficient for tracking, not for adoption. Each tranche needs a stronger contract before and after landing.

### Tranche 1 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- `workgraph` unit/integration tests for:
  - worktree cleanup safety
  - dead-agent recovery
  - operation logging for recovery commands
- one coordinator/service smoke from the local `workgraph` test suite

### Tranche 2 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- one spawn/executor routing smoke
- one service executor swap / coordinator path smoke

### Tranche 3 Contract

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
- The adopted line is more reliable in long-running cleanup/recovery scenarios.
- The next tranche boundary is explicit and queued, not hand-waved.

## Recommended Execution Order

1. Land Tranche 1 in `workgraph`.
2. Strengthen `driftdriver` compatibility gates to include the new tranche contract.
3. Re-run upstream tracking and confirm the adopted line remains intentionally diverged but materially improved.
4. Start a focused Tranche 2 review and lift only the routing changes that survive the stronger contract.
