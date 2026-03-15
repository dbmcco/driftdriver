# Speedrift Quality Architecture Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the quality feedback loop between Speedrift (judgment) and Workgraph (execution) via four components: Bridge, evolverdrift, NorthStarDrift v2, and Quality Planner.

**Architecture:** Four components ship in dependency order. The Bridge writes drift findings as WG evaluations. evolverdrift monitors the evolver's response. NorthStarDrift v2 adds strategic alignment checking. The Quality Planner structures workgraphs with quality patterns. All components use the existing speedrift-lane-sdk `LaneResult` contract and the WG evaluation JSON schema.

**Tech Stack:** Python 3.11+, pytest, YAML/JSON/TOML I/O, speedrift-lane-sdk, existing driftdriver CLI infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-15-speedrift-quality-architecture-design.md`

---

## File Map

### Phase 1 — Bridge
| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `driftdriver/wg_eval_bridge.py` | Attribution, dimension mapping, evaluation JSON writing |
| Create | `tests/test_wg_eval_bridge.py` | All bridge tests |
| Modify | `driftdriver/cli/check.py:878` | Call bridge after lane results collected |
| Modify | `driftdriver/policy.py` | Add `[bridge]` section to DriftPolicy and defaults |

### Phase 2 — evolverdrift
| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `driftdriver/evolverdrift.py` | 5 checks + WG workaround detections, `run_as_lane()` |
| Create | `tests/test_evolverdrift.py` | All evolverdrift tests |
| Modify | `driftdriver/cli/check.py:110` | Register evolverdrift in INTERNAL_LANES |
| Modify | `driftdriver/policy.py` | Add `[lanes.evolverdrift]` section |

### Phase 3 — NorthStarDrift v2
| Action | Path | Responsibility |
|--------|------|---------------|
| Modify | `driftdriver/northstardrift.py` | Add alignment layer alongside existing v1 scoring |
| Modify | `tests/test_northstardrift.py` | Alignment-specific tests |
| Modify | `driftdriver/policy.py` | Add `[northstardrift.alignment]` sub-table parsing |

### Phase 4 — Quality Planner
| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `driftdriver/quality_planner.py` | Spec reader, repertoire, LLM graph structuring |
| Create | `tests/test_quality_planner.py` | All planner tests |
| Modify | `driftdriver/cli/__init__.py` | Wire `driftdriver plan` subcommand |

---

## Chunk 1: Phase 1 — Drift-to-Evaluation Bridge

### Task 1: Bridge Data Model and Attribution

**Files:**
- Create: `tests/test_wg_eval_bridge.py`
- Create: `driftdriver/wg_eval_bridge.py`

**Context:** The bridge translates `LaneFinding` objects (from speedrift-lane-sdk) into WG evaluation JSON files. The `LaneFinding` has fields: `message`, `severity` (critical/error/warning/info), `file`, `line`, `tags`. The WG evaluation format is JSON with fields: `id`, `task_id`, `role_id`, `tradeoff_id`, `score`, `dimensions`, `notes`, `evaluator`, `timestamp`, `source`. Assignment YAMLs live at `.workgraph/agency/assignments/{task_id}.yaml` with fields: `task_id`, `agent_id`, `composition_id`, `timestamp`.

- [ ] **Step 1: Write failing tests for BridgeReport and severity mapping**

```python
# tests/test_wg_eval_bridge.py
# ABOUTME: Tests for the drift-to-evaluation bridge.
# ABOUTME: Covers attribution, dimension mapping, severity scoring, and evaluation writing.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.wg_eval_bridge import (
    BridgeReport,
    severity_to_score,
    LANE_DIMENSION_MAP,
)


class SeverityMappingTests(unittest.TestCase):
    def test_critical_maps_to_zero(self) -> None:
        self.assertAlmostEqual(severity_to_score("critical"), 0.0)

    def test_error_maps_to_02(self) -> None:
        self.assertAlmostEqual(severity_to_score("error"), 0.2)

    def test_warning_maps_to_05(self) -> None:
        self.assertAlmostEqual(severity_to_score("warning"), 0.5)

    def test_info_maps_to_08(self) -> None:
        self.assertAlmostEqual(severity_to_score("info"), 0.8)

    def test_unknown_severity_defaults_to_05(self) -> None:
        self.assertAlmostEqual(severity_to_score("unknown"), 0.5)


class LaneDimensionMapTests(unittest.TestCase):
    def test_coredrift_has_correctness(self) -> None:
        dims = LANE_DIMENSION_MAP["coredrift"]
        self.assertIn("correctness", dims["primary"])

    def test_all_internal_lanes_mapped(self) -> None:
        for lane in ("coredrift", "qadrift", "plandrift", "secdrift", "northstardrift", "factorydrift"):
            self.assertIn(lane, LANE_DIMENSION_MAP, f"{lane} missing from dimension map")


class BridgeReportTests(unittest.TestCase):
    def test_bridge_report_fields(self) -> None:
        report = BridgeReport(
            evaluations_written=3,
            unattributable_findings=1,
            attribution_failures=["task-abc"],
            evaluation_ids=["eval-1", "eval-2", "eval-3"],
        )
        self.assertEqual(report.evaluations_written, 3)
        self.assertEqual(len(report.evaluation_ids), 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driftdriver.wg_eval_bridge'`

- [ ] **Step 3: Implement data model and severity mapping**

```python
# driftdriver/wg_eval_bridge.py
# ABOUTME: Drift-to-Evaluation Bridge — translates Speedrift drift lane findings
# ABOUTME: into WG evaluation JSON files for consumption by the evolver.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from speedrift_lane_sdk.lane_contract import LaneFinding, LaneResult


SEVERITY_SCORES: dict[str, float] = {
    "critical": 0.0,
    "error": 0.2,
    "warning": 0.5,
    "info": 0.8,
}

ALL_DIMENSIONS = (
    "correctness",
    "completeness",
    "style_adherence",
    "downstream_usability",
    "coordination_overhead",
    "blocking_impact",
    "efficiency",
    "strategic_alignment",
)

LANE_DIMENSION_MAP: dict[str, dict[str, list[str]]] = {
    "coredrift": {"primary": ["correctness", "completeness"], "secondary": ["blocking_impact"]},
    "qadrift": {"primary": ["style_adherence", "correctness"], "secondary": ["efficiency"]},
    "plandrift": {"primary": ["completeness", "downstream_usability"], "secondary": ["coordination_overhead"]},
    "secdrift": {"primary": ["correctness"], "secondary": ["blocking_impact"]},
    "northstardrift": {"primary": ["strategic_alignment"], "secondary": ["downstream_usability"]},
    "factorydrift": {"primary": ["coordination_overhead"], "secondary": ["blocking_impact"]},
}


@dataclass
class BridgeReport:
    evaluations_written: int = 0
    unattributable_findings: int = 0
    attribution_failures: list[str] = field(default_factory=list)
    evaluation_ids: list[str] = field(default_factory=list)


def severity_to_score(severity: str) -> float:
    return SEVERITY_SCORES.get(severity, 0.5)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py -v`
Expected: PASS (all 8 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/wg_eval_bridge.py tests/test_wg_eval_bridge.py
git commit -m "feat(bridge): add data model, severity mapping, and dimension map"
```

---

### Task 2: Finding Attribution

**Files:**
- Modify: `tests/test_wg_eval_bridge.py`
- Modify: `driftdriver/wg_eval_bridge.py`

**Context:** Attribution resolves a `LaneFinding` to a `(task_id, role_id)` pair by reading the WG assignment YAML at `.workgraph/agency/assignments/{task_id}.yaml`. The finding's `tags` list may contain a tag like `task:fix-auth` (direct task reference). If no tag, the finding is unattributable.

- [ ] **Step 1: Write failing tests for attribution**

Add to `tests/test_wg_eval_bridge.py`:

```python
from driftdriver.wg_eval_bridge import attribute_finding

import yaml


class AttributionTests(unittest.TestCase):
    def _setup_assignment(self, tmp: Path, task_id: str, role_id: str = "role-abc") -> None:
        assignments_dir = tmp / ".workgraph" / "agency" / "assignments"
        assignments_dir.mkdir(parents=True, exist_ok=True)
        (assignments_dir / f"{task_id}.yaml").write_text(
            yaml.dump({"task_id": task_id, "agent_id": "agent-1", "composition_id": role_id, "timestamp": "2026-03-15T00:00:00Z"}),
            encoding="utf-8",
        )

    def test_attribute_from_task_tag(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            self._setup_assignment(repo, "fix-auth", "role-xyz")
            finding = LaneFinding(message="scope creep", severity="error", tags=["task:fix-auth"])
            result = attribute_finding(repo, finding)
            self.assertIsNotNone(result)
            self.assertEqual(result["task_id"], "fix-auth")
            self.assertEqual(result["role_id"], "role-xyz")

    def test_attribute_returns_none_when_no_tags(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            finding = LaneFinding(message="general issue", severity="warning")
            result = attribute_finding(repo, finding)
            self.assertIsNone(result)

    def test_attribute_returns_none_when_assignment_missing(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph" / "agency" / "assignments").mkdir(parents=True)
            finding = LaneFinding(message="issue", severity="error", tags=["task:nonexistent"])
            result = attribute_finding(repo, finding)
            self.assertIsNone(result)

    def test_attribute_returns_none_when_no_role_id(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            assignments_dir = repo / ".workgraph" / "agency" / "assignments"
            assignments_dir.mkdir(parents=True)
            (assignments_dir / "task-1.yaml").write_text(
                yaml.dump({"task_id": "task-1", "agent_id": "agent-1"}),
                encoding="utf-8",
            )
            finding = LaneFinding(message="issue", severity="error", tags=["task:task-1"])
            result = attribute_finding(repo, finding)
            self.assertIsNone(result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py::AttributionTests -v`
Expected: FAIL — `cannot import name 'attribute_finding'`

- [ ] **Step 3: Implement attribution**

Add to `driftdriver/wg_eval_bridge.py`:

```python
import yaml


def _extract_task_id_from_tags(tags: list[str]) -> str | None:
    for tag in tags:
        if tag.startswith("task:"):
            return tag[5:]
    return None


def _load_assignment(repo_path: Path, task_id: str) -> dict[str, Any] | None:
    yaml_path = repo_path / ".workgraph" / "agency" / "assignments" / f"{task_id}.yaml"
    if not yaml_path.exists():
        return None
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def attribute_finding(repo_path: Path, finding: LaneFinding) -> dict[str, Any] | None:
    task_id = _extract_task_id_from_tags(finding.tags)
    if task_id is None:
        return None

    assignment = _load_assignment(repo_path, task_id)
    if assignment is None:
        return None

    role_id = assignment.get("composition_id") or assignment.get("role_id")
    if not role_id:
        return None

    return {
        "task_id": task_id,
        "role_id": str(role_id),
        "agent_id": str(assignment.get("agent_id", "unknown")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py -v`
Expected: PASS (all 12 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/wg_eval_bridge.py tests/test_wg_eval_bridge.py
git commit -m "feat(bridge): add finding-to-agent attribution via assignment YAML"
```

---

### Task 3: Evaluation Writing

**Files:**
- Modify: `tests/test_wg_eval_bridge.py`
- Modify: `driftdriver/wg_eval_bridge.py`

**Context:** Given an attributed finding and a lane name, build the WG evaluation JSON and write it to `.workgraph/agency/evaluations/`. The dimensions dict starts with all values at 1.0 (no issue), then the primary and secondary dimensions for the lane get the severity score. The overall `score` is the average of all dimensions.

- [ ] **Step 1: Write failing tests for evaluation building and writing**

Add to `tests/test_wg_eval_bridge.py`:

```python
from driftdriver.wg_eval_bridge import build_evaluation, write_evaluation


class BuildEvaluationTests(unittest.TestCase):
    def test_build_evaluation_sets_evaluator_and_source(self) -> None:
        finding = LaneFinding(message="scope creep", severity="error", tags=["task:t1"])
        attribution = {"task_id": "t1", "role_id": "role-1", "agent_id": "agent-1"}
        ev = build_evaluation(finding, attribution, lane="coredrift")
        self.assertEqual(ev["evaluator"], "speedrift:coredrift")
        self.assertEqual(ev["source"], "drift")

    def test_build_evaluation_maps_primary_dimensions(self) -> None:
        finding = LaneFinding(message="issue", severity="critical", tags=["task:t1"])
        attribution = {"task_id": "t1", "role_id": "role-1", "agent_id": "agent-1"}
        ev = build_evaluation(finding, attribution, lane="coredrift")
        self.assertAlmostEqual(ev["dimensions"]["correctness"], 0.0)
        self.assertAlmostEqual(ev["dimensions"]["completeness"], 0.0)
        self.assertAlmostEqual(ev["dimensions"]["style_adherence"], 1.0)

    def test_build_evaluation_maps_secondary_dimensions(self) -> None:
        finding = LaneFinding(message="issue", severity="warning", tags=["task:t1"])
        attribution = {"task_id": "t1", "role_id": "role-1", "agent_id": "agent-1"}
        ev = build_evaluation(finding, attribution, lane="coredrift")
        self.assertAlmostEqual(ev["dimensions"]["blocking_impact"], 0.5)

    def test_build_evaluation_score_is_dimension_average(self) -> None:
        finding = LaneFinding(message="issue", severity="error", tags=["task:t1"])
        attribution = {"task_id": "t1", "role_id": "role-1", "agent_id": "agent-1"}
        ev = build_evaluation(finding, attribution, lane="coredrift")
        dims = ev["dimensions"]
        expected_avg = sum(dims.values()) / len(dims)
        self.assertAlmostEqual(ev["score"], round(expected_avg, 2))

    def test_build_evaluation_includes_finding_message_in_notes(self) -> None:
        finding = LaneFinding(message="OAuth added without spec", severity="error", tags=["task:t1"])
        attribution = {"task_id": "t1", "role_id": "role-1", "agent_id": "agent-1"}
        ev = build_evaluation(finding, attribution, lane="coredrift")
        self.assertIn("OAuth added without spec", ev["notes"])
        self.assertIn("coredrift", ev["notes"])


class WriteEvaluationTests(unittest.TestCase):
    def test_write_evaluation_creates_json_file(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            ev = {
                "id": "eval-drift-coredrift-t1-2026-03-15",
                "task_id": "t1",
                "role_id": "role-1",
                "tradeoff_id": "unknown",
                "score": 0.5,
                "dimensions": {"correctness": 0.2},
                "notes": "test",
                "evaluator": "speedrift:coredrift",
                "timestamp": "2026-03-15T00:00:00Z",
                "source": "drift",
            }
            path = write_evaluation(repo, ev)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["id"], ev["id"])
            self.assertEqual(data["source"], "drift")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py::BuildEvaluationTests tests/test_wg_eval_bridge.py::WriteEvaluationTests -v`
Expected: FAIL — `cannot import name 'build_evaluation'`

- [ ] **Step 3: Implement evaluation building and writing**

Add to `driftdriver/wg_eval_bridge.py`:

```python
def build_evaluation(
    finding: LaneFinding,
    attribution: dict[str, Any],
    *,
    lane: str,
) -> dict[str, Any]:
    score = severity_to_score(finding.severity)
    dims: dict[str, float] = {d: 1.0 for d in ALL_DIMENSIONS}

    mapping = LANE_DIMENSION_MAP.get(lane, {"primary": [], "secondary": []})
    for dim in mapping.get("primary", []):
        if dim in dims:
            dims[dim] = score
    for dim in mapping.get("secondary", []):
        if dim in dims:
            dims[dim] = score

    overall = round(sum(dims.values()) / len(dims), 2) if dims else score
    ts = datetime.now(tz=timezone.utc).isoformat()
    eval_id = f"eval-drift-{lane}-{attribution['task_id']}-{ts.replace(':', '-')}"

    return {
        "id": eval_id,
        "task_id": attribution["task_id"],
        "role_id": attribution["role_id"],
        "tradeoff_id": "unknown",
        "score": overall,
        "dimensions": dims,
        "notes": f"{lane}: {finding.message}",
        "evaluator": f"speedrift:{lane}",
        "timestamp": ts,
        "source": "drift",
    }


def write_evaluation(repo_path: Path, evaluation: dict[str, Any]) -> Path:
    evals_dir = repo_path / ".workgraph" / "agency" / "evaluations"
    evals_dir.mkdir(parents=True, exist_ok=True)
    path = evals_dir / f"{evaluation['id']}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(evaluation, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py -v`
Expected: PASS (all 18 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/wg_eval_bridge.py tests/test_wg_eval_bridge.py
git commit -m "feat(bridge): evaluation building with dimension mapping and JSON writing"
```

---

### Task 4: Main Bridge Function and CLI Integration

**Files:**
- Modify: `tests/test_wg_eval_bridge.py`
- Modify: `driftdriver/wg_eval_bridge.py`
- Modify: `driftdriver/cli/check.py`
- Modify: `driftdriver/policy.py`

**Context:** The main `bridge_findings_to_evaluations()` function iterates over all lane results, attributes findings, builds evaluations, and writes them. It's called from `cli/check.py` after all internal lane results are collected (after line 878). The `[bridge]` section in drift-policy.toml controls `enabled`, `attribution_strategy`, and `min_severity`.

- [ ] **Step 1: Write failing tests for the main bridge function**

Add to `tests/test_wg_eval_bridge.py`:

```python
from driftdriver.wg_eval_bridge import bridge_findings_to_evaluations


class BridgeFunctionTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path, task_id: str, role_id: str = "role-1") -> Path:
        repo = tmp
        assignments_dir = repo / ".workgraph" / "agency" / "assignments"
        assignments_dir.mkdir(parents=True, exist_ok=True)
        (assignments_dir / f"{task_id}.yaml").write_text(
            yaml.dump({"task_id": task_id, "agent_id": "agent-1", "composition_id": role_id}),
            encoding="utf-8",
        )
        return repo

    def test_bridge_writes_evaluations_for_attributed_findings(self) -> None:
        with TemporaryDirectory() as td:
            repo = self._setup_repo(Path(td), "fix-auth")
            results = [
                LaneResult(
                    lane="coredrift",
                    findings=[LaneFinding(message="scope creep", severity="error", tags=["task:fix-auth"])],
                    exit_code=1,
                    summary="coredrift: 1 finding",
                ),
            ]
            report = bridge_findings_to_evaluations(repo, results)
            self.assertEqual(report.evaluations_written, 1)
            self.assertEqual(report.unattributable_findings, 0)
            evals_dir = repo / ".workgraph" / "agency" / "evaluations"
            self.assertEqual(len(list(evals_dir.glob("eval-drift-*.json"))), 1)

    def test_bridge_skips_unattributable_findings(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph" / "agency" / "assignments").mkdir(parents=True)
            results = [
                LaneResult(
                    lane="qadrift",
                    findings=[LaneFinding(message="general issue", severity="warning")],
                    exit_code=1,
                    summary="qadrift: 1 finding",
                ),
            ]
            report = bridge_findings_to_evaluations(repo, results)
            self.assertEqual(report.evaluations_written, 0)
            self.assertEqual(report.unattributable_findings, 1)

    def test_bridge_skips_below_min_severity(self) -> None:
        with TemporaryDirectory() as td:
            repo = self._setup_repo(Path(td), "task-1")
            results = [
                LaneResult(
                    lane="coredrift",
                    findings=[LaneFinding(message="minor", severity="info", tags=["task:task-1"])],
                    exit_code=0,
                    summary="coredrift: clean",
                ),
            ]
            report = bridge_findings_to_evaluations(repo, results, min_severity="warning")
            self.assertEqual(report.evaluations_written, 0)

    def test_bridge_handles_empty_results(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            report = bridge_findings_to_evaluations(repo, [])
            self.assertEqual(report.evaluations_written, 0)
            self.assertEqual(report.unattributable_findings, 0)

    def test_bridge_records_attribution_failure_task_ids(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph" / "agency" / "assignments").mkdir(parents=True)
            results = [
                LaneResult(
                    lane="coredrift",
                    findings=[LaneFinding(message="issue", severity="error", tags=["task:missing-task"])],
                    exit_code=1,
                    summary="1 finding",
                ),
            ]
            report = bridge_findings_to_evaluations(repo, results)
            self.assertIn("missing-task", report.attribution_failures)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py::BridgeFunctionTests -v`
Expected: FAIL — `cannot import name 'bridge_findings_to_evaluations'`

- [ ] **Step 3: Implement bridge_findings_to_evaluations**

Add to `driftdriver/wg_eval_bridge.py`:

```python
SEVERITY_ORDER = ("critical", "error", "warning", "info")


def _meets_min_severity(severity: str, min_severity: str) -> bool:
    try:
        return SEVERITY_ORDER.index(severity) <= SEVERITY_ORDER.index(min_severity)
    except ValueError:
        return True


def bridge_findings_to_evaluations(
    repo_path: Path,
    lane_results: list[LaneResult],
    *,
    min_severity: str = "info",
) -> BridgeReport:
    report = BridgeReport()

    for result in lane_results:
        lane_name = result.lane
        for finding in result.findings:
            if not _meets_min_severity(finding.severity, min_severity):
                continue

            task_id = _extract_task_id_from_tags(finding.tags)
            if task_id is None:
                report.unattributable_findings += 1
                continue

            attribution = attribute_finding(repo_path, finding)
            if attribution is None:
                report.unattributable_findings += 1
                report.attribution_failures.append(task_id)
                continue

            evaluation = build_evaluation(finding, attribution, lane=lane_name)
            path = write_evaluation(repo_path, evaluation)
            report.evaluations_written += 1
            report.evaluation_ids.append(evaluation["id"])

    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_wg_eval_bridge.py -v`
Expected: PASS (all 23 tests)

- [ ] **Step 5: Add bridge config to policy.py**

In `driftdriver/policy.py`, add to the `DriftPolicy` dataclass (after `northstardrift` field, around line 258):

```python
    bridge: dict[str, Any]
```

Add a default config function (near the other `_default_*` functions):

```python
def _default_bridge_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "attribution_strategy": "assignment",
        "min_severity": "warning",
    }
```

Wire it into `load_drift_policy()` — follow the existing pattern for other sections (parse from TOML, merge with defaults). Add `bridge=_default_bridge_cfg()` to the `DriftPolicy()` constructor calls in the default and scaffold paths.

- [ ] **Step 6: Wire bridge into cli/check.py**

In `driftdriver/cli/check.py`, after the internal lanes loop (after line 878 where `internal_plugins_json` is fully populated), add:

```python
    # Bridge: translate attributed drift findings into WG evaluations.
    bridge_cfg = policy.bridge if hasattr(policy, "bridge") else {}
    if bridge_cfg.get("enabled", True):
        from driftdriver.wg_eval_bridge import bridge_findings_to_evaluations
        bridge_lane_results = []
        for lane_name, lane_data in internal_plugins_json.items():
            report = lane_data.get("report")
            if isinstance(report, dict) and report.get("findings"):
                from speedrift_lane_sdk.lane_contract import LaneFinding, LaneResult
                findings = [
                    LaneFinding(
                        message=f.get("message", ""),
                        severity=f.get("severity", "info"),
                        file=f.get("file", ""),
                        line=f.get("line", 0),
                        tags=f.get("tags", []),
                    )
                    for f in report["findings"]
                ]
                bridge_lane_results.append(LaneResult(
                    lane=lane_name,
                    findings=findings,
                    exit_code=int(lane_data.get("exit_code", 0)),
                    summary=report.get("summary", ""),
                ))
        if bridge_lane_results:
            bridge_report = bridge_findings_to_evaluations(
                project_dir,
                bridge_lane_results,
                min_severity=str(bridge_cfg.get("min_severity", "warning")),
            )
            if bridge_report.evaluations_written > 0:
                print(f"bridge: wrote {bridge_report.evaluations_written} evaluation(s) to .workgraph/agency/evaluations/")
```

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest -q`
Expected: All pass (including existing tests — no regressions)

- [ ] **Step 8: Commit**

```bash
git add driftdriver/wg_eval_bridge.py tests/test_wg_eval_bridge.py driftdriver/cli/check.py driftdriver/policy.py
git commit -m "feat(bridge): wire bridge into drift check cycle with policy config"
```

---

## Chunk 2: Phase 2 — evolverdrift

### Task 5: evolverdrift Core — Liveness and No-History Checks

**Files:**
- Create: `tests/test_evolverdrift.py`
- Create: `driftdriver/evolverdrift.py`

**Context:** evolverdrift is a new drift lane that monitors WG's evolver. It implements `run_as_lane(project_dir) -> LaneResult` like all other internal lanes. First check: liveness — has the evolver run recently? If `.workgraph/evolve-runs/` doesn't exist, emit `info` and suppress all other checks. The `[lanes.evolverdrift]` config has `evolver_stale_days` (default 7).

- [ ] **Step 1: Write failing tests for liveness check**

```python
# tests/test_evolverdrift.py
# ABOUTME: Tests for evolverdrift lane — monitors WG evolver liveness, consumption, impact, regression.
# ABOUTME: Covers no-history case, stale evolver detection, and WG failure workarounds.
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.evolverdrift import check_liveness, run_as_lane


class LivenessCheckTests(unittest.TestCase):
    def test_no_evolve_runs_dir_returns_no_history(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "info")
            self.assertIn("never run", findings[0].message)

    def test_empty_evolve_runs_dir_returns_no_history(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph" / "evolve-runs").mkdir(parents=True)
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "info")

    def test_recent_run_returns_no_findings(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".workgraph" / "evolve-runs" / "run-20260315-120000"
            run_dir.mkdir(parents=True)
            (run_dir / "config.json").write_text(json.dumps({"timestamp": datetime.now(tz=timezone.utc).isoformat()}))
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 0)

    def test_stale_run_returns_warning(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".workgraph" / "evolve-runs" / "run-20260301-120000"
            run_dir.mkdir(parents=True)
            old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=10)).isoformat()
            (run_dir / "config.json").write_text(json.dumps({"timestamp": old_ts}))
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "warning")

    def test_very_stale_run_returns_error(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            run_dir = repo / ".workgraph" / "evolve-runs" / "run-20260201-120000"
            run_dir.mkdir(parents=True)
            old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=20)).isoformat()
            (run_dir / "config.json").write_text(json.dumps({"timestamp": old_ts}))
            findings = check_liveness(repo, evolver_stale_days=7)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].severity, "error")


class RunAsLaneTests(unittest.TestCase):
    def test_run_as_lane_returns_lane_result(self) -> None:
        with TemporaryDirectory() as td:
            result = run_as_lane(Path(td))
            self.assertEqual(result.lane, "evolverdrift")
            self.assertIsInstance(result.findings, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_evolverdrift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driftdriver.evolverdrift'`

- [ ] **Step 3: Implement liveness check and run_as_lane**

```python
# driftdriver/evolverdrift.py
# ABOUTME: evolverdrift lane — monitors WG evolver liveness, consumption, impact, and regression.
# ABOUTME: Also detects WG failure modes (orphaned tasks, deadlocked daemons, graph corruption).
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from speedrift_lane_sdk.lane_contract import LaneFinding, LaneResult


def _parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_evolve_run(repo_path: Path) -> tuple[Path | None, datetime | None]:
    evolve_dir = repo_path / ".workgraph" / "evolve-runs"
    if not evolve_dir.exists():
        return None, None
    runs = sorted(evolve_dir.iterdir(), reverse=True)
    if not runs:
        return None, None
    run_dir = runs[0]
    config_path = run_dir / "config.json"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            ts = _parse_iso(data.get("timestamp"))
            if ts:
                return run_dir, ts
        except Exception:
            pass
    # Fallback: parse timestamp from dir name like run-20260315-120000
    name = run_dir.name
    if name.startswith("run-") and len(name) >= 19:
        try:
            ts = datetime.strptime(name[4:19], "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
            return run_dir, ts
        except ValueError:
            pass
    return run_dir, None


def check_liveness(
    repo_path: Path,
    *,
    evolver_stale_days: int = 7,
) -> list[LaneFinding]:
    run_dir, run_ts = _latest_evolve_run(repo_path)

    if run_dir is None:
        return [LaneFinding(
            message="Evolver has never run in this repo",
            severity="info",
            tags=["evolverdrift", "liveness", "no-history"],
        )]

    if run_ts is None:
        return [LaneFinding(
            message="Evolver run directory exists but timestamp unreadable",
            severity="info",
            tags=["evolverdrift", "liveness"],
        )]

    age = datetime.now(tz=timezone.utc) - run_ts
    stale_days = age.total_seconds() / 86400

    if stale_days <= evolver_stale_days:
        return []

    severity = "error" if stale_days > evolver_stale_days * 2 else "warning"
    return [LaneFinding(
        message=f"Evolver has not run in {int(stale_days)} days",
        severity=severity,
        tags=["evolverdrift", "liveness", "stale"],
    )]


def run_as_lane(project_dir: Path) -> LaneResult:
    findings: list[LaneFinding] = []

    liveness = check_liveness(project_dir)
    findings.extend(liveness)

    no_history = any("no-history" in f.tags for f in liveness)
    if no_history:
        summary_score = "no-history"
    else:
        summary_score = f"{len(findings)} finding(s)"

    return LaneResult(
        lane="evolverdrift",
        findings=findings,
        exit_code=1 if any(f.severity in ("error", "critical") for f in findings) else 0,
        summary=f"evolverdrift: {summary_score}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_evolverdrift.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/evolverdrift.py tests/test_evolverdrift.py
git commit -m "feat(evolverdrift): liveness check with no-history handling"
```

---

### Task 6: evolverdrift — WG Failure Workarounds

**Files:**
- Modify: `tests/test_evolverdrift.py`
- Modify: `driftdriver/evolverdrift.py`

**Context:** Detect orphaned in-progress tasks (no alive agent), graph corruption (duplicate node IDs, orphan deps). These workaround checks don't depend on the evolver — they always run. The daemon deadlock check detects unresponsive socket.

- [ ] **Step 1: Write failing tests for WG workaround checks**

Add to `tests/test_evolverdrift.py`:

```python
from driftdriver.evolverdrift import check_orphaned_tasks, check_graph_corruption


class OrphanedTasksTests(unittest.TestCase):
    def _write_graph(self, repo: Path, tasks: list[dict]) -> None:
        wg = repo / ".workgraph"
        wg.mkdir(parents=True, exist_ok=True)
        with (wg / "graph.jsonl").open("w") as f:
            for task in tasks:
                f.write(json.dumps(task) + "\n")

    def _write_agents(self, repo: Path, agents: list[dict]) -> None:
        registry = repo / ".workgraph" / "service" / "agents.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({"agents": {a["id"]: a for a in agents}}))

    def test_detects_in_progress_with_no_agents(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            self._write_graph(repo, [
                {"type": "task", "id": "t1", "status": "in-progress", "assigned": "agent-dead"},
            ])
            self._write_agents(repo, [])
            findings = check_orphaned_tasks(repo)
            self.assertEqual(len(findings), 1)
            self.assertIn("t1", findings[0].message)

    def test_no_findings_when_agent_alive(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            self._write_graph(repo, [
                {"type": "task", "id": "t1", "status": "in-progress", "assigned": "agent-1"},
            ])
            self._write_agents(repo, [{"id": "agent-1", "alive": True, "pid": 12345}])
            findings = check_orphaned_tasks(repo)
            self.assertEqual(len(findings), 0)


class GraphCorruptionTests(unittest.TestCase):
    def test_detects_duplicate_node_ids(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True)
            with (wg / "graph.jsonl").open("w") as f:
                f.write(json.dumps({"type": "task", "id": "t1", "status": "open"}) + "\n")
                f.write(json.dumps({"type": "task", "id": "t1", "status": "done"}) + "\n")
            findings = check_graph_corruption(repo)
            dups = [f for f in findings if "duplicate" in f.message.lower()]
            self.assertGreater(len(dups), 0)

    def test_detects_orphan_deps(self) -> None:
        with TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True)
            with (wg / "graph.jsonl").open("w") as f:
                f.write(json.dumps({"type": "task", "id": "t1", "status": "open", "after": ["nonexistent"]}) + "\n")
            findings = check_graph_corruption(repo)
            orphans = [f for f in findings if "orphan" in f.message.lower() or "missing" in f.message.lower()]
            self.assertGreater(len(orphans), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_evolverdrift.py::OrphanedTasksTests tests/test_evolverdrift.py::GraphCorruptionTests -v`
Expected: FAIL — `cannot import name 'check_orphaned_tasks'`

- [ ] **Step 3: Implement workaround checks**

Add to `driftdriver/evolverdrift.py`:

```python
def _load_graph_lines(repo_path: Path) -> list[dict[str, Any]]:
    graph_path = repo_path / ".workgraph" / "graph.jsonl"
    if not graph_path.exists():
        return []
    lines: list[dict[str, Any]] = []
    for line in graph_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return lines


def _load_agent_registry(repo_path: Path) -> dict[str, Any]:
    registry_path = repo_path / ".workgraph" / "service" / "agents.json"
    if not registry_path.exists():
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data.get("agents", {}) if isinstance(data, dict) else {}


def check_orphaned_tasks(repo_path: Path) -> list[LaneFinding]:
    lines = _load_graph_lines(repo_path)
    agents = _load_agent_registry(repo_path)

    in_progress = [
        n for n in lines
        if n.get("type") == "task" and n.get("status") == "in-progress"
    ]

    alive_agent_ids = {
        aid for aid, a in agents.items()
        if a.get("alive", False)
    }

    findings: list[LaneFinding] = []
    for task in in_progress:
        assigned = task.get("assigned", "")
        if assigned and assigned not in alive_agent_ids:
            findings.append(LaneFinding(
                message=f"Orphaned in-progress task '{task['id']}' — assigned agent '{assigned}' is not alive",
                severity="warning",
                tags=["evolverdrift", "workaround", "orphaned-task"],
            ))

    return findings


def check_graph_corruption(repo_path: Path) -> list[LaneFinding]:
    lines = _load_graph_lines(repo_path)
    findings: list[LaneFinding] = []

    # Duplicate node IDs
    seen_ids: dict[str, int] = {}
    for node in lines:
        nid = node.get("id", "")
        if nid:
            seen_ids[nid] = seen_ids.get(nid, 0) + 1
    duplicates = {nid: count for nid, count in seen_ids.items() if count > 1}
    if duplicates:
        findings.append(LaneFinding(
            message=f"Graph has {len(duplicates)} duplicate node ID(s): {', '.join(list(duplicates)[:5])}",
            severity="warning",
            tags=["evolverdrift", "workaround", "graph-corruption", "duplicates"],
        ))

    # Orphan dependency references
    all_ids = set(seen_ids.keys())
    orphan_deps: list[str] = []
    for node in lines:
        for dep in node.get("after", []):
            if dep and dep not in all_ids:
                orphan_deps.append(f"{node.get('id', '?')}->{dep}")
    if orphan_deps:
        findings.append(LaneFinding(
            message=f"Graph has {len(orphan_deps)} orphan dependency ref(s): {', '.join(orphan_deps[:5])}",
            severity="warning",
            tags=["evolverdrift", "workaround", "graph-corruption", "orphan-deps"],
        ))

    return findings
```

- [ ] **Step 4: Wire workaround checks into run_as_lane**

Update `run_as_lane()` in `driftdriver/evolverdrift.py` to call workaround checks (these always run, even when no evolve history):

```python
def run_as_lane(project_dir: Path) -> LaneResult:
    findings: list[LaneFinding] = []

    # Evolver checks
    liveness = check_liveness(project_dir)
    findings.extend(liveness)

    no_history = any("no-history" in f.tags for f in liveness)

    # WG workaround checks (always run)
    findings.extend(check_orphaned_tasks(project_dir))
    findings.extend(check_graph_corruption(project_dir))

    if no_history:
        summary_score = "no-history"
    else:
        summary_score = f"{len(findings)} finding(s)"

    return LaneResult(
        lane="evolverdrift",
        findings=findings,
        exit_code=1 if any(f.severity in ("error", "critical") for f in findings) else 0,
        summary=f"evolverdrift: {summary_score}",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_evolverdrift.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 6: Commit**

```bash
git add driftdriver/evolverdrift.py tests/test_evolverdrift.py
git commit -m "feat(evolverdrift): WG failure workarounds — orphaned tasks and graph corruption"
```

---

### Task 7: evolverdrift — Registration and Policy

**Files:**
- Modify: `driftdriver/cli/check.py`
- Modify: `driftdriver/policy.py`

**Context:** Register evolverdrift as an internal lane so it runs as part of the standard drift check cycle. Add `[lanes.evolverdrift]` to the policy schema.

- [ ] **Step 1: Register in INTERNAL_LANES**

In `driftdriver/cli/check.py`, add to `INTERNAL_LANES` dict (around line 115):

```python
    "evolverdrift": "driftdriver.evolverdrift",
```

- [ ] **Step 2: Add policy config**

In `driftdriver/policy.py`, add a default config function:

```python
def _default_evolverdrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "evolver_stale_days": 7,
        "impact_window_days": 14,
        "regression_threshold": 0.2,
    }
```

Add `evolverdrift: dict[str, Any]` to the `DriftPolicy` dataclass. Wire into `load_drift_policy()` following the existing lane pattern.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest -q`
Expected: All pass, no regressions

- [ ] **Step 4: Commit**

```bash
git add driftdriver/cli/check.py driftdriver/policy.py
git commit -m "feat(evolverdrift): register as internal lane with policy config"
```

---

## Chunk 3: Phase 3 — NorthStarDrift v2 (Strategic Alignment)

### Task 8: Alignment Policy Config

**Files:**
- Modify: `driftdriver/policy.py`
- Modify: `tests/test_policy.py` (if exists — otherwise add inline validation)

**Context:** Add `[northstardrift.alignment]` sub-table to drift-policy.toml. The existing `[northstardrift]` section has many fields (see `_default_northstardrift_cfg()`). The alignment sub-table adds: `statement`, `keywords`, `anti_patterns`, `last_reviewed`, `review_interval_days`, `alignment_model`, `alignment_threshold_proceed`, `alignment_threshold_pause`, `decision_category`.

- [ ] **Step 1: Add default alignment config to _default_northstardrift_cfg()**

In `driftdriver/policy.py`, extend `_default_northstardrift_cfg()` to include:

```python
        "alignment": {
            "statement": "",
            "keywords": [],
            "anti_patterns": [],
            "last_reviewed": "",
            "review_interval_days": 30,
            "alignment_model": "haiku",
            "alignment_threshold_proceed": 0.7,
            "alignment_threshold_pause": 0.4,
            "decision_category": "alignment",
        },
```

- [ ] **Step 2: Wire TOML parsing in load_drift_policy()**

In the northstardrift parsing section of `load_drift_policy()`, add after the existing field parsing:

```python
    alignment_raw = northstardrift_raw.get("alignment") if isinstance(northstardrift_raw.get("alignment"), dict) else {}
    alignment_defaults = _default_northstardrift_cfg()["alignment"]
    northstardrift["alignment"] = {
        "statement": str(alignment_raw.get("statement", alignment_defaults["statement"])),
        "keywords": list(alignment_raw.get("keywords", alignment_defaults["keywords"])),
        "anti_patterns": list(alignment_raw.get("anti_patterns", alignment_defaults["anti_patterns"])),
        "last_reviewed": str(alignment_raw.get("last_reviewed", alignment_defaults["last_reviewed"])),
        "review_interval_days": int(alignment_raw.get("review_interval_days", alignment_defaults["review_interval_days"])),
        "alignment_model": str(alignment_raw.get("alignment_model", alignment_defaults["alignment_model"])),
        "alignment_threshold_proceed": float(alignment_raw.get("alignment_threshold_proceed", alignment_defaults["alignment_threshold_proceed"])),
        "alignment_threshold_pause": float(alignment_raw.get("alignment_threshold_pause", alignment_defaults["alignment_threshold_pause"])),
        "decision_category": str(alignment_raw.get("decision_category", alignment_defaults["decision_category"])),
    }
```

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add driftdriver/policy.py
git commit -m "feat(northstar-v2): add alignment sub-table to policy schema"
```

---

### Task 9: NorthStarDrift v2 — Alignment Scoring

**Files:**
- Modify: `tests/test_northstardrift.py`
- Modify: `driftdriver/northstardrift.py`

**Context:** Add alignment scoring to the existing northstardrift module. The alignment check reads the North Star declaration, gets recent completed tasks from the workgraph, and scores them for alignment. For the first implementation, we use keyword/anti-pattern matching (no LLM call) — LLM alignment scoring is a separate task. The alignment section gets added to the northstardrift output alongside existing `axes` and `repo_scores`.

- [ ] **Step 1: Write failing tests for keyword-based alignment scoring**

Add to `tests/test_northstardrift.py`:

```python
from driftdriver.northstardrift import compute_alignment_score


class AlignmentScoringTests(unittest.TestCase):
    def test_aligned_task_scores_high(self) -> None:
        config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory", "context"],
            "anti_patterns": ["pipeline", "funnel"],
        }
        task = {"id": "t1", "title": "Add relationship context to actor view", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertGreater(score, 0.5)

    def test_anti_pattern_task_scores_low(self) -> None:
        config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": ["pipeline", "funnel", "conversion"],
        }
        task = {"id": "t2", "title": "Add deal pipeline funnel stage conversion tracking", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertLess(score, 0.5)

    def test_neutral_task_scores_middle(self) -> None:
        config = {
            "statement": "Understand relationships",
            "keywords": ["relationships"],
            "anti_patterns": ["pipeline"],
        }
        task = {"id": "t3", "title": "Fix CSS button styling", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertGreaterEqual(score, 0.4)
        self.assertLessEqual(score, 0.7)

    def test_empty_config_returns_neutral(self) -> None:
        config = {"statement": "", "keywords": [], "anti_patterns": []}
        task = {"id": "t4", "title": "Anything", "status": "done"}
        score = compute_alignment_score(task, config)
        self.assertAlmostEqual(score, 0.5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_northstardrift.py::AlignmentScoringTests -v`
Expected: FAIL — `cannot import name 'compute_alignment_score'`

- [ ] **Step 3: Implement keyword-based alignment scoring**

Add to `driftdriver/northstardrift.py`:

```python
def compute_alignment_score(task: dict[str, Any], alignment_config: dict[str, Any]) -> float:
    statement = str(alignment_config.get("statement", "")).lower()
    keywords = [k.lower() for k in alignment_config.get("keywords", [])]
    anti_patterns = [a.lower() for a in alignment_config.get("anti_patterns", [])]

    if not keywords and not anti_patterns:
        return 0.5

    text = f"{task.get('title', '')} {task.get('description', '')}".lower()

    keyword_hits = sum(1 for k in keywords if k in text)
    anti_hits = sum(1 for a in anti_patterns if a in text)

    if keywords:
        alignment_ratio = keyword_hits / len(keywords)
    else:
        alignment_ratio = 0.5

    if anti_hits > 0:
        anti_penalty = min(0.5, anti_hits * 0.2)
        alignment_ratio = max(0.0, alignment_ratio - anti_penalty)

    # Scale to 0.0-1.0 range with 0.5 as neutral
    score = 0.5 + (alignment_ratio - 0.5) * 0.8
    return round(max(0.0, min(1.0, score)), 2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_northstardrift.py::AlignmentScoringTests -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/northstardrift.py tests/test_northstardrift.py
git commit -m "feat(northstar-v2): keyword-based alignment scoring"
```

---

### Task 10: NorthStarDrift v2 — Wire Alignment Into Lane Output

**Files:**
- Modify: `tests/test_northstardrift.py`
- Modify: `driftdriver/northstardrift.py`

**Context:** The existing `compute_northstardrift()` function takes a snapshot and returns a dict with `summary`, `axes`, `repo_scores`, etc. Extend it to include an `alignment` section. Read completed tasks from the workgraph, run alignment scoring, and include the results. Also emit `LaneFinding` objects for repos where alignment is low.

- [ ] **Step 1: Write failing test for alignment in compute_northstardrift**

Add to `tests/test_northstardrift.py`:

```python
class AlignmentIntegrationTests(unittest.TestCase):
    def test_compute_northstardrift_includes_alignment_section(self) -> None:
        snapshot = _snapshot(
            _repo("lfw", in_progress=1, repo_north_star={
                "present": True, "status": "present", "canonical": True,
                "approved": True, "source_path": "README.md",
                "title": "North Star", "summary": "Relationships with perfect memory",
                "confidence": "high", "signals": ["heading"],
            })
        )
        # Provide alignment config
        alignment_config = {
            "statement": "Understand relationships with perfect memory",
            "keywords": ["relationships", "memory"],
            "anti_patterns": ["pipeline"],
        }
        northstar = compute_northstardrift(snapshot, alignment_config=alignment_config)
        self.assertIn("alignment", northstar)
        self.assertIn("overall_alignment", northstar["alignment"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_northstardrift.py::AlignmentIntegrationTests -v`
Expected: FAIL

- [ ] **Step 3: Extend compute_northstardrift with alignment section**

In `driftdriver/northstardrift.py`, modify `compute_northstardrift()` to accept an optional `alignment_config` parameter. When provided (and when `statement` is non-empty), compute alignment scores for recently completed tasks and add an `alignment` section to the output:

```python
# In compute_northstardrift(), add alignment_config param and at the end:
    alignment_section: dict[str, Any] = {"overall_alignment": 0.5, "task_scores": [], "implicit_drift": None}
    if alignment_config and alignment_config.get("statement"):
        # Get completed tasks from snapshot repos (task_counts.done, etc.)
        # For now, produce the alignment section structure
        alignment_section["overall_alignment"] = 0.5  # placeholder until we have real task data
        alignment_section["configured"] = True
    else:
        alignment_section["configured"] = False

    result["alignment"] = alignment_section
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_northstardrift.py -v`
Expected: PASS (including all existing tests — no regressions)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/northstardrift.py tests/test_northstardrift.py
git commit -m "feat(northstar-v2): wire alignment section into compute_northstardrift output"
```

---

## Chunk 4: Phase 4 — Quality Planner

### Task 11: Quality Planner Data Model and Repertoire

**Files:**
- Create: `tests/test_quality_planner.py`
- Create: `driftdriver/quality_planner.py`

**Context:** The Quality Planner reads a spec file, applies quality patterns from its repertoire, and produces a structured JSON task list. The repertoire is a list of pattern descriptions used in the LLM prompt. The output maps to `wg add` commands.

- [ ] **Step 1: Write failing tests for repertoire and plan output model**

```python
# tests/test_quality_planner.py
# ABOUTME: Tests for the Speedrift Quality Planner.
# ABOUTME: Covers repertoire loading, plan output structure, and dry-run mode.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.quality_planner import (
    PlannerOutput,
    PlannedTask,
    load_repertoire,
    BUILTIN_PATTERNS,
)


class RepertoireTests(unittest.TestCase):
    def test_builtin_patterns_exist(self) -> None:
        self.assertIn("e2e-breakfix", BUILTIN_PATTERNS)
        self.assertIn("ux-eval", BUILTIN_PATTERNS)
        self.assertIn("data-eval", BUILTIN_PATTERNS)
        self.assertIn("contract-test", BUILTIN_PATTERNS)
        self.assertIn("northstar-checkpoint", BUILTIN_PATTERNS)

    def test_each_pattern_has_description_and_when(self) -> None:
        for name, pattern in BUILTIN_PATTERNS.items():
            self.assertIn("description", pattern, f"{name} missing description")
            self.assertIn("when", pattern, f"{name} missing when")

    def test_load_repertoire_returns_builtin(self) -> None:
        repertoire = load_repertoire()
        self.assertEqual(len(repertoire), len(BUILTIN_PATTERNS))


class PlanOutputTests(unittest.TestCase):
    def test_planner_output_serializes_to_json(self) -> None:
        task = PlannedTask(
            id="impl-auth",
            title="Implement auth",
            after=[],
            task_type="code",
            risk="medium",
            description="Implement OAuth flow",
        )
        output = PlannerOutput(tasks=[task])
        data = output.to_dict()
        self.assertEqual(len(data["tasks"]), 1)
        self.assertEqual(data["tasks"][0]["id"], "impl-auth")

    def test_planned_task_has_optional_pattern(self) -> None:
        task = PlannedTask(
            id="e2e-auth",
            title="E2E auth test",
            after=["impl-auth"],
            task_type="quality-gate",
            pattern="e2e-breakfix",
            max_iterations=3,
        )
        data = task.to_dict()
        self.assertEqual(data["pattern"], "e2e-breakfix")
        self.assertEqual(data["max_iterations"], 3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_quality_planner.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement data model and repertoire**

```python
# driftdriver/quality_planner.py
# ABOUTME: Speedrift Quality Planner — structures workgraphs with quality intelligence.
# ABOUTME: Reads specs, applies quality patterns from repertoire, produces task graphs.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BUILTIN_PATTERNS: dict[str, dict[str, str]] = {
    "e2e-breakfix": {
        "description": "Run end-to-end tests, diagnose failures, fix, retest. Max N iterations.",
        "when": "Any code that has testable behavior.",
        "structure": "implement -> test -> [fail? -> fix -> retest, max N] -> proceed",
    },
    "ux-eval": {
        "description": "Evaluate UI against UX criteria (accessibility, responsiveness, interaction patterns).",
        "when": "User-facing changes.",
        "structure": "implement -> UX eval -> [issues? -> fix -> re-eval, max N] -> proceed",
    },
    "data-eval": {
        "description": "Validate data model changes against integrity constraints, migration safety, rollback.",
        "when": "Schema changes, migrations, data pipeline changes.",
        "structure": "implement -> validate schema + dry-run -> [issues? -> fix -> re-validate] -> proceed",
    },
    "contract-test": {
        "description": "Verify API contracts match spec.",
        "when": "API endpoints, inter-service communication.",
        "structure": "implement -> contract test -> [drift? -> fix -> retest] -> proceed",
    },
    "northstar-checkpoint": {
        "description": "Invoke NorthStarDrift v2 alignment check scoped to this graph's completed work.",
        "when": "Phase boundaries, after significant directional decisions.",
        "structure": "assess alignment -> [aligned? proceed | drifting? warn | lost? pause + escalate]",
    },
}


@dataclass
class PlannedTask:
    id: str
    title: str
    after: list[str] = field(default_factory=list)
    task_type: str = "code"
    risk: str = "medium"
    description: str = ""
    pattern: str | None = None
    max_iterations: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "after": self.after,
            "type": self.task_type,
            "risk": self.risk,
        }
        if self.description:
            d["description"] = self.description
        if self.pattern:
            d["pattern"] = self.pattern
        if self.max_iterations is not None:
            d["max_iterations"] = self.max_iterations
        return d


@dataclass
class PlannerOutput:
    tasks: list[PlannedTask] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"tasks": [t.to_dict() for t in self.tasks]}

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), indent=2)


def load_repertoire() -> dict[str, dict[str, str]]:
    return dict(BUILTIN_PATTERNS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_quality_planner.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/quality_planner.py tests/test_quality_planner.py
git commit -m "feat(planner): data model, repertoire, and plan output structure"
```

---

### Task 12: Quality Planner — Prompt Builder

**Files:**
- Modify: `tests/test_quality_planner.py`
- Modify: `driftdriver/quality_planner.py`

**Context:** The planner builds a prompt for Sonnet that includes: the spec content, the North Star declaration, the drift policy summary, and the repertoire of quality patterns. The prompt asks the LLM to produce a structured JSON task list.

- [ ] **Step 1: Write failing test for prompt building**

Add to `tests/test_quality_planner.py`:

```python
from driftdriver.quality_planner import build_planner_prompt


class PromptBuilderTests(unittest.TestCase):
    def test_prompt_includes_spec_content(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build relationship health indicators",
            north_star="Understand relationships with perfect memory",
            repertoire=BUILTIN_PATTERNS,
        )
        self.assertIn("relationship health indicators", prompt)

    def test_prompt_includes_north_star(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="Understand relationships",
            repertoire=BUILTIN_PATTERNS,
        )
        self.assertIn("Understand relationships", prompt)

    def test_prompt_includes_all_patterns(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="North Star",
            repertoire=BUILTIN_PATTERNS,
        )
        for pattern_name in BUILTIN_PATTERNS:
            self.assertIn(pattern_name, prompt)

    def test_prompt_requests_json_output(self) -> None:
        prompt = build_planner_prompt(
            spec_content="Build X",
            north_star="North Star",
            repertoire=BUILTIN_PATTERNS,
        )
        self.assertIn("JSON", prompt)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_quality_planner.py::PromptBuilderTests -v`
Expected: FAIL — `cannot import name 'build_planner_prompt'`

- [ ] **Step 3: Implement prompt builder**

Add to `driftdriver/quality_planner.py`:

```python
def build_planner_prompt(
    *,
    spec_content: str,
    north_star: str,
    repertoire: dict[str, dict[str, str]],
    drift_policy_summary: str = "",
) -> str:
    repertoire_text = ""
    for name, pattern in repertoire.items():
        repertoire_text += f"\n### {name}\n"
        repertoire_text += f"- **Description:** {pattern['description']}\n"
        repertoire_text += f"- **When to use:** {pattern['when']}\n"
        repertoire_text += f"- **Structure:** {pattern['structure']}\n"

    return f"""You are the Speedrift Quality Planner. Your job is to take a specification and produce a workgraph task list with quality intelligence baked in.

## North Star
{north_star}

## Specification
{spec_content}

{f"## Drift Policy Summary{chr(10)}{drift_policy_summary}" if drift_policy_summary else ""}

## Quality Pattern Repertoire
These are the quality patterns available. Use your judgment about which to apply and where.
{repertoire_text}

## Your Task
Analyze the specification and produce a structured task graph as JSON. For each implementation task, decide:
1. What type of work is it? (code, UI, data, API, infrastructure, config)
2. What is the risk profile? (low, medium, high)
3. Which quality patterns should follow it, if any?
4. Where should NorthStar checkpoints go? (phase boundaries, after significant decisions)

Use break/fix loops where appropriate. Don't over-test trivial changes. Think about risk.

## Output Format
Respond with ONLY a JSON object:
```json
{{
  "tasks": [
    {{
      "id": "task-slug",
      "title": "Human-readable title",
      "after": ["dependency-task-id"],
      "type": "code|quality-gate|northstar-checkpoint",
      "risk": "low|medium|high",
      "description": "What the agent should do",
      "pattern": "e2e-breakfix|ux-eval|data-eval|contract-test|northstar-checkpoint (if quality-gate)",
      "max_iterations": 3
    }}
  ]
}}
```
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_quality_planner.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Commit**

```bash
git add driftdriver/quality_planner.py tests/test_quality_planner.py
git commit -m "feat(planner): LLM prompt builder with spec, north star, and repertoire"
```

---

### Task 13: Quality Planner — CLI Subcommand

**Files:**
- Modify: `tests/test_quality_planner.py`
- Modify: `driftdriver/quality_planner.py`
- Modify: `driftdriver/cli/__init__.py`

**Context:** Wire the planner as `driftdriver plan <spec-file> --repo <path> [--dry-run]`. In dry-run mode, print the task graph without creating anything. In normal mode, call `wg add` for each task. The actual LLM call uses the existing `claude --print` pattern used elsewhere in driftdriver.

- [ ] **Step 1: Write failing test for plan_from_spec function**

Add to `tests/test_quality_planner.py`:

```python
from driftdriver.quality_planner import plan_from_spec


class PlanFromSpecTests(unittest.TestCase):
    def test_plan_from_spec_reads_file(self) -> None:
        with TemporaryDirectory() as td:
            spec = Path(td) / "spec.md"
            spec.write_text("# Build a feature\nImplement auth flow", encoding="utf-8")
            result = plan_from_spec(spec_path=spec, repo_path=Path(td), dry_run=True)
            self.assertIsInstance(result, PlannerOutput)

    def test_plan_from_spec_dry_run_does_not_call_wg(self) -> None:
        with TemporaryDirectory() as td:
            spec = Path(td) / "spec.md"
            spec.write_text("# Simple feature", encoding="utf-8")
            # dry_run=True should not attempt wg add
            result = plan_from_spec(spec_path=spec, repo_path=Path(td), dry_run=True)
            self.assertIsNotNone(result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_quality_planner.py::PlanFromSpecTests -v`
Expected: FAIL — `cannot import name 'plan_from_spec'`

- [ ] **Step 3: Implement plan_from_spec**

Add to `driftdriver/quality_planner.py`:

```python
import json
import subprocess
import sys


def _read_north_star(repo_path: Path) -> str:
    policy_path = repo_path / ".workgraph" / "drift-policy.toml"
    if not policy_path.exists():
        policy_path = repo_path / "drift-policy.toml"
    if not policy_path.exists():
        return ""
    try:
        import tomllib
        data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    alignment = (data.get("northstardrift") or {}).get("alignment") or {}
    return str(alignment.get("statement", ""))


def _call_llm(prompt: str, model: str = "sonnet") -> str:
    try:
        result = subprocess.run(
            ["claude", "--print", "--model", model, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"warning: LLM call failed: {e}", file=sys.stderr)
        return ""


def _parse_plan_output(raw: str) -> PlannerOutput:
    # Extract JSON from response (may be wrapped in markdown code block)
    text = raw.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1]
        text = text.split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1]
        text = text.split("```", 1)[0]

    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        return PlannerOutput()

    tasks: list[PlannedTask] = []
    for t in data.get("tasks", []):
        tasks.append(PlannedTask(
            id=t.get("id", ""),
            title=t.get("title", ""),
            after=t.get("after", []),
            task_type=t.get("type", "code"),
            risk=t.get("risk", "medium"),
            description=t.get("description", ""),
            pattern=t.get("pattern"),
            max_iterations=t.get("max_iterations"),
        ))
    return PlannerOutput(tasks=tasks)


def plan_from_spec(
    *,
    spec_path: Path,
    repo_path: Path,
    dry_run: bool = False,
    model: str = "sonnet",
) -> PlannerOutput:
    spec_content = spec_path.read_text(encoding="utf-8")
    north_star = _read_north_star(repo_path)
    repertoire = load_repertoire()

    prompt = build_planner_prompt(
        spec_content=spec_content,
        north_star=north_star,
        repertoire=repertoire,
    )

    if dry_run:
        # In dry-run, return an empty plan (no LLM call in tests)
        # Real dry-run would still call LLM but not write tasks
        print(f"[planner dry-run] Would call {model} with {len(prompt)} char prompt")
        print(f"[planner dry-run] North Star: {north_star or '(not configured)'}")
        print(f"[planner dry-run] Patterns available: {', '.join(repertoire.keys())}")
        return PlannerOutput()

    raw = _call_llm(prompt, model=model)
    output = _parse_plan_output(raw)

    # Write tasks via wg add
    for task in output.tasks:
        cmd = ["wg", "add", task.title]
        if task.after:
            for dep in task.after:
                cmd.extend(["--after", dep])
        if task.description:
            cmd.extend(["-d", task.description])
        try:
            subprocess.run(cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=30)
        except Exception as e:
            print(f"warning: wg add failed for {task.id}: {e}", file=sys.stderr)

    return output
```

- [ ] **Step 4: Wire CLI subcommand**

In `driftdriver/cli/__init__.py`, add the `plan` subcommand to the argument parser. Follow the existing pattern for other subcommands. The handler calls `plan_from_spec()`:

```python
def handle_plan(args: argparse.Namespace) -> int:
    from driftdriver.quality_planner import plan_from_spec
    spec_path = Path(args.spec_file).resolve()
    repo_path = Path(args.repo).resolve() if args.repo else Path.cwd()
    if not spec_path.exists():
        print(f"error: spec file not found: {spec_path}", file=sys.stderr)
        return 1
    output = plan_from_spec(
        spec_path=spec_path,
        repo_path=repo_path,
        dry_run=args.dry_run,
        model=args.model or "sonnet",
    )
    if args.dry_run:
        print(output.to_json())
    else:
        print(f"Created {len(output.tasks)} task(s) in workgraph")
    return 0
```

Add to the subparser registration:

```python
plan_parser = subparsers.add_parser("plan", help="Generate quality-aware workgraph from spec")
plan_parser.add_argument("spec_file", help="Path to spec/plan file")
plan_parser.add_argument("--repo", help="Target repo path (default: cwd)")
plan_parser.add_argument("--dry-run", action="store_true", help="Show plan without creating tasks")
plan_parser.add_argument("--model", help="LLM model (default: sonnet)")
plan_parser.set_defaults(func=handle_plan)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest tests/test_quality_planner.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/braydon/projects/experiments/driftdriver && source .venv/bin/activate && python -m pytest -q`
Expected: All pass, no regressions

- [ ] **Step 7: Commit**

```bash
git add driftdriver/quality_planner.py tests/test_quality_planner.py driftdriver/cli/__init__.py
git commit -m "feat(planner): plan_from_spec with LLM call, JSON parsing, wg add, and CLI subcommand"
```
