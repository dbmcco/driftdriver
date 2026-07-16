# Driftdriver Speedrift Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Speedrift owns task state and dependencies; use a fresh Pi Lunaroute worker and two fresh Pi Lunaroute review contexts for each task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Speedrift one deterministic graph-location contract, remove deprecated Pi executor coupling, repair bootstrap ordering, make Agency semantics honest, and make `speedriftd` the sole operator-facing supervisor.

**Architecture:** `driftdriver.workgraph` becomes the canonical graph-location module. A small JSON CLI exposes its decision to PlanForge. Runtime call sites consume the resolver instead of constructing `.workgraph` directly. Pi dispatch selects the Pi handler through the task's handler-first model string, while existing non-Pi legacy executor paths remain unchanged.

**Tech Stack:** Python 3.12, standard library, `dataclasses`, `unittest`, `pytest`, subprocess-based Workgraph integration.

## Global Constraints

- Preserve `.workgraph` and `.wg`; do not migrate repositories.
- Treat initialized state in both directories as `WorkgraphDirectoryConflictError`.
- Use `graph.jsonl` as the initialized graph-state marker.
- Return `.workgraph` as the creation candidate only when neither directory is initialized or already selected.
- Every Workgraph subprocess receives `--dir`.
- Pi dispatch never emits `--executor pi`.
- Claude and Amplifier legacy executor dispatch remain unchanged in this package.
- External Agency defaults to disabled and port `8000` when explicitly enabled.
- Do not stage existing unrelated changes in `.driftdriver/upstream-pins.toml`, `.workgraph/drift-policy.toml`, `AGENTS.md`, or `CLAUDE.md` from the original working tree.

---

### Task 1: Canonical graph-directory resolver

**Files:**
- Modify: `driftdriver/workgraph.py`
- Modify: `tests/test_workgraph.py`

**Interfaces:**
- Produces: `WorkgraphDirectoryConflictError(RuntimeError)`.
- Produces: `GraphDirectoryResolution(path: Path, initialized: bool, source: str)`.
- Produces: `resolve_workgraph_dir(project_dir: Path, explicit: Path | None = None) -> GraphDirectoryResolution`.
- Preserves: `find_workgraph_dir(explicit: Path | None) -> Path` as an initialized-graph compatibility wrapper.

- [ ] **Step 1: Add failing resolver tests**

Add tests equivalent to:

```python
from pathlib import Path
import tempfile
import unittest

from driftdriver.workgraph import (
    WorkgraphDirectoryConflictError,
    resolve_workgraph_dir,
)

class GraphDirectoryResolutionTests(unittest.TestCase):
    def test_resolves_initialized_legacy_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            graph = repo / ".workgraph"
            graph.mkdir()
            (graph / "graph.jsonl").write_text("", encoding="utf-8")
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, graph)
            self.assertTrue(result.initialized)
            self.assertEqual(result.source, "legacy")

    def test_resolves_initialized_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            graph = repo / ".wg"
            graph.mkdir()
            (graph / "graph.jsonl").write_text("", encoding="utf-8")
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, graph)
            self.assertTrue(result.initialized)
            self.assertEqual(result.source, "current")

    def test_rejects_two_initialized_graphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for name in (".workgraph", ".wg"):
                graph = repo / name
                graph.mkdir()
                (graph / "graph.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(
                WorkgraphDirectoryConflictError,
                r"\.workgraph.*\.wg",
            ):
                resolve_workgraph_dir(repo)

    def test_partial_legacy_directory_is_not_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            graph = repo / ".workgraph"
            graph.mkdir()
            (graph / "drift-policy.toml").write_text("", encoding="utf-8")
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, graph)
            self.assertFalse(result.initialized)
            self.assertEqual(result.source, "existing")

    def test_new_speedrift_repo_uses_legacy_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, repo / ".workgraph")
            self.assertFalse(result.initialized)
            self.assertEqual(result.source, "default")
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
uv run python -m unittest tests.test_workgraph -v
```

Expected: import failures for `GraphDirectoryResolution`, `WorkgraphDirectoryConflictError`, and `resolve_workgraph_dir`.

- [ ] **Step 3: Implement the resolver**

Use this public shape in `driftdriver/workgraph.py`:

```python
from dataclasses import dataclass

GRAPH_DIR_NAMES = (".workgraph", ".wg")

class WorkgraphDirectoryConflictError(RuntimeError):
    """Raised when a repository has two initialized Workgraph directories."""

@dataclass(frozen=True)
class GraphDirectoryResolution:
    path: Path
    initialized: bool
    source: str


def _is_initialized_graph(path: Path) -> bool:
    return (path / "graph.jsonl").is_file()


def resolve_workgraph_dir(
    project_dir: Path,
    explicit: Path | None = None,
) -> GraphDirectoryResolution:
    project_dir = project_dir.resolve()
    if explicit is not None:
        candidate = explicit.resolve()
        if candidate.name not in GRAPH_DIR_NAMES:
            candidate = candidate / ".workgraph"
        return GraphDirectoryResolution(
            path=candidate,
            initialized=_is_initialized_graph(candidate),
            source="explicit",
        )

    legacy = project_dir / ".workgraph"
    current = project_dir / ".wg"
    legacy_initialized = _is_initialized_graph(legacy)
    current_initialized = _is_initialized_graph(current)
    if legacy_initialized and current_initialized:
        raise WorkgraphDirectoryConflictError(
            "Two initialized Workgraph directories found: "
            f"{legacy} and {current}. Choose one graph before continuing."
        )
    if legacy_initialized:
        return GraphDirectoryResolution(legacy, True, "legacy")
    if current_initialized:
        return GraphDirectoryResolution(current, True, "current")
    if legacy.exists() and current.exists():
        raise WorkgraphDirectoryConflictError(
            "Two uninitialized Workgraph directories found: "
            f"{legacy} and {current}. Remove or archive the unintended directory."
        )
    if legacy.exists():
        return GraphDirectoryResolution(legacy, False, "existing")
    if current.exists():
        return GraphDirectoryResolution(current, False, "existing")
    return GraphDirectoryResolution(legacy, False, "default")
```

Update `find_workgraph_dir` to call the resolver, preserve its upward search behavior, and raise `FileNotFoundError` when the resolved candidate is not initialized.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
uv run python -m unittest tests.test_workgraph -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/workgraph.py tests/test_workgraph.py
git commit -m "feat(workgraph): resolve legacy and current graph directories"
```

---

### Task 2: Machine-readable graph-dir CLI and runtime adoption

**Files:**
- Create: `driftdriver/cli/graph_dir_cmd.py`
- Create: `tests/test_graph_dir_cli.py`
- Modify: `driftdriver/cli/__init__.py`
- Modify: `driftdriver/task_router.py`
- Modify: `driftdriver/speedriftd.py`
- Modify: `driftdriver/speedriftd_state.py`
- Modify: `driftdriver/ecosystem_hub/discovery.py`
- Modify: `driftdriver/ecosystem_hub/collector.py`
- Modify: `driftdriver/ecosystem_hub/snapshot.py`
- Modify: focused tests for each changed runtime module.

**Interfaces:**
- Produces CLI: `driftdriver --dir "$PWD" --json graph-dir`.
- Produces JSON keys: `project_dir`, `graph_dir`, `initialized`, `source`.
- Consumes: `resolve_workgraph_dir` from Task 1.

- [ ] **Step 1: Write failing CLI tests**

```python
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from driftdriver.cli.graph_dir_cmd import cmd_graph_dir

class GraphDirCommandTests(unittest.TestCase):
    def test_json_output_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as tmp, patch("builtins.print") as emit:
            args = argparse.Namespace(dir=tmp, json=True)
            self.assertEqual(cmd_graph_dir(args), 0)
            payload = json.loads(emit.call_args.args[0])
            self.assertEqual(payload["project_dir"], str(Path(tmp).resolve()))
            self.assertEqual(payload["graph_dir"], str(Path(tmp).resolve() / ".workgraph"))
            self.assertFalse(payload["initialized"])
            self.assertEqual(payload["source"], "default")
```

Add runtime regression tests asserting that `.wg`-only repositories are read by task routing and `speedriftd`, and that dual initialized directories fail before mutation.

- [ ] **Step 2: Verify failures**

Run:

```bash
uv run python -m unittest tests.test_graph_dir_cli tests.test_task_router tests.test_speedriftd tests.test_speedriftd_state -v
```

Expected: missing module/command failures and `.wg` path assertions failing.

- [ ] **Step 3: Implement and register the CLI**

`driftdriver/cli/graph_dir_cmd.py` must expose:

```python
def cmd_graph_dir(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir or Path.cwd()).resolve()
    result = resolve_workgraph_dir(project_dir)
    payload = {
        "project_dir": str(project_dir),
        "graph_dir": str(result.path),
        "initialized": result.initialized,
        "source": result.source,
    }
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(result.path)
    return 0


def register_graph_dir_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "graph-dir",
        help="Resolve the repository's canonical Workgraph directory",
    )
    parser.set_defaults(func=cmd_graph_dir)
```

Register it beside the other modular command registrations in `driftdriver/cli/__init__.py`.

- [ ] **Step 4: Replace runtime-critical hardcoded graph paths**

At each listed runtime call site, resolve once from the repository root:

```python
wg_dir = resolve_workgraph_dir(repo_path).path
```

Do not replace paths that refer to installation template literals rather than the current repository's live graph. Preserve current behavior for initialized `.workgraph` repositories.

- [ ] **Step 5: Run focused tests**

```bash
uv run python -m unittest \
  tests.test_graph_dir_cli \
  tests.test_task_router \
  tests.test_speedriftd \
  tests.test_speedriftd_state -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add \
  driftdriver/cli/graph_dir_cmd.py \
  driftdriver/cli/__init__.py \
  driftdriver/task_router.py \
  driftdriver/speedriftd.py \
  driftdriver/speedriftd_state.py \
  driftdriver/ecosystem_hub/discovery.py \
  driftdriver/ecosystem_hub/collector.py \
  driftdriver/ecosystem_hub/snapshot.py \
  tests/test_graph_dir_cli.py \
  tests/test_task_router.py \
  tests/test_speedriftd.py \
  tests/test_speedriftd_state.py
git commit -m "feat(runtime): use one graph directory decision"
```

---

### Task 3: Handler-first Pi dispatch and structured Workgraph failures

**Files:**
- Modify: `driftdriver/task_router.py`
- Modify: `tests/test_task_router.py`

**Interfaces:**
- Changes: `_dispatch_wg_spawn(..., wg_executor: str | None = None)`.
- Changes: `_dispatch_pi` calls `_dispatch_wg_spawn` without an executor flag.
- Preserves: `_dispatch_claude` passes `wg_executor="claude"`.
- Adds: `DispatchResult.error_code: str | None = None`.
- Adds: `_workgraph_error_code(stderr: str, stdout: str) -> str | None`.

- [ ] **Step 1: Add failing tests**

Add tests that patch `subprocess.run` and assert:

```python
self.assertNotIn("--executor", called_cmd)
self.assertEqual(called_cmd[-2:], ["--model", "pi:lunaroute/glm-5.2-nvfp4"])
```

Add a separate Claude test asserting:

```python
self.assertIn(["--executor", "claude"], [called_cmd[i:i+2] for i in range(len(called_cmd)-1)])
```

Add an error test with stderr:

```text
WG-EXEC-UNSELECTED: no explicit execution selection
```

and assert `result.error_code == "WG-EXEC-UNSELECTED"` while `result.error` retains the original message.

- [ ] **Step 2: Verify failures**

```bash
uv run python -m unittest tests.test_task_router -v
```

Expected: Pi command still contains `--executor pi`; `DispatchResult` has no `error_code`.

- [ ] **Step 3: Implement minimal dispatch changes**

Use this command construction:

```python
cmd = ["wg", "--dir", str(wg_dir), "spawn", task_id]
if wg_executor is not None:
    cmd.extend(["--executor", wg_executor])
model = task.get("model")
if model:
    cmd.extend(["--model", str(model)])
```

Use a protocol-code extractor:

```python
WORKGRAPH_ERROR_CODE_RE = re.compile(r"\b(WG-[A-Z0-9-]+)\b")

def _workgraph_error_code(stderr: str, stdout: str) -> str | None:
    match = WORKGRAPH_ERROR_CODE_RE.search(stderr) or WORKGRAPH_ERROR_CODE_RE.search(stdout)
    return match.group(1) if match else None
```

Do not infer Pi from the model provider. The handler is explicit in the `pi:` prefix.

- [ ] **Step 4: Run tests**

```bash
uv run python -m unittest tests.test_task_router -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/task_router.py tests/test_task_router.py
git commit -m "fix(dispatch): use handler-first Pi execution"
```

---

### Task 4: Workgraph bootstrap before Speedrift files

**Files:**
- Modify: `driftdriver/cli/check.py`
- Modify: `driftdriver/cli/install_cmd.py`
- Create: `tests/test_install_ordering.py`

**Interfaces:**
- Changes: `_ensure_wg_init(project_dir: Path) -> Path` returns the resolved graph directory.
- Adds: `_initialize_into_existing_candidate(project_dir: Path, target: Path) -> None`.
- Consumes: `resolve_workgraph_dir` from Task 1.

- [ ] **Step 1: Reproduce the partial-directory failure in a test**

```python
class InstallOrderingTests(unittest.TestCase):
    def test_existing_speedrift_files_do_not_block_workgraph_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            target = repo / ".workgraph"
            target.mkdir()
            (target / "drift-policy.toml").write_text("[routing]\n", encoding="utf-8")
            (target / "usage.log").write_text("", encoding="utf-8")

            def fake_check_call(cmd, cwd):
                init_dir = Path(cmd[2])
                init_dir.mkdir(parents=True, exist_ok=True)
                (init_dir / "graph.jsonl").write_text("", encoding="utf-8")
                (init_dir / "config.toml").write_text("", encoding="utf-8")

            with patch("driftdriver.cli.check.subprocess.check_call", side_effect=fake_check_call):
                resolved = _ensure_wg_init(repo)

            self.assertEqual(resolved, target)
            self.assertTrue((target / "graph.jsonl").exists())
            self.assertTrue((target / "drift-policy.toml").exists())
```

Add a test proving unexpected existing user files cause a precise failure rather than deletion.

- [ ] **Step 2: Verify failure**

```bash
uv run python -m unittest tests.test_install_ordering -v
```

Expected: current `_ensure_wg_init` invokes `wg init` directly against the existing candidate and fails or returns `None`.

- [ ] **Step 3: Implement non-destructive initialization**

When the selected candidate exists but lacks `graph.jsonl`:

1. Create a temporary sibling path with `tempfile.mkdtemp(prefix=f"{target.name}.init-", dir=target.parent)`.
2. Run `wg --dir "$TEMP_GRAPH_DIR" init` against that exact temporary path.
3. Move each initialized Workgraph entry into the existing target only when the destination does not exist.
4. Preserve `drift-policy.toml`, `usage.log`, and all other existing files.
5. Remove only the temporary directory created by this function.
6. Return the target path.

Do not pass a model to `wg init`; current Workgraph initialization is graph-only.

- [ ] **Step 4: Make cmd_install consume the returned path**

Replace the two-step pattern:

```python
_ensure_wg_init(project_dir)
wg_dir = find_workgraph_dir(project_dir)
```

with:

```python
wg_dir = _ensure_wg_init(project_dir)
```

- [ ] **Step 5: Run focused tests**

```bash
uv run python -m unittest tests.test_install_ordering tests.test_workgraph -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add driftdriver/cli/check.py driftdriver/cli/install_cmd.py tests/test_install_ordering.py
git commit -m "fix(install): initialize Workgraph before Speedrift state"
```

---

### Task 5: Honest external Agency boundary

**Files:**
- Modify: `driftdriver/planforge_agency.py`
- Modify: `tests/test_planforge_agency.py`

**Interfaces:**
- Changes: `compose_debate_prompt(..., agency_enabled: bool = False, agency_port: int = 8000, ...) -> ComposeResult`.
- Changes: `AgencyResult(prompt: str, agency_task_id: str | None)`.
- Changes: `ComposeResult(prompt: str, used_agency: bool, agency_task_id: str | None, learning_completed: bool, degradation_reason: str | None)`.
- Adds CLI flag: `--agency` to opt into experimental composition.

- [ ] **Step 1: Add failing tests**

Add tests asserting:

```python
result = compose_debate_prompt(
    role="architect",
    context={},
    desired_outcomes=[],
    session_dir="/tmp/session",
)
self.assertFalse(result.used_agency)
self.assertFalse(result.learning_completed)
self.assertEqual(result.degradation_reason, "disabled")
```

Patch `_call_agency` and assert it is not called by default.

Add an explicit-enabled test returning:

```json
{
  "assignments": [
    {
      "agency_task_id": "agency-123",
      "rendered_prompt": "composed"
    }
  ]
}
```

and assert `agency_task_id == "agency-123"`, `used_agency is True`, and `learning_completed is False`.

Add a CLI parser test asserting the default port is `8000` and `--agency` defaults false.

- [ ] **Step 2: Verify failures**

```bash
uv run python -m unittest tests.test_planforge_agency -v
```

Expected: current code calls Agency by default, uses port `8765`, and discards the Agency task identifier.

- [ ] **Step 3: Implement the result metadata and default-off branch**

The first branch in `compose_debate_prompt` must be:

```python
fallback = builtin_prompt(
    role=role,
    context=context,
    desired_outcomes=desired_outcomes,
)
if not agency_enabled:
    return ComposeResult(
        prompt=fallback,
        used_agency=False,
        agency_task_id=None,
        learning_completed=False,
        degradation_reason="disabled",
    )
```

When explicitly enabled, call port `8000`, preserve the identifier, and set `learning_completed=False`. On failure, return the built-in prompt with a non-secret failure category such as `"unavailable"` or `"invalid_response"`.

- [ ] **Step 4: Run tests**

```bash
uv run python -m unittest tests.test_planforge_agency -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/planforge_agency.py tests/test_planforge_agency.py
git commit -m "fix(agency): default to honest built-in composition"
```

---

### Task 6: speedriftd authority and generated guidance

**Files:**
- Modify: `driftdriver/templates/adapters/codex/AGENTS.md.partial`
- Modify: `driftdriver/templates/adapters/claude-code/CLAUDE.md.partial`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `tests/test_install_templates.py`

**Interfaces:**
- Documents: `speedriftd` is the sole operator-facing supervisor.
- Removes: unscoped instructions to run `wg service start` directly.
- Preserves: direct Workgraph service control as an internal implementation detail only.

- [ ] **Step 1: Add failing template tests**

Assert every generated Speedrift guidance surface contains:

```text
speedriftd is the repo-local runtime supervisor
```

and does not contain an imperative line matching:

```text
Use `wg service start` to dispatch work
```

- [ ] **Step 2: Verify failure**

```bash
uv run python -m unittest tests.test_install_templates -v
```

Expected: current templates or generated root guidance still instruct direct Workgraph service start.

- [ ] **Step 3: Update owning templates and regenerate tracked guidance**

Use complete sentences:

```markdown
`speedriftd` is the sole operator-facing runtime supervisor. Use its observe,
supervise, and autonomous modes to control dispatch. Direct `wg service start`
is an internal recovery or implementation command, not a parallel supervision
surface.
```

Keep `AGENTS.md` and `CLAUDE.md` in lock-step.

- [ ] **Step 4: Run tests**

```bash
uv run python -m unittest tests.test_install_templates -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add \
  driftdriver/templates/adapters/codex/AGENTS.md.partial \
  driftdriver/templates/adapters/claude-code/CLAUDE.md.partial \
  AGENTS.md CLAUDE.md tests/test_install_templates.py
git commit -m "docs(speedriftd): make supervision authority explicit"
```

---

### Task 7: Driftdriver regression and review gate

**Files:**
- No product files unless a failing regression demonstrates a defect introduced by Tasks 1-6.

- [ ] **Step 1: Run focused suites**

```bash
uv run python -m unittest \
  tests.test_workgraph \
  tests.test_graph_dir_cli \
  tests.test_task_router \
  tests.test_install_ordering \
  tests.test_planforge_agency \
  tests.test_speedriftd \
  tests.test_speedriftd_state \
  tests.test_install_templates -v
```

Expected: PASS.

- [ ] **Step 2: Run the full suite**

```bash
uv run python -m unittest discover -s tests
```

Expected: PASS.

- [ ] **Step 3: Run drift checks**

```bash
TASK_ID="${SPEEDRIFT_TASK_ID:?Speedrift must export SPEEDRIFT_TASK_ID for the active task}"
./.workgraph/drifts check --task "$TASK_ID" --write-log --create-followups
```

Expected: no unresolved blocking drift for the active Speedrift task.

- [ ] **Step 4: Request two Pi Lunaroute reviews**

The requirements reviewer checks the committed diff against `docs/superpowers/specs/2026-07-16-speedrift-maintenance-design.md`. The code-quality reviewer checks error handling, path resolution, test isolation, and absence of unrelated staged files.

- [ ] **Step 5: Push the branch**

```bash
git status --short
git push -u origin speedrift-maintenance-20260716
```

Expected: the branch is pushed and the original dirty main working tree is unchanged.
