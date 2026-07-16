# Speedrift Maintenance Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Speedrift is the task and dependency authority; Pi Lunaroute subagents provide fresh implementation and review contexts. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the current Speedrift/PlanForge/Workgraph compatibility drift, prove the repaired contracts against the tested Workgraph base, then update Workgraph and rerun live Pi Lunaroute validation.

**Architecture:** The work is split into three independently testable plans. Driftdriver first establishes the graph-location and runtime interfaces. PlanForge then consumes those interfaces and emits canonical handler-first task contracts. Workgraph is updated only after both maintenance branches are green against commit `58380883`.

**Tech Stack:** Python 3.12, `unittest`, `pytest`, JSON Schema Draft 2020-12, Rust 1.96.0, Cargo, Bash smoke tests, Workgraph, driftdriver, PlanForge V2, Pi `lunaroute/glm-5.2-nvfp4`.

## Global Constraints

- Pi remains the dominant execution harness.
- Canonical execution values are handler-first, for example `pi:lunaroute/glm-5.2-nvfp4`.
- Semantic `route_id` and concrete `execution_spec` remain separate.
- Existing `.workgraph` repositories are not migrated.
- Existing `.wg` repositories remain usable.
- Dual initialized graph directories are a hard error.
- Every Workgraph invocation receives an explicit `--dir`.
- `speedriftd` is the sole operator-facing supervision authority.
- External Agency is disabled by default and must not claim learning without evaluation submission.
- The parent agent is the only writer; Pi Lunaroute subagents are read-only reviewers unless a Speedrift task gives one an isolated worktree.
- Every repository uses an isolated feature branch and explicit path staging.
- Workgraph commit `e0b0a047` and patched-Pi installer fix `9cda81cb` remain a separate rollback unit from the Speedrift maintenance changes.

---

## Linked Plans

1. `docs/superpowers/plans/2026-07-16-speedrift-driftdriver-maintenance.md`
2. `docs/superpowers/plans/2026-07-16-speedrift-planforge-maintenance.md`
3. `docs/superpowers/plans/2026-07-16-speedrift-workgraph-rollout.md`

## Dependency Graph

```text
D1 graph resolver
 ├─ D2 graph-dir CLI and runtime adoption
 │   └─ P3 PlanForge graph resolver consumption
 ├─ D3 Pi dispatch and structured errors
 └─ D4 installer bootstrap repair

D5 Agency boundary      P1 canonical routing schema/normalizer
D6 supervision docs     P2 task Validation/Deliverables contract
                         └─ P4 PlanForge docs

[D1-D6 green on Workgraph 58380883]
[P1-P4 green on Workgraph 58380883]
                 │
                 ▼
W1 upstream Workgraph branch + installer fix
W2 smoke-fixture repairs
W3 real Pi Lunaroute/Workgraph/driftdriver validation
```

## Execution Sequence

### Phase 1: Driftdriver runtime contract

- [ ] Execute every task in `2026-07-16-speedrift-driftdriver-maintenance.md` using test-first commits.
- [ ] Run the driftdriver focused suites named in that plan.
- [ ] Run the full driftdriver test suite.
- [ ] Request a fresh Pi Lunaroute requirements review.
- [ ] Request a separate Pi Lunaroute code-quality review.
- [ ] Commit and push the driftdriver branch without touching unrelated main-tree files.

### Phase 2: PlanForge contract

- [ ] Execute every task in `2026-07-16-speedrift-planforge-maintenance.md` after the driftdriver `graph-dir` JSON interface is fixed.
- [ ] Run the PlanForge focused test file after every task.
- [ ] Run schema validation and dry-run materialization against a temporary `.workgraph` repository.
- [ ] Request a fresh Pi Lunaroute requirements review.
- [ ] Request a separate Pi Lunaroute code-quality review.
- [ ] Commit and push the PlanForge branch without staging unrelated workspace-skill changes.

### Phase 3: Workgraph update and live validation

- [ ] Confirm Phases 1 and 2 pass against Workgraph `58380883`.
- [ ] Execute `2026-07-16-speedrift-workgraph-rollout.md` from a clean worktree based on `e0b0a047`.
- [ ] Reapply installer fix `9cda81cb` as its own commit.
- [ ] Repair only the two known stale smoke fixtures in their own commit.
- [ ] Build, install, and test Workgraph.
- [ ] Run direct Pi Lunaroute, Workgraph worker, and driftdriver dispatch smokes.
- [ ] Re-run the focused Speedrift suites against the updated Workgraph binary.
- [ ] Push the Workgraph branch.

### Phase 4: Final landing

- [ ] Confirm every intended file is committed in its owning repository.
- [ ] Confirm every branch is pushed and reports its upstream state.
- [ ] Record follow-up work with traceable identifiers.
- [ ] Return any armed repository to `speedriftd` observe mode and release the lease.
- [ ] Produce the separate capability-level Speedrift design requested by Braydon.

## Cross-Repository Completion Gate

The maintenance package is complete only when all of the following are true:

```text
PlanForge canonical route tests: PASS
PlanForge graph-dir/materialization tests: PASS
PlanForge Validation/Deliverables tests: PASS
Driftdriver graph resolver tests: PASS
Driftdriver Pi dispatch tests: PASS
Driftdriver installer bootstrap test: PASS
Driftdriver Agency default-off tests: PASS
Driftdriver speedriftd authority tests: PASS
Workgraph cargo tests: PASS
Known Pi smoke fixtures: PASS
Direct Pi Lunaroute marker: PASS
Real Workgraph Pi Lunaroute task: done
Real driftdriver Pi Lunaroute dispatch: done
All branches: committed and pushed
All supervised repos: observe mode unless Braydon explicitly requests otherwise
```
