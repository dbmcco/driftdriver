# Drift Policy Factory Extensions (Proposed)

## Goal

Extend `.workgraph/drift-policy.toml` so autonomous factory operation is policy-driven and model-mediated, without hardcoded decision logic.

This proposal is additive. Existing repos can continue using current fields while adopting these sections incrementally.

## Proposed New Sections

## `[factory]`

Top-level ecosystem control loop settings.

- `enabled`: turn factory loop on/off.
- `cycle_seconds`: main planner cadence.
- `plan_only`: observe/plan without execution.
- `max_repos_per_cycle`: bound blast radius.
- `max_actions_per_cycle`: action budget.
- `write_decision_ledger`: persist model decisions and outcomes.
- `hard_stop_on_failed_verification`: stop cycle when verification fails.

## `[model]`

Model runtime controls for planner/diagnosis calls.

- `planner_profile`: model/profile used for cross-repo planning.
- `worker_profile`: model/profile used for repo-level decisions.
- `temperature`: reasoning variability.
- `adversarial_prompts`: enable challenge prompts before final decision.
- `max_tool_rounds`: bounded multi-turn tool reasoning.

## `[sourcedrift]`

External pull-down and ecosystem update integration policy.

- `enabled`
- `interval_seconds`
- `max_deltas_per_cycle`
- `auto_create_followups`
- `allow_auto_integrate`

## `[syncdrift]`

Repo alignment policy.

- `enabled`
- `allow_rebase`
- `allow_merge`
- `allow_destructive_sync` (default false)
- `require_clean_before_pr`

## `[stalledrift]`

Stall detection/unblocking policy.

- `enabled`
- `open_without_progress_minutes`
- `max_auto_unblock_actions`
- `auto_split_large_tasks`

## `[servicedrift]`

Service supervision policy.

- `enabled`
- `restart_budget_per_cycle`
- `restart_cooldown_seconds`
- `escalate_after_consecutive_failures`

## `[federatedrift]`

Upstream contribution policy.

- `enabled`
- `open_draft_prs`
- `auto_update_existing_drafts`
- `allow_auto_merge`
- `required_checks` (array of gate names)

## `[autonomy.default]` and `[[autonomy.repo]]`

Repo-level autonomy envelope.

- `level`: `observe | safe-fix | safe-pr | trusted-merge`
- `can_push`
- `can_open_pr`
- `can_merge`
- `max_actions_per_cycle`

Repo overrides in `[[autonomy.repo]]` keyed by `name`.

## Example Policy

```toml
schema = 2
mode = "redirect"
order = ["coredrift", "specdrift", "datadrift", "archdrift", "depsdrift", "uxdrift", "therapydrift", "fixdrift", "yagnidrift", "redrift"]

[factory]
enabled = true
cycle_seconds = 90
plan_only = false
max_repos_per_cycle = 5
max_actions_per_cycle = 12
write_decision_ledger = true
hard_stop_on_failed_verification = true

[model]
planner_profile = "gpt-5-high"
worker_profile = "gpt-5-medium"
temperature = 0.2
adversarial_prompts = true
max_tool_rounds = 6

[sourcedrift]
enabled = true
interval_seconds = 1800
max_deltas_per_cycle = 20
auto_create_followups = true
allow_auto_integrate = false

[syncdrift]
enabled = true
allow_rebase = true
allow_merge = true
allow_destructive_sync = false
require_clean_before_pr = true

[stalledrift]
enabled = true
open_without_progress_minutes = 120
max_auto_unblock_actions = 3
auto_split_large_tasks = true

[servicedrift]
enabled = true
restart_budget_per_cycle = 4
restart_cooldown_seconds = 180
escalate_after_consecutive_failures = 3

[federatedrift]
enabled = true
open_draft_prs = true
auto_update_existing_drafts = true
allow_auto_merge = false
required_checks = ["drifts", "tests", "lint"]

[autonomy.default]
level = "safe-fix"
can_push = false
can_open_pr = false
can_merge = false
max_actions_per_cycle = 2

[[autonomy.repo]]
name = "driftdriver"
level = "safe-pr"
can_push = true
can_open_pr = true
can_merge = false
max_actions_per_cycle = 3

[[autonomy.repo]]
name = "speedrift-ecosystem"
level = "observe"
can_push = false
can_open_pr = false
can_merge = false
max_actions_per_cycle = 1
```

## Compatibility Notes

- Current parser can ignore unknown sections, enabling staged adoption.
- Implement parser support in phases; default to conservative behavior when fields are absent.
- Keep policy as single source of truth for autonomy and safety boundaries.
