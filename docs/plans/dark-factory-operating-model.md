# Dark Factory Operating Model (Speedrift Ecosystem)

## Objective

Run a continuous, policy-bounded, model-mediated loop that keeps all managed repos healthy, aligned, and shipping while improving the system itself.

## End-to-End Cycle

1. Observe
- Collect ecosystem snapshot, drift artifacts, upstream deltas, service telemetry, and dependency graph state.

2. Decide
- `factorydrift` model chooses cross-repo priority and action sequence.
- Repo-level models choose diagnosis and fix strategy.

3. Execute
- Deterministic runner executes model tool calls under policy guardrails.

4. Verify
- Re-run required checks (`drifts`, tests, lint, contract rules, service health).

5. Record
- Persist decision ledger and outcomes in central register.

6. Learn
- Generate follow-up tasks and policy/prompt improvements from repeated failures and manual interventions.

## Cadence

- Fast loop (60-120s): `servicedrift`, `stalledrift`, planner refresh.
- Medium loop (15-30m): `syncdrift`, drift-spectrum repo checks.
- Slow loop (2-6h): `sourcedrift`, `federatedrift`, upstream PR pipeline.

## Ecosystem Scaling Strategy

- Dependency-aware scheduling: prioritize unblocker repos before blocked dependents.
- Concurrency caps per cycle to avoid noisy parallel churn.
- Per-repo autonomy tiers to safely absorb growth.
- Standardized register schema so adding repos is configuration, not architecture work.

## Metrics

- `repos_active`, `repos_stalled`, `repos_idle`, `repos_untracked`
- `mean_time_to_unblock`
- `service_uptime_pct`
- `verification_pass_rate`
- `autonomous_action_success_rate`
- `draft_pr_to_merge_rate`
- `manual_intervention_rate`

## Failure Handling

- On verify failure: rollback or park change, create explicit follow-up task, reduce autonomy if repeated.
- On repeated service restart failure: escalate and mark repo as constrained.
- On model indecision/tool thrash: hard stop for that repo in-cycle and require next-cycle retry.

## Phased Rollout

## Phase 0: Observe + Ledger

- Enable decision ledger writes.
- Keep execution mostly `plan_only`.
- Validate signal quality and explainability.

## Phase 1: Safe Repair

- Enable `safe-fix` on a small allowlist.
- Restrict to deterministic low-risk actions.

## Phase 2: Safe PR Flow

- Enable `federatedrift` draft PR automation.
- Require green verification gates before PR creation.

## Phase 3: Trusted Paths

- Enable selective `trusted-merge` on mature repos with sustained pass rates.
- Keep strict rollback and audit boundaries.

## Phase 4: Self-Improving Factory

- Auto-create improvement tasks for recurring failure patterns.
- Promote recurring successful interventions into reusable playbooks/policies.

## Roles

- Human: sets policy, autonomy envelopes, strategic goals.
- Model: plans and decides actions from evidence.
- Code: executes, verifies, enforces policy, records evidence.

## Practical First Implementation Slice

- Start with `sourcedrift + stalledrift + servicedrift` in central loop.
- Keep current repo drift spectrum unchanged.
- Add `factorydrift` planner output and register schema before broad automation.
