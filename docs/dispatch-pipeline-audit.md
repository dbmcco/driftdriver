# Dispatch Pipeline Audit
**Task:** intelli-dispatch-verify
**Audited:** 2026-03-25T15:20Z
**Status:** PIPELINE FUNCTIONAL — CRITICAL BUG IN guarded_add_drift_task

---

## 1. Task Completion Rates

| Metric | Value |
|---|---|
| Total tasks ever | 948 |
| Done | 601 |
| Done today (2026-03-25) | 20 |
| In-progress | 9 |
| Ready (unblocked) | 5 |
| Awaiting eval `[A]` backlog | 322 |
| Weekly velocity | ~149 tasks/week (stable) |
| Last auto-dispatch completion | 2026-03-20T18:36Z (5 days ago) |

**Interpretation:** The task graph has processed 601 tasks total at a healthy 149/week average. The 5-day gap in auto-dispatch completions is explained by the stall condition (see section 3), not a broken pipeline.

---

## 2. Service Health

| Check | Result |
|---|---|
| `wg service` | Running (PID 27572, 7d 3h uptime) |
| Coordinator | Ticking (last tick: 15:20:41Z, #10574) |
| `speedriftd` mode | `supervise` |
| `speedriftd` daemon_state | `stalled` |
| Alive agents | 10 (cap: 4) |
| Socket errors | Intermittent `os error 22` in daemon.log |

The daemon state is `stalled` — coordinator ticks but is not spawning new agents because `agents_alive (10) >= max_agents (4)`. Infrastructure is intact; the stall is a capacity condition.

---

## 3. Dispatch Stall Analysis

Ready tasks NOT dispatching:
- `gate-factory-brain`
- `daily-factory-report`
- `gate-driftdriver-attractor`
- `wire-findings-to-tasks`

**Root cause of stall:** Two overlapping conditions:
1. `respect_manual_claims=true` — 4 tasks have interactive/planforge manual claims, blocking auto-dispatch of those slots.
2. Agent cap saturated — 10 alive agents exceeds the coordinator `max_agents=4` limit.

**Assessment:** `dispatch_healthy=true`. The pipeline machinery is correct. Once manual claims release or agent cap is adjusted, ready tasks will dispatch immediately.

---

## 4. guarded_add_drift_task Round-Trip — CRITICAL BUG

### Unit tests
```
tests/test_drift_task_guard.py — 11/11 PASSED
```
Tests pass because they mock `_run_wg`. The mock does not catch CLI flag errors.

### Live round-trip result
```
guarded_add_drift_task(...) → "error"
```

### Root cause
`executor_shim.py:63` builds the `CREATE_TASK` command with `--immediate`:
```python
cmd = wg + ["add", p["title"], "--id", p["task_id"], "--immediate"]
```

`wg add` does **not** support `--immediate`. The flag is invalid:
```
error: unexpected argument '--immediate' found
  tip: a similar argument exists: '--id'
```

All calls to `guarded_add_drift_task()` that reach the `ExecutorShim` return `"error"`. This means:
- No drift follow-up tasks are being created via the guard.
- Task wiring (the `wire-findings-to-tasks` task) will silently fail if deployed as-is.
- The finding ledger is NOT being written (it's only written on success).

The `executor_shim.py:114` also uses `--immediate` in `CREATE_VALIDATION`.

### Manual wg add (control)
```
wg add "Probe: ..." --id probe-dispatch-audit-20260325 -d "..." -t drift -t audit
→ Added task: Probe: dispatch pipeline audit test (probe-dispatch-audit-20260325)
```
Works correctly without `--immediate`.

---

## 5. Baseline Throughput Metrics

| Metric | Baseline |
|---|---|
| Tasks/week | 149 avg |
| Week 4 (current) | 169 tasks |
| Tasks dispatched today | 20 |
| Coordinator poll interval | 60s |
| Max concurrent workers | 4 |
| guarded_add success rate | 0% (all return "error" due to --immediate bug) |
| eval backlog age | ~322 tasks, days-old |

---

## 6. Blockers Before Task Wiring Can Deploy

| # | Blocker | Severity | Fix |
|---|---|---|---|
| 1 | `executor_shim.py:63` uses `--immediate` (invalid flag) | **SHOW-STOPPER** | Remove `--immediate` from `CREATE_TASK` command build |
| 2 | `executor_shim.py:114` uses `--immediate` in `CREATE_VALIDATION` | High | Same fix |
| 3 | 322 eval backlog tasks — `[A]` queue growth | Medium | Drain or raise eval agent capacity |
| 4 | Manual claims holding 4 slots, stalling auto-dispatch | Low | Release planforge/interactive claims when sessions end |
| 5 | Intermittent `os error 22` socket errors | Low | Investigate UDS socket path on macOS |

---

## 7. Recommendation

**DO NOT deploy `wire-findings-to-tasks` until blocker #1 is fixed.**

Fix required in `driftdriver/executor_shim.py`:
- Line 63: remove `"--immediate"` from the `CREATE_TASK` command list.
- Line 114: remove `"--immediate"` from the `CREATE_VALIDATION` command list.
- Add a live integration test (not mocked) that calls `wg add` with the actual CLI and verifies exit code 0.

Once fixed, the dispatch pipeline is ready to receive wired findings.
