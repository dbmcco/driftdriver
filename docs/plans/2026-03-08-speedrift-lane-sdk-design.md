# speedrift-lane-sdk Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract duplicated workgraph helpers and lane contract types from 10 external drift lane repos into a shared SDK package.

**Architecture:** Single lightweight Python package (`speedrift-lane-sdk`) that provides the Workgraph helper class, lane output contract types (LaneResult/LaneFinding), and exit code constants. External lanes and driftdriver both depend on this package, eliminating ~1200 LOC of duplication and establishing a single source of truth for the lane protocol.

**Tech Stack:** Python 3.10+, dataclasses, subprocess (for wg CLI calls), pytest

---

## Package Structure

```
speedrift-lane-sdk/
├── pyproject.toml
├── src/
│   └── speedrift_lane_sdk/
│       ├── __init__.py          # re-exports public API
│       ├── workgraph.py         # Workgraph class, find_workgraph_dir()
│       ├── lane_contract.py     # LaneResult, LaneFinding, validate_lane_output()
│       └── constants.py         # ExitCode
└── tests/
    ├── test_workgraph.py
    ├── test_lane_contract.py
    └── test_constants.py
```

## Core Components

### workgraph.py — Unified Workgraph Helper

Supports both patterns found in external lanes:

- **Lazy/subprocess pattern** (7 lanes: specdrift, archdrift, datadrift, therapydrift, fixdrift, yagnidrift, redrift): Uses `show_task()` subprocess call for idempotency checks
- **Eager/in-memory pattern** (2 lanes: coredrift, uxdrift): Loads `graph.jsonl` upfront, checks in-memory dict

Unified interface:

```python
@dataclass
class Workgraph:
    wg_dir: Path
    project_dir: Path
    tasks: dict[str, dict[str, Any]] | None = None  # populated in eager mode

def find_workgraph_dir(start: Path) -> Path
    """Walk up from start to find .workgraph/ directory."""

def load_workgraph(project_dir: Path) -> Workgraph
    """Eager mode: read graph.jsonl, populate tasks dict."""

# Methods on Workgraph:
def show_task(self, task_id: str) -> dict | None
    """Lazy mode: subprocess wg show --json. Returns None if not found."""

def ensure_task(self, task_id: str, title: str, description: str = "",
                blocked_by: list[str] | None = None, tags: list[str] | None = None) -> bool
    """Idempotent task creation. Returns True if created, False if existed.
    Uses tasks dict if populated (eager), otherwise calls show_task (lazy)."""

def wg_log(self, task_id: str, message: str) -> None
    """Write a log entry via wg log subprocess."""
```

### lane_contract.py — Output Contract Types

Moved from driftdriver's `lane_contract.py`. Becomes the single source of truth.

```python
@dataclass
class LaneFinding:
    message: str
    severity: str  # "info", "warning", "error", "critical"
    file: str = ""
    line: int = 0
    tags: list[str] = field(default_factory=list)

@dataclass
class LaneResult:
    lane: str
    findings: list[LaneFinding]
    exit_code: int
    summary: str

def validate_lane_output(raw: str) -> LaneResult | None
    """Parse JSON string into LaneResult. Returns None on malformed input."""
```

### constants.py — Exit Codes

```python
class ExitCode:
    OK = 0
    USAGE = 2
    FINDINGS = 3
```

## Migration Path

1. **SDK published** to GitHub (`dbmcco/speedrift-lane-sdk`), installable via pip
2. **Driftdriver** replaces its `lane_contract.py` internals with imports from the SDK, thin re-export wrapper for backward compat
3. **External lanes** replace per-repo `workgraph.py` with `from speedrift_lane_sdk import Workgraph, find_workgraph_dir, ExitCode`
4. **No behavioral change** — lanes keep working standalone, SDK just eliminates copy-paste

## What's NOT in Scope

- CLI scaffolding (argparse helpers, text emitters) — too much per-lane variation
- `--emit-requests` protocol — YAGNI, followup interception already solved the boundary problem
- Any driftdriver-specific logic (authority budgets, directives, routing)
- Changes to how driftdriver invokes lanes (subprocess, JSON parsing — unchanged)

## ELI5

Every drift lane has its own copy of the same helper code — like 10 restaurants each independently making the same bread. The SDK is a shared bakery. All 10 import from one place. If we improve the recipe, everyone benefits. Lane-specific logic (the sandwich fillings) stays per-lane.

## Duplication Analysis (from audit)

| Item | Duplicated across | Lines saved |
|------|-------------------|-------------|
| `find_workgraph_dir()` | 10 repos | ~150 |
| `Workgraph` class | 10 repos | ~700 |
| `ensure_task()` | 10 repos | ~200 |
| `show_task()` | 7 repos | ~100 |
| `ExitCode` | 10 repos | ~50 |
| **Total** | | **~1200** |

Plus `LaneResult`/`LaneFinding` defined once instead of driftdriver owning it unilaterally.
