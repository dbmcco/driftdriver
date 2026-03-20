# Ecosystem Evaluation & Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add repo lifecycle classification, a new Operational Health scoring axis, and a `governancedrift` lane that detects and remediates ecosystem conformance violations — surfaced through the existing hub at port 8777.

**Architecture:** `ecosystem.toml` declares per-repo lifecycle intent (`active`/`maintenance`/`retired`/`experimental`) and daemon posture. A new `governancedrift` lane collects observed reality, computes conformance deltas, and routes findings to workgraph tasks or intelligence inbox signals. `northstardrift` gains a 6th axis (Operational Health) and filters its overall score to `active` repos only. The hub gains a conformance panel tab.

**Tech Stack:** Python 3.11+, TOML (stdlib tomllib), existing driftdriver lane pattern, existing hub snapshot/API/dashboard pattern, pytest (TDD, no mocks).

**Spec:** `docs/superpowers/specs/2026-03-19-ecosystem-evaluation-governance-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml` | Modify | Add `lifecycle` + `daemon_posture` to every `[repos.*]` entry |
| `driftdriver/ecosystem_hub/discovery.py` | Modify | Read `lifecycle`/`daemon_posture` from ecosystem.toml, expose in repo meta |
| `driftdriver/ecosystem_hub/snapshot.py` | Modify | Add `lifecycle`, `daemon_posture`, `conformance_findings`, `op_health_inputs` to per-repo snapshot fields |
| `driftdriver/driftdriver/governancedrift.py` | Create | Pipe: collection, delta computation, finding classification, Op. Health scoring. Model layer: interpretation, confidence, remediation routing |
| `driftdriver/northstardrift.py` | Modify | Rename `quality` → `product_quality` in AXES/weights/targets. Add `operational_health` axis. Filter overall score to `active` repos only |
| `driftdriver/ecosystem_hub/api.py` | Modify | Add `GET /api/conformance` endpoint |
| `driftdriver/ecosystem_hub/dashboard.py` | Modify | Add conformance panel tab: lifecycle map, violation table, real vs. raw score, one-click remediation |
| `tests/test_governancedrift.py` | Create | Unit tests for pipe (conformance delta, classification, Op. Health formula, routing logic) |
| `tests/test_northstardrift_governance.py` | Create | Unit tests for axis rename, Operational Health inputs, lifecycle filter |
| `tests/test_hub_conformance.py` | Create | Integration tests for /api/conformance endpoint and snapshot fields |

---

## Task 1: Classify all repos in ecosystem.toml

**Files:**
- Modify: `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml`

**Context:** The file uses `[repos.<name>]` TOML sections with `role`, `url`, `tags` fields. Add `lifecycle` and `daemon_posture` to every entry. No code changes — pure config.

**Lifecycle values:** `active` | `maintenance` | `retired` | `experimental`
**Daemon posture values:** `always-on` | `on-demand` | `never`

**Classification guide:**
- `active` + `always-on`: repos with ongoing work and live daemons expected (paia-os, paia-shell, paia-program, lfw-ai-graph-crm, lodestar, third-layer-news, driftdriver, samantha, derek, ingrid, caroline, grok-aurora-cli, paia-memory, paia-events, paia-identity, paia-triage, paia-work, paia-meetings, folio, paia-agent-runtime, paia-shell, assistant-system)
- `maintenance` + `on-demand`: repos that are stable/done but not retired (lessons-mcp, lfw-interview, synthyra-outreach-factory, training-assistant, vibez-monitor, workgraph, specdrift, uxdrift, speedrift, garmin-connect-sync)
- `retired` + `never`: explicitly retired repos (news-briefing, meridian)
- `experimental` + `never`: run repos, one-off experiments (speedrift-ecosystem-v2-run1 through run5, speedrift-ecosystem)

- [ ] **Step 1: Read the current file**

```bash
cat /Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml
```

- [ ] **Step 2: Add lifecycle + daemon_posture to every [repos.*] section**

Each entry should look like:
```toml
[repos.paia-os]
role = "service"
url = "..."
tags = [...]
lifecycle = "active"
daemon_posture = "always-on"

[repos.news-briefing]
role = "service"
url = "..."
tags = [...]
lifecycle = "retired"
daemon_posture = "never"
```

Use the classification guide above. When in doubt, default to `maintenance` + `on-demand`.

- [ ] **Step 3: Validate TOML parses cleanly**

```bash
python3 -c "import tomllib; data = tomllib.loads(open('/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml').read()); print(f'OK — {len(data[\"repos\"])} repos'); [print(f'  {n}: lifecycle={v.get(\"lifecycle\",\"MISSING\")} posture={v.get(\"daemon_posture\",\"MISSING\")}') for n,v in data['repos'].items()]"
```

Expected: all repos listed with lifecycle and daemon_posture, no "MISSING".

- [ ] **Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/speedrift-ecosystem
git add ecosystem.toml
git commit -m "feat: add lifecycle and daemon_posture classification to all repos"
```

---

## Task 2: Read lifecycle fields in discovery + expose in snapshot

**Files:**
- Modify: `driftdriver/ecosystem_hub/discovery.py` — function `_load_ecosystem_repo_meta` (around line 403)
- Modify: `driftdriver/ecosystem_hub/snapshot.py` — add lifecycle fields to per-repo snapshot output
- Test: `tests/test_hub_conformance.py` (create)

**Context:** `_load_ecosystem_repo_meta` currently returns `{name: {"path", "url", "tags"}}`. We extend it to also return `lifecycle` and `daemon_posture`. The snapshot builder uses this meta to populate per-repo data. No changes to how repos are discovered — only what metadata is returned.

- [ ] **Step 1: Write failing tests**

Create `tests/test_hub_conformance.py`:

```python
# ABOUTME: Tests for hub conformance panel — lifecycle metadata, snapshot fields, API endpoint.
import tomllib
import pytest
from pathlib import Path
from unittest.mock import patch


FIXTURE_TOML = """
schema = 1
suite = "speedrift"

[repos.active-repo]
role = "service"
tags = ["personal"]
lifecycle = "active"
daemon_posture = "always-on"

[repos.retired-repo]
role = "service"
tags = ["personal"]
lifecycle = "retired"
daemon_posture = "never"

[repos.no-lifecycle-repo]
role = "service"
tags = ["personal"]
"""


def test_load_ecosystem_repo_meta_returns_lifecycle(tmp_path):
    toml_file = tmp_path / "ecosystem.toml"
    toml_file.write_text(FIXTURE_TOML)

    from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repo_meta
    meta = _load_ecosystem_repo_meta(toml_file)

    assert meta["active-repo"]["lifecycle"] == "active"
    assert meta["active-repo"]["daemon_posture"] == "always-on"
    assert meta["retired-repo"]["lifecycle"] == "retired"
    assert meta["retired-repo"]["daemon_posture"] == "never"


def test_load_ecosystem_repo_meta_defaults_missing_lifecycle(tmp_path):
    toml_file = tmp_path / "ecosystem.toml"
    toml_file.write_text(FIXTURE_TOML)

    from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repo_meta
    meta = _load_ecosystem_repo_meta(toml_file)

    # Repos without lifecycle default to "active" (safe default — don't hide them)
    assert meta["no-lifecycle-repo"]["lifecycle"] == "active"
    assert meta["no-lifecycle-repo"]["daemon_posture"] == "always-on"
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_hub_conformance.py::test_load_ecosystem_repo_meta_returns_lifecycle -v
```

Expected: FAIL — `_load_ecosystem_repo_meta` doesn't return lifecycle fields yet.

- [ ] **Step 3: Modify `_load_ecosystem_repo_meta` in discovery.py**

Find the function (around line 403). After reading `tags`, also read `lifecycle` and `daemon_posture`:

```python
def _load_ecosystem_repo_meta(ecosystem_toml: Path) -> dict[str, dict[str, Any]]:
    """Return {name: {"path": str, "url": str, "tags": list, "lifecycle": str, "daemon_posture": str}} for all registered repos."""
    if not ecosystem_toml.exists():
        return {}
    data = tomllib.loads(ecosystem_toml.read_text(encoding="utf-8"))
    repos = data.get("repos")
    if not isinstance(repos, dict):
        return {}
    result = {}
    for name, value in repos.items():
        if not isinstance(value, dict):
            continue
        result[name] = {
            "path": value.get("path", ""),
            "url": value.get("url", ""),
            "tags": value.get("tags", []),
            "lifecycle": value.get("lifecycle", "active"),       # default: active
            "daemon_posture": value.get("daemon_posture", "always-on"),  # default: always-on
        }
    return result
```

- [ ] **Step 4: Add `lifecycle` and `daemon_posture` to per-repo snapshot fields**

Find where per-repo data is assembled in `snapshot.py` (search for where `"source"` or `"tags"` is set on a repo dict). Add alongside tags:

```python
repo_meta = repo_meta_map.get(repo_name, {})
# existing fields ...
"lifecycle": repo_meta.get("lifecycle", "active"),
"daemon_posture": repo_meta.get("daemon_posture", "always-on"),
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_hub_conformance.py -v
```

Expected: PASS.

- [ ] **Step 6: Verify live hub snapshot includes lifecycle fields**

```bash
curl -s http://127.0.0.1:8777/api/repos | python3 -c "
import json, sys
repos = json.load(sys.stdin)
for r in repos[:3]:
    print(r['name'], '| lifecycle:', r.get('lifecycle', 'MISSING'), '| posture:', r.get('daemon_posture', 'MISSING'))
"
```

Expected: lifecycle and daemon_posture present for first 3 repos.

- [ ] **Step 7: Commit**

```bash
git add driftdriver/ecosystem_hub/discovery.py driftdriver/ecosystem_hub/snapshot.py tests/test_hub_conformance.py
git commit -m "feat: expose lifecycle and daemon_posture in ecosystem hub repo meta and snapshot"
```

---

## Task 3: Implement governancedrift pipe

**Files:**
- Create: `driftdriver/governancedrift.py`
- Create/extend: `tests/test_governancedrift.py`

**Context:** This is the deterministic half of the new lane. It observes reality (daemon socket live/dead, process count, task statuses) and compares against declared intent from ecosystem.toml to produce structured findings. No LLM involved here.

Finding categories:
- `lifecycle-violation`: retired/experimental repo with live daemon or running agents
- `process-debt`: active repo with zombie agents (processes alive but tasks_ready = 0)
- `architecture-gap`: active repo missing north star, or no contracts on open tasks
- `posture-mismatch`: daemon socket state doesn't match declared daemon_posture

Operational Health score inputs (all 0–100):
- `process_cleanliness`: 100 minus zombie_ratio (zombie_agents / total_live_agents * 100), clamped
- `task_debt_inverse`: 100 minus failed_abandoned_ratio (failed+abandoned / total_tasks * 100), clamped
- `daemon_posture_alignment`: percent of repos where observed posture matches declared posture
- `abandoned_task_inverse`: 100 minus pressure from old abandoned tasks (age-weighted)

- [ ] **Step 1: Write failing tests**

Create `tests/test_governancedrift.py`:

```python
# ABOUTME: Tests for governancedrift pipe — conformance delta, finding classification, Operational Health scoring.
import pytest
from pathlib import Path
from driftdriver.governancedrift import (
    classify_finding,
    compute_conformance_delta,
    score_operational_health,
    route_remediation,
    FindingCategory,
)


# --- classify_finding ---

def test_retired_repo_with_live_daemon_is_lifecycle_violation():
    finding = classify_finding(
        repo="news-briefing",
        lifecycle="retired",
        daemon_posture="never",
        daemon_socket_live=True,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=False,
    )
    assert finding["category"] == FindingCategory.LIFECYCLE_VIOLATION
    assert finding["severity"] == "high"


def test_active_repo_with_zombie_agents_is_process_debt():
    finding = classify_finding(
        repo="paia-program",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=True,
        live_agent_count=30,
        tasks_ready=0,
        north_star_present=True,
    )
    assert finding["category"] == FindingCategory.PROCESS_DEBT
    assert finding["severity"] == "high"


def test_clean_active_repo_produces_no_finding():
    finding = classify_finding(
        repo="lodestar",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=True,
        live_agent_count=2,
        tasks_ready=3,
        north_star_present=True,
    )
    assert finding is None


def test_active_repo_missing_north_star_is_architecture_gap():
    finding = classify_finding(
        repo="garmin-connect-sync",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=False,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=False,
    )
    assert finding["category"] == FindingCategory.ARCHITECTURE_GAP


# --- compute_conformance_delta ---

def test_compute_conformance_delta_detects_retired_with_live_daemon():
    repos = [
        {
            "name": "news-briefing",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "daemon_socket_live": True,
            "live_agent_count": 0,
            "tasks_ready": 0,
            "north_star_present": False,
        }
    ]
    findings = compute_conformance_delta(repos)
    assert len(findings) == 1
    assert findings[0]["repo"] == "news-briefing"
    assert findings[0]["category"] == FindingCategory.LIFECYCLE_VIOLATION


def test_compute_conformance_delta_skips_experimental_repos():
    repos = [
        {
            "name": "speedrift-ecosystem-v2-run3",
            "lifecycle": "experimental",
            "daemon_posture": "never",
            "daemon_socket_live": False,  # compliant — no daemon
            "live_agent_count": 0,
            "tasks_ready": 0,
            "north_star_present": False,  # architecture-gap waived for experimental
        }
    ]
    findings = compute_conformance_delta(repos)
    assert len(findings) == 0  # experimental repos: architecture-gap not raised


# --- score_operational_health ---

def test_score_operational_health_clean_ecosystem():
    score = score_operational_health(
        zombie_ratio=0.0,
        failed_abandoned_ratio=0.0,
        posture_alignment_ratio=1.0,
        abandoned_age_pressure=0.0,
    )
    assert score == pytest.approx(100.0)


def test_score_operational_health_bad_ecosystem():
    score = score_operational_health(
        zombie_ratio=0.8,        # 80% zombie agents
        failed_abandoned_ratio=0.6,  # 60% failed/abandoned tasks
        posture_alignment_ratio=0.3,  # only 30% posture alignment
        abandoned_age_pressure=0.9,
    )
    assert score < 40.0


# --- route_remediation ---

def test_high_confidence_finding_routes_to_task():
    result = route_remediation(confidence=0.90, finding_category=FindingCategory.LIFECYCLE_VIOLATION)
    assert result == "workgraph_task"


def test_low_confidence_finding_routes_to_inbox():
    result = route_remediation(confidence=0.70, finding_category=FindingCategory.ARCHITECTURE_GAP)
    assert result == "inbox_signal"


def test_boundary_confidence_routes_to_inbox():
    # Exactly at threshold (0.85) routes to task
    assert route_remediation(confidence=0.85, finding_category=FindingCategory.PROCESS_DEBT) == "workgraph_task"
    # Just below routes to inbox
    assert route_remediation(confidence=0.84, finding_category=FindingCategory.PROCESS_DEBT) == "inbox_signal"
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_governancedrift.py -v
```

Expected: FAIL — module `governancedrift` doesn't exist yet.

- [ ] **Step 3: Implement `driftdriver/governancedrift.py`**

```python
# ABOUTME: governancedrift — pipe layer for ecosystem conformance checking and Operational Health scoring.
# ABOUTME: Deterministic: no LLM. Classifies findings, computes deltas, scores Op. Health.
from __future__ import annotations

import socket
from enum import Enum
from pathlib import Path
from typing import Any


class FindingCategory(str, Enum):
    LIFECYCLE_VIOLATION = "lifecycle-violation"
    PROCESS_DEBT = "process-debt"
    ARCHITECTURE_GAP = "architecture-gap"
    POSTURE_MISMATCH = "posture-mismatch"


def classify_finding(
    *,
    repo: str,
    lifecycle: str,
    daemon_posture: str,
    daemon_socket_live: bool,
    live_agent_count: int,
    tasks_ready: int,
    north_star_present: bool,
) -> dict[str, Any] | None:
    """Classify one repo's conformance state. Returns a finding dict or None if clean."""

    # Lifecycle violation: retired/experimental repo with live daemon
    if lifecycle in ("retired", "experimental") and daemon_socket_live:
        return {
            "repo": repo,
            "category": FindingCategory.LIFECYCLE_VIOLATION,
            "severity": "high",
            "declared": f"lifecycle={lifecycle}, daemon_posture={daemon_posture}",
            "observed": f"daemon_socket_live=True, live_agents={live_agent_count}",
        }

    # Process debt: active repo with zombie agents (agents alive, zero ready work)
    if lifecycle == "active" and live_agent_count > 0 and tasks_ready == 0:
        return {
            "repo": repo,
            "category": FindingCategory.PROCESS_DEBT,
            "severity": "high",
            "declared": f"lifecycle={lifecycle}",
            "observed": f"live_agents={live_agent_count}, tasks_ready=0",
        }

    # Posture mismatch: daemon running when declared never, or stopped when declared always-on
    if daemon_posture == "never" and daemon_socket_live:
        return {
            "repo": repo,
            "category": FindingCategory.POSTURE_MISMATCH,
            "severity": "medium",
            "declared": "daemon_posture=never",
            "observed": "daemon_socket_live=True",
        }

    # Architecture gap: active repo missing north star (skip for experimental)
    if lifecycle == "active" and not north_star_present:
        return {
            "repo": repo,
            "category": FindingCategory.ARCHITECTURE_GAP,
            "severity": "medium",
            "declared": "lifecycle=active (north star expected)",
            "observed": "north_star_present=False",
        }

    return None


def compute_conformance_delta(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run classify_finding across all repos, return list of findings."""
    findings = []
    for repo in repos:
        finding = classify_finding(
            repo=repo["name"],
            lifecycle=repo.get("lifecycle", "active"),
            daemon_posture=repo.get("daemon_posture", "always-on"),
            daemon_socket_live=repo.get("daemon_socket_live", False),
            live_agent_count=repo.get("live_agent_count", 0),
            tasks_ready=repo.get("tasks_ready", 0),
            north_star_present=repo.get("north_star_present", False),
        )
        if finding:
            findings.append(finding)
    return findings


def score_operational_health(
    *,
    zombie_ratio: float,
    failed_abandoned_ratio: float,
    posture_alignment_ratio: float,
    abandoned_age_pressure: float,
) -> float:
    """Compute Operational Health score 0–100 from four normalized inputs (all 0–1)."""
    process_cleanliness = max(0.0, 1.0 - zombie_ratio) * 100
    task_debt_inverse = max(0.0, 1.0 - failed_abandoned_ratio) * 100
    daemon_alignment = posture_alignment_ratio * 100
    abandoned_inverse = max(0.0, 1.0 - abandoned_age_pressure) * 100

    score = (
        0.30 * process_cleanliness
        + 0.25 * task_debt_inverse
        + 0.25 * daemon_alignment
        + 0.20 * abandoned_inverse
    )
    return round(min(100.0, max(0.0, score)), 1)


def route_remediation(*, confidence: float, finding_category: FindingCategory) -> str:
    """Route finding to workgraph_task (automatic) or inbox_signal (human judgment)."""
    if confidence >= 0.85:
        return "workgraph_task"
    return "inbox_signal"


def check_daemon_socket_live(repo_path: Path) -> bool:
    """Check if the workgraph daemon socket is listening for a repo."""
    sock_path = repo_path / ".workgraph" / "service" / "daemon.sock"
    if not sock_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(str(sock_path))
            return True
    except (OSError, ConnectionRefusedError):
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_governancedrift.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Check coverage**

```bash
python -m pytest tests/test_governancedrift.py --cov=driftdriver.governancedrift --cov-report=term-missing
```

Expected: >90% coverage.

- [ ] **Step 6: Commit**

```bash
git add driftdriver/governancedrift.py tests/test_governancedrift.py
git commit -m "feat: implement governancedrift pipe — conformance delta, classification, Op. Health scoring"
```

---

## Task 4: Update northstardrift — rename quality axis, add Operational Health, lifecycle filter

**Files:**
- Modify: `driftdriver/northstardrift.py`
- Create: `tests/test_northstardrift_governance.py`

**Context:** `northstardrift.py` (1629 lines) has:
- `AXES` list at ~line 14: `["continuity", "autonomy", "quality", "coordination", "self_improvement"]`
- `AXIS_WEIGHTS` dict at ~line 22: `{"continuity": 0.25, "autonomy": 0.20, "quality": 0.20, ...}`
- `DEFAULT_TARGETS` dict at ~line 33 with `"axes": {"quality": 80.0, ...}`
- Quality score computed via `_average_quality_score` and surrounding logic around lines 839–856

**Changes needed:**
1. Add `"product_quality"` and `"operational_health"` to AXES; remove `"quality"`
2. Update weights: continuity=0.22, autonomy=0.18, product_quality=0.18, coordination=0.18, self_improvement=0.12, operational_health=0.12
3. Update targets: product_quality=80.0, operational_health=75.0
4. Rename all `quality` score variables to `product_quality`
5. Add lifecycle filter: collect Op. Health inputs from snapshot and compute `operational_health` score
6. Filter `participating_repos` to only `active` repos for overall score

- [ ] **Step 1: Write failing tests**

Create `tests/test_northstardrift_governance.py`:

```python
# ABOUTME: Tests for northstardrift governance updates — axis rename, Op. Health, lifecycle filter.
import pytest


def test_axes_include_product_quality_not_quality():
    from driftdriver import northstardrift
    assert "product_quality" in northstardrift.AXIS_NAMES
    assert "quality" not in northstardrift.AXIS_NAMES


def test_axes_include_operational_health():
    from driftdriver import northstardrift
    assert "operational_health" in northstardrift.AXIS_NAMES


def test_axis_weights_sum_to_one():
    from driftdriver import northstardrift
    total = sum(northstardrift.AXIS_WEIGHTS.values())
    assert abs(total - 1.0) < 0.001


def test_default_targets_use_product_quality_not_quality():
    from driftdriver import northstardrift
    targets = northstardrift.DEFAULT_TARGETS
    axes = targets["axes"]
    assert "product_quality" in axes
    assert "quality" not in axes
    assert "operational_health" in axes


def test_operational_health_target_is_reasonable():
    from driftdriver import northstardrift
    target = northstardrift.DEFAULT_TARGETS["axes"]["operational_health"]
    assert 60.0 <= target <= 85.0
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_northstardrift_governance.py -v
```

Expected: FAIL — `product_quality` not in AXES, `quality` still present.

- [ ] **Step 3: Read current AXES and weights in northstardrift.py**

```bash
head -60 driftdriver/northstardrift.py
```

- [ ] **Step 4: Update AXIS_NAMES tuple**

The constant is named `AXIS_NAMES` (a tuple) at line 13. Find and replace:

Old:
```python
AXIS_NAMES = (
    "continuity",
    "autonomy",
    "quality",
    "coordination",
    "self_improvement",
)
```

New:
```python
AXIS_NAMES = (
    "continuity",
    "autonomy",
    "product_quality",
    "coordination",
    "self_improvement",
    "operational_health",
)
```

- [ ] **Step 5: Update AXIS_WEIGHTS**

Old:
```python
AXIS_WEIGHTS = {
    "continuity": 0.25,
    "autonomy": 0.20,
    "quality": 0.20,
    "coordination": 0.20,
    "self_improvement": 0.15,
}
```

New:
```python
AXIS_WEIGHTS = {
    "continuity": 0.22,
    "autonomy": 0.18,
    "product_quality": 0.18,
    "coordination": 0.18,
    "self_improvement": 0.12,
    "operational_health": 0.12,
}
```

- [ ] **Step 6: Update DEFAULT_TARGETS axes**

Find the `DEFAULT_TARGETS` dict and rename `"quality"` → `"product_quality"`, add `"operational_health": 75.0`.

- [ ] **Step 7: Rename quality variable throughout**

Run a careful search-and-replace for score variable names (`quality =`, `quality_score`, etc.) that refer to the axis score (not qadrift quality findings — those stay as-is since they're product quality signals). The function `_average_quality_score` and its callers compute the product quality axis value — rename internal variables to `product_quality` for clarity but the qadrift data input variable names (e.g. `qa_score`, `qa_high`) stay unchanged.

Search for all instances:
```bash
grep -n "\bquality\b" driftdriver/northstardrift.py | grep -v "qa_\|qadrift\|quality_score\|quality_risk\|quality_high\|quality_critical\|quality_population\|_quality_score\|repos_quality"
```

Rename only the axis-level variables (e.g., `quality = _clamp_score(...)` → `product_quality = _clamp_score(...)`).

- [ ] **Step 8: Add operational_health computation**

In the effectiveness score computation function (where `quality`, `continuity`, etc. are assembled), add:

```python
# Operational Health: read from snapshot op_health_inputs if available, else 0
op_health_inputs = overview.get("op_health_inputs", {})
operational_health = _clamp_score(
    score_operational_health(
        zombie_ratio=float(op_health_inputs.get("zombie_ratio", 0.0)),
        failed_abandoned_ratio=float(op_health_inputs.get("failed_abandoned_ratio", 0.0)),
        posture_alignment_ratio=float(op_health_inputs.get("posture_alignment_ratio", 1.0)),
        abandoned_age_pressure=float(op_health_inputs.get("abandoned_age_pressure", 0.0)),
    )
)
```

Import `score_operational_health` from `driftdriver.governancedrift` at the top of the file.

- [ ] **Step 9: Apply lifecycle filter to participating_repos**

Find where `participating_repos` is computed (search for `participating_repos =`). Filter to only repos where `lifecycle == "active"` before computing the overall score:

```python
# Only score active repos in overall effectiveness
participating_repos = [r for r in repos if r.get("lifecycle", "active") == "active"]
```

- [ ] **Step 10: Run tests**

```bash
python -m pytest tests/test_northstardrift_governance.py tests/test_governancedrift.py -v
```

Expected: all PASS.

- [ ] **Step 11: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Fix any failures caused by `quality` → `product_quality` rename before committing.

- [ ] **Step 12: Commit**

```bash
git add driftdriver/northstardrift.py tests/test_northstardrift_governance.py
git commit -m "feat: rename quality→product_quality, add operational_health axis and lifecycle filter to northstardrift"
```

---

## Task 5: Implement governancedrift model layer + /api/conformance endpoint

**Files:**
- Modify: `driftdriver/governancedrift.py` (add model layer functions)
- Modify: `driftdriver/ecosystem_hub/api.py` (add /api/conformance route)
- Extend: `tests/test_governancedrift.py` (model boundary tests)
- Extend: `tests/test_hub_conformance.py` (API endpoint test)

**Context:** The model layer interprets findings from the pipe, assesses confidence, generates narratives and prompts. It uses the existing LLM-call patterns already in driftdriver (see `qadrift.py` or `plandrift.py` for how model calls are structured). The `/api/conformance` endpoint reads from the hub snapshot's conformance findings and returns them as JSON.

- [ ] **Step 1: Write failing boundary tests for model layer**

Add to `tests/test_governancedrift.py`:

```python
from driftdriver.governancedrift import build_model_prompt, parse_model_response


def test_build_model_prompt_includes_required_fields():
    finding = {
        "repo": "paia-program",
        "category": FindingCategory.PROCESS_DEBT,
        "severity": "high",
        "declared": "lifecycle=active",
        "observed": "live_agents=30, tasks_ready=0",
    }
    prompt = build_model_prompt(finding)
    assert "paia-program" in prompt
    assert "process-debt" in prompt
    assert "live_agents=30" in prompt
    assert "confidence" in prompt.lower()


def test_parse_model_response_extracts_confidence_and_path():
    model_output = """
    confidence: 0.92
    remediation: workgraph_task
    narrative: paia-program has 30 agents alive with no ready tasks, indicating a runaway executor.
    claude_prompt: Stop the paia-program daemon and archive failed tasks.
    """
    result = parse_model_response(model_output)
    assert result["confidence"] == pytest.approx(0.92)
    assert result["remediation_path"] == "workgraph_task"
    assert "runaway" in result["narrative"]
    assert result["claude_prompt"] is not None


def test_parse_model_response_handles_missing_confidence():
    result = parse_model_response("some output without confidence field")
    assert result["confidence"] == 0.5  # safe default


def test_api_conformance_endpoint_with_fixture_snapshot(tmp_path):
    """Snapshot file with conformance findings → /api/conformance returns them."""
    import json

    findings = [
        {
            "repo": "news-briefing",
            "category": "lifecycle-violation",
            "severity": "high",
            "declared": "lifecycle=retired",
            "observed": "daemon_socket_live=True",
        }
    ]

    # Write a real fixture snapshot file — no mocks, real file I/O
    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(json.dumps({
        "conformance_findings": findings,
        "repos": [],
        "overview": {},
    }))

    from driftdriver.ecosystem_hub.snapshot import load_snapshot
    snapshot = load_snapshot(snapshot_file)

    assert len(snapshot["conformance_findings"]) == 1
    assert snapshot["conformance_findings"][0]["repo"] == "news-briefing"
    assert snapshot["conformance_findings"][0]["category"] == "lifecycle-violation"
```

- [ ] **Step 2: Run to verify tests fail**

```bash
python -m pytest tests/test_governancedrift.py::test_build_model_prompt_includes_required_fields tests/test_hub_conformance.py::test_api_conformance_endpoint -v
```

- [ ] **Step 3: Add model layer functions to `governancedrift.py`**

```python
import re


def build_model_prompt(finding: dict[str, Any]) -> str:
    """Build the deterministic prompt sent to the model for interpretation."""
    return (
        f"Repo: {finding['repo']}\n"
        f"Category: {finding['category']}\n"
        f"Severity: {finding['severity']}\n"
        f"Declared: {finding['declared']}\n"
        f"Observed: {finding['observed']}\n\n"
        "Interpret this conformance finding. Provide:\n"
        "- confidence: float 0.0–1.0 (your confidence in this finding)\n"
        "- remediation: workgraph_task or inbox_signal\n"
        "- narrative: one sentence explaining why this matters\n"
        "- claude_prompt: actionable instruction for a Claude worker\n"
    )


def parse_model_response(output: str) -> dict[str, Any]:
    """Parse model output into structured fields. Safe defaults on missing fields."""
    result: dict[str, Any] = {
        "confidence": 0.5,
        "remediation_path": "inbox_signal",
        "narrative": "",
        "claude_prompt": None,
    }

    if m := re.search(r"confidence[:\s]+([0-9.]+)", output, re.IGNORECASE):
        try:
            result["confidence"] = float(m.group(1))
        except ValueError:
            pass

    if m := re.search(r"remediation[:\s]+(workgraph_task|inbox_signal)", output, re.IGNORECASE):
        result["remediation_path"] = m.group(1)

    if m := re.search(r"narrative[:\s]+(.+?)(?:\n|claude_prompt|$)", output, re.IGNORECASE | re.DOTALL):
        result["narrative"] = m.group(1).strip()

    if m := re.search(r"claude_prompt[:\s]+(.+?)(?:\n\n|$)", output, re.IGNORECASE | re.DOTALL):
        result["claude_prompt"] = m.group(1).strip()

    return result
```

- [ ] **Step 4: Add `/api/conformance` endpoint to `api.py`**

Find the GET route dispatcher in `api.py` (around line 654 where routes are matched). Add:

```python
if route == "/api/conformance":
    snapshot = self._get_snapshot()
    findings = snapshot.get("conformance_findings", [])
    self._send_json({"findings": findings, "count": len(findings)})
    return
```

Also add a `handle_conformance_request` helper (used in tests) that takes the snapshot and returns JSON bytes.

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/test_governancedrift.py tests/test_hub_conformance.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add driftdriver/governancedrift.py driftdriver/ecosystem_hub/api.py tests/test_governancedrift.py tests/test_hub_conformance.py
git commit -m "feat: add governancedrift model layer and /api/conformance endpoint"
```

---

## Task 6: Add hub conformance panel to dashboard

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (4136 lines — add new tab section)

**Context:** The dashboard is a single Python file that renders HTML via `render_dashboard_html()`. It has existing tabs/sections. Add a "Conformance" tab that shows: lifecycle map, violation table, real vs. raw score comparison, and one-click remediation buttons. The panel fetches from `/api/conformance` via the existing WebSocket/fetch pattern already in the dashboard JS.

No tests for pure HTML generation — use the integration test approach: verify the endpoint returns data, verify the HTML contains the required section IDs.

- [ ] **Step 1: Read the dashboard tab structure**

```bash
grep -n "tab\|panel\|section\|nav" driftdriver/ecosystem_hub/dashboard.py | head -30
```

Identify where tabs are defined and how to add a new one.

- [ ] **Step 2: Add conformance tab HTML**

In `render_dashboard_html()`, add a conformance tab alongside existing tabs. The tab should contain:

```html
<!-- Lifecycle map: active/maintenance/retired/experimental buckets -->
<div id="conformance-panel">
  <div id="lifecycle-map">
    <h3>Lifecycle Map</h3>
    <div class="lifecycle-buckets">
      <div class="bucket" id="bucket-active"></div>
      <div class="bucket" id="bucket-maintenance"></div>
      <div class="bucket" id="bucket-retired"></div>
      <div class="bucket" id="bucket-experimental"></div>
    </div>
  </div>

  <!-- Score comparison -->
  <div id="score-comparison">
    <div class="score-card">
      <span class="label">Real Score (active repos only)</span>
      <span id="score-real" class="score-value"></span>
    </div>
    <div class="score-card">
      <span class="label">Raw Score (all repos)</span>
      <span id="score-raw" class="score-value"></span>
    </div>
  </div>

  <!-- Violation table -->
  <div id="violation-table">
    <h3>Conformance Violations</h3>
    <table>
      <thead>
        <tr><th>Repo</th><th>Category</th><th>Severity</th><th>Declared</th><th>Observed</th><th>Action</th></tr>
      </thead>
      <tbody id="violation-rows"></tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 3: Add JS to populate the panel**

Add a `loadConformancePanel()` JS function that:
1. `fetch("/api/conformance")` → populates violation-rows table
2. `fetch("/api/repos")` → buckets repos by lifecycle into lifecycle-map
3. `fetch("/api/effectiveness")` → shows real score (filtered) vs raw score side by side
4. One-click remediation: for `lifecycle-violation` findings, show a "Stop Daemon" button that calls `POST /api/repo/<name>/service/workgraph/stop`

Call `loadConformancePanel()` when the conformance tab is activated.

- [ ] **Step 4: Add conformance tab to navigation**

Add "Conformance" to the existing tab nav, with the same styling pattern as other tabs.

- [ ] **Step 5: Smoke test in browser**

```bash
# Restart hub to pick up dashboard changes
scripts/ecosystem_hub_daemon.sh restart 2>/dev/null || (scripts/ecosystem_hub_daemon.sh stop; sleep 2; scripts/ecosystem_hub_daemon.sh start)
sleep 3
curl -s http://127.0.0.1:8777/ | grep -c "conformance-panel"
```

Expected: `1` (the panel div exists in rendered HTML).

- [ ] **Step 6: Verify conformance API has data**

```bash
curl -s http://127.0.0.1:8777/api/conformance | python3 -m json.tool
```

Expected: JSON with `findings` array (may be empty until governancedrift runs its first cycle).

- [ ] **Step 7: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add conformance panel tab to ecosystem hub dashboard"
```

---

## Task 7: Wire governancedrift into ecosystem cycle

**Files:**
- Modify: `driftdriver/ecosystem_hub/services.py` (or wherever the hub tick cycle runs)
- Modify: `driftdriver/ecosystem_hub/snapshot.py` (add conformance_findings + op_health_inputs to snapshot output)

**Context:** The hub runs a tick cycle (see `services.py` and the `last_tick_at` field in the hub status). Add a `governancedrift` collection pass to this cycle: collect observed reality per repo (daemon socket live, live agent count via `ps`, task status counts from graph.jsonl), compute conformance delta, compute Op. Health inputs, write both to the snapshot so northstardrift and the dashboard can read them.

- [ ] **Step 1: Read the hub tick cycle**

```bash
grep -n "tick\|cycle\|snapshot\|generate" driftdriver/ecosystem_hub/services.py | head -30
```

Identify where to inject the governancedrift collection pass.

- [ ] **Step 2: Add process observation helper to `governancedrift.py`**

```python
import subprocess


def observe_repo(repo_path: Path) -> dict[str, Any]:
    """Collect observed reality for one repo. Returns dict for compute_conformance_delta."""
    daemon_live = check_daemon_socket_live(repo_path)

    # Count live Claude agent processes for this repo
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"claude.*{repo_path.name}"],
            capture_output=True, text=True, timeout=5
        )
        live_agent_count = len(result.stdout.strip().splitlines()) if result.returncode == 0 else 0
    except Exception:
        live_agent_count = 0

    # Count task statuses from graph.jsonl
    graph = repo_path / ".workgraph" / "graph.jsonl"
    tasks_ready = 0
    tasks_failed = 0
    tasks_abandoned = 0
    tasks_total = 0
    if graph.exists():
        import json as _json
        for line in graph.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = _json.loads(line)
                status = t.get("status", "")
                tasks_total += 1
                if status == "open":
                    tasks_ready += 1
                elif status == "failed":
                    tasks_failed += 1
                elif status == "abandoned":
                    tasks_abandoned += 1
            except Exception:
                pass

    north_star = (repo_path / "NORTH_STAR.md").exists() or (repo_path / "docs" / "NORTH_STAR.md").exists()

    return {
        "daemon_socket_live": daemon_live,
        "live_agent_count": live_agent_count,
        "tasks_ready": tasks_ready,
        "tasks_failed": tasks_failed,
        "tasks_abandoned": tasks_abandoned,
        "tasks_total": tasks_total,
        "north_star_present": north_star,
    }
```

- [ ] **Step 3: Add `collect_ecosystem_governance` function**

```python
def collect_ecosystem_governance(
    repos: list[dict[str, Any]],
    workspace_root: Path,
) -> dict[str, Any]:
    """
    Run a full governance collection pass over all repos.
    Returns {"conformance_findings": [...], "op_health_inputs": {...}}.
    """
    observed_repos = []
    total_live_agents = 0
    total_failed = 0
    total_abandoned = 0
    total_tasks = 0
    posture_aligned = 0

    for repo_meta in repos:
        repo_path = workspace_root / repo_meta["name"]
        if not repo_path.exists():
            continue
        observed = observe_repo(repo_path)
        merged = {**repo_meta, **observed}
        observed_repos.append(merged)

        total_live_agents += observed["live_agent_count"]
        total_failed += observed["tasks_failed"]
        total_abandoned += observed["tasks_abandoned"]
        total_tasks += observed["tasks_total"]

        # Posture alignment check
        declared = repo_meta.get("daemon_posture", "always-on")
        live = observed["daemon_socket_live"]
        if declared == "always-on" and live:
            posture_aligned += 1
        elif declared == "never" and not live:
            posture_aligned += 1
        elif declared == "on-demand":
            posture_aligned += 1  # on-demand: either state is acceptable

    findings = compute_conformance_delta(observed_repos)

    # Compute Op. Health inputs (normalized 0–1)
    total_processes = max(1, total_live_agents)
    zombie_agents = sum(
        r["live_agent_count"] for r in observed_repos
        if r["live_agent_count"] > 0 and r["tasks_ready"] == 0
    )

    op_health_inputs = {
        "zombie_ratio": min(1.0, zombie_agents / total_processes),
        "failed_abandoned_ratio": min(1.0, (total_failed + total_abandoned) / max(1, total_tasks)),
        "posture_alignment_ratio": posture_aligned / max(1, len(observed_repos)),
        "abandoned_age_pressure": min(1.0, total_abandoned / max(1, total_tasks)),
    }

    return {
        "conformance_findings": [
            {**f, "category": f["category"].value if hasattr(f["category"], "value") else f["category"]}
            for f in findings
        ],
        "op_health_inputs": op_health_inputs,
    }
```

- [ ] **Step 4: Call governancedrift in the hub tick cycle**

In `services.py`, in the tick/snapshot generation function, add a call to `collect_ecosystem_governance` and write its output to the snapshot. The snapshot dict gets two new top-level keys: `conformance_findings` and `op_health_inputs`.

- [ ] **Step 5: Trigger a manual hub tick and verify**

```bash
# Force a snapshot refresh (hub re-generates on each tick)
curl -s http://127.0.0.1:8777/api/conformance | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'Findings: {d[\"count\"]}')
for f in d['findings']:
    print(f'  [{f[\"severity\"]}] {f[\"repo\"]}: {f[\"category\"]}')
"
```

Expected: findings list showing actual conformance violations (e.g., news-briefing: lifecycle-violation).

- [ ] **Step 6: Verify Op. Health inputs appear in effectiveness**

```bash
curl -s http://127.0.0.1:8777/api/effectiveness | python3 -c "
import json, sys
d = json.load(sys.stdin)
axes = d['axes']
print('Axes:', list(axes.keys()))
print('operational_health:', axes.get('operational_health', {}).get('score', 'MISSING'))
print('product_quality:', axes.get('product_quality', {}).get('score', 'MISSING'))
"
```

Expected: `operational_health` present, `quality` gone, `product_quality` present.

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add driftdriver/governancedrift.py driftdriver/ecosystem_hub/services.py driftdriver/ecosystem_hub/snapshot.py
git commit -m "feat: wire governancedrift into hub cycle — conformance findings and Op. Health inputs in snapshot"
```

---

## Verification

After all tasks complete:

```bash
# 1. Ecosystem.toml: all repos classified
python3 -c "
import tomllib
data = tomllib.loads(open('/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml').read())
missing = [n for n,v in data['repos'].items() if 'lifecycle' not in v]
print(f'Repos without lifecycle: {missing or \"none — all classified\"}')
"

# 2. Hub: conformance violations detected
curl -s http://127.0.0.1:8777/api/conformance | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{d[\"count\"]} conformance violations found')"

# 3. Hub: 6 axes in effectiveness score
curl -s http://127.0.0.1:8777/api/effectiveness | python3 -c "import json,sys; d=json.load(sys.stdin); print('Axes:', list(d['axes'].keys()))"

# 4. Tests pass
cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_governancedrift.py tests/test_northstardrift_governance.py tests/test_hub_conformance.py -v
```

---

## Future Work (out of scope for this plan)

**Daemon supervisor enforcement:** Extend the hub's existing supervisor (`ecosystem_hub_daemon.sh`) to read lifecycle declarations and automatically stop daemons on `retired`/`experimental` repos between cycles. Requires a separate task once steps 1–7 are stable and the conformance data is proven reliable.

**governancedrift model layer activation:** The `build_model_prompt` and `parse_model_response` functions are implemented but not yet called in the cycle. Wire them in after the pipe proves stable — add LLM interpretation pass on hourly cadence (not every tick).
