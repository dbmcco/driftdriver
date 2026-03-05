# plandrift: Workgraph Plan Integrity (Model-Mediated)

## Objective
Add a deterministic + model-mediated control pass that reviews each repo workgraph for:
- intervening integration/e2e test gates
- explicit failure loopbacks for test tasks
- continuation edges to keep work moving across context/session boundaries
- dependency integrity gaps that block execution

## Why
Without this pass, plans can look complete but still miss:
- integration/e2e tasks on the critical path
- recovery routes when checks fail
- handoff/continuation tasks (`double-shot-latte`, `claude-session-driver`, optional `tmux`) to prevent stalls

## Module Contract
`plandrift` returns:
- `summary` counts/severity and risk state
- `top_findings` with deterministic evidence
- `recommended_reviews` with model prompt seeds
- `model_contract` that makes the model responsible for:
  - dependency updates
  - intervening test tasks
  - failure loopback design
  - continuation strategy/runtime choice

## Policy Surface
```toml
[plandrift]
enabled = true
interval_seconds = 14400
max_findings_per_repo = 40
emit_review_tasks = true
max_review_tasks_per_repo = 3
require_integration_tests = true
require_e2e_tests = true
require_failure_loopbacks = true
require_continuation_edges = true
continuation_runtime = "double-shot-latte"
orchestration_runtime = "claude-session-driver"
allow_tmux_fallback = true
hard_stop_on_critical = false
```

## Factory Integration
Factory planner adds `review_workgraph_plan` for repos with active work/dependency pressure.
Factory executor runs `plandrift`, then emits bounded repo-local tasks (`plandrift-*`) to the workgraph.

This keeps correction local and traceable, so local agents can pick up remediation without unsafe central mutation.
