# Speedrift Ecosystem Improvement Design

## Date: 2026-03-06

## Problem

The speedrift ecosystem has grown to 22K lines across 39 modules with 10+ drift lanes.
Several structural debts have accumulated:

1. Test infrastructure broken (pytest not installed for python 3.14)
2. 7 stalled in-progress tasks from factory auto-generation
3. Atlas dogfood review blocking 2 concrete fixes
4. Large modules (ecosystem_hub 5.8K, cli 2.9K) resist reasoning
5. No outcome feedback loop (recommendations have no result tracking)
6. SecDrift/QADrift findings advisory-only with no enforcement path
7. Lane plugin contract not automatically tested

## Design

### Phase 0: Foundation

**0a. Fix test infrastructure**
- Install pytest for current python (3.14 via homebrew)
- Run full suite, establish passing baseline
- Measure coverage gaps across all 39 modules

**0b. Clean stalled tasks**
- Triage 7 in-progress tasks (factory-stalledrift-*, plandrift-*, northstardrift-*)
- Abandon auto-generated tasks that have no human value
- Unblock real work items

### Phase 1: Atlas Dogfood Resolution

**1a. Complete Atlas dogfood review** (speedrift-review-atlas-dogfood-runtime)
- Finalize the review findings already captured
- Confirm the two follow-up tasks are correctly scoped
- Mark review done

**1b. Fix task-completing handler** (speedrift-fix-task-completing-zero-change)
- Fix grep -c under set -euo pipefail producing malformed CHANGED_FILES
- Add test coverage for zero-change and changed-file paths

**1c. Harden autostart guardrails** (speedrift-review-autostart-guardrails)
- Default autostart to observe/pause for active coding repos
- Require explicit opt-in for mutating background work
- Test the generated autostart contract

### Phase 2: Module Decomposition

**2a. Split ecosystem_hub.py (5829 lines)**
Target structure:
```
driftdriver/ecosystem_hub/
  __init__.py        -- re-exports for backward compat
  server.py          -- ThreadingHTTPServer, request routing
  dashboard.py       -- HTML generation, template rendering
  api.py             -- JSON API handlers (/api/status, /api/repos, etc.)
  websocket.py       -- WebSocket upgrade + live stream
  discovery.py       -- repo scanning, peer detection
  models.py          -- dataclasses (RepoState, HubConfig, etc.)
```

**2b. Split cli.py (2921 lines)**
Target structure:
```
driftdriver/cli/
  __init__.py        -- main() entrypoint, argparse setup
  check.py           -- check subcommand (lane routing, findings)
  install_cmd.py     -- install subcommand
  run.py             -- run subcommand (check + action plan)
  doctor.py          -- doctor + compact + queue subcommands
  speedriftd_cmd.py  -- speedriftd subcommand delegation
```

**2c. Backfill tests for new boundaries**
- Each extracted module gets import + smoke tests
- Existing tests must pass unchanged against new structure

### Phase 3: Outcome Feedback Loop

**3a. Feedback schema**
```python
@dataclass
class DriftOutcome:
    task_id: str
    lane: str
    finding_key: str
    recommendation: str       # what driftdriver suggested
    action_taken: str         # what the agent actually did
    outcome: str              # result: resolved | ignored | worsened | deferred
    evidence: list[str]       # file paths, test results, etc.
    timestamp: datetime
```
Stored as `.workgraph/drift-outcomes.jsonl` (append-only ledger).

**3b. Wire into factory cycles**
- After task completion, factorydrift records outcome vs prior drift findings
- Outcome recorded automatically when `wg done` fires on a drift follow-up task

**3c. Feed into smart routing**
- `smart_routing.py` reads outcome history
- Lanes with high "ignored" or "worsened" rates get escalated
- Lanes with high "resolved" rates get priority in auto-selection

### Phase 4: Quality & Conformance

**4a. Evidence quality gates**
- Add `severity` field to SecDrift/QADrift findings (info/warning/error/critical)
- Policy gains `[enforcement]` section:
  ```toml
  [enforcement]
  block_on_critical = true
  warn_on_error = true
  max_unresolved_warnings = 10
  ```
- `driftdriver check` exit code reflects enforcement level

**4b. Lane plugin conformance tests**
- Parse DRIFT_PLUGIN_CONTRACT.md into testable assertions
- Test suite validates: CLI interface, exit codes, output format, finding schema
- Any lane in the ecosystem can be tested: `python -m pytest tests/test_lane_conformance.py --lane coredrift`

## Dependency Graph

```
0a ──┬── 1a ── 1b
     │       └─ 1c
     ├── 2a ── 2c
     ├── 2b ──┘
     ├── 3a ── 3b ── 3c
     └── 4a
0b ──┘        4b (independent)
```

## Acceptance Criteria

- All existing tests pass on current python
- Stalled tasks resolved (closed or unblocked)
- Atlas dogfood fixes landed
- ecosystem_hub.py and cli.py split into subpackages
- Outcome feedback loop recording and queryable
- At least one enforcement threshold wired into policy
- Lane conformance test passing for coredrift
