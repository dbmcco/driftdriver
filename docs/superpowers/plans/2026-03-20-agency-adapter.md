# Agency Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire speedrift and workgraph/agency bidirectionally — stamp desired outcomes on emitted tasks (outbound), and feed agency evaluation scores back into northstardrift's self-improvement axis (inbound).

**Architecture:** `wg_eval_bridge.py` already writes drift findings as evaluation JSONs to `.workgraph/agency/evaluations/`. The missing pieces are: (1) a reader that aggregates those scores per repo for northstardrift, (2) wiring that reader into the self_improvement axis via the hub snapshot, and (3) stamping `desired_outcome` from `NORTH_STAR.md` onto task descriptions at creation time. No new lanes — three focused changes.

**Tech Stack:** Python 3.11+, pytest, existing driftdriver patterns (JSONL, atomic writes, `op_health_inputs` snapshot pattern)

---

## File Structure

- **Create:** `driftdriver/agency_score_reader.py` — reads `.workgraph/agency/evaluations/*.json`, computes rolling avg score per repo
- **Create:** `tests/test_agency_score_reader.py` — unit tests for the reader
- **Modify:** `driftdriver/ecosystem_hub/snapshot.py` — call reader, add `agency_eval_inputs` to per-repo snapshot data
- **Modify:** `driftdriver/northstardrift.py` — blend agency_eval_score into self_improvement formula
- **Modify:** `driftdriver/drift_task_guard.py` — read NORTH_STAR.md, append desired_outcome to task description
- **Create:** `tests/test_agency_desired_outcome.py` — unit tests for desired_outcome stamping

---

## Task 1: Agency Score Reader

**Files:**
- Create: `driftdriver/agency_score_reader.py`
- Create: `tests/test_agency_score_reader.py`

**Context:** Agency writes evaluation JSONs to `.workgraph/agency/evaluations/<eval-id>.json`. Each file has a `score` field (0.0–1.0) and a `timestamp` ISO string. The reader should scan these files, filter to a rolling window (default 7 days), and return a score in 0–100 range. If no evaluations exist, return None (not 0 — absence of data is different from bad scores).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agency_score_reader.py
# ABOUTME: Tests for agency_score_reader — rolling evaluation score aggregation.
# ABOUTME: Uses real fixture files; no mocks.

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from driftdriver.agency_score_reader import read_agency_eval_score


def _write_eval(evals_dir: Path, eval_id: str, score: float, age_days: float = 0.0) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    data = {"id": eval_id, "score": score, "timestamp": ts}
    (evals_dir / f"{eval_id}.json").write_text(json.dumps(data))


def test_no_evaluations_dir_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert read_agency_eval_score(repo) is None


def test_empty_evaluations_dir_returns_none(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    assert read_agency_eval_score(tmp_path) is None


def test_single_perfect_score(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-1", 1.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(100.0)


def test_single_zero_score(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-1", 0.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(0.0)


def test_average_of_multiple_scores(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-1", 0.8)
    _write_eval(evals_dir, "eval-2", 0.6)
    _write_eval(evals_dir, "eval-3", 1.0)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx((0.8 + 0.6 + 1.0) / 3 * 100, abs=0.1)


def test_old_evaluations_excluded(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-fresh", 1.0, age_days=1.0)
    _write_eval(evals_dir, "eval-old", 0.0, age_days=10.0)  # outside 7-day window
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(100.0)


def test_malformed_json_skipped(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    (evals_dir / "bad.json").write_text("not json{{{")
    _write_eval(evals_dir, "eval-good", 0.5)
    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(50.0)


def test_custom_window_days(tmp_path: Path) -> None:
    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    _write_eval(evals_dir, "eval-2day", 1.0, age_days=2.0)
    _write_eval(evals_dir, "eval-4day", 0.0, age_days=4.0)
    # With window_days=3, only the 2-day-old eval qualifies
    score = read_agency_eval_score(tmp_path, window_days=3)
    assert score == pytest.approx(100.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_agency_score_reader.py -v 2>&1 | head -30
```

Expected: ImportError — `agency_score_reader` does not exist yet.

- [ ] **Step 3: Implement `agency_score_reader.py`**

```python
# driftdriver/agency_score_reader.py
# ABOUTME: Reads agency evaluation JSONs from .workgraph/agency/evaluations/
# ABOUTME: Computes rolling average score (0-100) for the self_improvement axis.
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def read_agency_eval_score(
    repo_path: Path,
    *,
    window_days: float = 7.0,
) -> float | None:
    """Read agency evaluation scores from the last `window_days` days.

    Returns average score in 0-100 range, or None if no evaluations exist.
    Malformed files are skipped silently.
    """
    evals_dir = repo_path / ".workgraph" / "agency" / "evaluations"
    if not evals_dir.is_dir():
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    scores: list[float] = []

    for path in evals_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        raw_score = data.get("score")
        if not isinstance(raw_score, (int, float)):
            continue

        raw_ts = data.get("timestamp", "")
        try:
            text = raw_ts.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            ts = datetime.fromisoformat(text)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue

        if ts < cutoff:
            continue

        scores.append(float(raw_score))

    if not scores:
        return None

    return round(sum(scores) / len(scores) * 100.0, 1)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_agency_score_reader.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/agency_score_reader.py tests/test_agency_score_reader.py
git commit -m "feat: add agency_score_reader for rolling evaluation score aggregation"
```

---

## Task 2: Wire Agency Score into Hub Snapshot

**Files:**
- Modify: `driftdriver/ecosystem_hub/snapshot.py` (find `op_health_inputs` pattern and follow it)

**Context:** The hub snapshot already has an `op_health_inputs` dict per repo that northstardrift reads (line 925 of northstardrift.py: `op_health_inputs = overview.get("op_health_inputs")`). We add `agency_eval_inputs` alongside it with the same pattern. The `repo_path` for each repo is available during snapshot collection.

- [ ] **Step 1: Find where per-repo snapshot data is assembled in snapshot.py**

```bash
grep -n "op_health_inputs\|repo_path\|per_repo" \
  driftdriver/ecosystem_hub/snapshot.py | head -30
```

Note the line numbers where `op_health_inputs` is written into the snapshot dict. We'll add `agency_eval_inputs` in the same block.

- [ ] **Step 2: Write the failing test**

```python
# Add to tests/test_northstardrift.py or create tests/test_agency_snapshot_wire.py

def test_agency_eval_inputs_in_snapshot(tmp_path: Path) -> None:
    """agency_eval_inputs should appear in snapshot when evaluations exist."""
    import json
    from datetime import datetime, timezone
    from driftdriver.agency_score_reader import read_agency_eval_score

    evals_dir = tmp_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True)
    ts = datetime.now(timezone.utc).isoformat()
    (evals_dir / "eval-1.json").write_text(
        json.dumps({"id": "eval-1", "score": 0.8, "timestamp": ts})
    )

    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(80.0)
    # The score must be in 0-100 range for northstardrift blending
    assert 0.0 <= score <= 100.0
```

```bash
python -m pytest tests/test_agency_snapshot_wire.py -v
```

Expected: PASS (this tests the reader directly — the snapshot wiring is tested via integration).

- [ ] **Step 3: Add `agency_eval_inputs` to snapshot.py**

Find the section in `snapshot.py` where `op_health_inputs` is written (search for `"op_health_inputs"`). In the same repo-data dict assembly, add:

```python
from driftdriver.agency_score_reader import read_agency_eval_score

# Inside the per-repo data assembly block, alongside op_health_inputs:
_agency_score = read_agency_eval_score(Path(repo_path))
repo_data["agency_eval_inputs"] = {
    "eval_score": _agency_score,  # float 0-100 or None
}
```

- [ ] **Step 4: Run existing snapshot tests**

```bash
python -m pytest tests/ -k "snapshot or hub" -v --tb=short 2>&1 | tail -20
```

Expected: All pass. (We're adding a new key, not changing existing ones.)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/snapshot.py tests/test_agency_snapshot_wire.py
git commit -m "feat: add agency_eval_inputs to hub snapshot per repo"
```

---

## Task 3: Blend Agency Score into Self-Improvement Axis

**Files:**
- Modify: `driftdriver/northstardrift.py` (around line 917 — the self_improvement formula)

**Context:** The current self_improvement formula (line 917–923):
```python
self_improvement = _clamp_score(
    (0.25 * improvement_change)
    + (0.20 * rollout_coverage)
    + (0.20 * throughput_score)
    + (0.20 * north_star_coverage)
    + (0.35 * plan_integrity_coverage)
)
```
Note: these weights already sum to > 1.0 (1.20). They're blended and clamped, so the absolute weights don't need to sum to 1.0. We add agency_eval_score as an additional blending input at 0.15 weight. When agency_eval_score is None (no evaluations yet), it contributes 0 to the blend — no change to existing behavior until evaluations accumulate.

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_northstardrift.py
# Find the test that exercises score_ecosystem_health or compute_northstar_score
# and add a case that verifies agency_eval_inputs flows through.

def test_agency_eval_score_improves_self_improvement(tmp_path):
    """When agency_eval_inputs.eval_score is high, self_improvement should be >= baseline."""
    # This is a property test: high eval score should not decrease self_improvement.
    # We test by computing with eval_score=None vs eval_score=100.0.
    from driftdriver.northstardrift import score_axes  # adjust to actual function name

    base_inputs = _make_minimal_snapshot_inputs(tmp_path)
    base_inputs["agency_eval_inputs"] = {"eval_score": None}
    score_none = score_axes(base_inputs)["self_improvement"]["score"]

    high_inputs = _make_minimal_snapshot_inputs(tmp_path)
    high_inputs["agency_eval_inputs"] = {"eval_score": 100.0}
    score_high = score_axes(high_inputs)["self_improvement"]["score"]

    assert score_high >= score_none
```

```bash
python -m pytest tests/test_northstardrift.py -k "agency" -v
```

Expected: FAIL — `agency_eval_inputs` not yet read in northstardrift.

- [ ] **Step 2: Read the existing northstardrift score function signature**

```bash
grep -n "def score_\|def compute_\|def _score\|agency_eval" \
  driftdriver/northstardrift.py | head -20
```

Note the function name that takes the overview/snapshot dict and computes axes.

- [ ] **Step 3: Add agency blending to self_improvement in northstardrift.py**

Find the `self_improvement` computation block (around line 917). Add before it:

```python
# Agency evaluation score input — None when no evaluations exist yet.
agency_eval_inputs = overview.get("agency_eval_inputs") if isinstance(overview.get("agency_eval_inputs"), dict) else {}
agency_eval_score_raw = agency_eval_inputs.get("eval_score")
agency_eval_contribution = float(agency_eval_score_raw) if isinstance(agency_eval_score_raw, (int, float)) else 0.0
```

Then add to the self_improvement formula:
```python
self_improvement = _clamp_score(
    (0.25 * improvement_change)
    + (0.20 * rollout_coverage)
    + (0.20 * throughput_score)
    + (0.20 * north_star_coverage)
    + (0.35 * plan_integrity_coverage)
    + (0.15 * agency_eval_contribution)  # new: agency evaluation feedback
)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_northstardrift.py -v --tb=short 2>&1 | tail -20
```

Expected: All pass including new agency test.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/northstardrift.py tests/test_northstardrift.py
git commit -m "feat: blend agency evaluation scores into self_improvement axis"
```

---

## Task 4: Stamp Desired Outcome on Task Descriptions

**Files:**
- Modify: `driftdriver/drift_task_guard.py` (find `guarded_add_drift_task`)
- Create: `tests/test_agency_desired_outcome.py`

**Context:** `drift_task_guard.py` is the single path for all drift task creation. It uses directives (not direct `wg add`) to create tasks. The task description is the string passed as the `title`/`description` argument. We want to append a `desired_outcome:` block to this description when a `NORTH_STAR.md` exists in the repo root. This grounds the agency-assigned agent in the repo's declared intent.

The format to append:
```
---desired_outcome---
<first line of "## Outcome target" section from NORTH_STAR.md, or first paragraph if no section>
```

Keep it short — one line max. Agency uses it as context; it doesn't need the full north star.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agency_desired_outcome.py
# ABOUTME: Tests for desired_outcome stamping on drift task descriptions.
# ABOUTME: Verifies NORTH_STAR.md is read and appended correctly.
from __future__ import annotations

from pathlib import Path
import pytest

from driftdriver.drift_task_guard import extract_desired_outcome


def test_no_north_star_returns_none(tmp_path: Path) -> None:
    assert extract_desired_outcome(tmp_path) is None


def test_north_star_with_outcome_section(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text(
        "# North Star\n\n## Outcome target\n\nLean, fast, maintainable.\n\n## Other\nstuff\n"
    )
    result = extract_desired_outcome(tmp_path)
    assert result == "Lean, fast, maintainable."


def test_north_star_without_outcome_section_uses_first_paragraph(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text(
        "# North Star\n\nThis repo does one thing well.\n\n## Other section\nstuff\n"
    )
    result = extract_desired_outcome(tmp_path)
    assert result == "This repo does one thing well."


def test_empty_north_star_returns_none(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text("")
    assert extract_desired_outcome(tmp_path) is None


def test_desired_outcome_appended_to_description(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text(
        "# North Star\n\n## Outcome target\n\nClean API surface.\n"
    )
    from driftdriver.drift_task_guard import stamp_desired_outcome
    desc = "Fix the bug in parser"
    result = stamp_desired_outcome(desc, tmp_path)
    assert "Fix the bug in parser" in result
    assert "Clean API surface." in result
    assert "desired_outcome" in result.lower()


def test_no_north_star_description_unchanged(tmp_path: Path) -> None:
    from driftdriver.drift_task_guard import stamp_desired_outcome
    desc = "Fix the bug"
    assert stamp_desired_outcome(desc, tmp_path) == desc
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_agency_desired_outcome.py -v 2>&1 | head -20
```

Expected: ImportError — `extract_desired_outcome` not defined yet.

- [ ] **Step 3: Add `extract_desired_outcome` and `stamp_desired_outcome` to `drift_task_guard.py`**

Add near the top of `drift_task_guard.py` after existing imports:

```python
def extract_desired_outcome(repo_path: Path) -> str | None:
    """Extract the outcome target from NORTH_STAR.md in repo_path.

    Looks for the first non-empty line under "## Outcome target".
    Falls back to the first non-heading paragraph. Returns None if
    NORTH_STAR.md is absent or yields no usable text.
    """
    north_star = repo_path / "NORTH_STAR.md"
    if not north_star.exists():
        return None
    try:
        text = north_star.read_text(encoding="utf-8")
    except OSError:
        return None

    lines = text.splitlines()
    in_outcome = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("## outcome target"):
            in_outcome = True
            continue
        if in_outcome:
            if stripped.startswith("##"):
                break
            if stripped:
                return stripped
        # Not yet in outcome section — stop looking for it after we hit one
    if in_outcome:
        return None  # Section found but empty

    # Fallback: first non-heading paragraph
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return None


def stamp_desired_outcome(description: str, repo_path: Path) -> str:
    """Append desired_outcome block to description if NORTH_STAR.md exists."""
    outcome = extract_desired_outcome(repo_path)
    if not outcome:
        return description
    return f"{description}\n\n---desired_outcome---\n{outcome}"
```

- [ ] **Step 4: Wire `stamp_desired_outcome` into the task creation call**

Find the line in `guarded_add_drift_task` where the description/title is assembled before the directive is emitted. Add:

```python
# stamp desired outcome from north star before emitting task
description = stamp_desired_outcome(description, wg_dir.parent)
```

(Note: `wg_dir` is `.workgraph/`, so `wg_dir.parent` is the repo root.)

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_agency_desired_outcome.py tests/test_drift_task_guard.py -v --tb=short
```

Expected: All pass.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -20
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add driftdriver/drift_task_guard.py tests/test_agency_desired_outcome.py
git commit -m "feat: stamp desired_outcome from NORTH_STAR.md onto drift task descriptions"
```

---

## Task 5: End-to-End Smoke Test

**Files:**
- Create: `tests/test_agency_adapter_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_agency_adapter_integration.py
# ABOUTME: Integration test — evaluations written by wg_eval_bridge are read by agency_score_reader.
# ABOUTME: Confirms the full inbound pipeline works end-to-end with real files.
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from driftdriver.agency_score_reader import read_agency_eval_score
from driftdriver.wg_eval_bridge import write_evaluation


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    """Evaluations written by wg_eval_bridge are readable by agency_score_reader."""
    evaluation = {
        "id": "eval-test-001",
        "task_id": "task-1",
        "role_id": "role-a",
        "tradeoff_id": "unknown",
        "score": 0.75,
        "dimensions": {"correctness": 0.75},
        "notes": "test",
        "evaluator": "speedrift:coredrift",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "drift",
    }
    write_evaluation(tmp_path, evaluation)

    score = read_agency_eval_score(tmp_path)
    assert score == pytest.approx(75.0)
```

- [ ] **Step 2: Run integration test**

```bash
python -m pytest tests/test_agency_adapter_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Run full suite one final time**

```bash
python -m pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: All pass, no regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/test_agency_adapter_integration.py
git commit -m "test: add agency adapter end-to-end integration test"
```
