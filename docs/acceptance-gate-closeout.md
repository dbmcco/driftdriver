# Acceptance Gate — Arc Closeout Record

This document closes out the deterministic acceptance-criteria gate arc with
ceremony-verified evidence. Every deliverable below landed on main; this
closeout re-proves the arc through the full completion valve (object,
manifest, submit, FLIP + eval review, land, done).

## Deliverables and landed commits

| Deliverable | Commit |
|---|---|
| Deterministic evaluator + `check_task` (21 unit tests) | `6c3c1b6` |
| Acceptance CLI (check/status/reset) + read-only inspection | `78f9561` |
| Gate wired into `cmd_check` (completion-attempt guarded) | `24ba772` |
| Integration + e2e test coverage (blocking, degrade, ceiling, reset) | `5041a44` |
| Executor shim intercepts COMPLETE_TASK before `wg done` | `14b0a94` |
| Gate documentation (`docs/acceptance-gate.md`) | `bf130ad`, `b6ea8a0` |
| Config-driven degrade ceiling via `drift-policy.toml` | `8c2bf20` |
| Absolutize-after-runner-injection fix | `0e75c91` |

## Test evidence

- `tests/test_acceptance_gate.py` — evaluator pass/fail/degraded/no_criteria,
  ceiling enforcement, reset, contract extraction (21 tests).
- `tests/test_acceptance_gate_integration.py` — completion-path blocking,
  degrade flow, ceiling trip, reset, passthrough (8 tests).
- `tests/test_acceptance_gate_e2e.py` — acceptance CLI end to end,
  at-ceiling status, reset roundtrip (5 tests).
- `tests/test_executor_shim.py::TestCompleteTaskAcceptanceGate` — COMPLETE_TASK
  intercept: blocked prevents `wg done`; pass/degraded/no_criteria allow (4 tests).
- Full suite at closeout: **2595 passed, 0 failed** (29 skipped, 9 xfailed
  are pre-existing normal).

## Wiring points

1. `driftdriver/executor_shim.py` — dispatched-agent completions run the gate
   before `wg done`; blocked gates fail the directive with
   `error_code: acceptance_gate_blocked` and the full gate report.
2. `driftdriver/cli/check.py` — post-task checks report the gate in
   `plugins_json["acceptance_gate"]`; degrade recording is guarded to
   completion attempts.

## Degrade policy

Hard gate by default. Per-task degrade budget (default ceiling 3) recorded in
`.workgraph/service/acceptance-degrade.json`; ceiling configurable via
`drift-policy.toml` (`[acceptance] degrade_ceiling`). At ceiling the gate
reverts to hard mode. `driftdriver acceptance status/reset` inspect and clear.

## Deferred (real backlog, kept open)

- `repo-degrade-ledger` — repo-wide degraded/waived-gate ledger.
- `repo-degrade-ceiling` — repo-level ceiling with deadlock-safe escalation.
- `accept-gate-adversarial-review` — bounded adversarial review of gate
  design and wiring.
