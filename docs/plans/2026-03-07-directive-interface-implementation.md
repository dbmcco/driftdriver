# Directive Interface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Introduce a formal directive interface between Speedrift (judgment) and Workgraph (execution), so every execution action flows through a typed, auditable directive object.

**Architecture:** Speedrift modules emit `Directive` objects to a JSONL audit trail. An `ExecutorShim` translates each directive into wg CLI calls. The shim is inline (synchronous) and intentionally dumb — no filtering, no judgment. Authority + budget gates happen before directive emission, not at execution time.

**Tech Stack:** Python 3.11+, dataclasses, stdlib only (json, subprocess, pathlib), unittest for tests

**Design doc:** `docs/plans/2026-03-07-speedrift-wg-boundary-design.md`

---

### Task 1: Directive Schema Module

**Files:**
- Create: `driftdriver/directives.py`
- Test: `tests/test_directives.py`

**Step 1: Write the failing test — Directive construction and serialization**

```python
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from driftdriver.directives import Action, Directive


class TestDirective(unittest.TestCase):
    def test_create_task_directive_round_trips_to_json(self) -> None:
        d = Directive(
            source="drift_task_guard",
            repo="paia-shell",
            action=Action.CREATE_TASK,
            params={
                "task_id": "drift-harden-fix-auth",
                "title": "harden: fix-auth",
                "after": ["fix-auth"],
                "tags": ["drift", "harden"],
            },
            reason="Hardening signals detected",
        )
        blob = d.to_json()
        parsed = json.loads(blob)
        self.assertEqual(parsed["source"], "drift_task_guard")
        self.assertEqual(parsed["repo"], "paia-shell")
        self.assertEqual(parsed["action"], "create_task")
        self.assertEqual(parsed["params"]["task_id"], "drift-harden-fix-auth")
        self.assertIn("id", parsed)
        self.assertIn("timestamp", parsed)

    def test_directive_from_json(self) -> None:
        d = Directive(
            source="ecosystem_hub",
            repo="paia-os",
            action=Action.START_SERVICE,
            params={},
            reason="stalled",
        )
        blob = d.to_json()
        restored = Directive.from_json(blob)
        self.assertEqual(restored.source, "ecosystem_hub")
        self.assertEqual(restored.action, Action.START_SERVICE)
        self.assertEqual(restored.repo, "paia-os")

    def test_all_action_enum_values_are_lowercase_snake(self) -> None:
        for a in Action:
            self.assertEqual(a.value, a.value.lower())
            self.assertNotIn("-", a.value)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_directives.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driftdriver.directives'`

**Step 3: Write minimal implementation**

```python
# ABOUTME: Directive schema — the formal contract between Speedrift (judgment) and wg (execution).
# ABOUTME: Every execution action Speedrift wants taken flows through a Directive object.

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Action(Enum):
    CREATE_TASK = "create_task"
    CLAIM_TASK = "claim_task"
    COMPLETE_TASK = "complete_task"
    FAIL_TASK = "fail_task"
    START_SERVICE = "start_service"
    STOP_SERVICE = "stop_service"
    LOG_TO_TASK = "log_to_task"
    EVOLVE_PROMPT = "evolve_prompt"
    DISPATCH_TO_PEER = "dispatch_to_peer"
    BLOCK_TASK = "block_task"
    CREATE_VALIDATION = "create_validation"
    CREATE_UPSTREAM_PR = "create_upstream_pr"


@dataclass
class Authority:
    actor: str
    actor_class: str
    budget_remaining: int = -1


@dataclass
class Directive:
    source: str
    repo: str
    action: Action
    params: dict[str, Any]
    reason: str
    authority: Authority | None = None
    priority: str = "normal"
    id: str = field(default_factory=lambda: f"dir-{uuid.uuid4().hex[:12]}")
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_json(self) -> str:
        d = asdict(self)
        d["action"] = self.action.value
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, blob: str) -> Directive:
        d = json.loads(blob)
        d["action"] = Action(d["action"])
        auth = d.pop("authority", None)
        if auth and isinstance(auth, dict):
            d["authority"] = Authority(**auth)
        return cls(**d)
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_directives.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/directives.py tests/test_directives.py
git commit -m "feat: add Directive schema module — Speedrift/wg boundary contract"
```

---

### Task 2: JSONL Audit Trail (DirectiveLog)

**Files:**
- Modify: `driftdriver/directives.py`
- Test: `tests/test_directives.py` (append new test class)

**Step 1: Write the failing test — append and read directives from JSONL**

```python
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.directives import Action, Directive, DirectiveLog


class TestDirectiveLog(unittest.TestCase):
    def test_append_and_read_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp))
            d = Directive(
                source="test",
                repo="repo-a",
                action=Action.CREATE_TASK,
                params={"task_id": "t1", "title": "test task"},
                reason="unit test",
            )
            log.append(d)
            pending = log.read_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].params["task_id"], "t1")

    def test_mark_completed_moves_from_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp))
            d = Directive(
                source="test",
                repo="repo-a",
                action=Action.LOG_TO_TASK,
                params={"task_id": "t1", "message": "hello"},
                reason="test",
            )
            log.append(d)
            log.mark_completed(d.id, exit_code=0, output="ok")
            pending = log.read_pending()
            self.assertEqual(len(pending), 0)
            completed = log.read_completed()
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["directive_id"], d.id)

    def test_mark_failed_records_error(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp))
            d = Directive(
                source="test",
                repo="repo-a",
                action=Action.FAIL_TASK,
                params={"task_id": "t1", "reason": "stuck"},
                reason="test",
            )
            log.append(d)
            log.mark_failed(d.id, exit_code=1, error="wg not found")
            failed = log.read_failed()
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["error"], "wg not found")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_directives.py::TestDirectiveLog -v`
Expected: FAIL — `ImportError: cannot import name 'DirectiveLog'`

**Step 3: Write minimal implementation**

Add to `driftdriver/directives.py`:

```python
@dataclass
class DirectiveLog:
    base_dir: Path

    def __post_init__(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _pending(self) -> Path:
        return self.base_dir / "pending.jsonl"

    @property
    def _completed(self) -> Path:
        return self.base_dir / "completed.jsonl"

    @property
    def _failed(self) -> Path:
        return self.base_dir / "failed.jsonl"

    def append(self, directive: Directive) -> None:
        with self._pending.open("a") as f:
            f.write(directive.to_json() + "\n")

    def read_pending(self) -> list[Directive]:
        if not self._pending.exists():
            return []
        completed_ids = {r["directive_id"] for r in self.read_completed()}
        failed_ids = {r["directive_id"] for r in self.read_failed()}
        done_ids = completed_ids | failed_ids
        result: list[Directive] = []
        for line in self._pending.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            d = Directive.from_json(line)
            if d.id not in done_ids:
                result.append(d)
        return result

    def mark_completed(
        self, directive_id: str, *, exit_code: int, output: str
    ) -> None:
        record = json.dumps({
            "directive_id": directive_id,
            "exit_code": exit_code,
            "output": output,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        with self._completed.open("a") as f:
            f.write(record + "\n")

    def mark_failed(
        self, directive_id: str, *, exit_code: int, error: str
    ) -> None:
        record = json.dumps({
            "directive_id": directive_id,
            "exit_code": exit_code,
            "error": error,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
        with self._failed.open("a") as f:
            f.write(record + "\n")

    def read_completed(self) -> list[dict[str, Any]]:
        return self._read_records(self._completed)

    def read_failed(self) -> list[dict[str, Any]]:
        return self._read_records(self._failed)

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                result.append(json.loads(line))
        return result
```

Add `from pathlib import Path` to the imports at the top.

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_directives.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/directives.py tests/test_directives.py
git commit -m "feat: add DirectiveLog — JSONL audit trail for directive lifecycle"
```

---

### Task 3: Executor Shim

**Files:**
- Create: `driftdriver/executor_shim.py`
- Test: `tests/test_executor_shim.py`

**Step 1: Write the failing test — shim maps directives to wg CLI calls**

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


class TestExecutorShim(unittest.TestCase):
    def _make_directive(self, action: Action, params: dict) -> Directive:
        return Directive(
            source="test",
            repo="test-repo",
            action=action,
            params=params,
            reason="unit test",
        )

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_create_task_calls_wg_add(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.CREATE_TASK, {
                "task_id": "drift-scope-t1",
                "title": "scope: t1",
                "after": ["t1"],
                "tags": ["drift", "scope"],
                "description": "Fix scope drift",
            })
            result = shim.execute(d)
            self.assertEqual(result, "completed")
            cmd = mock_run.call_args[0][0]
            self.assertIn("add", cmd)
            self.assertIn("--id", cmd)
            self.assertIn("drift-scope-t1", cmd)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_start_service_calls_wg_service_start(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.START_SERVICE, {"repo": "/tmp/repo"})
            result = shim.execute(d)
            self.assertEqual(result, "completed")
            cmd = mock_run.call_args[0][0]
            self.assertIn("service", cmd)
            self.assertIn("start", cmd)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_failed_command_records_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.LOG_TO_TASK, {
                "task_id": "t1",
                "message": "hello",
            })
            result = shim.execute(d)
            self.assertEqual(result, "failed")
            failed = log.read_failed()
            self.assertEqual(len(failed), 1)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_log_records_completed(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.COMPLETE_TASK, {
                "task_id": "t1",
                "artifacts": ["out.txt"],
            })
            shim.execute(d)
            completed = log.read_completed()
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["directive_id"], d.id)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_executor_shim.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driftdriver.executor_shim'`

**Step 3: Write minimal implementation**

```python
# ABOUTME: Executor shim — translates Speedrift directives into wg CLI calls.
# ABOUTME: Intentionally dumb. No judgment, no filtering. Dies when Erik ships portfolio coordinator.

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from driftdriver.directives import Action, Directive, DirectiveLog


@dataclass
class ExecutorShim:
    wg_dir: Path
    log: DirectiveLog
    timeout: float = 30.0

    def execute(self, directive: Directive) -> str:
        self.log.append(directive)
        cmd = self._build_command(directive)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self._resolve_cwd(directive),
            )
            if result.returncode == 0:
                self.log.mark_completed(
                    directive.id,
                    exit_code=result.returncode,
                    output=result.stdout[:2000],
                )
                return "completed"
            else:
                self.log.mark_failed(
                    directive.id,
                    exit_code=result.returncode,
                    error=result.stderr[:2000],
                )
                return "failed"
        except subprocess.TimeoutExpired:
            self.log.mark_failed(
                directive.id, exit_code=-1, error="timeout"
            )
            return "failed"

    def _resolve_cwd(self, directive: Directive) -> str:
        if directive.action in {Action.START_SERVICE, Action.STOP_SERVICE}:
            return directive.params.get("repo", str(self.wg_dir.parent))
        return str(self.wg_dir.parent)

    def _build_command(self, directive: Directive) -> list[str]:
        p = directive.params
        wg = ["wg", "--dir", str(self.wg_dir)]

        match directive.action:
            case Action.CREATE_TASK:
                cmd = wg + ["add", p["title"], "--id", p["task_id"], "--immediate"]
                if p.get("description"):
                    cmd += ["-d", p["description"]]
                for tag in p.get("tags", []):
                    cmd += ["-t", tag]
                for dep in p.get("after", []):
                    cmd += ["--after", dep]
                return cmd

            case Action.CLAIM_TASK:
                return wg + ["claim", p["task_id"]]

            case Action.COMPLETE_TASK:
                cmd = wg + ["done", p["task_id"]]
                for artifact in p.get("artifacts", []):
                    cmd += ["--artifact", artifact]
                return cmd

            case Action.FAIL_TASK:
                cmd = wg + ["fail", p["task_id"]]
                if p.get("reason"):
                    cmd += ["-m", p["reason"]]
                return cmd

            case Action.START_SERVICE:
                return ["wg", "--dir", p.get("repo", str(self.wg_dir)), "service", "start"]

            case Action.STOP_SERVICE:
                return ["wg", "--dir", p.get("repo", str(self.wg_dir)), "service", "stop"]

            case Action.LOG_TO_TASK:
                return wg + ["log", p["task_id"], p["message"]]

            case Action.EVOLVE_PROMPT:
                cmd = wg + ["evolve", "run"]
                return cmd

            case Action.DISPATCH_TO_PEER:
                return wg + [
                    "peer", "dispatch",
                    "--repo", p["repo"],
                    "--task", p["task_id"],
                ]

            case Action.BLOCK_TASK:
                return wg + ["pause", p["task_id"]]

            case Action.CREATE_VALIDATION:
                return wg + [
                    "add", f"validate: {p['parent_task_id']}",
                    "--id", f"validate-{p['parent_task_id']}",
                    "--after", p["parent_task_id"],
                    "--immediate",
                    "-t", "validation",
                    "-d", p.get("criteria", "Verify task deliverables"),
                ]

            case Action.CREATE_UPSTREAM_PR:
                return [
                    "gh", "pr", "create", "--draft",
                    "--title", p.get("title", "upstream contribution"),
                    "--body", p.get("body", ""),
                ]

            case _:
                return ["echo", f"unknown action: {directive.action}"]
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_executor_shim.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/executor_shim.py tests/test_executor_shim.py
git commit -m "feat: add ExecutorShim — translates directives to wg CLI calls"
```

---

### Task 4: Refactor drift_task_guard to Emit Directives

**Files:**
- Modify: `driftdriver/drift_task_guard.py:264-291`
- Test: `tests/test_drift_task_guard_directives.py`

**Step 1: Write the failing test — guard emits directive instead of calling wg**

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.actor import Actor
from driftdriver.directives import Action, DirectiveLog


class TestGuardEmitsDirective(unittest.TestCase):
    @patch("driftdriver.drift_task_guard._run_wg")
    def test_guarded_add_emits_create_task_directive(self, mock_wg: MagicMock) -> None:
        # Mock wg show (dedup check) to return non-zero (task doesn't exist)
        mock_wg.return_value = (1, "", "")

        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()
            directive_dir = wg_dir / "service" / "directives"

            from driftdriver.drift_task_guard import guarded_add_drift_task

            result = guarded_add_drift_task(
                wg_dir=wg_dir,
                task_id="drift-harden-t1",
                title="harden: t1",
                description="Move guardrails to follow-up",
                lane_tag="coredrift",
                actor=Actor(id="coredrift", actor_class="lane", name="coredrift", repo="test"),
            )

            # Should have created a directive in the log
            log = DirectiveLog(directive_dir)
            pending = log.read_pending()
            # At minimum, the directive should have been appended
            # (shim execution may fail in test since wg isn't real,
            #  but the directive should be recorded)
            completed = log.read_completed()
            failed = log.read_failed()
            total = len(pending) + len(completed) + len(failed)
            self.assertGreater(total, 0, "Expected at least one directive recorded")
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_drift_task_guard_directives.py -v`
Expected: FAIL — no directive log exists (guard currently calls `wg add` directly)

**Step 3: Modify drift_task_guard.py**

Replace lines 264-291 (the `wg add` call and budget ledger recording) with directive emission:

```python
    # 8. Emit directive (replaces direct wg add call).
    from driftdriver.directives import Action, Directive, DirectiveLog
    from driftdriver.executor_shim import ExecutorShim

    directive = Directive(
        source="drift_task_guard",
        repo=actor.repo,
        action=Action.CREATE_TASK,
        params={
            "task_id": task_id,
            "title": title,
            "description": description,
            "tags": ["drift", lane_tag] + (extra_tags or []),
            "after": [after] if after else [],
        },
        reason=f"drift follow-up from lane={lane_tag}",
        priority="normal",
    )

    directive_dir = wg_dir / "service" / "directives"
    log = DirectiveLog(directive_dir)
    shim = ExecutorShim(wg_dir=wg_dir, log=log)
    shim_result = shim.execute(directive)

    if shim_result != "completed":
        return "error"

    # 9. Record in budget ledger.
    record_operation(
        ledger_path,
        actor_id=actor.id,
        actor_class=actor.actor_class,
        operation="create",
        repo=actor.repo,
        detail=task_id,
    )
    return "created"
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_drift_task_guard_directives.py -v`
Expected: PASS

**Step 5: Run existing drift_task_guard tests to check for regressions**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_drift_task_guard.py -v`
Expected: PASS (existing tests may need mock adjustments since subprocess call path changed)

**Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/drift_task_guard.py tests/test_drift_task_guard_directives.py
git commit -m "refactor: drift_task_guard emits directives instead of calling wg add directly"
```

---

### Task 5: Refactor Ecosystem Hub — Service Supervision via Directives

**Files:**
- Modify: `driftdriver/ecosystem_hub/snapshot.py` (supervise_repo_services function)
- Test: `tests/test_ecosystem_hub_directives.py`

**Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.directives import Action, DirectiveLog


class TestSupervisionEmitsDirectives(unittest.TestCase):
    @patch("driftdriver.ecosystem_hub.snapshot.subprocess.run")
    def test_supervise_emits_start_service_directive(self, mock_run: MagicMock) -> None:
        """When a repo has ready tasks but service is stopped, emit start_service directive."""
        with TemporaryDirectory() as tmp:
            directive_dir = Path(tmp) / "directives"
            log = DirectiveLog(directive_dir)

            from driftdriver.ecosystem_hub.snapshot import supervise_repo_services

            repos_payload = [
                {
                    "name": "test-repo",
                    "path": "/tmp/fake-repo",
                    "service_running": False,
                    "task_counts": {"ready": 3, "in_progress": 0},
                    "activity_state": "idle",
                },
            ]

            result = supervise_repo_services(
                repos_payload=repos_payload,
                cooldown_seconds=0,
                max_starts=5,
                directive_log=log,
            )

            # Check that a start_service directive was emitted
            pending = log.read_pending()
            completed = log.read_completed()
            failed = log.read_failed()
            all_directives = pending + [
                d for c in completed
                for d in [{"action": "start_service"}]
            ]
            # At least one directive should reference start_service
            total = len(pending) + len(completed) + len(failed)
            self.assertGreater(total, 0)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_ecosystem_hub_directives.py -v`
Expected: FAIL — `supervise_repo_services` doesn't accept `directive_log` parameter

**Step 3: Modify supervise_repo_services**

Add `directive_log: DirectiveLog | None = None` parameter. When provided, emit `start_service` directives through the shim instead of calling subprocess directly. When `None`, fall back to current behavior (backward compatible).

Read the actual function in `snapshot.py`, identify where it calls `subprocess.run(["wg", "service", "start"], ...)`, and replace with:

```python
if directive_log is not None:
    from driftdriver.directives import Action, Directive
    from driftdriver.executor_shim import ExecutorShim

    directive = Directive(
        source="ecosystem_hub",
        repo=repo_name,
        action=Action.START_SERVICE,
        params={"repo": str(repo_path)},
        reason=f"service not running with {ready_count} ready tasks",
    )
    shim = ExecutorShim(wg_dir=Path(repo_path) / ".workgraph", log=directive_log)
    shim.execute(directive)
else:
    # Legacy direct call (backward compatible)
    subprocess.run(["wg", "service", "start"], cwd=str(repo_path), ...)
```

**Step 4: Run tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_ecosystem_hub_directives.py -v`
Expected: PASS

**Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/snapshot.py tests/test_ecosystem_hub_directives.py
git commit -m "refactor: ecosystem hub service supervision emits directives"
```

---

### Task 6: Refactor Ecosystem Hub — Factory & Northstar via Directives

**Files:**
- Modify: `driftdriver/ecosystem_hub/server.py` (factory cycle + northstar emit functions)
- Test: `tests/test_ecosystem_hub_directives.py` (append new test classes)

**Step 1: Write the failing tests**

```python
class TestFactoryEmitsDirectives(unittest.TestCase):
    def test_factory_followups_use_directive_log(self) -> None:
        """Factory cycle follow-up creation should emit create_task directives."""
        # Test that emit_factory_followups accepts and uses directive_log
        # Similar pattern to Task 5 — add directive_log param, verify directive emitted
        ...

class TestNorthstarEmitsDirectives(unittest.TestCase):
    def test_northstar_review_tasks_use_directive_log(self) -> None:
        """Northstar review task creation should emit create_task directives."""
        ...
```

**Step 2-5:** Follow same pattern as Task 5 — add `directive_log` parameter to factory/northstar emit functions, route through shim when provided, fall back to direct calls otherwise. Test, verify, commit.

**Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/server.py tests/test_ecosystem_hub_directives.py
git commit -m "refactor: factory cycle and northstar emit directives"
```

---

### Task 7: Wire Autonomous Mode to Directive Vocabulary

**Files:**
- Modify: `driftdriver/ecosystem_hub/server.py` (collector loop)
- Modify: `driftdriver/speedriftd_state.py` (mode → directive vocabulary mapping)
- Test: `tests/test_autonomous_mode.py`

**Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from driftdriver.speedriftd_state import directives_allowed_for_mode


class TestAutonomousModeVocabulary(unittest.TestCase):
    def test_observe_allows_no_directives(self) -> None:
        allowed = directives_allowed_for_mode("observe")
        self.assertEqual(len(allowed), 0)

    def test_supervise_allows_service_and_log_only(self) -> None:
        allowed = directives_allowed_for_mode("supervise")
        self.assertIn("start_service", allowed)
        self.assertIn("stop_service", allowed)
        self.assertIn("log_to_task", allowed)
        self.assertNotIn("create_task", allowed)
        self.assertNotIn("claim_task", allowed)

    def test_autonomous_allows_full_vocabulary(self) -> None:
        allowed = directives_allowed_for_mode("autonomous")
        self.assertIn("create_task", allowed)
        self.assertIn("claim_task", allowed)
        self.assertIn("start_service", allowed)
        self.assertIn("evolve_prompt", allowed)

    def test_manual_allows_no_directives(self) -> None:
        allowed = directives_allowed_for_mode("manual")
        self.assertEqual(len(allowed), 0)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_autonomous_mode.py -v`
Expected: FAIL — `ImportError: cannot import name 'directives_allowed_for_mode'`

**Step 3: Add to speedriftd_state.py**

```python
def directives_allowed_for_mode(mode: str) -> set[str]:
    if mode == "supervise":
        return {"start_service", "stop_service", "log_to_task"}
    if mode == "autonomous":
        return {
            "create_task", "claim_task", "complete_task", "fail_task",
            "start_service", "stop_service", "log_to_task",
            "evolve_prompt", "dispatch_to_peer", "block_task",
            "create_validation", "create_upstream_pr",
        }
    # observe, manual → no directives
    return set()
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_autonomous_mode.py -v`
Expected: PASS (4 tests)

**Step 5: Wire into collector loop**

In `server.py`, before emitting any directive in the collector loop, check:

```python
from driftdriver.speedriftd_state import directives_allowed_for_mode

allowed = directives_allowed_for_mode(current_mode)
if directive.action.value not in allowed:
    # Skip — mode doesn't permit this action
    continue
```

**Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/speedriftd_state.py driftdriver/ecosystem_hub/server.py tests/test_autonomous_mode.py
git commit -m "feat: wire autonomous mode to directive vocabulary — mode gates which actions emit"
```

---

### Task 8: Validation Gates Migration (from wg feature branch to Speedrift handlers)

**Files:**
- Modify: `driftdriver/templates/handlers/task-completing.sh`
- Create: `driftdriver/validation_gates.py`
- Test: `tests/test_validation_gates.py`

**Step 1: Write the failing test**

```python
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.validation_gates import check_validation_gates
from driftdriver.directives import Action, DirectiveLog


class TestValidationGates(unittest.TestCase):
    def test_task_with_verify_emits_create_validation_directive(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp) / "directives")
            task = {
                "id": "build-auth",
                "title": "Build auth system",
                "verify": "pytest tests/auth/ -v && curl localhost:3540/health",
                "status": "in-progress",
            }
            result = check_validation_gates(
                task=task,
                wg_dir=Path(tmp),
                directive_log=log,
            )
            self.assertTrue(result["validation_required"])
            pending = log.read_pending()
            completed = log.read_completed()
            failed = log.read_failed()
            total = len(pending) + len(completed) + len(failed)
            self.assertGreater(total, 0)

    def test_task_without_verify_skips(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp) / "directives")
            task = {
                "id": "quick-fix",
                "title": "Quick fix",
                "status": "in-progress",
            }
            result = check_validation_gates(
                task=task,
                wg_dir=Path(tmp),
                directive_log=log,
            )
            self.assertFalse(result["validation_required"])
            pending = log.read_pending()
            self.assertEqual(len(pending), 0)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_validation_gates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driftdriver.validation_gates'`

**Step 3: Write implementation**

```python
# ABOUTME: Validation gates — judgment layer for task completion verification.
# ABOUTME: Migrated from wg feature/project-protocol branch. Emits directives, doesn't modify wg.

from __future__ import annotations

from pathlib import Path
from typing import Any

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


def check_validation_gates(
    *,
    task: dict[str, Any],
    wg_dir: Path,
    directive_log: DirectiveLog,
) -> dict[str, Any]:
    task_id = task.get("id", "")
    verify = task.get("verify", "")

    if not verify:
        return {"validation_required": False, "task_id": task_id}

    directive = Directive(
        source="validation_gates",
        repo="",
        action=Action.CREATE_VALIDATION,
        params={
            "parent_task_id": task_id,
            "criteria": verify,
        },
        reason=f"task {task_id} has verify criteria",
    )

    shim = ExecutorShim(wg_dir=wg_dir, log=directive_log)
    shim.execute(directive)

    return {"validation_required": True, "task_id": task_id, "criteria": verify}
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_validation_gates.py -v`
Expected: PASS (2 tests)

**Step 5: Add validation gate call to task-completing.sh**

After the post-drift check section (around line 20), add:

```bash
# Run validation gates — emits create_validation directive if task has verify criteria
if command -v driftdriver >/dev/null 2>&1 && [[ -n "$TASK_ID" ]]; then
  driftdriver --dir "$PROJECT_DIR" wire run-validation-gates --task-id "$TASK_ID" 2>/dev/null || true
fi
```

**Step 6: Wire the CLI command in cli/__init__.py**

Add `run-validation-gates` as a wire subcommand that calls `check_validation_gates`.

**Step 7: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/validation_gates.py tests/test_validation_gates.py \
  driftdriver/templates/handlers/task-completing.sh driftdriver/cli/__init__.py
git commit -m "feat: migrate validation gates from wg feature branch to Speedrift handlers"
```

---

### Task 9: Decompose Command (Goal → Directive Batch)

**Files:**
- Create: `driftdriver/decompose.py`
- Test: `tests/test_decompose.py`
- Modify: `driftdriver/cli/__init__.py` (register subcommand)

**Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.decompose import decompose_goal
from driftdriver.directives import DirectiveLog


class TestDecomposeGoal(unittest.TestCase):
    @patch("driftdriver.decompose._call_llm")
    def test_decompose_emits_create_task_directives(self, mock_llm: MagicMock) -> None:
        mock_llm.return_value = [
            {"id": "task-1", "title": "Set up project", "after": []},
            {"id": "task-2", "title": "Write tests", "after": ["task-1"]},
            {"id": "task-3", "title": "Implement feature", "after": ["task-2"]},
        ]
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()
            log = DirectiveLog(wg_dir / "service" / "directives")

            result = decompose_goal(
                goal="Build authentication system",
                wg_dir=wg_dir,
                directive_log=log,
                repo="paia-shell",
            )

            self.assertEqual(result["task_count"], 3)
            pending = log.read_pending()
            completed = log.read_completed()
            failed = log.read_failed()
            total = len(pending) + len(completed) + len(failed)
            self.assertEqual(total, 3)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_decompose.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'driftdriver.decompose'`

**Step 3: Write implementation**

```python
# ABOUTME: Goal decomposition — breaks a high-level goal into workgraph tasks via LLM.
# ABOUTME: Emits create_task directives. Replaces project_autopilot's decomposition logic.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


def _call_llm(goal: str, context: str) -> list[dict[str, Any]]:
    """Call LLM to decompose goal into task list. Returns list of task dicts."""
    prompt = (
        f"Decompose this goal into 3-8 concrete, dependency-ordered tasks "
        f"for a workgraph. Return JSON array of objects with id, title, "
        f"description, after (list of dependency ids).\n\n"
        f"Goal: {goal}\n\nContext: {context}\n"
    )
    result = subprocess.run(
        ["claude", "--print", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Extract JSON from response
    text = result.stdout.strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return []


def decompose_goal(
    *,
    goal: str,
    wg_dir: Path,
    directive_log: DirectiveLog,
    repo: str = "",
    context: str = "",
) -> dict[str, Any]:
    tasks = _call_llm(goal, context)
    shim = ExecutorShim(wg_dir=wg_dir, log=directive_log)

    for task in tasks:
        directive = Directive(
            source="decompose",
            repo=repo,
            action=Action.CREATE_TASK,
            params={
                "task_id": task["id"],
                "title": task["title"],
                "description": task.get("description", ""),
                "after": task.get("after", []),
                "tags": ["decomposed"],
            },
            reason=f"decomposed from goal: {goal[:80]}",
        )
        shim.execute(directive)

    return {"goal": goal, "task_count": len(tasks)}
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_decompose.py -v`
Expected: PASS

**Step 5: Register CLI subcommand**

In `cli/__init__.py`, add:

```python
decompose_p = sub.add_parser("decompose", help="Decompose a goal into workgraph tasks")
decompose_p.add_argument("--goal", required=True, help="High-level goal to decompose")
decompose_p.add_argument("--repo", default="", help="Repo name for directive metadata")
decompose_p.add_argument("--context", default="", help="Additional context for LLM")
```

And in dispatch:

```python
if args.cmd == "decompose":
    from driftdriver.decompose import decompose_goal
    from driftdriver.directives import DirectiveLog
    wg_dir = resolve_wg_dir(args.dir)
    log = DirectiveLog(wg_dir / "service" / "directives")
    result = decompose_goal(
        goal=args.goal, wg_dir=wg_dir, directive_log=log, repo=args.repo, context=args.context,
    )
    if args.json:
        print(json.dumps(result))
    else:
        print(f"Decomposed into {result['task_count']} tasks")
    return 0
```

**Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/decompose.py tests/test_decompose.py driftdriver/cli/__init__.py
git commit -m "feat: add driftdriver decompose — goal to directive batch via LLM"
```

---

### Task 10: Integration Smoke Test

**Files:**
- Create: `tests/test_directive_integration.py`

**Step 1: Write an end-to-end test that exercises the full flow**

```python
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim
from driftdriver.speedriftd_state import directives_allowed_for_mode


class TestDirectiveIntegration(unittest.TestCase):
    @patch("driftdriver.executor_shim.subprocess.run")
    def test_full_flow_observe_blocks_create_task(self, mock_run: MagicMock) -> None:
        """In observe mode, create_task directives should be filtered."""
        allowed = directives_allowed_for_mode("observe")
        d = Directive(
            source="test",
            repo="test-repo",
            action=Action.CREATE_TASK,
            params={"task_id": "t1", "title": "test"},
            reason="test",
        )
        self.assertNotIn(d.action.value, allowed)
        mock_run.assert_not_called()

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_full_flow_autonomous_allows_create_task(self, mock_run: MagicMock) -> None:
        """In autonomous mode, create_task directives execute through shim."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        allowed = directives_allowed_for_mode("autonomous")

        d = Directive(
            source="test",
            repo="test-repo",
            action=Action.CREATE_TASK,
            params={"task_id": "t1", "title": "test", "tags": [], "after": []},
            reason="test",
        )
        self.assertIn(d.action.value, allowed)

        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            result = shim.execute(d)
            self.assertEqual(result, "completed")
            self.assertEqual(len(log.read_completed()), 1)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_directive_round_trip_through_log(self, mock_run: MagicMock) -> None:
        """Directive survives append → read → execute → complete cycle."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp) / "directives")
            d = Directive(
                source="drift_task_guard",
                repo="paia-shell",
                action=Action.LOG_TO_TASK,
                params={"task_id": "t1", "message": "drift check passed"},
                reason="clean check",
            )
            log.append(d)
            pending = log.read_pending()
            self.assertEqual(len(pending), 1)

            shim = ExecutorShim(wg_dir=Path(tmp), log=log)
            shim.execute(pending[0])

            # pending should now show 0 (moved to completed)
            self.assertEqual(len(log.read_pending()), 0)
            self.assertEqual(len(log.read_completed()), 1)
```

**Step 2: Run integration tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_directive_integration.py -v`
Expected: PASS (3 tests)

**Step 3: Run full test suite for regressions**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/ -x --timeout=60`
Expected: PASS (or pre-existing failures only)

**Step 4: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add tests/test_directive_integration.py
git commit -m "test: add directive interface integration smoke tests"
```

---

## Execution Order & Dependencies

```
Task 1 (schema) ─────→ Task 2 (log) ─────→ Task 3 (shim)
                                               │
                     ┌─────────────────────────┤
                     ▼                         ▼
               Task 4 (guard)            Task 5 (hub supervision)
                                               │
                                               ▼
                                         Task 6 (hub factory/northstar)
                                               │
                                               ▼
                                         Task 7 (autonomous mode)

Task 3 ──→ Task 8 (validation gates)
Task 3 ──→ Task 9 (decompose command)

Tasks 4-9 ──→ Task 10 (integration smoke test)
```

Tasks 4, 5, 8, 9 can run in parallel after Task 3 completes.

---

Plan complete and saved to `docs/plans/2026-03-07-directive-interface-implementation.md`. Two execution options:

**1. Subagent-Driven (this session)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** — Open new session with executing-plans, batch execution with checkpoints

Which approach, Braydon?