# Factorydrift Model-Mediated Contract

## Purpose

Define the control contract for an autonomous Speedrift dark-factory loop across a growing multi-repo ecosystem.

The contract follows model-mediated architecture:
- Model decides.
- Code gathers evidence, executes tool calls, enforces policy guardrails, and verifies outcomes.

## North Star

Speedrift continuously operates, improves itself, and improves child repos with minimal human supervision while preserving safety, auditability, and quality.

## System Invariants

- Every repo remains Workgraph-first (`.workgraph/graph.jsonl` + drift lanes).
- Every autonomous action produces evidence, decision rationale, execution record, and verification result.
- No hidden heuristics in code that override model decisions (except explicit policy gates).
- All ecosystem-level decisions are traceable in the central register.

## Decision Ownership

| Decision Type | Owner | Notes |
| --- | --- | --- |
| Strategic intent / autonomy envelope | Human policy | Set in `drift-policy.toml` |
| Cross-repo priority and sequencing | Model | `factorydrift` planner |
| Repo diagnosis and fix strategy | Model | Via drift evidence + tools |
| Tool execution and retries | Code | Deterministic executor |
| Safety gates and stop conditions | Code + policy | Deterministic and explicit |

## Factory Cycle Contract

## Input Envelope

- `ecosystem_snapshot` from hub (`repos`, `overview`, dependency graph, status, updates).
- Per-repo drift artifacts (`coredrift/specdrift/datadrift/archdrift/depsdrift/uxdrift/therapydrift/fixdrift/yagnidrift/redrift`).
- Upstream signals (watch users, watched repos, watched reports, pull-down deltas).
- Service/supervisor telemetry.
- Policy envelope (autonomy level, budgets, protected branches, required checks).

## Model Tool Surface

- `get_repo_state(repo)`
- `get_repo_graph(repo)`
- `run_repo_drift_check(repo, lane_strategy)`
- `sync_repo(repo, mode)`
- `start_repo_service(repo)`
- `apply_repo_fix(repo, task_or_plan)`
- `open_draft_pr(repo, scope)`
- `update_workgraph(repo, task_changes, dependency_changes)`
- `record_factory_decision(payload)`

Code must execute these calls without post-hoc decision filtering.

## Output Envelope

- `cycle_id`
- `decision_trace` (model rationale summary + selected actions)
- `action_plan` (ordered list, dependency-aware)
- `tool_calls` (requested and executed)
- `verification_results`
- `outcomes` (`success`, `partial`, `failed`, `rolled_back`, `escalated`)
- `next_cycle_hints`

## Ecosystem Drift Modules

## `sourcedrift`

Role: external pull-down intelligence and integration pressure.

- Inputs: upstream commits/tags/releases, watched reports/users, local divergence.
- Model decisions: `ignore`, `track`, `integrate-now`, `queue-for-pr`.
- Deterministic gates: fetch timeout, max integrations per cycle, protected branch rules.
- Register writes: `upstream_delta`, `integration_decision`, `followup_tasks`.

## `syncdrift`

Role: repo cleanliness and alignment.

- Inputs: dirty tree, ahead/behind, lockfile mismatch, stale branches.
- Model decisions: `clean`, `rebase`, `merge`, `park`, `escalate`.
- Deterministic gates: disallow destructive git operations unless policy explicitly enables.
- Register writes: `sync_status`, `sync_actions`, `sync_verify`.

## `stalledrift`

Role: detect and unblock halted execution.

- Inputs: open/ready tasks, no in-progress, dependency deadlocks, missing refs, stale age.
- Model decisions: start task, split task, create missing dependency, reorder dependencies, close invalid tasks.
- Deterministic gates: dependency integrity checks before write.
- Register writes: `stall_state`, `stall_reasons`, `unblock_action`.

## `servicedrift`

Role: daemon and worker continuity.

- Inputs: service heartbeats, restart failures, cooldown windows, socket health.
- Model decisions: restart, defer, escalate to manual.
- Deterministic gates: restart budgets/cooldowns/hard-stop after repeated failures.
- Register writes: `service_state`, `restart_attempts`, `service_escalations`.

## `federatedrift`

Role: upstream contribution and reciprocity.

- Inputs: changed files, divergence from upstream, local-only fixes/features.
- Model decisions: candidate selection, scope split, PR draft packet.
- Deterministic gates: only draft PR unless policy allows stronger automation.
- Register writes: `upstream_candidates`, `draft_pr_requests`, `pr_outcomes`.

## `factorydrift`

Role: cross-repo planner and orchestrator.

- Inputs: all module summaries + policy budgets + dependency graph.
- Model decisions: cycle priority, action queue, concurrency, defer/escalate.
- Deterministic gates: max repos/actions/restarts per cycle, fail-fast rules.
- Register writes: `cycle_plan`, `action_queue`, `cycle_summary`.

## Guardrails (Deterministic)

- No direct writes to protected branches unless policy explicitly allows.
- Required verification checks must pass before promotion of changes.
- Max action budget per cycle and per repo.
- Auto-stop on repeated failed verifications.
- Full audit trail persisted per cycle.

## Conformance Requirements

- New behavior should be prompt/policy changes, not code heuristics.
- Tool-calling decisions remain model-controlled.
- Every autonomous action is replayable from central register records.
