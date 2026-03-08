# Drift Lane Boundary Compliance Audit

Audited: 2026-03-08
Branch: feature/directive-interface

## Summary

All 10 external drift lane repos contain `Workgraph` helper classes with direct `wg` subprocess calls.
Every lane has two mutation methods: `wg_log()` and `ensure_task()`. Both bypass driftdriver's
directive interface (`guarded_add_drift_task` -> `Directive` -> `ExecutorShim`).

Internal lanes (qadrift, secdrift, plandrift, factorydrift, northstardrift) already route through
`guarded_add_drift_task()` and are compliant.

Two repos listed in the task do not exist: `secdrift/` and `qadrift/` (these are internal lanes
inside driftdriver itself).

## Per-Lane Findings

### coredrift

- **Repo**: `/Users/braydon/projects/experiments/coredrift/`
- **Workgraph helper**: `wg_drift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — `subprocess.check_call(["wg", ..., "log", ...])` — called from `cli.py:173`
  - `ensure_task()` — `subprocess.check_call(["wg", ..., "add", ...])` — called from `cli.py:204,221,265`
  - `update_task_description()` — direct graph.jsonl file rewrite (not a wg CLI call)
  - `rewrite_graph_with_contracts()` — direct graph.jsonl file rewrite (not a wg CLI call)
- **Read-only**: `load_workgraph()` reads `graph.jsonl` directly (acceptable)
- **Called during driftdriver operation**: YES. Driftdriver passes `--create-followups` and `--write-log` flags to the coredrift subprocess. The lane's `_maybe_create_followups()` calls `wg.ensure_task()` directly, and its write-log path calls `wg.wg_log()` directly. Both bypass `guarded_add_drift_task()`.
- **Additional**: `_maybe_create_pit_stop()` also calls `wg.ensure_task()` (line 265).
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)
- **Variant**: Has `tasks` dict on Workgraph dataclass; uses in-memory dedup

### specdrift

- **Repo**: `/Users/braydon/projects/experiments/specdrift/`
- **Workgraph helper**: `specdrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — `subprocess.check_call(["wg", ..., "log", ...])` — called from `cli.py:69`
  - `ensure_task()` — `subprocess.check_call(["wg", ..., "add", ...])` — called from `cli.py:100`
  - `show_task()` — `subprocess.check_output(["wg", ..., "show", ...])` — READ-ONLY
- **Called during driftdriver operation**: YES. Same pattern as coredrift.
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)
- **Variant**: Uses `wg show` for idempotency check (subprocess per call)

### archdrift

- **Repo**: `/Users/braydon/projects/experiments/archdrift/`
- **Workgraph helper**: `archdrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:64`
  - `ensure_task()` — called from `cli.py:93,119` (architecture_not_updated + missing_redrift_arch_artifacts)
  - `show_task()` — READ-ONLY
- **Called during driftdriver operation**: YES
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)
- **Variant**: Same as specdrift (wg show for dedup)

### datadrift

- **Repo**: `/Users/braydon/projects/experiments/datadrift/`
- **Workgraph helper**: `datadrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:64`
  - `ensure_task()` — called from `cli.py:95`
  - `show_task()` — READ-ONLY
- **Called during driftdriver operation**: YES
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)

### depsdrift

- **Repo**: `/Users/braydon/projects/experiments/depsdrift/`
- **Workgraph helper**: `depsdrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:62`
  - `ensure_task()` — called from `cli.py:93`
  - `show_task()` — READ-ONLY
- **Called during driftdriver operation**: YES
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)

### uxdrift

- **Repo**: `/Users/braydon/projects/experiments/uxdrift/`
- **Workgraph helper**: `uxdrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:410` (via `_maybe_write_wg_log`)
  - `ensure_task()` — called from `cli.py:489`
- **Read-only**: `load_workgraph()` reads graph.jsonl; `choose_task_id()` reads tasks
- **Called during driftdriver operation**: YES
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)
- **Variant**: Has `tasks` dict; uses in-memory dedup. Note: driftdriver marks uxdrift as not supporting JSON output (`_plugin_supports_json` returns False).

### therapydrift

- **Repo**: `/Users/braydon/projects/experiments/therapydrift/`
- **Workgraph helper**: `therapydrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:193`
  - `ensure_task()` — called from `cli.py:226`
  - `show_task()` — READ-ONLY
- **Called during driftdriver operation**: YES (gated by `allow_auto_action` policy)
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)

### fixdrift

- **Repo**: `/Users/braydon/projects/experiments/fixdrift/`
- **Workgraph helper**: `fixdrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:62`
  - `ensure_task()` — called from `cli.py:92`
  - `show_task()` — READ-ONLY
- **Called during driftdriver operation**: YES
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)

### yagnidrift

- **Repo**: `/Users/braydon/projects/experiments/yagnidrift/`
- **Workgraph helper**: `yagnidrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:62`
  - `ensure_task()` — called from `cli.py:92`
  - `show_task()` — READ-ONLY
- **Called during driftdriver operation**: YES
- **Priority**: HIGH (ensure_task), MEDIUM (wg_log)

### redrift

- **Repo**: `/Users/braydon/projects/experiments/redrift/`
- **Workgraph helper**: `redrift/workgraph.py`
- **Mutations found**:
  - `wg_log()` — called from `cli.py:185,201,1027`
  - `ensure_task()` — called from `cli.py:625,653` (check followups) AND `cli.py:868,910` (execute phase scaffolding)
  - `show_task()` — READ-ONLY
  - `wg init` — `subprocess.check_call(["wg", "init", ...])` in `_bootstrap_v2_repo()` (cli.py:392)
  - `wg service start` — `subprocess.check_call(["wg", ..., "service", "start"])` in execute (cli.py:959)
- **Called during driftdriver operation**: YES. The `check` subcommand's `_maybe_create_followups` is invoked with `--create-followups`. The `execute` subcommand creates root + phase tasks directly.
- **Priority**: HIGH (ensure_task in check path), HIGH (ensure_task + wg init + wg service start in execute path)
- **Note**: redrift's `execute` command has the most complex boundary violations: it bootstraps entire workgraph repos, creates root tasks, creates phase tasks, and starts services — all bypassing the directive interface.

## Classification Summary

| Mutation Type | Count of Lanes | Risk | Bypass Type |
|---|---|---|---|
| `ensure_task()` (wg add) | 10/10 | HIGH | Bypasses authority budgets, dedup, quality modifiers, escalation log, directive audit trail |
| `wg_log()` | 10/10 | MEDIUM | Bypasses LOG_TO_TASK directive; no audit trail |
| `show_task()` | 8/10 | NONE | Read-only, acceptable |
| `wg init` | 1/10 (redrift) | MEDIUM | Bootstrap operation, not task creation |
| `wg service start` | 1/10 (redrift) | HIGH | Bypasses START_SERVICE directive |
| `graph.jsonl` direct rewrite | 1/10 (coredrift) | LOW | Contract injection; read-modify-write on data file |

## What Gets Bypassed

When lanes call `wg.ensure_task()` directly instead of going through `guarded_add_drift_task()`:

1. **Authority budget checks** — per-actor `max_active_tasks` and `max_creates_per_hour` limits are ignored
2. **Quality-adjusted budgets** — outcome feedback quality modifiers don't apply
3. **Global ceiling** — the safety net against runaway drift tasks across all lanes is bypassed
4. **Escalation recording** — capped findings don't get recorded to `escalations.jsonl`
5. **Budget ledger** — `budget-ledger.jsonl` doesn't get an entry, so hourly rate tracking is wrong
6. **Directive audit trail** — no Directive object is emitted; `pending.jsonl`/`completed.jsonl` miss the action
7. **Dedup via `wg show`** — lanes do their own dedup, but it's a subprocess call that could fail silently

## Remediation Plan

### Phase 1: Intercept at the driftdriver boundary (no lane repo changes)

The cleanest approach that requires NO changes to external lane repos:

**Strategy**: Driftdriver already passes `--create-followups` to lanes as a CLI flag. The key insight
is that driftdriver controls whether this flag is passed (via `_mode_flags()`). The fix is:

1. **Stop passing `--create-followups` to external lane subprocesses.** Driftdriver already
   determines which lanes should create followups via mode/policy. Instead of delegating the
   creation to the lane, driftdriver should:
   - Run the lane with `--json` only (no `--create-followups`)
   - Parse the lane's JSON output for findings
   - Create follow-up tasks itself via `guarded_add_drift_task()`

2. **This requires**: Standardizing how findings map to follow-up tasks. Each lane has a
   deterministic `_maybe_create_followups()` that maps finding kinds to task IDs/titles. This
   mapping needs to be replicated in driftdriver's lane runner.

3. **For `--write-log`**: Similarly, stop passing `--write-log` and have driftdriver emit
   `LOG_TO_TASK` directives instead.

**Complexity**: MEDIUM. The mapping from findings to follow-up tasks is currently embedded in each
lane's Python code. Moving it to driftdriver means maintaining the mapping centrally.

### Phase 2: Lane protocol extension (future)

The more principled approach for future work:

1. **Add a `--emit-requests` flag** to the lane CLI protocol. When set, lanes output task creation
   requests as structured JSON instead of executing them:
   ```json
   {"requests": [{"action": "create_task", "task_id": "...", "title": "...", "description": "...", "blocked_by": [...], "tags": [...]}]}
   ```

2. **Driftdriver's lane runner** parses these requests and routes each through `guarded_add_drift_task()`.

3. **Backward compatible**: Lanes without `--emit-requests` continue to work via Phase 1 approach.

**Complexity**: LOW per lane, but requires coordinated changes across 10 repos.

### Phase 3: `wg_log` migration (low priority)

Replace direct `wg_log()` calls with `LOG_TO_TASK` directives. Lower priority because:
- Log messages don't affect authority budgets
- They don't create graph mutations
- The audit trail gap is less severe

### Immediate Action Items

| Item | Priority | Approach |
|---|---|---|
| Stop passing `--create-followups` to external lanes when running from driftdriver | HIGH | Change `_mode_flags()` or `_plugin_cmd()` in driftdriver |
| Add finding-to-followup mapping in driftdriver's lane runner | HIGH | New module in driftdriver |
| Route all followup creation through `guarded_add_drift_task()` | HIGH | Already exists, just needs wiring |
| Stop passing `--write-log` to external lanes | MEDIUM | Same approach as create-followups |
| Route wg_log through LOG_TO_TASK directive | MEDIUM | New code path in driftdriver |
| Document redrift execute's direct wg mutations | LOW | Standalone mode, not called by driftdriver check |

### Redrift Execute — Special Case

The `redrift execute` command is invoked standalone (not through driftdriver's `cmd_check`). Its
direct `wg init`, `wg add`, and `wg service start` calls are standalone-mode operations that
bootstrap entirely new workgraph repos. These should eventually route through directives but are
lower priority because they don't run during normal driftdriver check operations.

## Internal Lanes (Compliant)

The following internal lanes already route through `guarded_add_drift_task()`:
- `qadrift` (driftdriver/qadrift.py)
- `secdrift` (driftdriver/secdrift.py)
- `plandrift` (driftdriver/plandrift.py)
- `factorydrift` (driftdriver/factorydrift.py)
- `northstardrift` (driftdriver/northstardrift.py)

These are the reference implementation for how external lanes should behave.
