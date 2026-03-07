# Dark Factory Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Restructure driftdriver from a monolithic orchestrator into six clean services (Graph, Planner, Dispatch, Quality, Learning, Gate) by removing dead code, eliminating parallel state stores, splitting policy, extracting the Planner, and wiring the learning loop.

**Architecture:** Six services all reading/writing through the workgraph task graph as single source of truth. Delete ~1K lines of dead code, rewrite ~4K lines, keep ~140K lines unchanged. This is a reorganization, not a rewrite.

**Tech Stack:** Python 3.12+ (stdlib only, no external deps), Rust (workgraph — untouched), pytest

**Design Doc:** `docs/plans/2026-03-06-dark-factory-redesign.md`

**Test command:** `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/ -x -q`

---

## Phase 1: Dead Code Removal

Remove 3 modules that have tests but zero production callers. This reduces surface area and eliminates confusion.

---

### Task 1: Delete adversarial_review.py

**Files:**
- Delete: `driftdriver/adversarial_review.py` (143 lines)
- Delete: `tests/test_adversarial_review.py` (63 lines)
- Modify: `driftdriver/install.py` — remove `write_reviewdrift_wrapper()` references
- Modify: `driftdriver/routing_models.py:26` — remove `"reviewdrift"` from KNOWN_LANES

**Step 1: Identify all import sites**

Run: `cd /Users/braydon/projects/experiments/driftdriver && grep -rn "adversarial_review\|reviewdrift" driftdriver/ tests/ --include="*.py"`

Verify only these files reference it:
- `driftdriver/adversarial_review.py` (the module itself)
- `driftdriver/install.py` (wrapper generation)
- `driftdriver/routing_models.py` (KNOWN_LANES set)
- `tests/test_adversarial_review.py` (tests)

**Step 2: Remove references from install.py**

Find and remove `write_reviewdrift_wrapper()` function and any calls to it. Also remove the `reviewdrift_wrapper.sh` template reference if present in `driftdriver/templates/`.

**Step 3: Remove "reviewdrift" from KNOWN_LANES**

In `driftdriver/routing_models.py`, remove `"reviewdrift"` from the KNOWN_LANES set (line 26).

**Step 4: Delete the module and tests**

```bash
rm driftdriver/adversarial_review.py
rm tests/test_adversarial_review.py
```

**Step 5: Run tests to verify nothing breaks**

Run: `python -m pytest tests/ -x -q`
Expected: All tests pass (the deleted tests are gone, no other tests depended on them)

**Step 6: Commit**

```bash
git add -A driftdriver/adversarial_review.py tests/test_adversarial_review.py driftdriver/install.py driftdriver/routing_models.py
git commit -m "remove: delete dead adversarial_review module and reviewdrift lane"
```

---

### Task 2: Delete tool_approval.py

**Files:**
- Delete: `driftdriver/tool_approval.py` (184 lines)
- Delete: `tests/test_tool_approval.py` (336 lines)

**Step 1: Verify no production imports**

Run: `grep -rn "tool_approval" driftdriver/ --include="*.py" | grep -v "^driftdriver/tool_approval.py"`
Expected: No results (only the test file imports it)

**Step 2: Delete the module and tests**

```bash
rm driftdriver/tool_approval.py
rm tests/test_tool_approval.py
```

**Step 3: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All pass

**Step 4: Commit**

```bash
git add -A driftdriver/tool_approval.py tests/test_tool_approval.py
git commit -m "remove: delete dead tool_approval module (45 tests, 0 production callers)"
```

---

### Task 3: Delete contrariandrift.py

**Files:**
- Delete: `driftdriver/contrariandrift.py` (251 lines)
- Delete: `tests/test_contrariandrift.py` (290 lines)
- Modify: `driftdriver/routing_models.py:24` — remove `"contrariandrift"` from KNOWN_LANES
- Modify: `driftdriver/cli/install_cmd.py` — remove `write_contrariandrift_wrapper()` and `ensure_contrariandrift_gitignore()`
- Modify: `driftdriver/install.py` — remove contrariandrift wrapper/gitignore functions
- Delete: `driftdriver/templates/contrariandrift_wrapper.sh` (if exists)

**Step 1: Identify all import sites**

Run: `grep -rn "contrariandrift" driftdriver/ tests/ --include="*.py"`

**Step 2: Remove from KNOWN_LANES**

In `driftdriver/routing_models.py`, remove `"contrariandrift"` from the KNOWN_LANES set (line 24).

**Step 3: Remove wrapper/gitignore functions from install modules**

In `driftdriver/cli/install_cmd.py` and `driftdriver/install.py`:
- Remove `ensure_contrariandrift_gitignore()` function
- Remove `write_contrariandrift_wrapper()` function
- Remove any calls to these functions

**Step 4: Delete the module and tests**

```bash
rm driftdriver/contrariandrift.py
rm tests/test_contrariandrift.py
```

**Step 5: Run tests**

Run: `python -m pytest tests/ -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add -A
git commit -m "remove: delete dead contrariandrift module and wrapper generation"
```

---

## Phase 2: Eliminate Parallel State Stores

The design mandates the Graph (workgraph) as the single state store. Currently three modules maintain parallel state: `autopilot_state.py`, `execution_state.py`, and parts of `speedriftd.py`. Remove the first two, then clean up speedriftd.

---

### Task 4: Inline execution_state into wire.py, then delete

`execution_state.py` (107 lines) is only imported by `wire.py` (for `list_interrupted`) and its own tests. The `list_interrupted()` function just reads JSON files from a directory. Inline the minimal logic needed into `wire.py` and delete the module.

**Files:**
- Delete: `driftdriver/execution_state.py` (107 lines)
- Delete: `tests/test_execution_state.py` (68 lines)
- Modify: `driftdriver/wire.py:10` — replace import with inline logic
- Modify: `tests/test_wire.py` — remove execution_state import if present

**Step 1: Write a test for the inlined cmd_recover**

In `tests/test_wire.py`, add a test that verifies `cmd_recover` works without the execution_state module:

```python
def test_cmd_recover_no_interrupted(tmp_path):
    """cmd_recover returns empty list when no recovery dir exists."""
    result = cmd_recover(tmp_path)
    assert result == []
```

**Step 2: Run to verify it passes (it should, since cmd_recover already works)**

Run: `python -m pytest tests/test_wire.py::test_cmd_recover_no_interrupted -v`

**Step 3: Inline list_interrupted into wire.py**

Replace the import in `driftdriver/wire.py:10`:
```python
# Before:
from driftdriver.execution_state import list_interrupted

# After: inline the function
def _list_interrupted(wg_dir: Path) -> list:
    """List interrupted task states from recovery directory."""
    recovery = wg_dir / "recovery"
    if not recovery.is_dir():
        return []
    results = []
    for state_file in recovery.glob("*.json"):
        try:
            data = json.loads(state_file.read_text())
            results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return results
```

Update `cmd_recover` to call `_list_interrupted` instead of `list_interrupted`.

Add `import json` to wire.py imports if not already present.

**Step 4: Delete module and tests**

```bash
rm driftdriver/execution_state.py
rm tests/test_execution_state.py
```

**Step 5: Check for any other references**

Run: `grep -rn "execution_state" driftdriver/ tests/ --include="*.py"`
Expected: No results

**Step 6: Run tests**

Run: `python -m pytest tests/ -x -q`

**Step 7: Commit**

```bash
git add -A
git commit -m "remove: inline execution_state into wire.py, delete parallel state module"
```

---

### Task 5: Migrate autopilot_state into graph metadata, then delete

`autopilot_state.py` (106 lines) is imported by:
- `driftdriver/cli/__init__.py` — `cmd_autopilot()` uses `clear_run_state`, `save_run_state`, `save_worker_event`
- `driftdriver/speedriftd.py` — `load_run_state`, `load_worker_events`
- `tests/test_autopilot_state.py` and `tests/test_speedriftd.py`

These functions read/write JSON files in `.workgraph/.autopilot/`. The graph should own this state. For now, keep the file I/O pattern but move the functions into the modules that use them (speedriftd gets the load functions, cli gets the save functions).

**Files:**
- Delete: `driftdriver/autopilot_state.py` (106 lines)
- Delete: `tests/test_autopilot_state.py` (210 lines)
- Modify: `driftdriver/speedriftd.py` — inline `load_run_state`, `load_worker_events`
- Modify: `driftdriver/cli/__init__.py` — inline `clear_run_state`, `save_run_state`, `save_worker_event` into `cmd_autopilot()`

**Step 1: Read the current autopilot_state.py to identify all exported functions**

Run: `grep "^def " driftdriver/autopilot_state.py`

Functions to relocate:
- `autopilot_dir(wg_dir)` → inline where used
- `ensure_dir(wg_dir)` → inline where used
- `save_worker_event(wg_dir, event)` → move to cli/__init__.py
- `save_run_state(wg_dir, state)` → move to cli/__init__.py
- `load_run_state(wg_dir)` → move to speedriftd.py
- `load_worker_events(wg_dir)` → move to speedriftd.py
- `clear_run_state(wg_dir)` → move to cli/__init__.py

**Step 2: Copy load functions into speedriftd.py**

At the top of speedriftd.py, add the inlined functions (these are simple JSON file I/O — ~30 lines total). Remove the `from driftdriver.autopilot_state import` line.

**Step 3: Copy save functions into cli/__init__.py**

Near `cmd_autopilot()`, add the inlined save/clear functions. Remove the `from driftdriver.autopilot_state import` line if it exists in cli/__init__.py imports.

**Step 4: Delete module and tests**

```bash
rm driftdriver/autopilot_state.py
rm tests/test_autopilot_state.py
```

**Step 5: Update test_speedriftd.py**

In `tests/test_speedriftd.py`, replace `from driftdriver.autopilot_state import save_run_state` with a local helper or inline the test setup.

**Step 6: Run tests**

Run: `python -m pytest tests/ -x -q`

**Step 7: Commit**

```bash
git add -A
git commit -m "remove: inline autopilot_state into consuming modules, delete parallel state module"
```

---

### Task 6: Delete project_profiles.py

`project_profiles.py` (115 lines) is imported only by `cli/__init__.py` for the `profile` command. Its analytics (lane stats, failure patterns) should eventually live in the Learning service but for now we just remove the dead module and stub the CLI command.

**Files:**
- Delete: `driftdriver/project_profiles.py` (115 lines)
- Delete: `tests/test_project_profiles.py` (127 lines)
- Modify: `driftdriver/cli/__init__.py:16` — remove import, stub or remove `cmd_profile`

**Step 1: Remove import from cli/__init__.py**

Remove line 16: `from driftdriver.project_profiles import build_profile, format_profile_report`

Find `cmd_profile` and either:
- Remove the command entirely from argparse, or
- Stub it to print "Profile command will be rebuilt in Learning service"

**Step 2: Delete module and tests**

```bash
rm driftdriver/project_profiles.py
rm tests/test_project_profiles.py
```

**Step 3: Run tests**

Run: `python -m pytest tests/ -x -q`

**Step 4: Commit**

```bash
git add -A
git commit -m "remove: delete project_profiles (will be rebuilt in Learning service)"
```

---

### Task 7: Delete pm_coordination.py

`pm_coordination.py` (195 lines) is imported by:
- `cli/__init__.py:15` — `get_ready_tasks` (used in orchestrate command)
- `project_autopilot.py:662` — `plan_peer_dispatch`, `dispatch_to_peer` (conditional import in `_run_peer_dispatch`)
- `tests/test_pm_coordination.py`, `tests/test_cross_repo_dispatch.py`

`get_ready_tasks()` is a thin wrapper around `wg ready --json`. Inline it. The peer dispatch functions move into project_autopilot since that's where they're called.

**Files:**
- Delete: `driftdriver/pm_coordination.py` (195 lines)
- Delete: `tests/test_pm_coordination.py` (114 lines)
- Delete: `tests/test_cross_repo_dispatch.py` (if it only tests pm_coordination)
- Modify: `driftdriver/cli/__init__.py:15` — inline `get_ready_tasks`
- Modify: `driftdriver/project_autopilot.py` — inline the peer dispatch functions

**Step 1: Inline get_ready_tasks into cli/__init__.py**

`get_ready_tasks()` calls `wg ready --json` and parses the output. It's ~15 lines. Add it as a private function `_get_ready_tasks()` near where it's used.

Remove `from driftdriver.pm_coordination import get_ready_tasks` from cli/__init__.py.

**Step 2: Inline peer dispatch into project_autopilot.py**

In `project_autopilot.py`, the `_run_peer_dispatch()` method (line 656-694) conditionally imports `plan_peer_dispatch` and `dispatch_to_peer`. Copy those two functions (~40 lines combined) from pm_coordination.py into project_autopilot.py as private functions.

**Step 3: Delete module and tests**

```bash
rm driftdriver/pm_coordination.py
rm tests/test_pm_coordination.py
rm tests/test_cross_repo_dispatch.py  # verify this only tests pm_coordination first
```

**Step 4: Run tests**

Run: `python -m pytest tests/ -x -q`

**Step 5: Commit**

```bash
git add -A
git commit -m "remove: inline pm_coordination into consuming modules, delete module"
```

---

## Phase 3: Policy Split

Split `policy.py` (1,088 lines) into routing policy (what runs, in what order) and enforcement policy (post-drift verdicts). Clean boundary at line 1030.

---

### Task 8: Extract evaluate_enforcement into policy_enforcement.py

**Files:**
- Create: `driftdriver/policy_enforcement.py` (~70 lines)
- Create: `tests/test_policy_enforcement.py`
- Modify: `driftdriver/policy.py` — remove `evaluate_enforcement` and `_SEVERITY_RANK`
- Modify: any callers of `evaluate_enforcement` — update import path

**Step 1: Write the failing test**

```python
# tests/test_policy_enforcement.py
from driftdriver.policy_enforcement import evaluate_enforcement, SEVERITY_RANK

def test_enforcement_disabled_returns_clean():
    """When enforcement is disabled, always return clean result."""
    from driftdriver.policy import load_drift_policy
    # Create a minimal policy with enforcement disabled
    ...
```

Actually — since we're extracting, not writing new code, the pattern is:

**Step 1: Find all callers of evaluate_enforcement**

Run: `grep -rn "evaluate_enforcement\|_SEVERITY_RANK" driftdriver/ tests/ --include="*.py"`

**Step 2: Create policy_enforcement.py**

```python
# ABOUTME: Post-drift enforcement evaluation — determines block/warn/pass verdicts
# ABOUTME: Extracted from policy.py to separate routing decisions from enforcement actions

from __future__ import annotations
from typing import Any

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def evaluate_enforcement(
    policy,  # DriftPolicy — imported lazily to avoid circular
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    # ... (copy lines 1036-1088 from policy.py verbatim)
```

**Step 3: Update policy.py**

Remove `_SEVERITY_RANK` (line 1033) and `evaluate_enforcement` (lines 1036-1088) from policy.py. Add a re-export for backwards compatibility:

```python
# At bottom of policy.py:
from driftdriver.policy_enforcement import evaluate_enforcement  # noqa: F401
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_policy.py -x -q`
Expected: All pass (re-export maintains compatibility)

**Step 5: Update direct callers to import from new location**

Find callers and update imports. Then remove the re-export from policy.py.

**Step 6: Write a dedicated test for the extracted module**

```python
# tests/test_policy_enforcement.py
from driftdriver.policy_enforcement import evaluate_enforcement, SEVERITY_RANK

def test_severity_rank_ordering():
    assert SEVERITY_RANK["info"] < SEVERITY_RANK["critical"]

def test_enforcement_disabled():
    from unittest.mock import MagicMock
    policy = MagicMock()
    policy.enforcement = {"enabled": False}
    result = evaluate_enforcement(policy, [{"severity": "critical"}])
    assert result["blocked"] is False
    assert result["exit_code"] == 0

def test_critical_finding_blocks():
    from unittest.mock import MagicMock
    policy = MagicMock()
    policy.enforcement = {"enabled": True, "block_on_critical": True, "warn_on_error": True, "max_unresolved_warnings": 10}
    result = evaluate_enforcement(policy, [{"severity": "critical"}])
    assert result["blocked"] is True
    assert result["exit_code"] == 2
```

**Step 7: Run full test suite**

Run: `python -m pytest tests/ -x -q`

**Step 8: Commit**

```bash
git add driftdriver/policy_enforcement.py tests/test_policy_enforcement.py driftdriver/policy.py
git commit -m "refactor: extract enforcement evaluation from policy.py into policy_enforcement.py"
```

---

## Phase 4: Wire Dead Code to Life

Two modules exist but are never called in production. Wire them in.

---

### Task 9: Wire contract_enrichment into task-claimed handler

`contract_enrichment.py` has `enrich_contract()` and `enrich_with_peer_learnings()` but nothing calls them. The task-claimed.sh handler is the right place — it already primes knowledge, but doesn't enrich the contract.

**Files:**
- Modify: `driftdriver/wire.py:74-82` — `cmd_enrich` already exists, verify it works
- Modify: `driftdriver/templates/handlers/task-claimed.sh` — add enrichment call after priming
- Create: `tests/test_contract_enrichment_integration.py` — verify end-to-end

**Step 1: Write a test for cmd_enrich**

```python
# tests/test_contract_enrichment_integration.py
from driftdriver.wire import cmd_enrich

def test_cmd_enrich_with_empty_knowledge():
    result = cmd_enrich("task-1", "Implement feature X", "myproject", [])
    assert result["learnings_added"] == 0
    assert result["contract_updated"] is False

def test_cmd_enrich_with_relevant_knowledge():
    knowledge = [
        {"category": "pattern", "content": "Always run tests before commit", "confidence": 0.9, "scope": ""},
    ]
    result = cmd_enrich("task-1", "Implement feature X", "myproject", knowledge)
    # Should add relevant learnings
    assert isinstance(result["learnings_added"], int)
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_contract_enrichment_integration.py -v`

**Step 3: Add enrichment to task-claimed.sh**

After the priming block (lines 23-28), add:

```bash
# Enrich task contract with relevant prior learnings
if command -v driftdriver >/dev/null 2>&1 && [[ -n "$TASK_ID" ]]; then
  ENRICHED=$(driftdriver --dir "$PROJECT_DIR" wire enrich \
    --task-id "$TASK_ID" \
    --description "${WG_TASK_DESCRIPTION:-}" \
    --project "$(basename "$PROJECT_DIR")" 2>/dev/null || echo "")
  if [[ -n "$ENRICHED" ]]; then
    wg_log "$TASK_ID" "contract-enriched: $ENRICHED"
  fi
fi
```

**Step 4: Run tests**

Run: `python -m pytest tests/ -x -q`

**Step 5: Commit**

```bash
git add driftdriver/templates/handlers/task-claimed.sh tests/test_contract_enrichment_integration.py
git commit -m "feat: wire contract_enrichment into task-claimed handler"
```

---

### Task 10: Add knowledge decay to cold_distillation.py

Currently confidence only goes up. Entries that haven't been confirmed recently should lose confidence over time.

**Files:**
- Modify: `driftdriver/cold_distillation.py` — add `apply_decay()` function
- Modify: `tests/test_cold_distillation.py` — add decay tests

**Step 1: Write the failing test**

```python
# In tests/test_cold_distillation.py (add to existing file)

from driftdriver.cold_distillation import apply_decay

def test_apply_decay_reduces_confidence():
    """Entries not seen in recent events lose confidence."""
    entries = [
        {"category": "pattern", "content": "old finding", "confidence": 0.9, "last_confirmed": "2026-01-01"},
        {"category": "pattern", "content": "recent finding", "confidence": 0.9, "last_confirmed": "2026-03-06"},
    ]
    decayed = apply_decay(entries, reference_date="2026-03-06", half_life_days=30)
    # Old entry (65+ days) should have reduced confidence
    assert decayed[0]["confidence"] < 0.9
    # Recent entry should be unchanged
    assert decayed[1]["confidence"] == 0.9

def test_apply_decay_no_last_confirmed_uses_default():
    """Entries without last_confirmed get decayed from epoch."""
    entries = [{"category": "x", "content": "y", "confidence": 0.8}]
    decayed = apply_decay(entries, reference_date="2026-03-06", half_life_days=30)
    assert decayed[0]["confidence"] < 0.8

def test_apply_decay_preserves_minimum():
    """Confidence never goes below 0.05."""
    entries = [{"category": "x", "content": "y", "confidence": 0.1, "last_confirmed": "2020-01-01"}]
    decayed = apply_decay(entries, reference_date="2026-03-06", half_life_days=30)
    assert decayed[0]["confidence"] >= 0.05
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cold_distillation.py::test_apply_decay_reduces_confidence -v`
Expected: `ImportError: cannot import name 'apply_decay'`

**Step 3: Implement apply_decay**

In `driftdriver/cold_distillation.py`, add:

```python
import math
from datetime import datetime

def apply_decay(
    entries: list[dict],
    reference_date: str = "",
    half_life_days: int = 30,
    min_confidence: float = 0.05,
) -> list[dict]:
    """Apply exponential decay to knowledge entries based on age.

    Entries with a 'last_confirmed' date lose confidence exponentially.
    Half-life controls how quickly: at half_life_days since last confirmation,
    confidence drops to 50% of its current value.

    Entries without last_confirmed are treated as very old.
    Minimum confidence is clamped to min_confidence.
    """
    if not reference_date:
        reference_date = datetime.now().strftime("%Y-%m-%d")
    ref = datetime.strptime(reference_date, "%Y-%m-%d")

    result = []
    for entry in entries:
        entry = dict(entry)  # don't mutate originals
        last = entry.get("last_confirmed", "2020-01-01")
        try:
            last_dt = datetime.strptime(last, "%Y-%m-%d")
        except (ValueError, TypeError):
            last_dt = datetime(2020, 1, 1)

        days_since = (ref - last_dt).days
        if days_since <= 0:
            result.append(entry)
            continue

        decay_factor = math.pow(0.5, days_since / half_life_days)
        entry["confidence"] = max(min_confidence, round(entry.get("confidence", 0.5) * decay_factor, 4))
        result.append(entry)
    return result
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cold_distillation.py -v`
Expected: All pass

**Step 5: Wire decay into the distill() function**

Add decay as a step after pruning:

```python
def distill(events, existing_knowledge, prune_threshold=0.2):
    # ... existing logic ...
    surviving, entries_pruned = prune_low_confidence(all_knowledge, threshold=prune_threshold)

    # Apply decay to surviving entries
    surviving = apply_decay(surviving)

    entries_remaining = len(surviving)
    # ... return result ...
```

**Step 6: Run full tests**

Run: `python -m pytest tests/ -x -q`

**Step 7: Commit**

```bash
git add driftdriver/cold_distillation.py tests/test_cold_distillation.py
git commit -m "feat: add exponential knowledge decay to cold distillation"
```

---

## Phase 5: Extract Planner Service

Extract the decomposition logic from project_autopilot.py into a focused planner module. The Planner writes tasks to the Graph and stops — it doesn't dispatch or check quality.

---

### Task 11: Create planner.py from project_autopilot decomposition logic

**Files:**
- Create: `driftdriver/planner.py` (~120 lines)
- Create: `tests/test_planner.py`
- Modify: `driftdriver/project_autopilot.py` — import from planner instead of inline

**Step 1: Write the failing test**

```python
# tests/test_planner.py
# ABOUTME: Tests for the Planner service — goal decomposition into task subgraphs
# ABOUTME: Planner writes tasks to the graph and stops; no dispatch or quality checking

from driftdriver.planner import build_decompose_prompt, DECOMPOSE_PROMPT_TEMPLATE

def test_build_decompose_prompt_includes_goal():
    prompt = build_decompose_prompt("Build a REST API for user management")
    assert "REST API" in prompt
    assert "user management" in prompt

def test_decompose_prompt_template_has_wg_add():
    """Template instructs planner to use wg add for task creation."""
    assert "wg add" in DECOMPOSE_PROMPT_TEMPLATE or "wg" in DECOMPOSE_PROMPT_TEMPLATE
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_planner.py -v`
Expected: `ModuleNotFoundError: No module named 'driftdriver.planner'`

**Step 3: Create planner.py**

Extract from `project_autopilot.py`:
- `DECOMPOSE_PROMPT_TEMPLATE` (lines 20-42)
- `build_decompose_prompt()` (lines 295-300)
- `decompose_goal()` (lines 858-888) — the function that launches the planner worker
- `AutopilotConfig` dataclass (lines 112-121) — needed by decompose_goal

```python
# driftdriver/planner.py
# ABOUTME: Planner service — decomposes goals into task subgraphs
# ABOUTME: Writes tasks to the workgraph and stops; no dispatch or quality checking

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

DECOMPOSE_PROMPT_TEMPLATE = """..."""  # Copy from project_autopilot.py lines 20-42

@dataclass
class PlannerConfig:
    goal: str
    dry_run: bool = False
    max_tasks: int = 20

def build_decompose_prompt(goal: str) -> str:
    """Build the prompt that instructs the planner to decompose a goal into tasks."""
    return DECOMPOSE_PROMPT_TEMPLATE.format(goal=goal)
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_planner.py -v`
Expected: All pass

**Step 5: Update project_autopilot.py to import from planner**

```python
# In project_autopilot.py, replace inline DECOMPOSE_PROMPT_TEMPLATE and build_decompose_prompt:
from driftdriver.planner import DECOMPOSE_PROMPT_TEMPLATE, build_decompose_prompt
```

**Step 6: Run full tests**

Run: `python -m pytest tests/ -x -q`

**Step 7: Commit**

```bash
git add driftdriver/planner.py tests/test_planner.py driftdriver/project_autopilot.py
git commit -m "refactor: extract Planner service from project_autopilot"
```

---

## Phase 6: Internal Lane Standardization

Internal lanes (qadrift, secdrift) bypass the plugin contract that external lanes use. Wrap them so they respond to the same CLI interface.

---

### Task 12: Define the lane plugin contract

**Files:**
- Create: `driftdriver/lane_contract.py` (~50 lines)
- Create: `tests/test_lane_contract.py`

**Step 1: Write the failing test**

```python
# tests/test_lane_contract.py
# ABOUTME: Tests for the standard lane plugin contract
# ABOUTME: All drift lanes (internal and external) must conform to this interface

from driftdriver.lane_contract import LaneFinding, LaneResult, validate_lane_output

def test_lane_result_has_required_fields():
    result = LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean")
    assert result.lane == "coredrift"
    assert result.exit_code == 0

def test_validate_lane_output_accepts_valid():
    raw = '{"lane": "qadrift", "findings": [], "exit_code": 0, "summary": "ok"}'
    result = validate_lane_output(raw)
    assert result.lane == "qadrift"

def test_validate_lane_output_rejects_missing_lane():
    raw = '{"findings": [], "exit_code": 0}'
    result = validate_lane_output(raw)
    assert result is None
```

**Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_lane_contract.py -v`

**Step 3: Implement lane_contract.py**

```python
# driftdriver/lane_contract.py
# ABOUTME: Standard plugin contract for all drift lanes (internal and external)
# ABOUTME: Defines the interface every lane must implement for consistent routing

from __future__ import annotations
import json
from dataclasses import dataclass, field

@dataclass
class LaneFinding:
    message: str
    severity: str = "info"  # info, warning, error, critical
    file: str = ""
    line: int = 0
    tags: list[str] = field(default_factory=list)

@dataclass
class LaneResult:
    lane: str
    findings: list[LaneFinding]
    exit_code: int
    summary: str

def validate_lane_output(raw: str) -> LaneResult | None:
    """Parse raw JSON output from a lane into a LaneResult.

    Returns None if the output is malformed or missing required fields.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if "lane" not in data:
        return None

    findings = []
    for f in data.get("findings", []):
        findings.append(LaneFinding(
            message=f.get("message", ""),
            severity=f.get("severity", "info"),
            file=f.get("file", ""),
            line=f.get("line", 0),
            tags=f.get("tags", []),
        ))

    return LaneResult(
        lane=data["lane"],
        findings=findings,
        exit_code=data.get("exit_code", 0),
        summary=data.get("summary", ""),
    )
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_lane_contract.py -v`

**Step 5: Commit**

```bash
git add driftdriver/lane_contract.py tests/test_lane_contract.py
git commit -m "feat: define standard lane plugin contract for internal/external lanes"
```

---

## Phase 7: Self-Reflect Integration

Wire `self_reflect.py` into the task completion handler so learnings are captured automatically, not just via manual CLI invocation.

---

### Task 13: Wire self_reflect into task-completing handler

**Files:**
- Modify: `driftdriver/templates/handlers/task-completing.sh` — add reflect call
- Modify: `driftdriver/wire.py:34-43` — verify `cmd_reflect` works end-to-end

**Step 1: Verify cmd_reflect works**

Run: `python -c "from driftdriver.wire import cmd_reflect; print(cmd_reflect('/tmp'))"`
Expected: Should return the formatted learnings string (likely "No learnings extracted from this task." with no events)

**Step 2: Add reflect call to task-completing.sh**

Read the current task-completing.sh, then add after the drift check:

```bash
# Extract learnings from task execution
if command -v driftdriver >/dev/null 2>&1; then
  LEARNINGS=$(driftdriver --dir "$PROJECT_DIR" wire reflect 2>/dev/null || echo "")
  if [[ -n "$LEARNINGS" && "$LEARNINGS" != *"No learnings"* ]]; then
    wg_log "$TASK_ID" "self-reflect: $LEARNINGS"
  fi
fi
```

**Step 3: Run tests**

Run: `python -m pytest tests/ -x -q`

**Step 4: Commit**

```bash
git add driftdriver/templates/handlers/task-completing.sh
git commit -m "feat: wire self-reflect into task-completing handler for automatic learning"
```

---

## Verification

After all 13 tasks, run the full suite and verify:

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/ -x -q --tb=short
```

### Expected outcomes:
- ~100 tests removed (dead module tests)
- ~10 tests added (new modules: policy_enforcement, planner, lane_contract, contract_enrichment_integration, decay)
- All remaining tests pass
- Total LOC reduced by ~1,000 lines (dead code)
- Clean service boundaries established for Graph, Planner, Quality, Learning

### Files deleted (7 modules + 7 test files):
- `driftdriver/adversarial_review.py`
- `driftdriver/tool_approval.py`
- `driftdriver/contrariandrift.py`
- `driftdriver/execution_state.py`
- `driftdriver/autopilot_state.py`
- `driftdriver/project_profiles.py`
- `driftdriver/pm_coordination.py`
- `tests/test_adversarial_review.py`
- `tests/test_tool_approval.py`
- `tests/test_contrariandrift.py`
- `tests/test_execution_state.py`
- `tests/test_autopilot_state.py`
- `tests/test_project_profiles.py`
- `tests/test_pm_coordination.py`
- `tests/test_cross_repo_dispatch.py`

### Files created (4 modules + 4 test files):
- `driftdriver/policy_enforcement.py`
- `driftdriver/planner.py`
- `driftdriver/lane_contract.py`
- `tests/test_policy_enforcement.py`
- `tests/test_planner.py`
- `tests/test_lane_contract.py`
- `tests/test_contract_enrichment_integration.py`

---

## Future Plans (Not in This Pass)

These are identified in the design doc but are large enough to warrant separate plans:

1. **Dispatch service extraction** — extract dispatch from speedriftd into a standalone service with worker capability registration
2. **Always-on Learning** — replace session-end-only pending.jsonl with real-time graph event listening
3. **Internal lane migration** — wrap qadrift/secdrift/factorydrift/northstardrift/plandrift in the lane_contract interface
4. **Gate notifications** — add Slack/Matrix notification channels for UAT triggers
5. **Hub thinning** — move aggregation logic from ecosystem_hub into Learning service
6. **Cross-repo knowledge quality gates** — filter knowledge.jsonl before sharing between repos
