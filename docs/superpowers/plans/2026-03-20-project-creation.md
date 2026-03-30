# Project Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autonomous project creation from a north star declaration — drop a NORTH_STAR.md into `/factory/intake/<project-name>/`, factory scaffolds a new repo and drives it toward `production-ready` via the attractor loop. Complex declarations route through a Design Panel (multi-specialist LLM session) before the attractor starts.

**Architecture:** An `intake.py` module scans the workspace's `/factory/intake/` directory, computes a `complexity_score` from the north star declaration, and routes: simple path (scaffold + attractor task) or complex path (Design Panel → decomposed plan → attractor). The Design Panel in `design_panel.py` runs 5 specialist prompts via Haiku, validates quality with Sonnet, and synthesizes a `decomposed_plan.md` + pre-seeded wg tasks. The hub snapshot gains a `creation_pipeline` section. No new daemons — intake runs as a wg cycle task.

**Tech Stack:** Python 3.11+, subprocess (git, wg), urllib (Anthropic API), pytest with real tmp_path repos

---

## File Structure

- **Create:** `driftdriver/factory/__init__.py` — empty, makes factory a package
- **Create:** `driftdriver/factory/intake.py` — scan `/factory/intake/`, parse NORTH_STAR.md, compute complexity_score, route
- **Create:** `driftdriver/factory/scaffold.py` — git init new repo, write minimal NORTH_STAR.md + drift-policy.toml, init workgraph, create attractor task
- **Create:** `driftdriver/factory/design_panel.py` — 5 specialist LLM calls + quality gate + Sonnet synthesis → decomposed_plan.md + wg tasks
- **Modify:** `driftdriver/ecosystem_hub/snapshot.py` — call intake scan, add `creation_pipeline` key to snapshot dict
- **Create:** `tests/test_factory_intake.py` — unit tests for parsing and complexity routing
- **Create:** `tests/test_factory_scaffold.py` — integration tests with real git + wg stubs
- **Create:** `tests/test_factory_design_panel.py` — unit tests with injected LLM callers

---

## Task 1: Intake Parser + Complexity Scorer

**Files:**
- Create: `driftdriver/factory/__init__.py`
- Create: `driftdriver/factory/intake.py`
- Create: `tests/test_factory_intake.py`

**Context:** The intake NORTH_STAR.md format:
```markdown
# North Star — project-name

One paragraph: what this is.

## Outcome target
One concrete statement of done.

## Current phase
`onboarded`

## Complexity hints (optional)
- domain_count: 3
- has_external_integrations: true
- estimated_loc: 5000
```

`complexity_score = 0.4 * domain_normalized + 0.3 * has_external + 0.2 * loc_normalized + 0.1 * dep_normalized`

- `domain_normalized` = min(domain_count / 5, 1.0)  (5+ domains → max score)
- `has_external` = 1.0 if has_external_integrations else 0.0
- `loc_normalized` = min(estimated_loc / 10000, 1.0)
- `dep_normalized` = 0.0 (not yet surfaced in NORTH_STAR format — always 0 for now)

Threshold: `< 0.5` → simple path. `>= 0.5` → Design Panel.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_factory_intake.py
# ABOUTME: Tests for factory intake parser and complexity scorer.
# ABOUTME: Uses real tmp_path NORTH_STAR.md files; no mocks.
from __future__ import annotations

from pathlib import Path
import pytest

from driftdriver.factory.intake import (
    compute_complexity_score,
    parse_north_star,
    scan_intake_dir,
    IntakeProject,
)


def _write_ns(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_parse_simple_north_star(tmp_path: Path) -> None:
    ns_path = tmp_path / "myproject" / "NORTH_STAR.md"
    _write_ns(ns_path, """\
# North Star — myproject

This is a simple tool.

## Outcome target
Fast, reliable, single-purpose.

## Current phase
`onboarded`
""")
    project = parse_north_star("myproject", ns_path)
    assert project.name == "myproject"
    assert project.outcome_target == "Fast, reliable, single-purpose."
    assert project.current_phase == "onboarded"
    assert project.complexity_hints == {}


def test_parse_north_star_with_hints(tmp_path: Path) -> None:
    ns_path = tmp_path / "bigproject" / "NORTH_STAR.md"
    _write_ns(ns_path, """\
# North Star — bigproject

A multi-domain platform.

## Outcome target
Production-grade API platform.

## Current phase
`onboarded`

## Complexity hints
- domain_count: 4
- has_external_integrations: true
- estimated_loc: 8000
""")
    project = parse_north_star("bigproject", ns_path)
    assert project.complexity_hints["domain_count"] == 4
    assert project.complexity_hints["has_external_integrations"] is True
    assert project.complexity_hints["estimated_loc"] == 8000


def test_complexity_score_simple_project() -> None:
    hints = {}  # no hints → minimal complexity
    score = compute_complexity_score(hints)
    assert score < 0.5


def test_complexity_score_complex_project() -> None:
    hints = {
        "domain_count": 5,
        "has_external_integrations": True,
        "estimated_loc": 10000,
    }
    score = compute_complexity_score(hints)
    assert score >= 0.5


def test_scan_intake_dir_finds_projects(tmp_path: Path) -> None:
    intake_dir = tmp_path / "factory" / "intake"
    for name in ["alpha", "beta"]:
        ns = intake_dir / name / "NORTH_STAR.md"
        _write_ns(ns, f"# North Star — {name}\n\nDoes {name} things.\n\n## Outcome target\nWork well.\n\n## Current phase\n`onboarded`\n")
    projects = scan_intake_dir(intake_dir)
    assert len(projects) == 2
    names = {p.name for p in projects}
    assert names == {"alpha", "beta"}


def test_scan_intake_dir_missing_returns_empty(tmp_path: Path) -> None:
    projects = scan_intake_dir(tmp_path / "nonexistent" / "intake")
    assert projects == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_factory_intake.py -v 2>&1 | head -20
```

Expected: ImportError — `factory.intake` does not exist.

- [ ] **Step 3: Implement `intake.py`**

```python
# driftdriver/factory/intake.py
# ABOUTME: Scans /factory/intake/ for new project declarations.
# ABOUTME: Parses NORTH_STAR.md, computes complexity score, routes to scaffold or Design Panel.
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class IntakeProject:
    name: str
    north_star_path: Path
    summary: str
    outcome_target: str
    current_phase: str
    complexity_hints: dict[str, Any] = field(default_factory=dict)


def parse_north_star(project_name: str, ns_path: Path) -> IntakeProject:
    """Parse a NORTH_STAR.md and return an IntakeProject."""
    text = ns_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    summary = ""
    outcome_target = ""
    current_phase = "onboarded"
    hints: dict[str, Any] = {}

    section = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].lower()
            continue
        if stripped.startswith("# "):
            continue  # title line

        if section is None and stripped and not summary:
            summary = stripped
            continue

        if section and "outcome target" in section:
            if stripped and not outcome_target:
                outcome_target = stripped

        if section and "current phase" in section:
            if stripped:
                current_phase = stripped.strip("`")

        if section and "complexity hints" in section:
            m = re.match(r"-\s+(\w+):\s+(.+)", stripped)
            if m:
                key = m.group(1).strip()
                val_str = m.group(2).strip().lower()
                if val_str.isdigit():
                    hints[key] = int(val_str)
                elif val_str in ("true", "yes"):
                    hints[key] = True
                elif val_str in ("false", "no"):
                    hints[key] = False
                else:
                    try:
                        hints[key] = int(val_str)
                    except ValueError:
                        hints[key] = val_str

    return IntakeProject(
        name=project_name,
        north_star_path=ns_path,
        summary=summary,
        outcome_target=outcome_target,
        current_phase=current_phase,
        complexity_hints=hints,
    )


def compute_complexity_score(hints: dict[str, Any]) -> float:
    """Compute complexity_score from NORTH_STAR.md complexity hints.

    Returns float in [0.0, 1.0]. Threshold: < 0.5 → simple path, >= 0.5 → Design Panel.
    """
    domain_count = int(hints.get("domain_count") or 0)
    has_external = 1.0 if hints.get("has_external_integrations") else 0.0
    estimated_loc = int(hints.get("estimated_loc") or 0)

    domain_normalized = min(domain_count / 5.0, 1.0)
    loc_normalized = min(estimated_loc / 10_000.0, 1.0)
    dep_normalized = 0.0  # not yet surfaced in NORTH_STAR format

    score = (
        0.4 * domain_normalized
        + 0.3 * has_external
        + 0.2 * loc_normalized
        + 0.1 * dep_normalized
    )
    return round(score, 3)


def scan_intake_dir(intake_dir: Path) -> list[IntakeProject]:
    """Scan /factory/intake/ for NORTH_STAR.md files. Returns all valid projects."""
    if not intake_dir.is_dir():
        return []
    projects = []
    for ns_path in sorted(intake_dir.glob("*/NORTH_STAR.md")):
        project_name = ns_path.parent.name
        try:
            project = parse_north_star(project_name, ns_path)
            projects.append(project)
        except Exception:
            continue
    return projects
```

- [ ] **Step 4: Run tests**

```bash
python3 -m pytest tests/test_factory_intake.py -v
```

Expected: All 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/factory/__init__.py driftdriver/factory/intake.py tests/test_factory_intake.py
git commit -m "feat: add factory intake parser and complexity scorer"
```

---

## Task 2: Scaffold (Simple Path)

**Files:**
- Create: `driftdriver/factory/scaffold.py`
- Create: `tests/test_factory_scaffold.py`

**Context:** The simple path scaffold:
1. `git init <workspace_root>/<project_name>/`
2. Copy NORTH_STAR.md from intake to new repo root
3. Write minimal `drift-policy.toml`
4. Run `wg init` (or create `.workgraph/` structure manually if wg not installed)
5. Write `.workgraph/attractors/onboarded-to-production-ready.toml` to declare attractor target
6. Register in `ecosystem.toml` (append `[repos.<name>]` with path)

The scaffold should be idempotent — if the repo already exists, skip.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_factory_scaffold.py
# ABOUTME: Tests for factory scaffold — git init, drift-policy, workgraph structure.
# ABOUTME: Uses real git and real tmp_path; wg init is stubbed via a callable.
from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

from driftdriver.factory.intake import IntakeProject
from driftdriver.factory.scaffold import scaffold_project, ScaffoldResult


def _make_project(name: str, tmp_path: Path) -> IntakeProject:
    ns_path = tmp_path / "intake" / name / "NORTH_STAR.md"
    ns_path.parent.mkdir(parents=True)
    ns_path.write_text(
        f"# North Star — {name}\n\nDoes {name}.\n\n## Outcome target\nWork well.\n\n## Current phase\n`onboarded`\n"
    )
    from driftdriver.factory.intake import parse_north_star
    return parse_north_star(name, ns_path)


def test_scaffold_creates_git_repo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    result = scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    assert result.success
    assert (workspace / "myapp" / ".git").exists()


def test_scaffold_writes_north_star(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    ns = workspace / "myapp" / "NORTH_STAR.md"
    assert ns.exists()
    assert "North Star" in ns.read_text()


def test_scaffold_writes_drift_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    policy = workspace / "myapp" / "drift-policy.toml"
    assert policy.exists()


def test_scaffold_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    r1 = scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    r2 = scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    assert r1.success
    assert r2.skipped  # second call skips existing repo


def test_scaffold_creates_attractor_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    attractor = workspace / "myapp" / ".workgraph" / "attractors" / "onboarded-to-production-ready.toml"
    assert attractor.exists()
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_factory_scaffold.py -v 2>&1 | head -20
```

Expected: ImportError — `factory.scaffold` not defined.

- [ ] **Step 3: Implement `scaffold.py`**

```python
# driftdriver/factory/scaffold.py
# ABOUTME: Scaffolds new repos from factory intake declarations.
# ABOUTME: git init + NORTH_STAR.md + drift-policy.toml + workgraph attractor structure.
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from driftdriver.factory.intake import IntakeProject


@dataclass
class ScaffoldResult:
    success: bool = False
    skipped: bool = False
    error: str = ""
    repo_path: Path | None = None


_MINIMAL_DRIFT_POLICY = """\
[policy]
version = "1.0"
lifecycle = "active"
daemon_posture = "supervised"

[budgets.lane]
max_open = 3
max_hourly = 10

[attractor]
target = "production-ready"
"""

_ATTRACTOR_TOML = """\
[attractor]
from_state = "onboarded"
to_state = "production-ready"
description = "Drive new project from scaffold to production-ready state"

[circuit_breakers]
max_passes = 3
task_budget = 30
"""


def _git_init(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "factory@driftdriver"],
        cwd=str(repo_path), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Dark Factory"],
        cwd=str(repo_path), check=True, capture_output=True,
    )


def scaffold_project(
    project: IntakeProject,
    *,
    workspace_root: Path,
    wg_init: Callable[[Path], None] | None = None,
) -> ScaffoldResult:
    """Scaffold a new repo for the given project.

    Returns ScaffoldResult with success=True or skipped=True if already exists.
    wg_init is injectable (default calls `wg init` via subprocess).
    """
    repo_path = workspace_root / project.name

    if (repo_path / ".git").exists():
        return ScaffoldResult(skipped=True, repo_path=repo_path)

    try:
        repo_path.mkdir(parents=True, exist_ok=True)

        # 1. git init
        _git_init(repo_path)

        # 2. Copy NORTH_STAR.md from intake
        ns_content = project.north_star_path.read_text(encoding="utf-8")
        (repo_path / "NORTH_STAR.md").write_text(ns_content, encoding="utf-8")

        # 3. Minimal drift-policy.toml
        (repo_path / "drift-policy.toml").write_text(_MINIMAL_DRIFT_POLICY, encoding="utf-8")

        # 4. Workgraph structure
        wg_dir = repo_path / ".workgraph"
        wg_dir.mkdir(exist_ok=True)
        (wg_dir / "graph.jsonl").touch()

        # 5. Attractor declaration
        attractors_dir = wg_dir / "attractors"
        attractors_dir.mkdir(exist_ok=True)
        (attractors_dir / "onboarded-to-production-ready.toml").write_text(
            _ATTRACTOR_TOML, encoding="utf-8"
        )

        # 6. Run wg init (injectable for testing)
        if wg_init is not None:
            wg_init(repo_path)
        else:
            _default_wg_init(repo_path)

        # 7. Initial git commit
        subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: scaffold {project.name} from dark factory intake"],
            cwd=str(repo_path), check=True, capture_output=True,
        )

        return ScaffoldResult(success=True, repo_path=repo_path)

    except Exception as exc:
        return ScaffoldResult(error=str(exc))


def _default_wg_init(repo_path: Path) -> None:
    """Run `wg init` in the new repo. Best-effort — fails silently."""
    try:
        subprocess.run(
            ["wg", "init"],
            cwd=str(repo_path),
            capture_output=True,
            timeout=15.0,
        )
    except Exception:
        pass  # wg not installed — workgraph structure already created manually
```

- [ ] **Step 4: Run scaffold tests**

```bash
python3 -m pytest tests/test_factory_scaffold.py -v
```

Expected: All 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/factory/scaffold.py tests/test_factory_scaffold.py
git commit -m "feat: add factory scaffold (git init, drift-policy, workgraph attractor)"
```

---

## Task 3: Design Panel (Complex Path)

**Files:**
- Create: `driftdriver/factory/design_panel.py`
- Create: `tests/test_factory_design_panel.py`

**Context:** The Design Panel runs 5 specialist prompts against the north star, validates each (>= 100 words), then Sonnet synthesizes into a decomposed plan. All LLM callers are injectable for testing. Specialists: Architect, UX Critic, Security Reviewer, Domain Expert, Contrarian.

**Protocol:**
1. Each specialist receives the north star text → writes perspective (>= 100 words)
2. Quality gate: if any transcript < 100 words, Sonnet re-requests with specific feedback
3. Moderator (Sonnet) synthesizes all 5 into:
   - `decomposed_plan.md` written to the new repo
   - A list of pre-seeded wg task titles to create

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_factory_design_panel.py
# ABOUTME: Tests for Design Panel — 5 specialist LLM calls + quality gate + synthesis.
# ABOUTME: All LLM callers injected; no real API calls.
from __future__ import annotations

from pathlib import Path
import pytest

from driftdriver.factory.design_panel import (
    run_design_panel,
    DesignPanelResult,
    _quality_gate,
)


def _make_long_text(words: int = 120) -> str:
    return " ".join(["word"] * words)


def _fake_specialist_caller(role: str, north_star: str) -> str:
    """Returns a valid (>100 word) specialist perspective."""
    return _make_long_text(120)


def _fake_short_caller(role: str, north_star: str) -> str:
    """Returns a short (< 100 word) response to trigger quality gate."""
    return "Too short."


def _fake_moderator(transcripts: dict[str, str], north_star: str) -> dict:
    return {
        "plan_summary": "Build a clean, focused service.",
        "tasks": [
            "Set up core data model",
            "Implement API endpoints",
            "Add test coverage",
            "Write NORTH_STAR alignment check",
        ],
    }


def test_quality_gate_passes_long_transcript() -> None:
    transcript = _make_long_text(120)
    assert _quality_gate(transcript) is True


def test_quality_gate_fails_short_transcript() -> None:
    assert _quality_gate("Too short.") is False


def test_run_design_panel_success(tmp_path: Path) -> None:
    north_star = "# North Star — myapp\n\nDoes great things.\n\n## Outcome target\nWork well.\n"
    repo_path = tmp_path / "myapp"
    repo_path.mkdir()
    result = run_design_panel(
        north_star=north_star,
        repo_path=repo_path,
        specialist_caller=_fake_specialist_caller,
        moderator_caller=_fake_moderator,
    )
    assert result.success
    assert len(result.tasks) >= 1
    assert (repo_path / "decomposed_plan.md").exists()


def test_run_design_panel_quality_gate_triggers_retry(tmp_path: Path) -> None:
    """If specialist response is too short, it retries (up to 2 times)."""
    call_counts: dict[str, int] = {}

    def _counting_caller(role: str, north_star: str) -> str:
        call_counts[role] = call_counts.get(role, 0) + 1
        if call_counts[role] == 1:
            return "Too short."  # fail quality gate on first call
        return _make_long_text(120)  # succeed on retry

    result = run_design_panel(
        north_star="# North Star — test\n\nDoes things.\n\n## Outcome target\nWork.\n",
        repo_path=tmp_path,
        specialist_caller=_counting_caller,
        moderator_caller=_fake_moderator,
    )
    assert result.success
    # At least one role should have been called twice
    assert any(count >= 2 for count in call_counts.values())
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_factory_design_panel.py -v 2>&1 | head -20
```

Expected: ImportError.

- [ ] **Step 3: Implement `design_panel.py`**

```python
# driftdriver/factory/design_panel.py
# ABOUTME: Multi-specialist LLM session for complex project decomposition.
# ABOUTME: 5 specialists (Architect, UX, Security, Domain, Contrarian) + Sonnet moderator.
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_API_VERSION = "2023-06-01"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_SONNET_MODEL = "claude-sonnet-4-6"
_MIN_WORDS = 100
_MAX_RETRIES = 2

SPECIALIST_ROLES = [
    "Architect",
    "UX Critic",
    "Security Reviewer",
    "Domain Expert",
    "Contrarian",
]

_SPECIALIST_PROMPTS = {
    "Architect": (
        "You are a software architect reviewing a new project's north star declaration. "
        "Write a detailed perspective covering: system design, component boundaries, "
        "key integration patterns, and potential architectural risks. Be specific."
    ),
    "UX Critic": (
        "You are a UX critic reviewing a new project. "
        "Write a detailed perspective covering: user experience quality, interaction patterns, "
        "surface area concerns, and usability risks. Be specific and critical."
    ),
    "Security Reviewer": (
        "You are a security reviewer. "
        "Write a detailed perspective covering: attack surface, auth patterns, "
        "data handling risks, and security requirements for this project. Be specific."
    ),
    "Domain Expert": (
        "You are a domain expert. "
        "Write a detailed perspective on business logic correctness, domain model fidelity, "
        "and whether the declared outcome target is achievable. Be specific."
    ),
    "Contrarian": (
        "You are a contrarian reviewer. "
        "Challenge the assumptions in this north star declaration. "
        "Identify overbuilding, gaps, unrealistic goals, and what could go wrong. "
        "Be direct and critical."
    ),
}


@dataclass
class DesignPanelResult:
    success: bool = False
    transcripts: dict[str, str] = field(default_factory=dict)
    plan_summary: str = ""
    tasks: list[str] = field(default_factory=list)
    error: str = ""


def _quality_gate(transcript: str) -> bool:
    """Return True if transcript meets quality threshold (>= 100 words)."""
    return len(transcript.split()) >= _MIN_WORDS


def _default_specialist_caller(role: str, north_star: str) -> str:
    """Call Haiku to get a specialist perspective."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    system_prompt = _SPECIALIST_PROMPTS.get(role, "You are an expert reviewer.")
    payload = {
        "model": _HAIKU_MODEL,
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": f"North star:\n\n{north_star}"}],
    }
    request = Request(
        _ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Haiku API error {exc.code}") from exc
    content = body.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "").strip()
    return ""


def _default_moderator_caller(transcripts: dict[str, str], north_star: str) -> dict:
    """Call Sonnet to synthesize specialist transcripts into a decomposed plan."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    specialists_text = "\n\n".join(
        f"## {role}\n{text}" for role, text in transcripts.items()
    )
    prompt = (
        f"You are moderating a design panel for a new software project.\n\n"
        f"North star:\n{north_star}\n\n"
        f"Specialist perspectives:\n{specialists_text}\n\n"
        f"Synthesize these into a decomposed implementation plan. "
        f'Respond with ONLY JSON: {{"plan_summary": "<2-3 sentences>", "tasks": ["<task 1>", "<task 2>", ...]}}'
        f"\n\nProvide 4-8 concrete, actionable tasks that an agent can execute."
    )
    payload = {
        "model": _SONNET_MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        _ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Sonnet API error {exc.code}") from exc
    content = body.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "").strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
    return {"plan_summary": "Synthesis failed.", "tasks": []}


def run_design_panel(
    north_star: str,
    repo_path: Path,
    *,
    specialist_caller: Callable[[str, str], str] | None = None,
    moderator_caller: Callable[[dict[str, str], str], dict] | None = None,
) -> DesignPanelResult:
    """Run the design panel for a complex project.

    Returns DesignPanelResult with transcripts, plan summary, and pre-seeded tasks.
    Writes decomposed_plan.md to repo_path.
    """
    s_caller = specialist_caller or _default_specialist_caller
    m_caller = moderator_caller or _default_moderator_caller
    transcripts: dict[str, str] = {}

    for role in SPECIALIST_ROLES:
        transcript = ""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                transcript = s_caller(role, north_star)
            except Exception as exc:
                transcript = f"[{role} call failed: {exc}]"
                break
            if _quality_gate(transcript):
                break
            # Quality gate failed — retry with more specific prompt on next attempt
            if attempt < _MAX_RETRIES:
                north_star = (
                    north_star
                    + f"\n\n[Note to {role}: your previous response was too brief. "
                    f"Please provide at least {_MIN_WORDS} words of specific analysis.]"
                )
        transcripts[role] = transcript

    try:
        synthesis = m_caller(transcripts, north_star)
    except Exception as exc:
        return DesignPanelResult(error=str(exc), transcripts=transcripts)

    plan_summary = str(synthesis.get("plan_summary") or "")
    tasks = [str(t) for t in (synthesis.get("tasks") or []) if t]

    # Write decomposed_plan.md to repo
    plan_lines = [
        "# Decomposed Implementation Plan\n\n",
        f"## Summary\n\n{plan_summary}\n\n",
        "## Tasks\n\n",
    ]
    for i, task in enumerate(tasks, 1):
        plan_lines.append(f"{i}. {task}\n")
    plan_lines.append("\n## Specialist Perspectives\n\n")
    for role, text in transcripts.items():
        plan_lines.append(f"### {role}\n\n{text}\n\n")

    try:
        repo_path.mkdir(parents=True, exist_ok=True)
        (repo_path / "decomposed_plan.md").write_text("".join(plan_lines), encoding="utf-8")
    except Exception:
        pass

    return DesignPanelResult(
        success=True,
        transcripts=transcripts,
        plan_summary=plan_summary,
        tasks=tasks,
    )
```

- [ ] **Step 4: Run design panel tests**

```bash
python3 -m pytest tests/test_factory_design_panel.py -v
```

Expected: All 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/factory/design_panel.py tests/test_factory_design_panel.py
git commit -m "feat: add Design Panel (5-specialist LLM session + Sonnet synthesis)"
```

---

## Task 4: Route + Hub Pipeline

**Files:**
- Modify: `driftdriver/factory/intake.py` — add `route_project` function that orchestrates simple vs complex path
- Modify: `driftdriver/ecosystem_hub/snapshot.py` — add `creation_pipeline` key
- Create: `tests/test_factory_route.py` — routing tests

**Context:** `route_project` is the entry point called from a wg cycle task. It checks if the project has already been scaffolded (`.factory-state.json` in the intake dir records status). The hub snapshot adds a `creation_pipeline` section showing each intake project's status.

Status progression: `intake` → `scaffolded` → `design-panel` → `building` → `converged`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_factory_route.py
# ABOUTME: Tests for intake routing — simple vs complex path selection.
# ABOUTME: Uses injected scaffold and design_panel callables.
from __future__ import annotations

from pathlib import Path
import pytest

from driftdriver.factory.intake import compute_complexity_score, IntakeProject, route_project


def _make_project(name: str, hints: dict, tmp_path: Path) -> IntakeProject:
    ns_path = tmp_path / "intake" / name / "NORTH_STAR.md"
    ns_path.parent.mkdir(parents=True)
    ns_path.write_text(f"# North Star — {name}\n\nDoes {name}.\n\n## Outcome target\nWork.\n\n## Current phase\n`onboarded`\n")
    from driftdriver.factory.intake import parse_north_star
    p = parse_north_star(name, ns_path)
    p.complexity_hints.update(hints)
    return p


def test_simple_project_routes_to_scaffold(tmp_path: Path) -> None:
    project = _make_project("simple", {}, tmp_path)
    assert compute_complexity_score(project.complexity_hints) < 0.5

    scaffolded = []
    panel_run = []

    result = route_project(
        project,
        workspace_root=tmp_path / "workspace",
        scaffold_fn=lambda p, ws: scaffolded.append(p.name) or type("R", (), {"success": True, "skipped": False, "error": "", "repo_path": ws / p.name})(),
        design_panel_fn=lambda p, rp: panel_run.append(p.name),
    )
    assert "simple" in scaffolded
    assert "simple" not in panel_run


def test_complex_project_routes_to_design_panel(tmp_path: Path) -> None:
    project = _make_project("complex", {
        "domain_count": 5,
        "has_external_integrations": True,
        "estimated_loc": 10000,
    }, tmp_path)
    assert compute_complexity_score(project.complexity_hints) >= 0.5

    scaffolded = []
    panel_run = []

    result = route_project(
        project,
        workspace_root=tmp_path / "workspace",
        scaffold_fn=lambda p, ws: scaffolded.append(p.name) or type("R", (), {"success": True, "skipped": False, "error": "", "repo_path": ws / p.name})(),
        design_panel_fn=lambda p, rp: panel_run.append(p.name),
    )
    assert "complex" in scaffolded
    assert "complex" in panel_run
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_factory_route.py -v 2>&1 | head -20
```

Expected: ImportError — `route_project` not defined.

- [ ] **Step 3: Add `route_project` to `intake.py`**

Add to `driftdriver/factory/intake.py`:

```python
import json
from typing import Callable, Any

_COMPLEXITY_THRESHOLD = 0.5
_STATE_FILENAME = ".factory-state.json"


def _read_state(intake_project_dir: Path) -> dict[str, Any]:
    state_file = intake_project_dir / _STATE_FILENAME
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_state(intake_project_dir: Path, state: dict[str, Any]) -> None:
    state_file = intake_project_dir / _STATE_FILENAME
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def route_project(
    project: IntakeProject,
    *,
    workspace_root: Path,
    scaffold_fn: Callable | None = None,
    design_panel_fn: Callable | None = None,
) -> dict[str, Any]:
    """Route a project through simple or complex creation path.

    Returns status dict. Records progress in .factory-state.json in the intake dir.
    """
    from driftdriver.factory.scaffold import scaffold_project

    state = _read_state(project.north_star_path.parent)
    if state.get("status") == "scaffolded" and state.get("design_panel_done"):
        return {"status": "already_complete", "project": project.name}

    # Step 1: Scaffold (idempotent)
    _scaffold = scaffold_fn or (lambda p, ws: scaffold_project(p, workspace_root=ws))
    scaffold_result = _scaffold(project, workspace_root)

    if not (scaffold_result.success or scaffold_result.skipped):
        return {"status": "scaffold_failed", "error": scaffold_result.error, "project": project.name}

    repo_path = scaffold_result.repo_path or (workspace_root / project.name)
    state["status"] = "scaffolded"
    state["repo_path"] = str(repo_path)
    _write_state(project.north_star_path.parent, state)

    # Step 2: Route by complexity
    complexity = compute_complexity_score(project.complexity_hints)
    if complexity >= _COMPLEXITY_THRESHOLD and not state.get("design_panel_done"):
        _panel = design_panel_fn or (
            lambda p, rp: __import__(
                "driftdriver.factory.design_panel", fromlist=["run_design_panel"]
            ).run_design_panel(
                north_star=p.north_star_path.read_text(encoding="utf-8"),
                repo_path=rp,
            )
        )
        _panel(project, repo_path)
        state["design_panel_done"] = True
        _write_state(project.north_star_path.parent, state)

    return {"status": "routed", "project": project.name, "complexity": complexity}
```

- [ ] **Step 4: Add `creation_pipeline` to hub snapshot**

In `driftdriver/ecosystem_hub/snapshot.py`, after the agency_eval_inputs block, add:

```python
from driftdriver.factory.intake import scan_intake_dir

# Scan factory intake directory for creation pipeline status
_intake_dir = workspace_root / "factory" / "intake"
_intake_projects = []
try:
    from driftdriver.factory.intake import scan_intake_dir as _scan_intake
    for _p in _scan_intake(_intake_dir):
        _state_file = _p.north_star_path.parent / ".factory-state.json"
        _state = {}
        if _state_file.exists():
            try:
                import json as _json
                _state = _json.loads(_state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        _intake_projects.append({
            "name": _p.name,
            "status": _state.get("status", "intake"),
            "complexity": __import__("driftdriver.factory.intake", fromlist=["compute_complexity_score"]).compute_complexity_score(_p.complexity_hints),
            "design_panel_done": _state.get("design_panel_done", False),
            "repo_path": _state.get("repo_path"),
        })
except Exception:
    pass
```

And in the snapshot dict:
```python
"creation_pipeline": _intake_projects,
```

- [ ] **Step 5: Run routing tests**

```bash
python3 -m pytest tests/test_factory_route.py -v
```

Expected: Both PASS.

- [ ] **Step 6: Run full suite for regressions**

```bash
python3 -m pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: No new failures.

- [ ] **Step 7: Commit**

```bash
git add driftdriver/factory/intake.py driftdriver/ecosystem_hub/snapshot.py tests/test_factory_route.py
git commit -m "feat: add project creation routing and creation_pipeline hub section"
```

---
