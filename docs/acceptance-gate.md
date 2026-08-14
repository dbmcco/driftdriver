# Acceptance Completion Gate

The deterministic acceptance-criteria gate sits on the task completion path.
When a post-task check runs against an in-progress task (a completion
attempt), the gate evaluates the task's `verify` commands from its
`wg-contract` before the completion is trusted.

Spec: `docs/superpowers/specs/2026-08-06-add-a-deterministic-acceptance-criteria-gate-to-the-workgraph-ta-spec.md`

## How it works

1. `check_task` (in `driftdriver/acceptance_gate.py`) reads the task's
   description from `graph.jsonl` and extracts `verify = [...]` commands
   from the `wg-contract` fence.
2. Each verify command runs in the repo root. All passing → gate passes.
   Any failing → the gate degrades or blocks (below).
3. Tasks with no verify commands pass with status `no_criteria` — the gate
   only does deterministic checks; semantic criteria remain the critic's job.

The gate fires at two points:

1. **Dispatched-agent completions** — `executor_shim.py` runs the gate
   before executing `wg done`. A blocked gate keeps the task's current
   status and fails the directive with `error_code: acceptance_gate_blocked`
   plus the full gate report.
2. **Post-task checks** — `cmd_check` reports the gate in
   `plugins_json["acceptance_gate"]`. Degrade recording is guarded to
   completion attempts (task status `in-progress`); pre-task and manual
   checks are read-only.

## Degrade-to-advisory escape hatch

A failing gate first *degrades*: the task completes with a warning, and a
per-repo counter (`.workgraph/service/acceptance-degrade.json`) increments
for that task. Each task gets a degrade budget (ceiling, default 3). Once a
task hits the ceiling, the gate reverts to hard mode for that task and
blocks completion until the failures are fixed or the counter is reset.

The ceiling is per-task within a per-repo state file, so a broken repo
cannot rack up waived gates across unrelated tasks and lose signal.

## Configuring the ceiling

The degrade ceiling is configurable per repo in `drift-policy.toml`:

```toml
[acceptance]
degrade_ceiling = 5   # default 3
```

Invalid values (non-integer or < 1), a missing section, or an unreadable
policy file all fall back safely to the default of 3 — a zero ceiling would
silently make the gate un-degradable, so it is treated as invalid. The
`wg_dir` resolution matches the degrade state file (`.workgraph` preferred,
`.wg` accepted).

## CLI

```
driftdriver acceptance check <task_id> [--dir REPO] [--json]  # read-only gate run; exit 1 if blocked
driftdriver acceptance status [--dir REPO] [--json]           # per-task counts, ceiling, at-ceiling list
driftdriver acceptance reset [task_id] [--dir REPO]           # reset one task (or all); prints count cleared
```

`acceptance check` is read-only by design — it never consumes degrade
budget. Only the wired completion path records a degrade.

## Tests

- `tests/test_acceptance_gate.py` — evaluator pass/fail/degraded/no_criteria,
  ceiling enforcement, reset, status, contract extraction (unit).
- `tests/test_acceptance_gate_integration.py` — completion-path blocking,
  degrade flow, ceiling trip, reset, passthrough via `check_task` on a real
  temp workgraph.
- `tests/test_executor_shim.py::TestCompleteTaskAcceptanceGate` — the
  COMPLETE_TASK intercept: blocked prevents `wg done`, pass/degraded/
  no_criteria allow it.
- `tests/test_acceptance_gate_e2e.py` — the `acceptance` CLI subcommands
  end to end against a real repo directory.

## Pattern lineage

Matches `signal_gate.py`: hard gate by default, degrade-to-advisory hatch,
escalate when ignored. Future work (out of scope this phase): cross-repo
degrade aggregation.
