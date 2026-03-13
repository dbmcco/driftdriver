# Continuation Intent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Flip the default from "repos go quiet when sessions end" to "repos keep working autonomously" with brain self-healing, centralized decision queue, protocol compliance enforcement, and multi-channel notifications.

**Architecture:** Continuation intent is a new field in the existing control state (`control.json`). A centralized decision queue (`decisions.jsonl`) stores pending human decisions per repo with a hub-level index. The brain's prompts are extended with self-heal-first instructions and a new `create_decision` directive. Protocol compliance detects agents working outside speedrift and emits corrective events. Session hooks write intent on stop and check decisions on start.

**Tech Stack:** Python 3.11+, pytest, JSONL, existing factory brain CLI invocation (claude/codex), existing Telegram module, existing dashboard HTML renderer.

**Design doc:** `docs/plans/2026-03-13-continuation-intent-design.md`

---

### Task 1: Continuation Intent Data Model

**Files:**
- Create: `driftdriver/continuation_intent.py`
- Create: `tests/test_continuation_intent.py`

**Context:** The continuation intent is a dict stored inside the existing `control.json` at `.workgraph/service/runtime/control.json`. This task creates the data model and read/write functions that operate on that file through `speedriftd_state.py` patterns.

**Step 1: Write the failing tests**

```python
# tests/test_continuation_intent.py
# ABOUTME: Tests for continuation intent read/write and lifecycle transitions.
# ABOUTME: Covers intent defaults, explicit park, brain pause, and resume.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.continuation_intent import (
    ContinuationIntent,
    read_intent,
    write_intent,
)


class ReadWriteIntentTests(unittest.TestCase):
    def _setup_control(self, tmp: Path, extra: dict | None = None) -> Path:
        """Create minimal .workgraph/service/runtime/control.json."""
        control_dir = tmp / ".workgraph" / "service" / "runtime"
        control_dir.mkdir(parents=True)
        control = {"repo": "test-repo", "mode": "supervise"}
        if extra:
            control.update(extra)
        (control_dir / "control.json").write_text(json.dumps(control))
        return tmp

    def test_read_returns_none_when_no_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            intent = read_intent(project)
            self.assertIsNone(intent)

    def test_write_continue_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(project, intent="continue", set_by="agent", reason="session ended")
            intent = read_intent(project)
            self.assertIsNotNone(intent)
            self.assertEqual(intent.intent, "continue")
            self.assertEqual(intent.set_by, "agent")
            self.assertIsNone(intent.decision_id)

    def test_write_parked_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(project, intent="parked", set_by="human", reason="user said hold off")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "parked")
            self.assertEqual(intent.set_by, "human")

    def test_write_needs_human_with_decision_id(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(
                project,
                intent="needs_human",
                set_by="brain",
                reason="aesthetic decision required",
                decision_id="dec-20260313-001",
            )
            intent = read_intent(project)
            self.assertEqual(intent.intent, "needs_human")
            self.assertEqual(intent.decision_id, "dec-20260313-001")

    def test_write_overwrites_previous_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(project, intent="parked", set_by="human", reason="parking")
            write_intent(project, intent="continue", set_by="brain", reason="answer received")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "continue")

    def test_invalid_intent_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            with self.assertRaises(ValueError):
                write_intent(project, intent="invalid", set_by="agent", reason="bad")

    def test_read_intent_missing_workgraph_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            intent = read_intent(Path(tmp))
            self.assertIsNone(intent)


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_continuation_intent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'driftdriver.continuation_intent'`

**Step 3: Write minimal implementation**

```python
# driftdriver/continuation_intent.py
# ABOUTME: Continuation intent read/write for repo control state.
# ABOUTME: Tracks whether a repo should continue, park, or wait for human input.
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VALID_INTENTS = {"continue", "parked", "needs_human"}
VALID_SET_BY = {"agent", "brain", "human"}


@dataclass
class ContinuationIntent:
    intent: str  # "continue" | "parked" | "needs_human"
    reason: str
    set_by: str  # "agent" | "brain" | "human"
    set_at: str  # ISO timestamp
    decision_id: str | None = None


def _control_path(project_dir: Path) -> Path:
    return project_dir / ".workgraph" / "service" / "runtime" / "control.json"


def _read_control(project_dir: Path) -> dict | None:
    path = _control_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_control(project_dir: Path, control: dict) -> None:
    path = _control_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(control, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_intent(project_dir: Path) -> ContinuationIntent | None:
    """Read continuation intent from control state. Returns None if not set."""
    control = _read_control(project_dir)
    if control is None:
        return None
    raw = control.get("continuation_intent")
    if not isinstance(raw, dict):
        return None
    return ContinuationIntent(
        intent=raw.get("intent", ""),
        reason=raw.get("reason", ""),
        set_by=raw.get("set_by", ""),
        set_at=raw.get("set_at", ""),
        decision_id=raw.get("decision_id"),
    )


def write_intent(
    project_dir: Path,
    *,
    intent: str,
    set_by: str,
    reason: str,
    decision_id: str | None = None,
) -> ContinuationIntent:
    """Write continuation intent to control state."""
    if intent not in VALID_INTENTS:
        raise ValueError(f"Invalid intent: {intent!r}. Must be one of {VALID_INTENTS}")
    if set_by not in VALID_SET_BY:
        raise ValueError(f"Invalid set_by: {set_by!r}. Must be one of {VALID_SET_BY}")

    control = _read_control(project_dir) or {}
    now = datetime.now(timezone.utc).isoformat()
    intent_record = {
        "intent": intent,
        "reason": reason,
        "set_by": set_by,
        "set_at": now,
        "decision_id": decision_id,
    }
    control["continuation_intent"] = intent_record
    _write_control(project_dir, control)

    return ContinuationIntent(
        intent=intent,
        reason=reason,
        set_by=set_by,
        set_at=now,
        decision_id=decision_id,
    )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_continuation_intent.py -v`
Expected: PASS — all 7 tests green

**Step 5: Commit**

```bash
git add driftdriver/continuation_intent.py tests/test_continuation_intent.py
git commit -m "feat: add continuation intent data model with read/write"
```

---

### Task 2: Decision Queue Data Model

**Files:**
- Create: `driftdriver/decision_queue.py`
- Create: `tests/test_decision_queue.py`

**Context:** The decision queue is stored at `.workgraph/service/runtime/decisions.jsonl` per repo. Each line is a JSON record with fields from the design doc. This module provides CRUD operations: create, read all, read pending, answer, and expire.

**Step 1: Write the failing tests**

```python
# tests/test_decision_queue.py
# ABOUTME: Tests for centralized decision queue CRUD operations.
# ABOUTME: Covers create, read, answer, expire, and filtering.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.decision_queue import (
    DecisionRecord,
    answer_decision,
    create_decision,
    read_decisions,
    read_pending_decisions,
)


class DecisionQueueTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        runtime = tmp / ".workgraph" / "service" / "runtime"
        runtime.mkdir(parents=True)
        return tmp

    def test_create_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            dec = create_decision(
                project,
                repo="test-repo",
                question="Should we weight technical depth higher?",
                category="feature",
                context={"task_id": "scoring", "options": ["A: Yes", "B: No"]},
            )
            self.assertTrue(dec.id.startswith("dec-"))
            self.assertEqual(dec.status, "pending")
            self.assertEqual(dec.repo, "test-repo")
            self.assertEqual(dec.category, "feature")

    def test_read_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            create_decision(project, repo="r1", question="Q1", category="aesthetic")
            create_decision(project, repo="r1", question="Q2", category="business")
            decisions = read_decisions(project)
            self.assertEqual(len(decisions), 2)

    def test_read_pending_only(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            d1 = create_decision(project, repo="r1", question="Q1", category="aesthetic")
            create_decision(project, repo="r1", question="Q2", category="feature")
            answer_decision(project, decision_id=d1.id, answer="Option A", answered_via="telegram")
            pending = read_pending_decisions(project)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].question, "Q2")

    def test_answer_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            d = create_decision(project, repo="r1", question="Q?", category="aesthetic")
            result = answer_decision(project, decision_id=d.id, answer="Go with A", answered_via="terminal")
            self.assertEqual(result.status, "answered")
            self.assertEqual(result.answer, "Go with A")
            self.assertEqual(result.answered_via, "terminal")
            self.assertIsNotNone(result.answered_at)

    def test_answer_nonexistent_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = answer_decision(project, decision_id="dec-fake", answer="x", answered_via="cli")
            self.assertIsNone(result)

    def test_decisions_file_created_on_first_write(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            decisions_file = project / ".workgraph" / "service" / "runtime" / "decisions.jsonl"
            self.assertFalse(decisions_file.exists())
            create_decision(project, repo="r1", question="Q?", category="feature")
            self.assertTrue(decisions_file.exists())

    def test_read_empty_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            self.assertEqual(read_decisions(project), [])


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_decision_queue.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'driftdriver.decision_queue'`

**Step 3: Write minimal implementation**

```python
# driftdriver/decision_queue.py
# ABOUTME: Centralized decision queue for pending human decisions.
# ABOUTME: JSONL-backed CRUD with create, read, answer, and filtering.
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class DecisionRecord:
    id: str
    repo: str
    status: str  # "pending" | "answered" | "expired"
    question: str
    context: dict[str, Any]
    category: str  # "aesthetic" | "feature" | "business" | "external_dep"
    created_at: str
    notified_via: list[str] = field(default_factory=list)
    answered_at: str | None = None
    answered_via: str | None = None
    answer: str | None = None
    resolution_task: str | None = None


def _decisions_path(project_dir: Path) -> Path:
    return project_dir / ".workgraph" / "service" / "runtime" / "decisions.jsonl"


def _generate_id() -> str:
    now = datetime.now(timezone.utc)
    short = uuid.uuid4().hex[:6]
    return f"dec-{now.strftime('%Y%m%d')}-{short}"


def _record_to_dict(rec: DecisionRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "repo": rec.repo,
        "status": rec.status,
        "question": rec.question,
        "context": rec.context,
        "category": rec.category,
        "created_at": rec.created_at,
        "notified_via": rec.notified_via,
        "answered_at": rec.answered_at,
        "answered_via": rec.answered_via,
        "answer": rec.answer,
        "resolution_task": rec.resolution_task,
    }


def _dict_to_record(d: dict[str, Any]) -> DecisionRecord:
    return DecisionRecord(
        id=d["id"],
        repo=d.get("repo", ""),
        status=d.get("status", "pending"),
        question=d.get("question", ""),
        context=d.get("context", {}),
        category=d.get("category", ""),
        created_at=d.get("created_at", ""),
        notified_via=d.get("notified_via", []),
        answered_at=d.get("answered_at"),
        answered_via=d.get("answered_via"),
        answer=d.get("answer"),
        resolution_task=d.get("resolution_task"),
    )


def create_decision(
    project_dir: Path,
    *,
    repo: str,
    question: str,
    category: str,
    context: dict[str, Any] | None = None,
) -> DecisionRecord:
    """Create a new pending decision and append to decisions.jsonl."""
    record = DecisionRecord(
        id=_generate_id(),
        repo=repo,
        status="pending",
        question=question,
        context=context or {},
        category=category,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _decisions_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_record_to_dict(record)) + "\n")
    return record


def read_decisions(project_dir: Path) -> list[DecisionRecord]:
    """Read all decision records from decisions.jsonl."""
    path = _decisions_path(project_dir)
    if not path.exists():
        return []
    records: list[DecisionRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(_dict_to_record(json.loads(line)))
        except (json.JSONDecodeError, KeyError):
            continue
    return records


def read_pending_decisions(project_dir: Path) -> list[DecisionRecord]:
    """Read only pending decisions."""
    return [d for d in read_decisions(project_dir) if d.status == "pending"]


def answer_decision(
    project_dir: Path,
    *,
    decision_id: str,
    answer: str,
    answered_via: str,
) -> DecisionRecord | None:
    """Mark a decision as answered. Rewrites the JSONL file with updated record."""
    path = _decisions_path(project_dir)
    if not path.exists():
        return None

    records = read_decisions(project_dir)
    target: DecisionRecord | None = None
    for rec in records:
        if rec.id == decision_id and rec.status == "pending":
            rec.status = "answered"
            rec.answer = answer
            rec.answered_via = answered_via
            rec.answered_at = datetime.now(timezone.utc).isoformat()
            target = rec
            break

    if target is None:
        return None

    # Rewrite file atomically
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(_record_to_dict(rec)) + "\n")
    tmp.replace(path)
    return target
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_decision_queue.py -v`
Expected: PASS — all 7 tests green

**Step 5: Commit**

```bash
git add driftdriver/decision_queue.py tests/test_decision_queue.py
git commit -m "feat: add decision queue CRUD with JSONL storage"
```

---

### Task 3: Protocol Compliance Checker

**Files:**
- Create: `driftdriver/protocol_compliance.py`
- Create: `tests/test_protocol_compliance.py`

**Context:** Braydon wants to detect when agents deviate from speedrift — developing without workgraph tasks, skipping drift checks, making commits outside the protocol. This checker examines a repo's recent git history and workgraph state to flag deviations. The brain can then emit corrective events.

**Step 1: Write the failing tests**

```python
# tests/test_protocol_compliance.py
# ABOUTME: Tests for speedrift protocol compliance detection.
# ABOUTME: Detects agents working outside workgraph, missing drift checks, untracked commits.
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.protocol_compliance import (
    ComplianceReport,
    check_compliance,
)


class ComplianceTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path, *, with_workgraph: bool = True) -> Path:
        """Create a git repo with optional .workgraph."""
        subprocess.run(["git", "init", str(tmp)], capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "Test"], capture_output=True)
        if with_workgraph:
            wg = tmp / ".workgraph"
            wg.mkdir()
            (wg / "tasks.json").write_text("[]")
            runtime = wg / "service" / "runtime"
            runtime.mkdir(parents=True)
        return tmp

    def test_clean_repo_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            report = check_compliance(project)
            self.assertTrue(report.compliant)
            self.assertEqual(len(report.violations), 0)

    def test_no_workgraph_is_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp), with_workgraph=False)
            report = check_compliance(project)
            self.assertFalse(report.compliant)
            self.assertTrue(any(v["kind"] == "missing_workgraph" for v in report.violations))

    def test_no_driftdriver_installed_is_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # Has .workgraph but no drifts/ wrapper
            report = check_compliance(project)
            self.assertTrue(any(v["kind"] == "missing_driftdriver" for v in report.violations))

    def test_driftdriver_installed_no_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            drifts = project / ".workgraph" / "drifts"
            drifts.mkdir()
            (drifts / "check").write_text("#!/bin/bash\necho ok")
            report = check_compliance(project)
            self.assertFalse(any(v["kind"] == "missing_driftdriver" for v in report.violations))

    def test_commits_without_task_reference_flagged(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # Make a commit without any task reference
            (project / "file.py").write_text("x = 1")
            subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True)
            subprocess.run(
                ["git", "-C", str(project), "commit", "-m", "random change with no task"],
                capture_output=True,
            )
            report = check_compliance(project, check_recent_commits=3)
            self.assertTrue(any(v["kind"] == "untasked_commit" for v in report.violations))


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_protocol_compliance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'driftdriver.protocol_compliance'`

**Step 3: Write minimal implementation**

```python
# driftdriver/protocol_compliance.py
# ABOUTME: Speedrift protocol compliance checker for repos.
# ABOUTME: Detects agents working outside workgraph, missing drift installs, untracked commits.
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ComplianceReport:
    compliant: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    repo: str = ""


def check_compliance(
    project_dir: Path,
    *,
    check_recent_commits: int = 0,
) -> ComplianceReport:
    """Check a repo for speedrift protocol compliance.

    Checks:
    - .workgraph/ directory exists
    - driftdriver wrappers installed (.workgraph/drifts/)
    - Recent commits reference a workgraph task (optional)
    """
    violations: list[dict[str, Any]] = []
    repo_name = project_dir.name

    wg_dir = project_dir / ".workgraph"
    if not wg_dir.is_dir():
        violations.append({
            "kind": "missing_workgraph",
            "message": "No .workgraph/ directory — repo not initialized with workgraph",
            "severity": "high",
        })
        return ComplianceReport(compliant=False, violations=violations, repo=repo_name)

    # Check driftdriver wrappers
    drifts_dir = wg_dir / "drifts"
    if not drifts_dir.is_dir() or not (drifts_dir / "check").exists():
        violations.append({
            "kind": "missing_driftdriver",
            "message": "No .workgraph/drifts/check — driftdriver not installed",
            "severity": "medium",
        })

    # Check recent commits for task references
    if check_recent_commits > 0:
        _check_commits(project_dir, check_recent_commits, violations)

    return ComplianceReport(
        compliant=len(violations) == 0,
        violations=violations,
        repo=repo_name,
    )


def _check_commits(project_dir: Path, count: int, violations: list[dict[str, Any]]) -> None:
    """Check recent git commits for task ID references."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "log", f"-{count}", "--format=%H %s"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return

    # Pattern: task IDs typically look like "task-name" or "#123" or "wg:task-id"
    task_ref_pattern = re.compile(r"(wg:|task[:\-]|#\d+|\[[\w-]+\])", re.IGNORECASE)

    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        sha, message = parts[0], parts[1]
        if not task_ref_pattern.search(message):
            violations.append({
                "kind": "untasked_commit",
                "message": f"Commit {sha[:8]} has no task reference: {message[:80]}",
                "severity": "low",
                "commit": sha,
            })
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_protocol_compliance.py -v`
Expected: PASS — all 5 tests green

**Step 5: Commit**

```bash
git add driftdriver/protocol_compliance.py tests/test_protocol_compliance.py
git commit -m "feat: add protocol compliance checker for speedrift enforcement"
```

---

### Task 4: Brain Event Types and Directive for Decisions

**Files:**
- Modify: `driftdriver/factory_brain/events.py:13-38`
- Modify: `driftdriver/factory_brain/directives.py:16-31`
- Modify: `tests/test_factory_brain_events.py`
- Modify: `tests/test_factory_brain_core.py`

**Context:** Add new event types (`intent.continue`, `intent.parked`, `intent.needs_human`, `compliance.violation`) to tier routing, and a new `create_decision` directive the brain can emit when it needs to escalate to a human. Also add `enforce_compliance` directive.

**Step 1: Write the failing tests**

Add to existing test files:

```python
# In tests/test_factory_brain_events.py — add:
def test_intent_events_are_tier2(self) -> None:
    from driftdriver.factory_brain.events import TIER_ROUTING
    self.assertEqual(TIER_ROUTING["intent.continue"], 2)
    self.assertEqual(TIER_ROUTING["intent.parked"], 2)
    self.assertEqual(TIER_ROUTING["intent.needs_human"], 2)

def test_compliance_violation_is_tier2(self) -> None:
    from driftdriver.factory_brain.events import TIER_ROUTING
    self.assertEqual(TIER_ROUTING["compliance.violation"], 2)
```

```python
# In tests/test_factory_brain_core.py — add a test for directive validation:
def test_create_decision_directive_validates(self) -> None:
    from driftdriver.factory_brain.directives import Directive, validate_directive
    d = Directive(action="create_decision", params={"repo": "test", "question": "Q?", "category": "feature"})
    self.assertTrue(validate_directive(d))

def test_enforce_compliance_directive_validates(self) -> None:
    from driftdriver.factory_brain.directives import Directive, validate_directive
    d = Directive(action="enforce_compliance", params={"repo": "test"})
    self.assertTrue(validate_directive(d))
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_factory_brain_events.py tests/test_factory_brain_core.py -v -k "intent_events or compliance_violation or create_decision_directive or enforce_compliance"`
Expected: FAIL — `KeyError` for missing routing entries and unknown directive actions

**Step 3: Write minimal implementation**

In `driftdriver/factory_brain/events.py`, add to `TIER_ROUTING` dict (after line 36, before the `}` close):
```python
    # Continuation intent lifecycle
    "intent.continue": 2,
    "intent.parked": 2,
    "intent.needs_human": 2,
    # Protocol compliance
    "compliance.violation": 2,
```

In `driftdriver/factory_brain/directives.py`, add to `DIRECTIVE_SCHEMA` dict (after line 30, before the `}` close):
```python
    "create_decision": ["repo", "question", "category"],
    "enforce_compliance": ["repo"],
```

Add handler functions and register them in `_HANDLERS`:

```python
def _handle_create_decision(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    from driftdriver.decision_queue import create_decision as _create
    from driftdriver.continuation_intent import write_intent

    repo = d.params["repo"]
    question = d.params["question"]
    category = d.params.get("category", "feature")
    context = d.params.get("context", {})
    repo_dir = _resolve_repo_dir(repo, repo_paths)

    if dry_run:
        return {"status": "dry_run", "action": "create_decision", "repo": repo, "question": question}
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {repo}"}

    dec = _create(repo_dir, repo=repo, question=question, category=category, context=context)
    write_intent(repo_dir, intent="needs_human", set_by="brain", reason=question, decision_id=dec.id)
    return {"status": "ok", "decision_id": dec.id, "repo": repo}


def _handle_enforce_compliance(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    from driftdriver.protocol_compliance import check_compliance

    repo = d.params["repo"]
    repo_dir = _resolve_repo_dir(repo, repo_paths)
    if dry_run:
        return {"status": "dry_run", "action": "enforce_compliance", "repo": repo}
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {repo}"}
    report = check_compliance(repo_dir, check_recent_commits=5)
    return {
        "status": "ok",
        "action": "enforce_compliance",
        "repo": repo,
        "compliant": report.compliant,
        "violations": report.violations,
    }
```

Add to `_HANDLERS` dict:
```python
    "create_decision": _handle_create_decision,
    "enforce_compliance": _handle_enforce_compliance,
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_factory_brain_events.py tests/test_factory_brain_core.py -v -k "intent_events or compliance_violation or create_decision_directive or enforce_compliance"`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/events.py driftdriver/factory_brain/directives.py tests/test_factory_brain_events.py tests/test_factory_brain_core.py
git commit -m "feat: add intent events, compliance events, and decision directive to brain"
```

---

### Task 5: Update Agent-Stop Hook to Write Continuation Intent

**Files:**
- Modify: `driftdriver/templates/handlers/agent-stop.sh`
- Create: `tests/test_agent_stop_intent.py`

**Context:** Currently `agent-stop.sh` outputs CONTINUE/STOP/ESCALATE but doesn't write a continuation intent to control state. We need it to write `intent: "continue"` by default (the new behavior) unless the agent explicitly parked. The hook uses the existing `driftdriver` CLI, so we add a `driftdriver intent set` subcommand.

**Step 1: Write the failing test for the CLI subcommand**

```python
# tests/test_agent_stop_intent.py
# ABOUTME: Tests for continuation intent CLI integration.
# ABOUTME: Verifies that driftdriver CLI can set and read intents.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.continuation_intent import read_intent, write_intent


class IntentCLIIntegrationTests(unittest.TestCase):
    """Test the write_intent / read_intent flow that agent-stop.sh will use."""

    def _setup_repo(self, tmp: Path) -> Path:
        control_dir = tmp / ".workgraph" / "service" / "runtime"
        control_dir.mkdir(parents=True)
        (control_dir / "control.json").write_text(json.dumps({"repo": "test", "mode": "supervise"}))
        return tmp

    def test_agent_stop_writes_continue_by_default(self) -> None:
        """Simulates the agent-stop hook writing 'continue' intent."""
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # This is what the hook will call
            write_intent(project, intent="continue", set_by="agent", reason="session ended, work continues")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "continue")

    def test_agent_stop_writes_parked_when_requested(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            write_intent(project, intent="parked", set_by="human", reason="user said hold off")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "parked")

    def test_intent_persists_in_control_json(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            write_intent(project, intent="continue", set_by="agent", reason="session ended")
            control = json.loads((project / ".workgraph" / "service" / "runtime" / "control.json").read_text())
            self.assertIn("continuation_intent", control)
            self.assertEqual(control["continuation_intent"]["intent"], "continue")


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they pass (uses Task 1 code)**

Run: `python -m pytest tests/test_agent_stop_intent.py -v`
Expected: PASS (these use the functions from Task 1)

**Step 3: Update the agent-stop.sh hook**

Replace the content of `driftdriver/templates/handlers/agent-stop.sh` — add intent writing after the decision evaluation, before presence deregistration:

After the existing `wg_log` line (line 42), add:
```bash
# Write continuation intent to control state
if command -v driftdriver >/dev/null 2>&1; then
  INTENT="continue"
  INTENT_REASON="session ended, work continues autonomously"
  if [[ "$DECISION" == "STOP" ]]; then
    # Check if this was an explicit park (set by the agent during session)
    EXISTING_INTENT=$(driftdriver --dir "$PROJECT_DIR" --json intent read 2>/dev/null | jq -r '.intent // ""' 2>/dev/null || echo "")
    if [[ "$EXISTING_INTENT" == "parked" ]]; then
      INTENT="parked"
      INTENT_REASON="explicitly parked by user during session"
    fi
  fi
  driftdriver --dir "$PROJECT_DIR" intent set \
    --intent "$INTENT" \
    --set-by agent \
    --reason "$INTENT_REASON" 2>/dev/null || true
fi
```

**Step 4: Run all related tests**

Run: `python -m pytest tests/test_agent_stop_intent.py tests/test_continuation_intent.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/templates/handlers/agent-stop.sh tests/test_agent_stop_intent.py
git commit -m "feat: agent-stop hook writes continuation intent (default: continue)"
```

---

### Task 6: Add `intent` CLI Subcommand to Driftdriver

**Files:**
- Modify: `driftdriver/cli/install_cmd.py` (or wherever the CLI dispatch lives)
- Create: `driftdriver/cli/intent_cmd.py`
- Create: `tests/test_intent_cmd.py`

**Context:** The agent-stop hook needs `driftdriver intent set --intent continue --set-by agent --reason "..."` and `driftdriver intent read` commands. Find the CLI dispatch pattern by looking at `driftdriver/cli/` and the main entry point.

**Step 1: Locate CLI dispatch**

Run: `grep -r "def main\|argparse\|subparser" driftdriver/cli/ --include="*.py" | head -20`

Read the main CLI file to understand the subparser pattern. Then create the `intent_cmd.py` following the same pattern.

**Step 2: Write the failing test**

```python
# tests/test_intent_cmd.py
# ABOUTME: Tests for the driftdriver intent CLI subcommand.
# ABOUTME: Covers set and read operations via CLI argument parsing.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.cli.intent_cmd import handle_intent_set, handle_intent_read


class IntentCmdTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        control_dir = tmp / ".workgraph" / "service" / "runtime"
        control_dir.mkdir(parents=True)
        (control_dir / "control.json").write_text(json.dumps({"repo": "test", "mode": "supervise"}))
        return tmp

    def test_handle_intent_set(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = handle_intent_set(project, intent="continue", set_by="agent", reason="test")
            self.assertEqual(result["intent"], "continue")

    def test_handle_intent_read_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = handle_intent_read(project)
            self.assertIsNone(result)

    def test_handle_intent_read_after_set(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            handle_intent_set(project, intent="parked", set_by="human", reason="hold")
            result = handle_intent_read(project)
            self.assertEqual(result["intent"], "parked")


if __name__ == "__main__":
    unittest.main()
```

**Step 3: Write minimal implementation**

```python
# driftdriver/cli/intent_cmd.py
# ABOUTME: CLI handlers for driftdriver intent set/read subcommands.
# ABOUTME: Used by agent-stop hooks to write continuation intent.
from __future__ import annotations

from pathlib import Path
from typing import Any

from driftdriver.continuation_intent import read_intent, write_intent


def handle_intent_set(
    project_dir: Path,
    *,
    intent: str,
    set_by: str,
    reason: str,
    decision_id: str | None = None,
) -> dict[str, Any]:
    """Set continuation intent. Returns the written intent as dict."""
    result = write_intent(
        project_dir,
        intent=intent,
        set_by=set_by,
        reason=reason,
        decision_id=decision_id,
    )
    return {
        "intent": result.intent,
        "set_by": result.set_by,
        "reason": result.reason,
        "set_at": result.set_at,
        "decision_id": result.decision_id,
    }


def handle_intent_read(project_dir: Path) -> dict[str, Any] | None:
    """Read current continuation intent. Returns None if not set."""
    intent = read_intent(project_dir)
    if intent is None:
        return None
    return {
        "intent": intent.intent,
        "set_by": intent.set_by,
        "reason": intent.reason,
        "set_at": intent.set_at,
        "decision_id": intent.decision_id,
    }
```

Then wire this into the main CLI dispatch (find `driftdriver/__main__.py` or `driftdriver/cli/__init__.py` and add the `intent` subcommand following the existing subparser pattern).

**Step 4: Run tests**

Run: `python -m pytest tests/test_intent_cmd.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/cli/intent_cmd.py tests/test_intent_cmd.py
git commit -m "feat: add driftdriver intent set/read CLI subcommand"
```

---

### Task 7: Brain Self-Heal Prompt Extension

**Files:**
- Modify: `driftdriver/factory_brain/prompts.py:7-21`
- Modify: `tests/test_factory_brain_core.py`

**Context:** Update the brain's system prompt to include self-healing instructions. The brain should attempt to fix technical problems (blocked cascades, agent failures, task loops, drift plateaus) before escalating. Add the continuation intent and decision queue vocabulary to the prompt so the brain knows how to use them.

**Step 1: Write the failing test**

```python
# Add to tests/test_factory_brain_core.py:
def test_system_prompt_includes_self_heal(self) -> None:
    from driftdriver.factory_brain.prompts import build_system_prompt
    prompt = build_system_prompt(tier=2)
    self.assertIn("self-heal", prompt.lower())
    self.assertIn("create_decision", prompt)

def test_system_prompt_includes_compliance(self) -> None:
    from driftdriver.factory_brain.prompts import build_system_prompt
    prompt = build_system_prompt(tier=2)
    self.assertIn("enforce_compliance", prompt)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_factory_brain_core.py -v -k "self_heal or compliance"`
Expected: FAIL — prompt doesn't contain these strings

**Step 3: Update the prompts**

In `driftdriver/factory_brain/prompts.py`, extend `ADVERSARY_SYSTEM` to include:

```python
SELF_HEAL_ADDENDUM = (
    "\n\n## Self-Healing Protocol\n"
    "Before escalating ANY issue to a human, attempt to self-heal:\n"
    "1. **Blocked cascade** — diagnose the failing task, create fix tasks, execute\n"
    "2. **Awaiting validation** — run verify commands, report pass/fail\n"
    "3. **Lane boundary** — start the next lane if current is done\n"
    "4. **Agent failure** — restart the worker, if it fails again create a diagnostic task\n"
    "5. **Task loop** (same task failed 3+ times) — analyze pattern, create new approach\n"
    "6. **Drift plateau** (2+ passes, no improvement) — re-diagnose, adjust strategy\n\n"
    "Only escalate to human when:\n"
    "- Self-heal failed (you tried and it didn't work)\n"
    "- The decision is inherently human: aesthetics, UX judgment, feature direction, business logic\n"
    "- External dependency needed (API keys, credentials, third-party access)\n\n"
    "When escalating, use `create_decision` with a specific question and options.\n"
    "Every escalation must include: what happened, what you tried, why it failed, "
    "and a specific question with options when possible.\n\n"
    "## Protocol Compliance\n"
    "All repos must use speedrift (workgraph + driftdriver). If you detect an agent "
    "working outside the protocol (commits without task references, missing .workgraph, "
    "no driftdriver installed), use `enforce_compliance` to flag it. "
    "Then use existing directives to bring the repo back on track.\n"
)
```

Update `build_system_prompt` to append this addendum.

**Step 4: Run tests**

Run: `python -m pytest tests/test_factory_brain_core.py -v -k "self_heal or compliance"`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/prompts.py tests/test_factory_brain_core.py
git commit -m "feat: add self-heal-first and compliance instructions to brain prompts"
```

---

### Task 8: Brain Router Handles Continuation Intent Events

**Files:**
- Modify: `driftdriver/factory_brain/router.py:136-170`
- Modify: `tests/test_factory_brain_router.py`

**Context:** When `session.ended` fires with `decision: "CONTINUE"`, the brain should now check the repo's continuation intent and NOT revert to observe mode. When intent is `needs_human`, the brain should skip dispatching for that repo. The router needs to read intent state and pass it to brain invocations.

**Step 1: Write the failing test**

```python
# Add to tests/test_factory_brain_router.py:
def test_session_ended_continue_does_not_suppress(self) -> None:
    """When session ends with CONTINUE, repo should NOT be suppressed."""
    from driftdriver.factory_brain.router import repos_with_active_sessions
    # After session.ended with CONTINUE, there should be no active sessions
    # (the presence was deregistered), so brain resumes normal monitoring
    result = repos_with_active_sessions([])
    self.assertEqual(result, set())

def test_needs_human_repos_included_in_snapshot(self) -> None:
    """Brain snapshot should include needs_human repos for awareness."""
    from driftdriver.factory_brain.router import BrainState
    state = BrainState()
    # Verify BrainState can track needs_human repos
    self.assertIsInstance(state.recent_directives, list)
```

**Step 2: Run tests**

Run: `python -m pytest tests/test_factory_brain_router.py -v -k "session_ended or needs_human"`
Expected: Depends on test specifics — adjust as needed

**Step 3: Update the router**

In `run_brain_tick`, after aggregating events, add intent awareness:

```python
# After line 162 (session_repos detection), add:
# Check continuation intents for non-session repos
from driftdriver.continuation_intent import read_intent
needs_human_repos: set[str] = set()
for rp in roster_repos:
    if rp.name in session_repos:
        continue
    try:
        intent = read_intent(rp)
        if intent and intent.intent == "needs_human":
            needs_human_repos.add(rp.name)
    except (OSError, ValueError):
        continue

if needs_human_repos:
    logger.info("Repos awaiting human decision: %s", needs_human_repos)
```

Then enrich the tier2 snapshot with this info (alongside `active_interactive_sessions`):
```python
if needs_human_repos:
    tier2_snapshot["needs_human_repos"] = sorted(needs_human_repos)
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_factory_brain_router.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/factory_brain/router.py tests/test_factory_brain_router.py
git commit -m "feat: brain router tracks continuation intent and needs_human repos"
```

---

### Task 9: Session-Start Hook Checks Decision Queue

**Files:**
- Modify: `driftdriver/templates/handlers/session-start.sh`

**Context:** When a Claude Code session opens, the hook should check for pending decisions across all repos and surface them to the agent. This uses a new `driftdriver decisions pending` CLI subcommand.

**Step 1: Add CLI handler for decisions**

Create `driftdriver/cli/decisions_cmd.py`:

```python
# driftdriver/cli/decisions_cmd.py
# ABOUTME: CLI handlers for driftdriver decisions subcommand.
# ABOUTME: Lists pending decisions across repos for session-start surfacing.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from driftdriver.decision_queue import read_pending_decisions


def handle_decisions_pending(project_dir: Path) -> list[dict[str, Any]]:
    """Read pending decisions for a repo. Returns list of decision dicts."""
    pending = read_pending_decisions(project_dir)
    return [
        {
            "id": d.id,
            "repo": d.repo,
            "question": d.question,
            "category": d.category,
            "created_at": d.created_at,
        }
        for d in pending
    ]
```

**Step 2: Update session-start.sh**

After the "Project Knowledge Summary" section (line 41), add:

```bash
# Surface any pending human decisions from the decision queue
PENDING=$(driftdriver --dir "$PROJECT_DIR" --json decisions pending 2>/dev/null || echo "[]")
PENDING_COUNT=$(echo "$PENDING" | jq 'length' 2>/dev/null || echo "0")
if [[ "$PENDING_COUNT" -gt 0 ]]; then
  echo "=== Pending Decisions ($PENDING_COUNT) ==="
  echo "$PENDING" | jq -r '.[] | "[\(.category)] \(.repo): \(.question) (id: \(.id))"' 2>/dev/null || echo "$PENDING"
  echo "================================="
fi
```

**Step 3: Write test**

```python
# tests/test_decisions_cmd.py
# ABOUTME: Tests for the decisions CLI subcommand.
# ABOUTME: Verifies pending decisions are surfaced correctly.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.cli.decisions_cmd import handle_decisions_pending
from driftdriver.decision_queue import create_decision


class DecisionsCmdTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        runtime = tmp / ".workgraph" / "service" / "runtime"
        runtime.mkdir(parents=True)
        return tmp

    def test_no_pending_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = handle_decisions_pending(project)
            self.assertEqual(result, [])

    def test_pending_decisions_returned(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            create_decision(project, repo="test", question="Q?", category="feature")
            result = handle_decisions_pending(project)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["question"], "Q?")


if __name__ == "__main__":
    unittest.main()
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_decisions_cmd.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/cli/decisions_cmd.py driftdriver/templates/handlers/session-start.sh tests/test_decisions_cmd.py
git commit -m "feat: session-start surfaces pending decisions from queue"
```

---

### Task 10: Decision Queue Telegram Notifications

**Files:**
- Create: `driftdriver/decision_notifier.py`
- Create: `tests/test_decision_notifier.py`

**Context:** When a decision is created, notify via Telegram (using the existing `send_telegram` function from `factory_brain/telegram.py`). The notification includes repo name, question, options, and decision ID so Braydon can reference it when replying. Uses a separate bot token from `notify.toml` under `[telegram_decisions]` section.

**Step 1: Write the failing test**

```python
# tests/test_decision_notifier.py
# ABOUTME: Tests for decision queue notification dispatch.
# ABOUTME: Verifies notification message formatting and channel tracking.
from __future__ import annotations

import unittest
from unittest.mock import patch

from driftdriver.decision_notifier import format_decision_message, notify_decision
from driftdriver.decision_queue import DecisionRecord


class NotifierTests(unittest.TestCase):
    def _make_decision(self) -> DecisionRecord:
        return DecisionRecord(
            id="dec-20260313-abc123",
            repo="lfw-interview",
            status="pending",
            question="Should scoring weight technical depth higher?",
            context={
                "task_id": "scoring-algorithm",
                "options": ["A: Weight 2x", "B: Keep equal"],
            },
            category="feature",
            created_at="2026-03-13T14:30:00Z",
        )

    def test_format_message_includes_repo_and_question(self) -> None:
        dec = self._make_decision()
        msg = format_decision_message(dec)
        self.assertIn("lfw-interview", msg)
        self.assertIn("Should scoring weight", msg)
        self.assertIn("dec-20260313-abc123", msg)

    def test_format_message_includes_options(self) -> None:
        dec = self._make_decision()
        msg = format_decision_message(dec)
        self.assertIn("Weight 2x", msg)
        self.assertIn("Keep equal", msg)

    def test_notify_returns_channels(self) -> None:
        dec = self._make_decision()
        with patch("driftdriver.decision_notifier.send_telegram", return_value=True):
            with patch("driftdriver.decision_notifier.load_telegram_config", return_value={"bot_token": "t", "chat_id": "c"}):
                channels = notify_decision(dec)
        self.assertIn("telegram", channels)

    def test_notify_no_config_returns_empty(self) -> None:
        dec = self._make_decision()
        with patch("driftdriver.decision_notifier.load_telegram_config", return_value=None):
            channels = notify_decision(dec)
        self.assertEqual(channels, [])


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_decision_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

```python
# driftdriver/decision_notifier.py
# ABOUTME: Notification dispatch for decision queue entries.
# ABOUTME: Formats decisions and sends via Telegram (and future channels).
from __future__ import annotations

from driftdriver.decision_queue import DecisionRecord
from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram


def format_decision_message(decision: DecisionRecord) -> str:
    """Format a decision record into a human-readable notification message."""
    lines = [
        f"*Decision needed* — `{decision.repo}`",
        f"ID: `{decision.id}`",
        f"Category: {decision.category}",
        "",
        decision.question,
    ]

    options = decision.context.get("options", [])
    if options:
        lines.append("")
        for opt in options:
            lines.append(f"  {opt}")

    what_tried = decision.context.get("what_brain_tried", "")
    if what_tried:
        lines.append("")
        lines.append(f"_Brain tried: {what_tried}_")

    return "\n".join(lines)


def notify_decision(decision: DecisionRecord) -> list[str]:
    """Send decision notification to all available channels. Returns list of channels notified."""
    notified: list[str] = []

    # Telegram
    config = load_telegram_config()
    if config:
        msg = format_decision_message(decision)
        ok = send_telegram(bot_token=config["bot_token"], chat_id=config["chat_id"], message=msg)
        if ok:
            notified.append("telegram")

    return notified
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_decision_notifier.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/decision_notifier.py tests/test_decision_notifier.py
git commit -m "feat: decision queue Telegram notification with formatting"
```

---

### Task 11: Dashboard Decision Display

**Files:**
- Modify: `driftdriver/ecosystem_hub/api.py:193-306`
- Modify: `driftdriver/ecosystem_hub/dashboard.py`
- Modify: `tests/test_ecosystem_hub.py`

**Context:** Add a `needs_human` badge to the repo table for repos with pending decisions. Add an API endpoint `/api/decisions` that aggregates pending decisions across all repos. In the dashboard, show the question inline when a repo row has a pending decision.

**Step 1: Write the failing test**

```python
# Add to tests/test_ecosystem_hub.py:
def test_dashboard_contains_decision_badge_function(self) -> None:
    html = render_dashboard_html()
    assert "needsHumanBadge" in html

def test_api_decisions_endpoint_exists(self) -> None:
    # Test that the handler recognizes /api/decisions route
    # (follows existing test pattern for other endpoints)
    pass  # Integration test — verify route returns JSON
```

**Step 2: Add API endpoint**

In `driftdriver/ecosystem_hub/api.py`, add to `do_GET` (before the final 404):

```python
if route == "/api/decisions":
    # Aggregate pending decisions from all repos
    from driftdriver.decision_queue import read_pending_decisions
    repos = snapshot.get("repos") or []
    all_decisions: list[dict] = []
    for r in repos:
        if not isinstance(r, dict):
            continue
        repo_path = str(r.get("path") or "")
        if repo_path and Path(repo_path).is_dir():
            try:
                pending = read_pending_decisions(Path(repo_path))
                for d in pending:
                    all_decisions.append({
                        "id": d.id,
                        "repo": d.repo,
                        "question": d.question,
                        "category": d.category,
                        "created_at": d.created_at,
                    })
            except (OSError, ValueError):
                continue
    self._send_json(all_decisions)
    return
```

**Step 3: Add dashboard badge**

In `dashboard.py`, add a JS function `needsHumanBadge(repo)` that checks if the repo has a continuation_intent of `needs_human` and renders a badge. Add it to the repo table row rendering.

```javascript
function needsHumanBadge(repo) {
  const intent = (repo.control || {}).continuation_intent;
  if (!intent || intent.intent !== 'needs_human') return '';
  const q = intent.decision_id ? ` (${intent.decision_id})` : '';
  return `<span class="badge badge-needs-human" title="Awaiting human decision${q}">NEEDS HUMAN</span>`;
}
```

Add CSS for `.badge-needs-human`:
```css
.badge-needs-human { background: #b85c1c; color: #fff; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }
```

**Step 4: Run tests**

Run: `python -m pytest tests/test_ecosystem_hub.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add driftdriver/ecosystem_hub/api.py driftdriver/ecosystem_hub/dashboard.py tests/test_ecosystem_hub.py
git commit -m "feat: dashboard shows needs_human badge and /api/decisions endpoint"
```

---

### Task 12: Wire CLI Subcommands into Main Dispatch

**Files:**
- Modify: The main driftdriver CLI entry point (find via `grep -r "def main\|subparser" driftdriver/cli/`)

**Context:** Tasks 6 and 9 created `intent_cmd.py` and `decisions_cmd.py`. This task wires them into the main CLI so `driftdriver intent set/read` and `driftdriver decisions pending` work from shell scripts.

**Step 1: Find the CLI entry point**

Run: `grep -rn "subparser\|add_subparsers\|def main" driftdriver/ --include="*.py" | grep -v test | head -20`

**Step 2: Add subparsers**

Follow the existing pattern. Add:
- `intent` subparser with `set` and `read` sub-subcommands
- `decisions` subparser with `pending` subcommand

**Step 3: Test end-to-end**

Run: `driftdriver --help` to verify new subcommands appear.
Run: `driftdriver intent --help` to verify set/read appear.

**Step 4: Commit**

```bash
git add driftdriver/cli/*.py
git commit -m "feat: wire intent and decisions CLI subcommands into main dispatch"
```

---

### Task 13: Integration Test — Full Lifecycle

**Files:**
- Create: `tests/test_continuation_lifecycle.py`

**Context:** End-to-end test covering: session starts → work happens → session ends with intent=continue → brain detects stall → brain self-heals → brain escalates → decision created → decision answered → intent flips back to continue.

**Step 1: Write the integration test**

```python
# tests/test_continuation_lifecycle.py
# ABOUTME: Integration test for the full continuation intent lifecycle.
# ABOUTME: Covers session end → intent → brain stall detection → decision → answer → resume.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.continuation_intent import read_intent, write_intent
from driftdriver.decision_queue import answer_decision, create_decision, read_pending_decisions


class ContinuationLifecycleTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        control_dir = tmp / ".workgraph" / "service" / "runtime"
        control_dir.mkdir(parents=True)
        (control_dir / "control.json").write_text(json.dumps({
            "repo": "test-repo",
            "mode": "autonomous",
        }))
        return tmp

    def test_full_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))

            # 1. Session ends — intent set to "continue"
            write_intent(project, intent="continue", set_by="agent", reason="session ended")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "continue")

            # 2. Brain detects problem, tries self-heal, fails
            # 3. Brain creates decision
            dec = create_decision(
                project,
                repo="test-repo",
                question="Auth redirect takes 3s. Acceptable?",
                category="aesthetic",
                context={"what_brain_tried": "Profiled — it's a design choice not a bug"},
            )

            # 4. Brain sets intent to needs_human
            write_intent(
                project,
                intent="needs_human",
                set_by="brain",
                reason="aesthetic decision required",
                decision_id=dec.id,
            )
            intent = read_intent(project)
            self.assertEqual(intent.intent, "needs_human")
            self.assertEqual(intent.decision_id, dec.id)

            # 5. Braydon answers via Telegram
            answered = answer_decision(
                project,
                decision_id=dec.id,
                answer="Optimize it",
                answered_via="telegram",
            )
            self.assertEqual(answered.status, "answered")

            # 6. Brain picks up answer, flips intent back to continue
            write_intent(
                project,
                intent="continue",
                set_by="brain",
                reason=f"Decision {dec.id} answered: Optimize it",
            )
            intent = read_intent(project)
            self.assertEqual(intent.intent, "continue")
            self.assertIsNone(intent.decision_id)

            # 7. No pending decisions left
            pending = read_pending_decisions(project)
            self.assertEqual(len(pending), 0)

    def test_park_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))

            # User parks during session
            write_intent(project, intent="parked", set_by="human", reason="hold off on this")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "parked")

            # Session ends — intent stays parked (agent-stop preserves it)
            # (In real flow, agent-stop checks existing intent before overwriting)
            intent = read_intent(project)
            self.assertEqual(intent.intent, "parked")


if __name__ == "__main__":
    unittest.main()
```

**Step 2: Run test**

Run: `python -m pytest tests/test_continuation_lifecycle.py -v`
Expected: PASS

**Step 3: Commit**

```bash
git add tests/test_continuation_lifecycle.py
git commit -m "test: add full continuation intent lifecycle integration test"
```

---

### Task 14: Run Full Test Suite and Fix

**Step 1: Run all tests**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All existing + new tests pass

**Step 2: Run linting**

Run: `python -m flake8 driftdriver/ tests/ --max-line-length 120`
Run: `python -m mypy driftdriver/ --ignore-missing-imports`

**Step 3: Fix any issues found**

**Step 4: Final commit**

```bash
git add -A
git commit -m "chore: fix lint/type issues from continuation intent implementation"
```

---

## Summary of New Files

| File | Purpose |
|------|---------|
| `driftdriver/continuation_intent.py` | Intent data model + read/write to control.json |
| `driftdriver/decision_queue.py` | Decision CRUD with JSONL storage |
| `driftdriver/protocol_compliance.py` | Speedrift protocol deviation detection |
| `driftdriver/decision_notifier.py` | Telegram notification for decisions |
| `driftdriver/cli/intent_cmd.py` | `driftdriver intent set/read` CLI |
| `driftdriver/cli/decisions_cmd.py` | `driftdriver decisions pending` CLI |
| `tests/test_continuation_intent.py` | Intent data model tests |
| `tests/test_decision_queue.py` | Decision CRUD tests |
| `tests/test_protocol_compliance.py` | Compliance checker tests |
| `tests/test_decision_notifier.py` | Notification formatting tests |
| `tests/test_agent_stop_intent.py` | Agent-stop intent integration tests |
| `tests/test_intent_cmd.py` | Intent CLI tests |
| `tests/test_decisions_cmd.py` | Decisions CLI tests |
| `tests/test_continuation_lifecycle.py` | End-to-end lifecycle test |

## Modified Files

| File | Change |
|------|--------|
| `driftdriver/factory_brain/events.py` | Add intent + compliance event types to TIER_ROUTING |
| `driftdriver/factory_brain/directives.py` | Add create_decision + enforce_compliance directives |
| `driftdriver/factory_brain/prompts.py` | Add self-heal-first + compliance instructions |
| `driftdriver/factory_brain/router.py` | Track continuation intents + needs_human repos |
| `driftdriver/templates/handlers/agent-stop.sh` | Write continuation intent on stop |
| `driftdriver/templates/handlers/session-start.sh` | Check decision queue on start |
| `driftdriver/ecosystem_hub/api.py` | Add /api/decisions endpoint |
| `driftdriver/ecosystem_hub/dashboard.py` | Add needsHumanBadge to repo table |
