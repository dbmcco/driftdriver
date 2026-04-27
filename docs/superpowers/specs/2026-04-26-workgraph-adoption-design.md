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

This tranche is now landed in the adopted line as the narrow coordinator-side
cleanup sweep that the spawn substrate was designed to support.

Scope:
- service-side worktree lifecycle module
- atomic worktree cleanup sweep from the coordinator tick
- branch/worktree reaping gated by `cleanup_pending marker AND agent not live AND task terminal`

Deferred from the broader upstream tranche:
- richer heartbeat-freshness liveness invariant
- operator-facing worktree garbage collection
- orphaned-worktree scavenging beyond marked happy-path cleanup

Why this shape:
- it keeps the first lifecycle adoption narrow and testable
- it composes directly with the existing wrapper marker and dead-agent triage
- it avoids dragging in the broader upstream service/runtime behavior before execution-routing work

### Tranche 4: Execution Routing

This tranche is now partially landed as the narrow provider-model routing slice,
extended with endpoint-aware native execution and config-layer endpoint command
surfaces.

Landed scope:
- `provider:model` parsing for known upstream prefixes
- effective executor inference from model prefix
- daemon start / tick / status / reconfigure paths using effective executor
- coordinator-adjacent command paths using effective executor where raw executor assumptions were still embedded
- endpoint lookup helpers for configured `llm_endpoints`
- spawn-time `WG_LLM_PROVIDER` / `WG_ENDPOINT(_NAME)` / `WG_ENDPOINT_URL` / `WG_API_KEY`
  propagation for native execution
- native executor precedence that favors spawn-resolved endpoint env over legacy
  `[native_executor]` fallback config
- `wg config --endpoint` writing the default configured endpoint plus `local:` model
  normalization for bare model names
- `wg config --set-endpoint` binding named endpoints to dispatch roles
- `wg config --models` reporting resolved endpoint bindings in text and JSON output

Deferred from the broader upstream tranche:
- top-level init/setup endpoint surfaces beyond `wg config`
- `wg service set-executor`
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
- `workgraph` worktree lifecycle contract passes end to end:
  - wrapper writes `.wg-cleanup-pending` inside the isolated worktree
  - `wg service tick` reaps the marked worktree after agent exit
  - the associated worktree branch is deleted
- recovery and worktree-spawn contracts remain green

### Tranche 4 Contract

- existing Speedrift wrapper/managed-surface checks stay green
- `provider:model` parsing resolves known upstream prefixes correctly
- daemon reconfigure from disk infers the correct executor from `coordinator.model`
- daemon reconfigure with a model-only override infers the correct executor when the executor is not explicitly overridden
- service/runtime surfaces report the effective executor rather than the raw default field
- spawn-time endpoint routing resolves the correct provider and endpoint metadata
- native executor prefers spawn-resolved endpoint env over legacy `[native_executor]` config
- native executor still honors configured endpoint url/key when spawn env is absent
- `wg config --endpoint` writes a default named endpoint and normalizes bare models to
  `local:<model>`
- `wg config --set-endpoint` persists role-level endpoint bindings
- `wg config --models` exposes resolved endpoint bindings for operators and tooling

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
- The adopted line has coordinator-driven worktree lifecycle cleanup for the marked happy path.
- The adopted line has narrow provider-model execution routing across the service/runtime paths.
- The adopted line has endpoint-aware native execution routing wired through configured
  `llm_endpoints` and spawn-time env propagation.
- The adopted line has config-layer endpoint command surfaces that Speedrift can rely
  on when setting repo runtime intent.
- Broader execution-routing work is explicitly reduced to the remaining residuals above.

## Recommended Execution Order

1. Land Tranche 1 in `workgraph`.
2. Land Tranche 2 in `workgraph`.
3. Land the narrow Tranche 3 coordinator cleanup sweep in `workgraph`.
4. Strengthen `driftdriver` compatibility gates to include all landed tranche contracts.
5. Re-run upstream tracking and confirm the adopted line remains intentionally diverged but materially improved.
6. Land the narrow Tranche 4 provider-model execution-routing slice.
7. Extend that slice with endpoint-aware native execution routing.
8. Extend that slice with config-layer endpoint command surfaces.
9. Carry the remaining execution-routing and session-runtime work only after stronger service-level contracts exist.
