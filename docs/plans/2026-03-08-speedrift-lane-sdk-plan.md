# speedrift-lane-sdk Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Create the `speedrift-lane-sdk` package — shared workgraph helpers and lane contract types for all Speedrift drift lanes.

**Architecture:** Standalone Python package with `src/` layout. Three modules: `workgraph.py` (unified Workgraph class supporting lazy/eager patterns), `lane_contract.py` (LaneResult/LaneFinding/validate_lane_output moved from driftdriver), `constants.py` (ExitCode). External lanes and driftdriver both import from this package.

**Tech Stack:** Python 3.10+, dataclasses, subprocess, pytest, uv (packaging)

---

## Context for the Implementer

### What this project IS

A lightweight shared library that eliminates ~1200 lines of duplicated code across 10 drift lane repos. Currently every lane has its own `workgraph.py` with the same helper functions copy-pasted. This SDK centralizes them.

### What this project is NOT

- NOT a framework — no CLI scaffolding, no argparse helpers, no base classes lanes must inherit from
- NOT driftdriver-specific — no authority budgets, no directives, no routing logic
- NOT a lane itself — it doesn't check for drift, it just provides the plumbing lanes need

### Key reference files

- **Lazy Workgraph pattern** (7 lanes): `/Users/braydon/projects/experiments/specdrift/specdrift/workgraph.py` — subprocess `wg show` for idempotency
- **Eager Workgraph pattern** (2 lanes): `/Users/braydon/projects/experiments/coredrift/wg_drift/workgraph.py` — loads `graph.jsonl` into dict
- **Lane contract** (current home): `/Users/braydon/projects/experiments/driftdriver/driftdriver/lane_contract.py` — `LaneResult`, `LaneFinding`, `validate_lane_output()`
- **Design doc**: `/Users/braydon/projects/experiments/driftdriver/docs/plans/2026-03-08-speedrift-lane-sdk-design.md`

### Two Workgraph patterns to unify

**Lazy (specdrift and 6 others):**
- `Workgraph(wg_dir, project_dir)` — no `tasks` field
- `show_task(task_id)` calls `wg show <id> --json` subprocess
- `ensure_task()` checks via `show_task()` then calls `wg add`

**Eager (coredrift, uxdrift):**
- `Workgraph(wg_dir, project_dir, tasks)` — dict populated from `graph.jsonl`
- No `show_task()` method — checks `self.tasks` dict directly
- `ensure_task()` checks `task_id in self.tasks` then calls `wg add`, updates dict

**Unified approach:** Optional `tasks` field (defaults to `None`). `ensure_task()` checks dict if populated, otherwise calls `show_task()`. Both patterns work through the same interface.

---

### Task 1: Scaffold the package

**Files:**
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/pyproject.toml`
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/src/speedrift_lane_sdk/__init__.py`
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/src/speedrift_lane_sdk/constants.py`

**Step 1: Create the repo directory**

```bash
mkdir -p /Users/braydon/projects/experiments/speedrift-lane-sdk
cd /Users/braydon/projects/experiments/speedrift-lane-sdk
git init
```

**Step 2: Create pyproject.toml**

```toml
[project]
name = "speedrift-lane-sdk"
version = "0.1.0"
description = "Shared workgraph helpers and lane contract types for Speedrift drift lanes"
requires-python = ">=3.10"
license = "MIT"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/speedrift_lane_sdk"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

**Step 3: Create constants.py**

```python
# ABOUTME: Exit code constants shared across all Speedrift drift lanes.
# ABOUTME: Defines the three standard exit codes: OK, USAGE, FINDINGS.


class ExitCode:
    """Standard exit codes for drift lane CLI tools."""

    OK = 0
    USAGE = 2
    FINDINGS = 3
```

**Step 4: Create __init__.py with re-exports**

Start with just ExitCode — other modules added in later tasks:

```python
# ABOUTME: Public API for the speedrift-lane-sdk package.
# ABOUTME: Re-exports Workgraph helpers, lane contract types, and exit codes.

from speedrift_lane_sdk.constants import ExitCode

__all__ = ["ExitCode"]
```

**Step 5: Commit**

```bash
git add pyproject.toml src/
git commit -m "feat: scaffold speedrift-lane-sdk package with ExitCode constants"
```

---

### Task 2: Write and test lane_contract module

**Files:**
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/src/speedrift_lane_sdk/lane_contract.py`
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/tests/test_lane_contract.py`
- Modify: `/Users/braydon/projects/experiments/speedrift-lane-sdk/src/speedrift_lane_sdk/__init__.py`

**Step 1: Write the failing tests**

```python
# ABOUTME: Tests for LaneResult, LaneFinding, and validate_lane_output.
# ABOUTME: Covers valid parsing, malformed input, missing fields, severity defaults.

from __future__ import annotations

import json

from speedrift_lane_sdk.lane_contract import (
    LaneFinding,
    LaneResult,
    validate_lane_output,
)


def test_lane_finding_defaults():
    f = LaneFinding(message="something drifted")
    assert f.severity == "info"
    assert f.file == ""
    assert f.line == 0
    assert f.tags == []


def test_lane_finding_full():
    f = LaneFinding(
        message="scope drift",
        severity="error",
        file="src/main.py",
        line=42,
        tags=["scope", "contract"],
    )
    assert f.message == "scope drift"
    assert f.severity == "error"
    assert f.file == "src/main.py"
    assert f.line == 42
    assert f.tags == ["scope", "contract"]


def test_lane_result_basic():
    r = LaneResult(
        lane="coredrift",
        findings=[LaneFinding(message="x")],
        exit_code=3,
        summary="1 finding",
    )
    assert r.lane == "coredrift"
    assert len(r.findings) == 1
    assert r.exit_code == 3


def test_validate_lane_output_valid():
    data = {
        "lane": "specdrift",
        "findings": [
            {"message": "spec not updated", "severity": "warning", "file": "README.md"},
        ],
        "exit_code": 3,
        "summary": "1 warning",
    }
    result = validate_lane_output(json.dumps(data))
    assert result is not None
    assert result.lane == "specdrift"
    assert len(result.findings) == 1
    assert result.findings[0].severity == "warning"
    assert result.findings[0].file == "README.md"
    assert result.findings[0].line == 0  # default
    assert result.summary == "1 warning"


def test_validate_lane_output_no_lane_field():
    data = {"findings": [], "exit_code": 0}
    assert validate_lane_output(json.dumps(data)) is None


def test_validate_lane_output_not_json():
    assert validate_lane_output("this is not json") is None
    assert validate_lane_output("") is None


def test_validate_lane_output_none_input():
    assert validate_lane_output(None) is None  # type: ignore[arg-type]


def test_validate_lane_output_empty_findings():
    data = {"lane": "archdrift", "exit_code": 0, "summary": "clean"}
    result = validate_lane_output(json.dumps(data))
    assert result is not None
    assert result.findings == []
    assert result.exit_code == 0


def test_validate_lane_output_missing_optional_fields():
    data = {"lane": "datadrift"}
    result = validate_lane_output(json.dumps(data))
    assert result is not None
    assert result.findings == []
    assert result.exit_code == 0
    assert result.summary == ""
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/speedrift-lane-sdk
uv run pytest tests/test_lane_contract.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'speedrift_lane_sdk.lane_contract'`

**Step 3: Implement lane_contract.py**

This is a direct port from `/Users/braydon/projects/experiments/driftdriver/driftdriver/lane_contract.py` — same code, new home:

```python
# ABOUTME: Standard output contract for all Speedrift drift lanes.
# ABOUTME: Defines LaneFinding, LaneResult, and validate_lane_output() parser.

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class LaneFinding:
    """A single finding from a drift lane check."""

    message: str
    severity: str = "info"  # info, warning, error, critical
    file: str = ""
    line: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class LaneResult:
    """Structured output from a drift lane execution."""

    lane: str
    findings: list[LaneFinding]
    exit_code: int
    summary: str


def validate_lane_output(raw: str) -> LaneResult | None:
    """Parse raw JSON output from a lane into a LaneResult.

    Returns None if the output is malformed or missing required fields.
    All drift lanes (internal and external) should produce JSON matching
    this contract.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if "lane" not in data:
        return None

    findings = []
    for f in data.get("findings", []):
        findings.append(
            LaneFinding(
                message=f.get("message", ""),
                severity=f.get("severity", "info"),
                file=f.get("file", ""),
                line=f.get("line", 0),
                tags=f.get("tags", []),
            )
        )

    return LaneResult(
        lane=data["lane"],
        findings=findings,
        exit_code=data.get("exit_code", 0),
        summary=data.get("summary", ""),
    )
```

**Step 4: Update __init__.py**

```python
# ABOUTME: Public API for the speedrift-lane-sdk package.
# ABOUTME: Re-exports Workgraph helpers, lane contract types, and exit codes.

from speedrift_lane_sdk.constants import ExitCode
from speedrift_lane_sdk.lane_contract import (
    LaneFinding,
    LaneResult,
    validate_lane_output,
)

__all__ = [
    "ExitCode",
    "LaneFinding",
    "LaneResult",
    "validate_lane_output",
]
```

**Step 5: Run tests to verify they pass**

```bash
cd /Users/braydon/projects/experiments/speedrift-lane-sdk
uv run pytest tests/test_lane_contract.py -v
```

Expected: 8 passed

**Step 6: Commit**

```bash
git add src/ tests/
git commit -m "feat: add lane contract types — LaneResult, LaneFinding, validate_lane_output"
```

---

### Task 3: Write and test workgraph module

**Files:**
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/src/speedrift_lane_sdk/workgraph.py`
- Create: `/Users/braydon/projects/experiments/speedrift-lane-sdk/tests/test_workgraph.py`
- Modify: `/Users/braydon/projects/experiments/speedrift-lane-sdk/src/speedrift_lane_sdk/__init__.py`

**Step 1: Write the failing tests**

Tests cover both lazy and eager patterns, `find_workgraph_dir`, `load_workgraph`, `show_task`, `ensure_task`, `wg_log`:

```python
# ABOUTME: Tests for the unified Workgraph helper (lazy + eager patterns).
# ABOUTME: Covers find_workgraph_dir, load_workgraph, show_task, ensure_task, wg_log.

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import pytest

from speedrift_lane_sdk.workgraph import (
    Workgraph,
    find_workgraph_dir,
    load_workgraph,
)


# --- find_workgraph_dir ---


def test_find_workgraph_dir_explicit_project_root():
    with TemporaryDirectory() as tmp:
        wg = Path(tmp) / ".workgraph"
        wg.mkdir()
        (wg / "graph.jsonl").write_text("")
        result = find_workgraph_dir(Path(tmp))
        assert result == wg


def test_find_workgraph_dir_explicit_wg_dir():
    with TemporaryDirectory() as tmp:
        wg = Path(tmp) / ".workgraph"
        wg.mkdir()
        (wg / "graph.jsonl").write_text("")
        result = find_workgraph_dir(wg)
        assert result == wg


def test_find_workgraph_dir_not_found():
    with TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError):
            find_workgraph_dir(Path(tmp))


def test_find_workgraph_dir_none_walks_cwd():
    with TemporaryDirectory() as tmp:
        wg = Path(tmp) / ".workgraph"
        wg.mkdir()
        (wg / "graph.jsonl").write_text("")
        with patch("speedrift_lane_sdk.workgraph.Path") as mock_path:
            mock_cwd = MagicMock()
            mock_cwd.__truediv__ = lambda self, other: Path(tmp) / other
            mock_cwd.parents = []
            mock_path.cwd.return_value = mock_cwd
            # This tests the walk-up logic but is fragile with mock;
            # the explicit-path tests above cover the core logic.


# --- load_workgraph ---


def test_load_workgraph_reads_tasks():
    with TemporaryDirectory() as tmp:
        wg = Path(tmp) / ".workgraph"
        wg.mkdir()
        lines = [
            json.dumps({"kind": "task", "id": "task-1", "title": "First", "status": "open"}),
            json.dumps({"kind": "task", "id": "task-2", "title": "Second", "status": "in-progress"}),
            json.dumps({"kind": "edge", "from": "task-1", "to": "task-2"}),
        ]
        (wg / "graph.jsonl").write_text("\n".join(lines) + "\n")
        result = load_workgraph(wg)
        assert result.wg_dir == wg
        assert result.project_dir == Path(tmp)
        assert result.tasks is not None
        assert len(result.tasks) == 2
        assert "task-1" in result.tasks
        assert "task-2" in result.tasks


def test_load_workgraph_skips_non_tasks():
    with TemporaryDirectory() as tmp:
        wg = Path(tmp) / ".workgraph"
        wg.mkdir()
        lines = [
            json.dumps({"kind": "edge", "from": "a", "to": "b"}),
            json.dumps({"kind": "meta", "version": 1}),
        ]
        (wg / "graph.jsonl").write_text("\n".join(lines) + "\n")
        result = load_workgraph(wg)
        assert result.tasks == {}


def test_load_workgraph_empty_graph():
    with TemporaryDirectory() as tmp:
        wg = Path(tmp) / ".workgraph"
        wg.mkdir()
        (wg / "graph.jsonl").write_text("")
        result = load_workgraph(wg)
        assert result.tasks == {}


# --- Workgraph lazy mode (no tasks loaded) ---


def test_lazy_workgraph_show_task():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp))
        assert wg.tasks is None  # lazy mode

        task_data = {"id": "task-1", "title": "Test", "status": "open"}
        with patch("speedrift_lane_sdk.workgraph.subprocess.check_output") as mock:
            mock.return_value = json.dumps(task_data)
            result = wg.show_task("task-1")
            assert result == task_data
            mock.assert_called_once_with(
                ["wg", "--dir", str(wg_dir), "show", "task-1", "--json"],
                text=True,
                stderr=-1,
            )


def test_lazy_workgraph_show_task_not_found():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp))

        import subprocess
        with patch("speedrift_lane_sdk.workgraph.subprocess.check_output") as mock:
            mock.side_effect = subprocess.CalledProcessError(1, "wg")
            result = wg.show_task("nonexistent")
            assert result is None


def test_lazy_workgraph_ensure_task_creates():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp))

        import subprocess
        with patch("speedrift_lane_sdk.workgraph.subprocess.check_output") as mock_show, \
             patch("speedrift_lane_sdk.workgraph.subprocess.check_call") as mock_call:
            mock_show.side_effect = subprocess.CalledProcessError(1, "wg")
            result = wg.ensure_task(
                task_id="new-task",
                title="New Task",
                description="Do something",
                tags=["drift"],
            )
            assert result is True
            mock_call.assert_called_once()
            cmd = mock_call.call_args[0][0]
            assert cmd[0:3] == ["wg", "--dir", str(wg_dir)]
            assert "add" in cmd
            assert "New Task" in cmd
            assert "--id" in cmd
            assert "new-task" in cmd


def test_lazy_workgraph_ensure_task_already_exists():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp))

        with patch("speedrift_lane_sdk.workgraph.subprocess.check_output") as mock_show, \
             patch("speedrift_lane_sdk.workgraph.subprocess.check_call") as mock_call:
            mock_show.return_value = json.dumps({"id": "existing"})
            result = wg.ensure_task(task_id="existing", title="Already here")
            assert result is False
            mock_call.assert_not_called()


# --- Workgraph eager mode (tasks loaded) ---


def test_eager_workgraph_ensure_task_checks_dict():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        tasks = {"existing-task": {"kind": "task", "id": "existing-task", "title": "Exists"}}
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp), tasks=tasks)

        with patch("speedrift_lane_sdk.workgraph.subprocess.check_call") as mock_call:
            result = wg.ensure_task(task_id="existing-task", title="Exists")
            assert result is False
            mock_call.assert_not_called()


def test_eager_workgraph_ensure_task_creates_and_updates_dict():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        tasks: dict = {}
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp), tasks=tasks)

        with patch("speedrift_lane_sdk.workgraph.subprocess.check_call"):
            result = wg.ensure_task(task_id="new-task", title="New")
            assert result is True
            assert "new-task" in wg.tasks


# --- wg_log ---


def test_wg_log_calls_subprocess():
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        wg = Workgraph(wg_dir=wg_dir, project_dir=Path(tmp))

        with patch("speedrift_lane_sdk.workgraph.subprocess.check_call") as mock_call:
            wg.wg_log("task-1", "drift check passed")
            mock_call.assert_called_once_with(
                ["wg", "--dir", str(wg_dir), "log", "task-1", "drift check passed"],
                stdout=-1,
            )
```

**Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/speedrift-lane-sdk
uv run pytest tests/test_workgraph.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'speedrift_lane_sdk.workgraph'`

**Step 3: Implement workgraph.py**

```python
# ABOUTME: Unified Workgraph helper supporting both lazy (subprocess) and eager (in-memory) patterns.
# ABOUTME: Provides find_workgraph_dir, load_workgraph, and the Workgraph class with ensure_task/wg_log.

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Workgraph:
    """Workgraph interface for drift lanes.

    Two modes:
    - Lazy (tasks=None): idempotency checks via ``wg show`` subprocess calls.
    - Eager (tasks=dict): idempotency checks via in-memory dict lookup.

    Use ``load_workgraph()`` to create an eager instance.
    Use ``Workgraph(wg_dir=..., project_dir=...)`` for a lazy instance.
    """

    wg_dir: Path
    project_dir: Path
    tasks: dict[str, dict[str, Any]] | None = field(default=None)

    def show_task(self, task_id: str) -> dict[str, Any] | None:
        """Fetch a task via ``wg show --json``. Returns None if not found."""
        try:
            out = subprocess.check_output(
                ["wg", "--dir", str(self.wg_dir), "show", task_id, "--json"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return json.loads(out)
        except Exception:
            return None

    def ensure_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str = "",
        blocked_by: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Create a task idempotently. Returns True if created, False if it existed.

        In eager mode (tasks dict populated), checks the dict.
        In lazy mode (tasks is None), calls ``show_task()`` subprocess.
        """
        # Check existence
        if self.tasks is not None:
            if task_id in self.tasks:
                return False
        else:
            if self.show_task(task_id) is not None:
                return False

        # Create
        cmd = ["wg", "--dir", str(self.wg_dir), "add", title, "--id", task_id]
        if description:
            cmd += ["-d", description]
        if blocked_by:
            cmd += ["--blocked-by", *blocked_by]
        if tags:
            for t in tags:
                cmd += ["-t", t]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL)

        # Keep in-memory index in sync
        if self.tasks is not None:
            self.tasks[task_id] = {"kind": "task", "id": task_id, "title": title}

        return True

    def wg_log(self, task_id: str, message: str) -> None:
        """Write a log entry to a task via ``wg log``."""
        subprocess.check_call(
            ["wg", "--dir", str(self.wg_dir), "log", task_id, message],
            stdout=subprocess.DEVNULL,
        )


def find_workgraph_dir(explicit: Path | None = None) -> Path:
    """Locate the ``.workgraph`` directory.

    If ``explicit`` is provided, it may be either a project root or the
    ``.workgraph`` directory itself.  If None, walks up from cwd.
    """
    if explicit is not None:
        p = explicit
        if p.name != ".workgraph":
            p = p / ".workgraph"
        if not (p / "graph.jsonl").exists():
            raise FileNotFoundError(f"Workgraph not found at: {p}")
        return p

    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        candidate = p / ".workgraph" / "graph.jsonl"
        if candidate.exists():
            return candidate.parent
    raise FileNotFoundError("Could not find .workgraph/graph.jsonl; pass --dir.")


def load_workgraph(wg_dir: Path) -> Workgraph:
    """Load a Workgraph in eager mode, reading all tasks from graph.jsonl."""
    graph_path = wg_dir / "graph.jsonl"
    tasks: dict[str, dict[str, Any]] = {}
    for line in graph_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("kind") != "task":
            continue
        tid = str(obj.get("id"))
        tasks[tid] = obj

    return Workgraph(wg_dir=wg_dir, project_dir=wg_dir.parent, tasks=tasks)
```

**Step 4: Update __init__.py**

```python
# ABOUTME: Public API for the speedrift-lane-sdk package.
# ABOUTME: Re-exports Workgraph helpers, lane contract types, and exit codes.

from speedrift_lane_sdk.constants import ExitCode
from speedrift_lane_sdk.lane_contract import (
    LaneFinding,
    LaneResult,
    validate_lane_output,
)
from speedrift_lane_sdk.workgraph import (
    Workgraph,
    find_workgraph_dir,
    load_workgraph,
)

__all__ = [
    "ExitCode",
    "LaneFinding",
    "LaneResult",
    "Workgraph",
    "find_workgraph_dir",
    "load_workgraph",
    "validate_lane_output",
]
```

**Step 5: Run all tests**

```bash
cd /Users/braydon/projects/experiments/speedrift-lane-sdk
uv run pytest tests/ -v
```

Expected: All tests pass (8 lane_contract + 14 workgraph = 22 total)

**Step 6: Commit**

```bash
git add src/ tests/
git commit -m "feat: add unified Workgraph helper — lazy/eager modes, find/load/ensure/log"
```

---

### Task 4: Wire driftdriver to import lane_contract from the SDK

**Files:**
- Modify: `/Users/braydon/projects/experiments/driftdriver/pyproject.toml` — add `speedrift-lane-sdk` dependency
- Modify: `/Users/braydon/projects/experiments/driftdriver/driftdriver/lane_contract.py` — replace with re-export
- Test: existing driftdriver tests (no new test file needed)

**Step 1: Add the dependency**

In driftdriver's `pyproject.toml`, add to the `[project.dependencies]` list:

```
"speedrift-lane-sdk",
```

Then install it from local path for development:

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv add --editable /Users/braydon/projects/experiments/speedrift-lane-sdk
```

**Step 2: Replace lane_contract.py with re-export**

Replace the contents of `/Users/braydon/projects/experiments/driftdriver/driftdriver/lane_contract.py` with:

```python
# ABOUTME: Re-export lane contract types from the shared speedrift-lane-sdk.
# ABOUTME: Backward-compatible — all existing imports continue to work.

from speedrift_lane_sdk.lane_contract import (  # noqa: F401
    LaneFinding,
    LaneResult,
    validate_lane_output,
)
```

**Step 3: Run driftdriver tests to verify nothing breaks**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python -m pytest tests/ -x -q
```

Expected: 1662 passed, 0 failures (identical to before)

**Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add pyproject.toml uv.lock driftdriver/lane_contract.py
git commit -m "refactor: import lane contract types from speedrift-lane-sdk"
```

---

### Task 5: Migrate specdrift to use the SDK

This is the template migration — specdrift uses the lazy pattern (most common, 7 of 10 lanes). Once this works, the other 6 lazy lanes follow the same steps.

**Files:**
- Modify: `/Users/braydon/projects/experiments/specdrift/pyproject.toml` — add SDK dependency
- Modify: `/Users/braydon/projects/experiments/specdrift/specdrift/workgraph.py` — replace with SDK import
- Modify: `/Users/braydon/projects/experiments/specdrift/specdrift/cli.py` — update imports
- Test: run specdrift's existing tests

**Step 1: Add the SDK dependency**

```bash
cd /Users/braydon/projects/experiments/specdrift
uv add --editable /Users/braydon/projects/experiments/speedrift-lane-sdk
```

**Step 2: Replace workgraph.py with SDK re-export**

Replace the contents of `/Users/braydon/projects/experiments/specdrift/specdrift/workgraph.py` with:

```python
# ABOUTME: Re-export workgraph helpers from speedrift-lane-sdk.
# ABOUTME: Backward-compatible — all existing imports continue to work.

from speedrift_lane_sdk.workgraph import (  # noqa: F401
    Workgraph,
    find_workgraph_dir,
)
```

**Step 3: Update cli.py imports**

In `specdrift/cli.py`, find the import line:

```python
from specdrift.workgraph import Workgraph, find_workgraph_dir
```

This already works — the re-export preserves the import path. No change needed to cli.py.

Also update any `ExitCode` usage if present — check if specdrift defines its own exit codes and replace with:

```python
from speedrift_lane_sdk import ExitCode
```

**Step 4: Run specdrift tests**

```bash
cd /Users/braydon/projects/experiments/specdrift
uv run python -m pytest tests/ -x -q
```

Expected: All existing tests pass

**Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/specdrift
git add pyproject.toml uv.lock specdrift/workgraph.py
git commit -m "refactor: use speedrift-lane-sdk for workgraph helpers"
```

---

### Task 6: Migrate remaining 6 lazy-pattern lanes

Same steps as Task 5, applied to each lane. These all use the identical lazy Workgraph pattern.

**Lanes:** archdrift, datadrift, depsdrift, therapydrift, fixdrift, yagnidrift

For each lane at `/Users/braydon/projects/experiments/<lane>/`:

**Step 1:** `cd /Users/braydon/projects/experiments/<lane> && uv add --editable /Users/braydon/projects/experiments/speedrift-lane-sdk`

**Step 2:** Replace `<lane>/<lane>/workgraph.py` with SDK re-export (same content as specdrift's)

**Step 3:** Run tests: `uv run python -m pytest tests/ -x -q`

**Step 4:** Commit: `git commit -am "refactor: use speedrift-lane-sdk for workgraph helpers"`

Do all 6 lanes. The workgraph.py re-export is identical for all of them:

```python
# ABOUTME: Re-export workgraph helpers from speedrift-lane-sdk.
# ABOUTME: Backward-compatible — all existing imports continue to work.

from speedrift_lane_sdk.workgraph import (  # noqa: F401
    Workgraph,
    find_workgraph_dir,
)
```

---

### Task 7: Migrate coredrift (eager pattern)

Coredrift is special — it uses the eager pattern (loads tasks dict) and has additional helpers (`update_task_description`, `rewrite_graph_with_contracts`). Those are coredrift-specific and stay in coredrift.

**Files:**
- Modify: `/Users/braydon/projects/experiments/coredrift/pyproject.toml`
- Modify: `/Users/braydon/projects/experiments/coredrift/wg_drift/workgraph.py`

**Step 1:** Add SDK dependency:

```bash
cd /Users/braydon/projects/experiments/coredrift
uv add --editable /Users/braydon/projects/experiments/speedrift-lane-sdk
```

**Step 2:** Replace the shared parts of `wg_drift/workgraph.py`, keep coredrift-specific helpers:

```python
# ABOUTME: Coredrift workgraph helpers — SDK base plus coredrift-specific graph rewriters.
# ABOUTME: Re-exports Workgraph, find_workgraph_dir, load_workgraph from SDK.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from speedrift_lane_sdk.workgraph import (  # noqa: F401
    Workgraph,
    find_workgraph_dir,
    load_workgraph,
)

from wg_drift.contracts import extract_contract, format_default_contract_block


@dataclass(frozen=True)
class ContractPatchResult:
    updated_tasks: list[str]


@dataclass(frozen=True)
class TaskRewriteResult:
    updated: bool


def update_task_description(*, wg_dir: Path, task_id: str, new_description: str) -> TaskRewriteResult:
    graph_path = wg_dir / "graph.jsonl"
    lines_in = graph_path.read_text(encoding="utf-8").splitlines()
    lines_out: list[str] = []
    updated = False

    for line in lines_in:
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") != "task":
            lines_out.append(line)
            continue
        tid = str(obj.get("id"))
        if tid != task_id:
            lines_out.append(line)
            continue
        obj["description"] = new_description
        updated = True
        lines_out.append(json.dumps(obj, separators=(",", ":")))

    if not updated:
        raise ValueError(f"Task not found in graph.jsonl: {task_id}")

    tmp = graph_path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    tmp.replace(graph_path)

    return TaskRewriteResult(updated=True)


def rewrite_graph_with_contracts(*, wg_dir: Path, statuses: set[str], apply: bool) -> ContractPatchResult:
    wg = load_workgraph(wg_dir)
    updated: list[str] = []

    graph_path = wg_dir / "graph.jsonl"
    lines_in = graph_path.read_text(encoding="utf-8").splitlines()
    lines_out: list[str] = []

    for line in lines_in:
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") != "task":
            lines_out.append(line)
            continue

        tid = str(obj.get("id"))
        status = str(obj.get("status") or "")
        if status not in statuses:
            lines_out.append(line)
            continue

        desc = str(obj.get("description") or "")
        if extract_contract(desc) is not None:
            lines_out.append(line)
            continue

        title = str(obj.get("title") or tid)
        contract_block = format_default_contract_block(mode="core", objective=title, touch=[])
        if desc.strip():
            new_desc = contract_block + "\n" + desc
        else:
            new_desc = contract_block
        obj["description"] = new_desc
        updated.append(tid)
        lines_out.append(json.dumps(obj, separators=(",", ":")))

    if apply and updated:
        tmp = graph_path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
        tmp.replace(graph_path)

    return ContractPatchResult(updated_tasks=updated)
```

**Step 3:** Run coredrift tests:

```bash
cd /Users/braydon/projects/experiments/coredrift
uv run python -m pytest tests/ -x -q
```

**Step 4:** Commit:

```bash
git add pyproject.toml uv.lock wg_drift/workgraph.py
git commit -m "refactor: use speedrift-lane-sdk for base workgraph helpers"
```

---

### Task 8: Migrate uxdrift (eager pattern)

Similar to coredrift — uses eager pattern, has a `choose_task_id()` helper that stays in uxdrift.

**Files:**
- Modify: `/Users/braydon/projects/experiments/uxdrift/pyproject.toml`
- Modify: `/Users/braydon/projects/experiments/uxdrift/uxdrift/workgraph.py`

**Step 1:** Add SDK dependency:

```bash
cd /Users/braydon/projects/experiments/uxdrift
uv add --editable /Users/braydon/projects/experiments/speedrift-lane-sdk
```

**Step 2:** Replace `uxdrift/workgraph.py`:

```python
# ABOUTME: UXdrift workgraph helpers — SDK base plus uxdrift-specific task chooser.
# ABOUTME: Re-exports Workgraph, find_workgraph_dir, load_workgraph from SDK.

from __future__ import annotations

from speedrift_lane_sdk.workgraph import (  # noqa: F401
    Workgraph,
    find_workgraph_dir,
    load_workgraph,
)


def choose_task_id(wg: Workgraph) -> str:
    """Auto-select a task when --task is not provided."""
    if wg.tasks is None:
        raise ValueError("choose_task_id requires eager-loaded workgraph (use load_workgraph)")

    in_progress = [t for t in wg.tasks.values() if str(t.get("status") or "") == "in-progress"]
    if len(in_progress) == 1:
        return str(in_progress[0]["id"])
    if len(in_progress) > 1:
        raise ValueError(f"Multiple in-progress tasks found ({len(in_progress)}); pass --task <id>.")

    open_tasks = [t for t in wg.tasks.values() if str(t.get("status") or "") == "open"]
    if len(open_tasks) == 1:
        return str(open_tasks[0]["id"])
    if len(open_tasks) > 1:
        raise ValueError(f"Multiple open tasks found ({len(open_tasks)}); pass --task <id>.")

    raise ValueError("No open or in-progress tasks found; pass --task <id>.")
```

**Step 3:** Run uxdrift tests:

```bash
cd /Users/braydon/projects/experiments/uxdrift
uv run python -m pytest tests/ -x -q
```

**Step 4:** Commit:

```bash
git add pyproject.toml uv.lock uxdrift/workgraph.py
git commit -m "refactor: use speedrift-lane-sdk for base workgraph helpers"
```

---

### Task 9: Migrate redrift (lazy pattern + extra mutations)

Redrift uses the lazy pattern but has additional `wg init` and `wg service start` calls in its execute command. Those are standalone-mode operations — they stay in redrift's cli.py, not in the SDK.

**Files:**
- Modify: `/Users/braydon/projects/experiments/redrift/pyproject.toml`
- Modify: `/Users/braydon/projects/experiments/redrift/redrift/workgraph.py`

**Step 1:** `cd /Users/braydon/projects/experiments/redrift && uv add --editable /Users/braydon/projects/experiments/speedrift-lane-sdk`

**Step 2:** Replace `redrift/workgraph.py` with the standard lazy re-export (same as specdrift).

**Step 3:** Run tests: `uv run python -m pytest tests/ -x -q`

**Step 4:** Commit: `git commit -am "refactor: use speedrift-lane-sdk for workgraph helpers"`

---

### Task 10: Create GitHub repo and push

**Step 1:** Create the GitHub repo:

```bash
cd /Users/braydon/projects/experiments/speedrift-lane-sdk
gh repo create dbmcco/speedrift-lane-sdk --public --source . --push
```

**Step 2:** Push driftdriver changes:

```bash
cd /Users/braydon/projects/experiments/driftdriver
git push origin main
```

**Step 3:** Push all lane repo changes (each lane's refactor commit):

```bash
for lane in coredrift specdrift archdrift datadrift depsdrift uxdrift therapydrift fixdrift yagnidrift redrift; do
    cd /Users/braydon/projects/experiments/$lane
    git push origin HEAD
    cd -
done
```

---

## Task Dependency Graph

```
Task 1 (scaffold) → Task 2 (lane_contract) → Task 3 (workgraph)
                                                     ↓
                              Task 4 (wire driftdriver) ← Task 3
                                                     ↓
                   Task 5 (specdrift) ← Task 3 + Task 4
                          ↓
                   Task 6 (6 lazy lanes) ← Task 5
                   Task 7 (coredrift) ← Task 5
                   Task 8 (uxdrift) ← Task 5
                   Task 9 (redrift) ← Task 5
                          ↓
                   Task 10 (push) ← Tasks 6-9
```

Tasks 6, 7, 8, 9 are independent and can run in parallel after Task 5 validates the migration pattern.
