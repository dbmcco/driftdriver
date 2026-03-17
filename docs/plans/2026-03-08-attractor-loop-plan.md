# Attractor Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the three-layer convergence engine — bundles, attractor planner, and attractor loop — that autonomously drives repos toward declared target states.

**Architecture:** Four new modules in driftdriver: `bundles.py` (load/validate/parameterize TOML bundles), `attractors.py` (load attractor definitions with inheritance, evaluate criteria), `attractor_planner.py` (model-mediated bundle selection and composition), `attractor_loop.py` (diagnose→plan→execute→re-diagnose→converge orchestration). Integration via factorydrift tick. Cross-repo sequencing via ecosystem hub dependency graph.

**Tech Stack:** Python 3.10+, dataclasses, TOML (tomllib), existing directive/ExecutorShim infrastructure, existing lane contract

---

## Context for the Implementer

### What this project IS

An autonomous convergence engine. It detects the gap between a repo's current state and a target state (attractor), selects pre-built task graph templates (bundles) to close the gap, executes them, and loops until the gap is zero or it plateaus and escalates.

### What this project is NOT

- NOT a replacement for drift lanes — lanes produce findings, unchanged
- NOT a replacement for the directive system — all task creation flows through existing `guarded_add_drift_task()`
- NOT a replacement for factorydrift — factorydrift triggers the attractor loop

### Key reference files

- **Design doc**: `/Users/braydon/projects/experiments/driftdriver/docs/plans/2026-03-08-attractor-loop-design.md`
- **factorydrift**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/factorydrift.py` — factory cycle, `build_factory_cycle()`, `_plan_repo_actions()`
- **drift_task_guard**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/drift_task_guard.py` — `guarded_add_drift_task()` function
- **directives**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/directives.py` — `Action` enum, `Directive` dataclass, `DirectiveLog`
- **outcome**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/outcome.py` — `DriftOutcome`, `write_outcome()`, `query_outcomes()`
- **lane_contract**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/lane_contract.py` — `LaneResult`, `LaneFinding`
- **check.py**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/cli/check.py` — `_create_followups_from_findings()`, lane subprocess invocation
- **ecosystem hub**: `/Users/braydon/projects/experiments/driftdriver/driftdriver/ecosystem_hub/snapshot.py` — `collect_ecosystem_snapshot()`, repo dependency graph

### Key integration points

- `guarded_add_drift_task(wg_dir, task_id, title, description, lane_tag, actor, extra_tags, after)` → returns `"created"` | `"existing"` | `"capped"` | `"unauthorized"` | `"error"`
- `DriftOutcome(task_id, lane, finding_key, recommendation, action_taken, outcome, evidence, timestamp, actor_id)` — extend with `bundle_id`
- `Directive(source, repo, action, params, reason, authority, priority)` — used by drift_task_guard
- `LaneResult(lane, findings, exit_code, summary)` — output from all lanes
- `LaneFinding(message, severity, file, line, tags)` — individual finding

---

### Task 1: Bundle loader and data types

**Files:**
- Create: `driftdriver/bundles.py`
- Create: `tests/test_bundles.py`
- Create: `driftdriver/bundles/scope-drift.toml` (first built-in bundle)

**Step 1: Write the failing tests**

```python
# ABOUTME: Tests for bundle loading, validation, and parameterization.
# ABOUTME: Covers TOML parsing, template interpolation, and bundle registry.

from __future__ import annotations

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.bundles import (
    TaskTemplate,
    Bundle,
    BundleInstance,
    load_bundle,
    load_bundles_from_dir,
    parameterize_bundle,
)


def test_task_template_fields():
    t = TaskTemplate(
        id_template="{finding_id}-write-test",
        title_template="Write test for {task_title}",
        description_template="Cover {evidence}",
        tags=["drift", "attractor"],
        after=[],
        verify="pytest tests/ -x -q",
    )
    assert t.id_template == "{finding_id}-write-test"
    assert t.verify == "pytest tests/ -x -q"


def test_bundle_fields():
    b = Bundle(
        id="scope-drift",
        finding_kinds=["scope_drift", "scope-drift"],
        description="Fix scope drift",
        tasks=[],
    )
    assert b.id == "scope-drift"
    assert "scope_drift" in b.finding_kinds


def test_load_bundle_from_toml():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "test-bundle.toml"
        p.write_text("""
[bundle]
id = "test-bundle"
finding_kinds = ["test_finding"]
description = "A test bundle"

[[tasks]]
id_template = "{finding_id}-fix"
title_template = "Fix {task_title}"
description_template = "Address {evidence}"
tags = ["drift"]

[[tasks]]
id_template = "{finding_id}-verify"
title_template = "Verify {task_title}"
after = ["{finding_id}-fix"]
verify = "pytest -x"
""")
        bundle = load_bundle(p)
        assert bundle.id == "test-bundle"
        assert len(bundle.tasks) == 2
        assert bundle.tasks[1].after == ["{finding_id}-fix"]
        assert bundle.tasks[1].verify == "pytest -x"


def test_load_bundle_missing_fields():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.toml"
        p.write_text("[bundle]\nid = 'bad'\n")
        with pytest.raises(ValueError, match="finding_kinds"):
            load_bundle(p)


def test_load_bundles_from_dir():
    with TemporaryDirectory() as tmp:
        (Path(tmp) / "a.toml").write_text("""
[bundle]
id = "a"
finding_kinds = ["a_finding"]
description = "Bundle A"

[[tasks]]
id_template = "{finding_id}-fix"
title_template = "Fix"
""")
        (Path(tmp) / "b.toml").write_text("""
[bundle]
id = "b"
finding_kinds = ["b_finding"]
description = "Bundle B"

[[tasks]]
id_template = "{finding_id}-fix"
title_template = "Fix"
""")
        (Path(tmp) / "not-toml.txt").write_text("ignore me")
        bundles = load_bundles_from_dir(Path(tmp))
        assert len(bundles) == 2
        ids = {b.id for b in bundles}
        assert ids == {"a", "b"}


def test_parameterize_bundle():
    bundle = Bundle(
        id="scope-drift",
        finding_kinds=["scope_drift"],
        description="Fix scope drift",
        tasks=[
            TaskTemplate(
                id_template="{finding_id}-update-contract",
                title_template="Update contract for {task_title}",
                description_template="Scope drifted: {evidence}",
                tags=["drift", "attractor"],
            ),
            TaskTemplate(
                id_template="{finding_id}-verify",
                title_template="Verify {task_title}",
                after=["{finding_id}-update-contract"],
                verify="pytest -x",
            ),
        ],
    )
    context = {
        "finding_id": "coredrift-scope-task42",
        "task_title": "Implement auth",
        "evidence": "touched files outside contract scope",
        "file": "src/auth.py",
        "repo_name": "paia-shell",
    }
    instance = parameterize_bundle(bundle, context)
    assert instance.bundle_id == "scope-drift"
    assert instance.finding_id == "coredrift-scope-task42"
    assert len(instance.tasks) == 2
    assert instance.tasks[0]["task_id"] == "coredrift-scope-task42-update-contract"
    assert instance.tasks[0]["title"] == "Update contract for Implement auth"
    assert instance.tasks[1]["after"] == ["coredrift-scope-task42-update-contract"]
    assert instance.confidence == "high"


def test_parameterize_bundle_missing_context_key():
    bundle = Bundle(
        id="test",
        finding_kinds=["x"],
        description="test",
        tasks=[
            TaskTemplate(
                id_template="{finding_id}-fix",
                title_template="Fix {nonexistent_key}",
            ),
        ],
    )
    instance = parameterize_bundle(bundle, {"finding_id": "f1"})
    # Missing keys should be left as-is (not crash)
    assert "{nonexistent_key}" in instance.tasks[0]["title"]
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_bundles.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement bundles.py**

```python
# ABOUTME: Bundle loader, validator, and parameterizer for attractor loop.
# ABOUTME: Loads TOML bundle definitions and interpolates finding context into task templates.

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskTemplate:
    """A parameterized task template within a bundle."""

    id_template: str
    title_template: str
    description_template: str = ""
    tags: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    verify: str = ""


@dataclass
class Bundle:
    """A reusable task graph fragment mapped to finding-kinds."""

    id: str
    finding_kinds: list[str]
    description: str
    tasks: list[TaskTemplate]


@dataclass
class BundleInstance:
    """A parameterized bundle ready for directive emission."""

    bundle_id: str
    finding_id: str
    tasks: list[dict[str, Any]]  # parameterized task dicts
    confidence: str = "high"  # "high" or "low"


def _safe_format(template: str, context: dict[str, str]) -> str:
    """Format a template string, leaving unknown keys as-is."""
    result = template
    for key, value in context.items():
        result = result.replace("{" + key + "}", value)
    return result


def load_bundle(path: Path) -> Bundle:
    """Load a bundle from a TOML file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    b = data.get("bundle", {})
    if "id" not in b:
        raise ValueError(f"Bundle at {path} missing 'id'")
    if "finding_kinds" not in b:
        raise ValueError(f"Bundle at {path} missing 'finding_kinds'")

    tasks = []
    for t in data.get("tasks", []):
        tasks.append(TaskTemplate(
            id_template=t.get("id_template", ""),
            title_template=t.get("title_template", ""),
            description_template=t.get("description_template", ""),
            tags=t.get("tags", []),
            after=t.get("after", []),
            verify=t.get("verify", ""),
        ))

    return Bundle(
        id=b["id"],
        finding_kinds=b["finding_kinds"],
        description=b.get("description", ""),
        tasks=tasks,
    )


def load_bundles_from_dir(directory: Path) -> list[Bundle]:
    """Load all bundles from a directory of TOML files."""
    bundles = []
    for p in sorted(directory.glob("*.toml")):
        bundles.append(load_bundle(p))
    return bundles


def parameterize_bundle(
    bundle: Bundle,
    context: dict[str, str],
) -> BundleInstance:
    """Fill in a bundle's templates with finding-specific context.

    Context keys: finding_id, task_title, evidence, file, repo_name.
    """
    tasks = []
    for t in bundle.tasks:
        task_dict: dict[str, Any] = {
            "task_id": _safe_format(t.id_template, context),
            "title": _safe_format(t.title_template, context),
            "description": _safe_format(t.description_template, context),
            "tags": list(t.tags),
        }
        if t.after:
            task_dict["after"] = [_safe_format(a, context) for a in t.after]
        if t.verify:
            task_dict["verify"] = _safe_format(t.verify, context)
        tasks.append(task_dict)

    return BundleInstance(
        bundle_id=bundle.id,
        finding_id=context.get("finding_id", ""),
        tasks=tasks,
        confidence="high",
    )
```

**Step 4: Create the first built-in bundle**

Create `driftdriver/bundles/scope-drift.toml`:
```toml
[bundle]
id = "scope-drift"
finding_kinds = ["scope_drift", "scope-drift", "hardening_in_core"]
description = "Fix scope drift by updating contract and re-checking"

[[tasks]]
id_template = "{finding_id}-update-contract"
title_template = "Update contract scope for {task_title}"
description_template = "Scope drifted: {evidence}. Update the wg-contract block to reflect actual touched files."
tags = ["drift", "attractor", "contract"]

[[tasks]]
id_template = "{finding_id}-verify-scope"
title_template = "Verify scope alignment for {task_title}"
description_template = "Re-run coredrift to confirm scope drift is resolved."
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-update-contract"]
```

**Step 5: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_bundles.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add driftdriver/bundles.py driftdriver/bundles/ tests/test_bundles.py
git commit -m "feat: add bundle loader — TOML-based reusable task graph fragments"
```

---

### Task 2: Built-in bundle set

**Files:**
- Create: `driftdriver/bundles/missing-intervening-tests.toml`
- Create: `driftdriver/bundles/missing-failure-loopback.toml`
- Create: `driftdriver/bundles/spec-not-updated.toml`
- Create: `driftdriver/bundles/dependency-outdated.toml`
- Create: `driftdriver/bundles/security-finding.toml`
- Create: `driftdriver/bundles/scaffold-workgraph.toml`
- Create: `driftdriver/bundles/scaffold-tests.toml`
- Create: `tests/test_bundles_builtin.py`

**Step 1: Create all bundle TOML files**

`missing-intervening-tests.toml`:
```toml
[bundle]
id = "missing-intervening-tests"
finding_kinds = ["missing-intervening-tests", "missing-test-gate"]
description = "Add integration test gate downstream of implementation task"

[[tasks]]
id_template = "{finding_id}-write-test"
title_template = "Write integration test for {task_title}"
description_template = "Add test coverage verifying {evidence}"
tags = ["drift", "attractor", "testing"]

[[tasks]]
id_template = "{finding_id}-verify-gate"
title_template = "Verify test gate passes for {task_title}"
description_template = "Run test suite, confirm coverage threshold met"
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-write-test"]
verify = "pytest tests/ -x -q"
```

`missing-failure-loopback.toml`:
```toml
[bundle]
id = "missing-failure-loopback"
finding_kinds = ["missing-failure-loopback", "missing_failure_loopback"]
description = "Add failure recovery path for test tasks"

[[tasks]]
id_template = "{finding_id}-add-loopback"
title_template = "Add failure loopback for {task_title}"
description_template = "Test task lacks recovery path: {evidence}. Add a loopback edge so failures route to a fix task."
tags = ["drift", "attractor", "planning"]

[[tasks]]
id_template = "{finding_id}-verify-loopback"
title_template = "Verify loopback exists for {task_title}"
description_template = "Re-run plandrift to confirm failure loopback is wired."
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-add-loopback"]
```

`spec-not-updated.toml`:
```toml
[bundle]
id = "spec-not-updated"
finding_kinds = ["spec_not_updated", "spec-not-updated"]
description = "Update spec to match current implementation"

[[tasks]]
id_template = "{finding_id}-update-spec"
title_template = "Update spec for {task_title}"
description_template = "Spec drift detected: {evidence}. Update the specdrift block to match implementation."
tags = ["drift", "attractor", "spec"]

[[tasks]]
id_template = "{finding_id}-verify-spec"
title_template = "Verify spec alignment for {task_title}"
description_template = "Re-run specdrift to confirm spec is current."
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-update-spec"]
```

`dependency-outdated.toml`:
```toml
[bundle]
id = "dependency-outdated"
finding_kinds = ["dependency_outdated", "lock_not_updated", "deps-outdated"]
description = "Update outdated dependency and verify"

[[tasks]]
id_template = "{finding_id}-update-dep"
title_template = "Update dependency for {task_title}"
description_template = "Dependency outdated: {evidence}. Update and regenerate lockfile."
tags = ["drift", "attractor", "deps"]

[[tasks]]
id_template = "{finding_id}-verify-dep"
title_template = "Verify dependency update for {task_title}"
description_template = "Run tests after dependency update to confirm no regressions."
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-update-dep"]
verify = "pytest tests/ -x -q"
```

`security-finding.toml`:
```toml
[bundle]
id = "security-finding"
finding_kinds = ["security_vulnerability", "security-finding", "secdrift_critical", "secdrift_high"]
description = "Address security finding and re-scan"

[[tasks]]
id_template = "{finding_id}-fix-security"
title_template = "Fix security finding: {task_title}"
description_template = "Security issue: {evidence}. Apply fix and verify."
tags = ["drift", "attractor", "security"]

[[tasks]]
id_template = "{finding_id}-rescan"
title_template = "Re-scan security for {task_title}"
description_template = "Re-run secdrift to confirm finding is resolved."
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-fix-security"]
```

`scaffold-workgraph.toml`:
```toml
[bundle]
id = "scaffold-workgraph"
finding_kinds = ["no_workgraph", "missing_workgraph"]
description = "Initialize workgraph and install driftdriver in a repo"

[[tasks]]
id_template = "{finding_id}-wg-init"
title_template = "Initialize workgraph in {repo_name}"
description_template = "Run wg init to create .workgraph/ directory."
tags = ["drift", "attractor", "onboarding"]

[[tasks]]
id_template = "{finding_id}-install-driftdriver"
title_template = "Install driftdriver in {repo_name}"
description_template = "Run driftdriver install to wire drift lanes."
tags = ["drift", "attractor", "onboarding"]
after = ["{finding_id}-wg-init"]

[[tasks]]
id_template = "{finding_id}-ensure-contracts"
title_template = "Ensure contracts in {repo_name}"
description_template = "Run ensure-contracts --apply to add wg-contract blocks to open tasks."
tags = ["drift", "attractor", "onboarding"]
after = ["{finding_id}-install-driftdriver"]
```

`scaffold-tests.toml`:
```toml
[bundle]
id = "scaffold-tests"
finding_kinds = ["no_tests", "missing_test_directory"]
description = "Create test directory and initial smoke test"

[[tasks]]
id_template = "{finding_id}-create-tests"
title_template = "Create test directory for {repo_name}"
description_template = "Create tests/ directory with initial smoke test to establish testing baseline."
tags = ["drift", "attractor", "testing", "onboarding"]

[[tasks]]
id_template = "{finding_id}-verify-tests"
title_template = "Verify test suite runs in {repo_name}"
description_template = "Run pytest to confirm test infrastructure works."
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-create-tests"]
verify = "pytest tests/ -x -q"
```

**Step 2: Write test that loads all built-in bundles**

```python
# ABOUTME: Tests that all built-in bundle TOML files load without errors.
# ABOUTME: Validates bundle IDs, required fields, and task template structure.

from __future__ import annotations

from pathlib import Path

from driftdriver.bundles import load_bundles_from_dir, parameterize_bundle


BUNDLES_DIR = Path(__file__).resolve().parent.parent / "driftdriver" / "bundles"


def test_all_builtin_bundles_load():
    bundles = load_bundles_from_dir(BUNDLES_DIR)
    assert len(bundles) >= 8  # scope-drift + 7 new ones
    ids = {b.id for b in bundles}
    assert "scope-drift" in ids
    assert "missing-intervening-tests" in ids
    assert "scaffold-workgraph" in ids


def test_all_builtin_bundles_have_tasks():
    bundles = load_bundles_from_dir(BUNDLES_DIR)
    for b in bundles:
        assert len(b.tasks) >= 1, f"Bundle {b.id} has no tasks"
        assert len(b.finding_kinds) >= 1, f"Bundle {b.id} has no finding_kinds"


def test_all_builtin_bundles_parameterize():
    bundles = load_bundles_from_dir(BUNDLES_DIR)
    context = {
        "finding_id": "test-finding-123",
        "task_title": "Test Task",
        "evidence": "something drifted",
        "file": "src/main.py",
        "repo_name": "test-repo",
    }
    for b in bundles:
        instance = parameterize_bundle(b, context)
        assert instance.bundle_id == b.id
        assert len(instance.tasks) == len(b.tasks)
        for task in instance.tasks:
            assert "{finding_id}" not in task["task_id"], f"Unresolved template in {b.id}"
```

**Step 3: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_bundles.py tests/test_bundles_builtin.py -v`
Expected: All pass

**Step 4: Commit**

```bash
git add driftdriver/bundles/ tests/test_bundles_builtin.py
git commit -m "feat: add 8 built-in bundles — scope, tests, loopback, spec, deps, security, scaffolding"
```

---

### Task 3: Attractor loader with inheritance

**Files:**
- Create: `driftdriver/attractors.py`
- Create: `tests/test_attractors.py`
- Create: `driftdriver/attractors/onboarded.toml`
- Create: `driftdriver/attractors/production-ready.toml`
- Create: `driftdriver/attractors/hardened.toml`

**Step 1: Write the failing tests**

```python
# ABOUTME: Tests for attractor loading, inheritance resolution, and criteria evaluation.
# ABOUTME: Covers TOML parsing, extends chain, and gap detection against lane findings.

from __future__ import annotations

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.attractors import (
    AttractorCriterion,
    Attractor,
    load_attractor,
    load_attractors_from_dir,
    resolve_attractor,
    evaluate_attractor,
)
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_attractor_criterion_fields():
    c = AttractorCriterion(lane="coredrift", max_actionable_findings=0)
    assert c.lane == "coredrift"
    assert c.max_actionable_findings == 0


def test_attractor_fields():
    a = Attractor(
        id="test",
        description="A test attractor",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    assert a.id == "test"
    assert len(a.criteria) == 1


def test_load_attractor_from_toml():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.toml"
        p.write_text("""
[attractor]
id = "test"
description = "Test attractor"

[[criteria]]
lane = "coredrift"
max_actionable_findings = 0

[[criteria]]
lane = "plandrift"
max_actionable_findings = 0
require = ["test-gates"]
""")
        a = load_attractor(p)
        assert a.id == "test"
        assert len(a.criteria) == 2
        assert a.criteria[1].require == ["test-gates"]


def test_load_attractor_with_extends():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "child.toml"
        p.write_text("""
[attractor]
id = "child"
extends = "parent"
description = "Child attractor"

[[criteria]]
lane = "secdrift"
max_actionable_findings = 0
""")
        a = load_attractor(p)
        assert a.extends == "parent"


def test_resolve_attractor_inheritance():
    parent = Attractor(
        id="parent",
        description="Parent",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    child = Attractor(
        id="child",
        description="Child",
        extends="parent",
        criteria=[AttractorCriterion(lane="secdrift", max_actionable_findings=0)],
    )
    registry = {"parent": parent, "child": child}
    resolved = resolve_attractor("child", registry)
    assert len(resolved.criteria) == 2  # parent's coredrift + child's secdrift
    lanes = {c.lane for c in resolved.criteria}
    assert lanes == {"coredrift", "secdrift"}


def test_resolve_attractor_no_extends():
    a = Attractor(id="standalone", description="No parent", criteria=[])
    registry = {"standalone": a}
    resolved = resolve_attractor("standalone", registry)
    assert resolved.id == "standalone"


def test_evaluate_attractor_all_met():
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[
            AttractorCriterion(lane="coredrift", max_actionable_findings=0),
            AttractorCriterion(lane="specdrift", max_actionable_findings=0),
        ],
    )
    lane_results = {
        "coredrift": LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean"),
        "specdrift": LaneResult(lane="specdrift", findings=[], exit_code=0, summary="clean"),
    }
    gap = evaluate_attractor(attractor, lane_results)
    assert gap.converged is True
    assert gap.unmet_criteria == []


def test_evaluate_attractor_findings_exceed_threshold():
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[
            AttractorCriterion(lane="coredrift", max_actionable_findings=0),
        ],
    )
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="scope drift", severity="warning")],
            exit_code=3,
            summary="1 finding",
        ),
    }
    gap = evaluate_attractor(attractor, lane_results)
    assert gap.converged is False
    assert len(gap.unmet_criteria) == 1
    assert gap.unmet_criteria[0].lane == "coredrift"
    assert gap.actionable_finding_count == 1


def test_evaluate_attractor_info_not_actionable():
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[
            AttractorCriterion(lane="coredrift", max_actionable_findings=0),
        ],
    )
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="minor note", severity="info")],
            exit_code=0,
            summary="1 info",
        ),
    }
    gap = evaluate_attractor(attractor, lane_results)
    assert gap.converged is True  # info is not actionable
```

**Step 2: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractors.py -v`
Expected: FAIL

**Step 3: Implement attractors.py**

```python
# ABOUTME: Attractor loader with inheritance resolution and criteria evaluation.
# ABOUTME: Defines target states repos converge toward via the attractor loop.

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from driftdriver.lane_contract import LaneResult

ACTIONABLE_SEVERITIES = {"warning", "error", "critical"}


@dataclass
class AttractorCriterion:
    """A single criterion that must be met for an attractor to be satisfied."""

    lane: str = ""
    custom: str = ""
    max_actionable_findings: int = 0
    require: list[str] = field(default_factory=list)
    threshold: float = 0.0


@dataclass
class Attractor:
    """A target state for a repo."""

    id: str
    description: str
    extends: str = ""
    criteria: list[AttractorCriterion] = field(default_factory=list)


@dataclass
class AttractorGap:
    """Result of evaluating an attractor against current state."""

    converged: bool
    unmet_criteria: list[AttractorCriterion]
    actionable_finding_count: int = 0


def load_attractor(path: Path) -> Attractor:
    """Load an attractor from a TOML file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    a = data.get("attractor", {})
    if "id" not in a:
        raise ValueError(f"Attractor at {path} missing 'id'")

    criteria = []
    for c in data.get("criteria", []):
        criteria.append(AttractorCriterion(
            lane=c.get("lane", ""),
            custom=c.get("custom", ""),
            max_actionable_findings=c.get("max_actionable_findings", 0),
            require=c.get("require", []),
            threshold=c.get("threshold", 0.0),
        ))

    return Attractor(
        id=a["id"],
        description=a.get("description", ""),
        extends=a.get("extends", ""),
        criteria=criteria,
    )


def load_attractors_from_dir(directory: Path) -> dict[str, Attractor]:
    """Load all attractors from a directory, keyed by ID."""
    registry: dict[str, Attractor] = {}
    for p in sorted(directory.glob("*.toml")):
        a = load_attractor(p)
        registry[a.id] = a
    return registry


def resolve_attractor(attractor_id: str, registry: dict[str, Attractor]) -> Attractor:
    """Resolve inheritance chain and return a fully-merged attractor."""
    if attractor_id not in registry:
        raise ValueError(f"Unknown attractor: {attractor_id}")

    attractor = registry[attractor_id]
    if not attractor.extends:
        return attractor

    parent = resolve_attractor(attractor.extends, registry)
    merged_criteria = list(parent.criteria) + list(attractor.criteria)

    return Attractor(
        id=attractor.id,
        description=attractor.description,
        criteria=merged_criteria,
    )


def _count_actionable(result: LaneResult) -> int:
    """Count findings with actionable severity."""
    return sum(1 for f in result.findings if f.severity in ACTIONABLE_SEVERITIES)


def evaluate_attractor(
    attractor: Attractor,
    lane_results: dict[str, LaneResult],
) -> AttractorGap:
    """Evaluate whether an attractor's criteria are met by current lane results."""
    unmet: list[AttractorCriterion] = []
    total_actionable = 0

    for criterion in attractor.criteria:
        if criterion.lane:
            result = lane_results.get(criterion.lane)
            if result is None:
                unmet.append(criterion)
                continue
            actionable = _count_actionable(result)
            total_actionable += actionable
            if actionable > criterion.max_actionable_findings:
                unmet.append(criterion)

    return AttractorGap(
        converged=len(unmet) == 0,
        unmet_criteria=unmet,
        actionable_finding_count=total_actionable,
    )
```

**Step 4: Create built-in attractor TOML files**

`driftdriver/attractors/onboarded.toml`:
```toml
[attractor]
id = "onboarded"
description = "Repo has workgraph, drift lanes installed, at least one task"

[[criteria]]
lane = "coredrift"
max_actionable_findings = 5
```

`driftdriver/attractors/production-ready.toml`:
```toml
[attractor]
id = "production-ready"
extends = "onboarded"
description = "Repo is test-covered, drift-clean, deps current, security scanned"

[[criteria]]
lane = "coredrift"
max_actionable_findings = 0

[[criteria]]
lane = "plandrift"
max_actionable_findings = 0
require = ["test-gates", "failure-loopbacks"]

[[criteria]]
lane = "depsdrift"
max_actionable_findings = 0

[[criteria]]
lane = "secdrift"
max_actionable_findings = 0
```

`driftdriver/attractors/hardened.toml`:
```toml
[attractor]
id = "hardened"
extends = "production-ready"
description = "Production-ready plus strict security and comprehensive coverage"

[[criteria]]
lane = "secdrift"
max_actionable_findings = 0

[[criteria]]
custom = "test_coverage_above"
threshold = 90
```

**Step 5: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractors.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add driftdriver/attractors.py driftdriver/attractors/ tests/test_attractors.py
git commit -m "feat: add attractor loader with inheritance and criteria evaluation"
```

---

### Task 4: Extend outcome.py with bundle_id

**Files:**
- Modify: `driftdriver/outcome.py`
- Modify: existing outcome tests (find via `grep -r "test.*outcome" tests/`)

**Step 1: Read the current outcome.py and its tests**

Read `/Users/braydon/projects/experiments/driftdriver/driftdriver/outcome.py` and find existing tests.

**Step 2: Add bundle_id field to DriftOutcome**

Add `bundle_id: str = ""` as the last field on the DriftOutcome dataclass. Since all serialization uses dataclass field iteration, this is backward-compatible — existing records without `bundle_id` will get the default `""` on read.

**Step 3: Write a test for bundle_id round-trip**

Add to the existing outcome test file:

```python
def test_outcome_with_bundle_id():
    outcome = DriftOutcome(
        task_id="task-1",
        lane="coredrift",
        finding_key="scope_drift",
        recommendation="fix scope",
        action_taken="updated contract",
        outcome="resolved",
        evidence=["contract updated"],
        timestamp=datetime.now(timezone.utc),
        actor_id="attractor-loop",
        bundle_id="scope-drift",
    )
    d = outcome.to_dict()
    assert d["bundle_id"] == "scope-drift"

    restored = DriftOutcome.from_dict(d)
    assert restored.bundle_id == "scope-drift"


def test_outcome_without_bundle_id_defaults():
    # Backward compat: old records without bundle_id
    d = {
        "task_id": "task-1",
        "lane": "coredrift",
        "finding_key": "x",
        "recommendation": "y",
        "action_taken": "z",
        "outcome": "resolved",
        "evidence": [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor_id": "human",
    }
    restored = DriftOutcome.from_dict(d)
    assert restored.bundle_id == ""
```

**Step 4: Run all tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/ -x -q`
Expected: 1662+ passed (existing + new)

**Step 5: Commit**

```bash
git add driftdriver/outcome.py tests/
git commit -m "feat: extend DriftOutcome with bundle_id for effectiveness tracking"
```

---

### Task 5: Attractor planner

**Files:**
- Create: `driftdriver/attractor_planner.py`
- Create: `tests/test_attractor_planner.py`

**Step 1: Write the failing tests**

```python
# ABOUTME: Tests for the attractor planner — bundle selection, composition, and plan generation.
# ABOUTME: Covers deterministic planning (no model call) and plan structure validation.

from __future__ import annotations

from driftdriver.attractor_planner import (
    ConvergencePlan,
    EscalationRecord,
    select_bundles_for_findings,
    build_convergence_plan,
)
from driftdriver.bundles import Bundle, TaskTemplate, BundleInstance
from driftdriver.attractors import Attractor, AttractorCriterion, AttractorGap
from driftdriver.lane_contract import LaneFinding, LaneResult


def _make_bundle(id: str, finding_kinds: list[str]) -> Bundle:
    return Bundle(
        id=id,
        finding_kinds=finding_kinds,
        description=f"Bundle {id}",
        tasks=[
            TaskTemplate(id_template="{finding_id}-fix", title_template="Fix {task_title}"),
            TaskTemplate(
                id_template="{finding_id}-verify",
                title_template="Verify {task_title}",
                after=["{finding_id}-fix"],
            ),
        ],
    )


def test_select_bundles_exact_match():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [LaneFinding(message="scope drift", severity="warning", tags=["scope_drift"])]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    assert len(selected) == 1
    assert selected[0].bundle_id == "scope-drift"
    assert selected[0].confidence == "high"


def test_select_bundles_no_match():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [LaneFinding(message="unknown thing", severity="warning", tags=["unknown"])]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    assert len(selected) == 0


def test_select_bundles_dedup():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [
        LaneFinding(message="drift 1", severity="warning", tags=["scope_drift"]),
        LaneFinding(message="drift 2", severity="error", tags=["scope_drift"]),
    ]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    # Same bundle shouldn't be selected twice for same task
    assert len(selected) == 1


def test_select_bundles_skips_info():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [LaneFinding(message="minor", severity="info", tags=["scope_drift"])]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    assert len(selected) == 0


def test_build_convergence_plan():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="scope drift", severity="warning", tags=["scope_drift"])],
            exit_code=3,
            summary="1 finding",
        ),
    }
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    plan = build_convergence_plan(
        attractor=attractor,
        lane_results=lane_results,
        bundles=bundles,
        repo="test-repo",
        pass_number=0,
    )
    assert plan.attractor == "clean"
    assert plan.repo == "test-repo"
    assert len(plan.bundle_instances) == 1
    assert plan.budget_cost == 2  # 2 tasks in the bundle
    assert plan.escalations == []


def test_build_convergence_plan_with_escalation():
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="unknown", severity="error", tags=["novel_finding"])],
            exit_code=3,
            summary="1 finding",
        ),
    }
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    plan = build_convergence_plan(
        attractor=attractor,
        lane_results=lane_results,
        bundles=[],  # no bundles match
        repo="test-repo",
        pass_number=0,
    )
    assert len(plan.bundle_instances) == 0
    assert len(plan.escalations) == 1
    assert plan.escalations[0].reason == "no_matching_bundle"
```

**Step 2: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractor_planner.py -v`
Expected: FAIL

**Step 3: Implement attractor_planner.py**

```python
# ABOUTME: Attractor planner — selects and composes bundles to close the gap between current state and attractor.
# ABOUTME: Deterministic bundle matching with escalation for unmatched findings. Model call deferred to future.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from driftdriver.attractors import Attractor, evaluate_attractor, ACTIONABLE_SEVERITIES
from driftdriver.bundles import Bundle, BundleInstance, parameterize_bundle
from driftdriver.lane_contract import LaneFinding, LaneResult


@dataclass
class EscalationRecord:
    """A finding the planner cannot address with available bundles."""

    repo: str = ""
    attractor: str = ""
    reason: str = ""  # "no_matching_bundle", "low_confidence", "plateau", "budget_exhausted", "timeout"
    remaining_findings: list[dict[str, Any]] = field(default_factory=list)
    passes_completed: int = 0
    bundles_applied: list[str] = field(default_factory=list)
    bundle_outcomes: dict[str, str] = field(default_factory=dict)
    suggested_action: str = ""
    suggested_prompt: str = ""


@dataclass
class ConvergencePlan:
    """Output of the planner — what to do this pass."""

    attractor: str
    repo: str
    pass_number: int
    bundle_instances: list[BundleInstance] = field(default_factory=list)
    cross_bundle_edges: list[tuple[str, str]] = field(default_factory=list)
    budget_cost: int = 0
    escalations: list[EscalationRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def select_bundles_for_findings(
    findings: list[LaneFinding],
    bundles: list[Bundle],
    *,
    lane: str,
    task_id: str,
) -> list[BundleInstance]:
    """Match findings to bundles and return parameterized instances.

    Deduplicates: each bundle is instantiated at most once per lane+task.
    Skips info-severity findings.
    """
    # Build finding-kind to bundle lookup
    kind_to_bundle: dict[str, Bundle] = {}
    for b in bundles:
        for kind in b.finding_kinds:
            kind_to_bundle[kind] = b

    seen_bundle_ids: set[str] = set()
    instances: list[BundleInstance] = []

    for finding in findings:
        if finding.severity not in ACTIONABLE_SEVERITIES:
            continue

        matched_bundle: Bundle | None = None
        for tag in finding.tags:
            if tag in kind_to_bundle:
                matched_bundle = kind_to_bundle[tag]
                break

        if matched_bundle is None:
            continue

        if matched_bundle.id in seen_bundle_ids:
            continue
        seen_bundle_ids.add(matched_bundle.id)

        context = {
            "finding_id": f"{lane}-{matched_bundle.id}-{task_id}",
            "task_title": finding.message[:80],
            "evidence": finding.message,
            "file": finding.file,
            "repo_name": "",
        }
        instance = parameterize_bundle(matched_bundle, context)
        instances.append(instance)

    return instances


def build_convergence_plan(
    *,
    attractor: Attractor,
    lane_results: dict[str, LaneResult],
    bundles: list[Bundle],
    repo: str,
    pass_number: int,
    outcome_history: dict[str, float] | None = None,
) -> ConvergencePlan:
    """Build a convergence plan for one pass.

    Selects bundles for each lane's findings, escalates unmatched findings.
    """
    all_instances: list[BundleInstance] = []
    escalations: list[EscalationRecord] = []
    budget_cost = 0

    gap = evaluate_attractor(attractor, lane_results)
    if gap.converged:
        return ConvergencePlan(
            attractor=attractor.id,
            repo=repo,
            pass_number=pass_number,
        )

    for lane_name, result in lane_results.items():
        actionable = [f for f in result.findings if f.severity in ACTIONABLE_SEVERITIES]
        if not actionable:
            continue

        instances = select_bundles_for_findings(
            actionable, bundles, lane=lane_name, task_id=f"pass{pass_number}",
        )
        all_instances.extend(instances)

        # Find unmatched findings
        matched_tags: set[str] = set()
        for inst in instances:
            bundle = next((b for b in bundles if b.id == inst.bundle_id), None)
            if bundle:
                matched_tags.update(bundle.finding_kinds)

        for finding in actionable:
            if not any(tag in matched_tags for tag in finding.tags):
                escalations.append(EscalationRecord(
                    repo=repo,
                    attractor=attractor.id,
                    reason="no_matching_bundle",
                    remaining_findings=[{"message": finding.message, "severity": finding.severity}],
                    suggested_action=f"Create a bundle for finding kind: {finding.tags}",
                ))

    for inst in all_instances:
        budget_cost += len(inst.tasks)

    return ConvergencePlan(
        attractor=attractor.id,
        repo=repo,
        pass_number=pass_number,
        bundle_instances=all_instances,
        budget_cost=budget_cost,
        escalations=escalations,
    )
```

**Step 4: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractor_planner.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add driftdriver/attractor_planner.py tests/test_attractor_planner.py
git commit -m "feat: add attractor planner — deterministic bundle selection and plan generation"
```

---

### Task 6: Attractor loop orchestrator

**Files:**
- Create: `driftdriver/attractor_loop.py`
- Create: `tests/test_attractor_loop.py`

**Step 1: Write the failing tests**

```python
# ABOUTME: Tests for the attractor loop — convergence detection, pass orchestration, circuit breakers.
# ABOUTME: Uses mocked lane runs and task creation to test loop logic without real wg calls.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
from typing import Any

from driftdriver.attractor_loop import (
    AttractorRun,
    PassResult,
    CircuitBreakers,
    run_attractor_pass,
    check_convergence,
    run_attractor_loop,
)
from driftdriver.attractors import Attractor, AttractorCriterion, AttractorGap
from driftdriver.attractor_planner import ConvergencePlan, EscalationRecord
from driftdriver.bundles import BundleInstance
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_circuit_breakers_defaults():
    cb = CircuitBreakers()
    assert cb.max_passes == 3
    assert cb.plateau_threshold == 2
    assert cb.max_tasks_per_cycle == 30


def test_check_convergence_converged():
    passes = [
        PassResult(pass_number=0, findings_before=3, findings_after=0, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers())
    assert status == "converged"


def test_check_convergence_plateau():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=3, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=2, findings_before=3, findings_after=3, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers(plateau_threshold=2))
    assert status == "plateau"


def test_check_convergence_max_passes():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=4, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=4, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=2, findings_before=3, findings_after=2, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers(max_passes=3))
    assert status == "max_passes"


def test_check_convergence_improving():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=3, findings_after=1, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers())
    assert status == "continue"


def test_attractor_run_fields():
    run = AttractorRun(
        repo="test-repo",
        attractor="production-ready",
        status="converged",
    )
    assert run.repo == "test-repo"
    assert run.passes == []


def test_pass_result_fields():
    pr = PassResult(
        pass_number=0,
        findings_before=5,
        findings_after=2,
        duration_seconds=15.5,
        bundles_applied=["scope-drift"],
        bundle_outcomes={"scope-drift": "resolved"},
    )
    assert pr.findings_before == 5
    assert pr.findings_after == 2
```

**Step 2: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractor_loop.py -v`
Expected: FAIL

**Step 3: Implement attractor_loop.py**

```python
# ABOUTME: Attractor loop — orchestrates diagnose/plan/execute/re-diagnose convergence cycle.
# ABOUTME: Runs per-repo passes with circuit breakers, cross-repo sequencing deferred to caller.

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.attractor_planner import (
    ConvergencePlan,
    EscalationRecord,
    build_convergence_plan,
)
from driftdriver.attractors import (
    Attractor,
    AttractorGap,
    evaluate_attractor,
    load_attractors_from_dir,
    resolve_attractor,
)
from driftdriver.bundles import Bundle, load_bundles_from_dir
from driftdriver.lane_contract import LaneResult


@dataclass
class CircuitBreakers:
    """Limits for the attractor loop."""

    max_passes: int = 3
    max_tasks_per_cycle: int = 30
    max_dispatches_per_cycle: int = 10
    plateau_threshold: int = 2  # consecutive passes with no improvement
    pass_timeout_seconds: int = 1800


@dataclass
class PassResult:
    """Result of a single pass through the loop."""

    pass_number: int
    findings_before: int
    findings_after: int
    duration_seconds: float
    bundles_applied: list[str] = field(default_factory=list)
    bundle_outcomes: dict[str, str] = field(default_factory=dict)
    plan: ConvergencePlan | None = None


@dataclass
class AttractorRun:
    """Full record of an attractor loop execution for one repo."""

    repo: str
    attractor: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    passes: list[PassResult] = field(default_factory=list)
    status: str = "pending"  # pending, converged, plateau, escalated, budget_exhausted, max_passes, timeout
    remaining_findings: list[dict[str, Any]] = field(default_factory=list)
    escalations: list[EscalationRecord] = field(default_factory=list)


def check_convergence(passes: list[PassResult], breakers: CircuitBreakers) -> str:
    """Determine loop status from pass history.

    Returns: 'converged', 'plateau', 'max_passes', or 'continue'.
    """
    if not passes:
        return "continue"

    last = passes[-1]

    # Converged: no actionable findings remain
    if last.findings_after == 0:
        return "converged"

    # Max passes exceeded
    if len(passes) >= breakers.max_passes:
        return "max_passes"

    # Plateau: N consecutive passes with no improvement
    if len(passes) >= breakers.plateau_threshold:
        recent = passes[-breakers.plateau_threshold:]
        counts = [p.findings_after for p in recent]
        if all(c >= counts[0] for c in counts):
            return "plateau"

    return "continue"


def run_attractor_pass(
    *,
    repo: str,
    repo_path: Path,
    attractor: Attractor,
    bundles: list[Bundle],
    pass_number: int,
    diagnose_fn: Any,  # callable(repo_path) -> dict[str, LaneResult]
    execute_fn: Any,  # callable(plan, repo_path) -> dict[str, str]  (bundle_id -> outcome)
) -> PassResult:
    """Execute a single attractor pass: diagnose → plan → execute → re-diagnose."""
    start = time.monotonic()

    # Diagnose
    lane_results = diagnose_fn(repo_path)
    findings_before = sum(
        1 for r in lane_results.values()
        for f in r.findings if f.severity in ("warning", "error", "critical")
    )

    # Plan
    plan = build_convergence_plan(
        attractor=attractor,
        lane_results=lane_results,
        bundles=bundles,
        repo=repo,
        pass_number=pass_number,
    )

    # Execute (if there's anything to do)
    bundle_outcomes: dict[str, str] = {}
    bundles_applied: list[str] = []
    if plan.bundle_instances:
        bundle_outcomes = execute_fn(plan, repo_path)
        bundles_applied = [inst.bundle_id for inst in plan.bundle_instances]

    # Re-diagnose
    lane_results_after = diagnose_fn(repo_path)
    findings_after = sum(
        1 for r in lane_results_after.values()
        for f in r.findings if f.severity in ("warning", "error", "critical")
    )

    elapsed = time.monotonic() - start

    return PassResult(
        pass_number=pass_number,
        findings_before=findings_before,
        findings_after=findings_after,
        duration_seconds=elapsed,
        bundles_applied=bundles_applied,
        bundle_outcomes=bundle_outcomes,
        plan=plan,
    )


def run_attractor_loop(
    *,
    repo: str,
    repo_path: Path,
    attractor: Attractor,
    bundles: list[Bundle],
    breakers: CircuitBreakers | None = None,
    diagnose_fn: Any,
    execute_fn: Any,
) -> AttractorRun:
    """Run the full attractor loop for a single repo until convergence or circuit breaker."""
    if breakers is None:
        breakers = CircuitBreakers()

    run = AttractorRun(repo=repo, attractor=attractor.id)
    tasks_emitted = 0

    for pass_number in range(breakers.max_passes):
        result = run_attractor_pass(
            repo=repo,
            repo_path=repo_path,
            attractor=attractor,
            bundles=bundles,
            pass_number=pass_number,
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
        )
        run.passes.append(result)

        # Track budget
        if result.plan:
            tasks_emitted += result.plan.budget_cost
            run.escalations.extend(result.plan.escalations)

        if tasks_emitted >= breakers.max_tasks_per_cycle:
            run.status = "budget_exhausted"
            break

        status = check_convergence(run.passes, breakers)
        if status == "converged":
            run.status = "converged"
            break
        elif status == "plateau":
            run.status = "plateau"
            break
        elif status == "max_passes":
            run.status = "max_passes"
            break
        # else: continue

    if run.status == "pending":
        run.status = "max_passes"

    # Record remaining findings from last pass
    if run.passes and run.passes[-1].findings_after > 0:
        last_pass = run.passes[-1]
        if last_pass.plan:
            for esc in last_pass.plan.escalations:
                run.remaining_findings.extend(esc.remaining_findings)

    return run


def save_attractor_run(run: AttractorRun, service_dir: Path) -> None:
    """Persist an attractor run to the service directory."""
    attractor_dir = service_dir / "attractor"
    attractor_dir.mkdir(parents=True, exist_ok=True)

    # Current run
    current = attractor_dir / "current-run.json"
    current.write_text(json.dumps(_run_to_dict(run), indent=2), encoding="utf-8")

    # History
    history_dir = attractor_dir / "history"
    history_dir.mkdir(exist_ok=True)
    ts = run.started_at.replace(":", "-").replace("+", "")
    history_file = history_dir / f"{ts}.json"
    history_file.write_text(json.dumps(_run_to_dict(run), indent=2), encoding="utf-8")


def _run_to_dict(run: AttractorRun) -> dict[str, Any]:
    """Serialize an AttractorRun for JSON persistence."""
    return {
        "repo": run.repo,
        "attractor": run.attractor,
        "started_at": run.started_at,
        "status": run.status,
        "passes": [
            {
                "pass_number": p.pass_number,
                "findings_before": p.findings_before,
                "findings_after": p.findings_after,
                "duration_seconds": p.duration_seconds,
                "bundles_applied": p.bundles_applied,
                "bundle_outcomes": p.bundle_outcomes,
            }
            for p in run.passes
        ],
        "remaining_findings": run.remaining_findings,
        "escalation_count": len(run.escalations),
    }
```

**Step 4: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractor_loop.py -v`
Expected: All pass

**Step 5: Run full suite**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/ -x -q`
Expected: All 1662+ pass

**Step 6: Commit**

```bash
git add driftdriver/attractor_loop.py tests/test_attractor_loop.py
git commit -m "feat: add attractor loop — convergence orchestrator with circuit breakers"
```

---

### Task 7: Wire attractor loop into factorydrift

**Files:**
- Modify: `driftdriver/factorydrift.py`
- Create: `tests/test_attractor_factory_integration.py`

**Step 1: Read factorydrift.py to find the integration point**

Read `/Users/braydon/projects/experiments/driftdriver/driftdriver/factorydrift.py`. Find where `build_factory_cycle()` returns its action plan and where `run_as_lane()` assembles the cycle. The attractor loop should be callable from within the factory cycle for repos that have a declared attractor target.

**Step 2: Write integration test**

```python
# ABOUTME: Integration test for attractor loop triggered by factorydrift cycle.
# ABOUTME: Verifies factory cycle calls attractor loop for repos with declared attractors.

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.attractor_loop import AttractorRun, run_attractor_loop, CircuitBreakers
from driftdriver.attractors import Attractor, AttractorCriterion
from driftdriver.bundles import Bundle, TaskTemplate
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_attractor_loop_converges_in_two_passes():
    """Simulate a loop that fixes findings on pass 1 and converges on pass 2."""
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    bundles = [Bundle(
        id="scope-drift",
        finding_kinds=["scope_drift"],
        description="Fix scope",
        tasks=[TaskTemplate(id_template="{finding_id}-fix", title_template="Fix")],
    )]

    call_count = {"diagnose": 0}

    def mock_diagnose(repo_path):
        call_count["diagnose"] += 1
        # First two calls (pass 0: before + after): findings present then resolved
        # Third call (pass 1: before): no findings
        if call_count["diagnose"] <= 1:
            return {
                "coredrift": LaneResult(
                    lane="coredrift",
                    findings=[LaneFinding(message="scope drift", severity="warning", tags=["scope_drift"])],
                    exit_code=3, summary="1 finding",
                ),
            }
        return {"coredrift": LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean")}

    def mock_execute(plan, repo_path):
        return {inst.bundle_id: "resolved" for inst in plan.bundle_instances}

    with TemporaryDirectory() as tmp:
        run = run_attractor_loop(
            repo="test-repo",
            repo_path=Path(tmp),
            attractor=attractor,
            bundles=bundles,
            breakers=CircuitBreakers(max_passes=5),
            diagnose_fn=mock_diagnose,
            execute_fn=mock_execute,
        )

    assert run.status == "converged"
    assert len(run.passes) >= 1


def test_attractor_loop_plateaus():
    """Simulate a loop that can't fix findings — plateaus and escalates."""
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )

    def mock_diagnose(repo_path):
        return {
            "coredrift": LaneResult(
                lane="coredrift",
                findings=[LaneFinding(message="stuck", severity="error", tags=["unknown"])],
                exit_code=3, summary="stuck",
            ),
        }

    def mock_execute(plan, repo_path):
        return {}

    with TemporaryDirectory() as tmp:
        run = run_attractor_loop(
            repo="test-repo",
            repo_path=Path(tmp),
            attractor=attractor,
            bundles=[],
            breakers=CircuitBreakers(max_passes=3, plateau_threshold=2),
            diagnose_fn=mock_diagnose,
            execute_fn=mock_execute,
        )

    assert run.status in ("plateau", "max_passes")
```

**Step 3: Add attractor loop entry point to factorydrift**

This is the wiring step. Read `factorydrift.py` to find the right integration point — likely in `build_factory_cycle()` or as a new action kind that the factory cycle can emit and execute. Add a function like:

```python
def _maybe_run_attractor_loop(
    *,
    repo_name: str,
    repo_path: Path,
    policy: Any,
) -> AttractorRun | None:
    """Run the attractor loop for a repo if it has a declared attractor target."""
    # Read attractor target from drift-policy.toml
    # Load attractors and bundles
    # Build diagnose_fn and execute_fn
    # Call run_attractor_loop
    # Return the run result
```

The exact integration depends on what you find when you read factorydrift.py. The key constraint: the attractor loop must run AFTER the factory cycle's analysis phase and BEFORE the final report.

**Step 4: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_attractor_factory_integration.py tests/ -x -q`
Expected: All pass

**Step 5: Commit**

```bash
git add driftdriver/factorydrift.py tests/test_attractor_factory_integration.py
git commit -m "feat: wire attractor loop into factorydrift cycle"
```

---

### Task 8: CLI entry point and drift-policy.toml support

**Files:**
- Modify: `driftdriver/cli/__init__.py` — add `attractor` subcommand
- Modify: `driftdriver/install.py` — add `[attractor]` section to drift-policy.toml template
- Create: `tests/test_cli_attractor.py`

**Step 1: Read cli/__init__.py to understand subcommand pattern**

Read `/Users/braydon/projects/experiments/driftdriver/driftdriver/cli/__init__.py` to see how existing subcommands (check, factory, doctor, etc.) are registered.

**Step 2: Add `attractor` subcommand**

Add a subcommand that allows:
```bash
# Show current attractor state for this repo
driftdriver attractor status

# Run one attractor pass (diagnose + plan only, dry run)
driftdriver attractor plan

# Run the full loop
driftdriver attractor run

# List available attractors
driftdriver attractor list

# Set repo attractor target
driftdriver attractor set production-ready
```

**Step 3: Add `[attractor]` to drift-policy.toml template**

In install.py, add to the policy template:

```toml
[attractor]
target = "onboarded"

[attractor.circuit_breakers]
max_passes = 3
max_tasks_per_cycle = 30
max_dispatches_per_cycle = 10
plateau_threshold = 2
pass_timeout_seconds = 1800
```

**Step 4: Write tests for CLI**

```python
# ABOUTME: Tests for the attractor CLI subcommand.
# ABOUTME: Covers status, list, and plan commands.

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from driftdriver.cli import main


def test_attractor_list(capsys):
    with patch("sys.argv", ["driftdriver", "attractor", "list"]):
        try:
            main()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    assert "onboarded" in out or "production-ready" in out
```

**Step 5: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && uv run pytest tests/test_cli_attractor.py tests/ -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add driftdriver/cli/__init__.py driftdriver/install.py tests/test_cli_attractor.py
git commit -m "feat: add attractor CLI subcommand and drift-policy.toml support"
```

---

## Task Dependency Graph

```
Task 1 (bundles.py) → Task 2 (built-in bundles)
                                ↓
Task 3 (attractors.py) ← independent of 1-2
                                ↓
Task 4 (outcome.py bundle_id) ← independent
                                ↓
Task 5 (planner) ← Tasks 1, 3
        ↓
Task 6 (loop) ← Task 5
        ↓
Task 7 (factorydrift integration) ← Task 6
Task 8 (CLI + policy) ← Task 6
```

Tasks 1+2 (bundles), 3 (attractors), and 4 (outcome extension) are independent and can run in parallel. Task 5 depends on 1+3. Task 6 depends on 5. Tasks 7 and 8 depend on 6 and can run in parallel.
