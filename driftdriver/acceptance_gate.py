#!/usr/bin/env python3
"""Deterministic acceptance-criteria gate for workgraph task completion.

The gate runs a task's ``verify`` commands (from its wg-contract block) before
``wg done`` sticks. If any verify command fails, completion is blocked. An
operator can degrade (override) up to a per-repo ceiling, after which the gate
hard-blocks until reset.

Model-mediated split:
  - Code owns: running verify commands, checking exit codes, counting degrades.
  - Model owns: semantic acceptance criteria (prose like "the implementation is
    clean") — those are advisory, handled by the critic, not this gate.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default per-repo degrade ceiling. Overridable via drift-policy.toml:
#   [acceptance]
#   degrade_ceiling = 5
DEFAULT_DEGRADE_CEILING = 3


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CriterionResult:
    """Result of checking one acceptance criterion (verify command)."""

    command: str
    passed: bool
    exit_code: int | None = None
    stderr: str = ""

    @property
    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        detail = f" (exit {self.exit_code})" if self.exit_code is not None else ""
        return f"[{status}] {self.command}{detail}"


@dataclass
class GateResult:
    """Overall gate verdict for a task."""

    status: str  # "pass" | "blocked" | "degraded" | "no_criteria"
    task_id: str
    results: list[CriterionResult] = field(default_factory=list)
    degrade_count: int = 0
    degrade_ceiling: int = 3
    reason: str = ""

    @property
    def passed_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def is_blocking(self) -> bool:
        """True when the gate blocks completion (status == 'blocked')."""
        return self.status == "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task_id": self.task_id,
            "passed": self.passed_count,
            "total": self.total_count,
            "degrade_count": self.degrade_count,
            "degrade_ceiling": self.degrade_ceiling,
            "reason": self.reason,
            "results": [
                {"command": r.command, "passed": r.passed, "exit_code": r.exit_code}
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Degrade state (per-repo)
# ---------------------------------------------------------------------------


def _degrade_state_path(repo: Path) -> Path:
    """Path to the per-repo degrade counter file."""
    wg = repo / ".workgraph"
    if not wg.exists():
        wg = repo / ".wg"
    return wg / "service" / "acceptance-degrade.json"


def _degrade_quarantine_path(repo: Path) -> Path:
    """Marker file: present while the degrade state was quarantined as corrupt.

    While this marker exists the gate fails closed (hard block) for the repo
    until an operator runs `driftdriver acceptance reset`. This prevents the
    CRIT-1 fail-open: corrupt/missing-content state can never silently
    restore a task's degrade budget.
    """
    return _degrade_state_path(repo).with_suffix(".quarantine")


def _degrade_lock_path(repo: Path) -> Path:
    return _degrade_state_path(repo).with_suffix(".lock")


@contextlib.contextmanager
def _degrade_lock(repo: Path):
    """Serialize the degrade read-modify-write cycle (CRIT-2).

    flock-based: releases automatically on process death, so a crashed
    holder cannot wedge the gate. Advisory, same-uid — the threat model is
    lost updates between concurrent completions, not hostile locking.
    """
    path = _degrade_lock_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def load_degrade_state(repo: Path) -> dict[str, int] | None:
    """Load the per-repo degrade counter: {task_id: degrade_count}.

    Returns None when the state is quarantined (corrupt or previously
    corrupt): the caller must fail closed, not treat it as a fresh budget.
    On corruption the offending file is renamed to
    ``acceptance-degrade.json.corrupt-<timestamp>`` for inspection and a
    quarantine marker is left behind; only `reset_degrade` clears it.
    """
    state_path = _degrade_state_path(repo)
    if _degrade_quarantine_path(repo).exists():
        return None
    if not state_path.exists():
        return {}
    try:
        state = json.loads(state_path.read_text())
    except (json.JSONDecodeError, OSError):
        # CRIT-1 remediation: quarantine + fail closed. Never silently
        # reset the budget — that is the outcome the ceiling exists to
        # prevent.
        ts = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        try:
            state_path.rename(state_path.with_name(f"{state_path.name}.corrupt-{ts}"))
        except OSError:
            pass
        try:
            _degrade_quarantine_path(repo).write_text(
                f"quarantined {dt.datetime.now().isoformat()}\n", encoding="utf-8"
            )
        except OSError:
            pass
        return None
    if not isinstance(state, dict):
        return {}
    return {str(k): int(v) for k, v in state.items() if isinstance(v, (int, float))}


def save_degrade_state(repo: Path, state: dict[str, int]) -> None:
    """Persist the per-repo degrade counter atomically (tmp + replace)."""
    path = _degrade_state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def reset_degrade(repo: Path, task_id: str | None = None) -> int:
    """Reset degrade count for a task (or all tasks if None). Returns count reset.

    Also clears any quarantine marker: reset is the operator escape from the
    fail-closed state left by a corrupt state file.
    """
    with _degrade_lock(repo):
        quarantine = _degrade_quarantine_path(repo)
        was_quarantined = quarantine.exists()
        state = load_degrade_state(repo)
        if state is None:
            state = {}
        if task_id:
            count = state.pop(task_id, 0)
            save_degrade_state(repo, state)
        else:
            count = len(state)
            save_degrade_state(repo, {})
        if was_quarantined or not task_id:
            # Clear the marker on full reset, and on task reset if present
            # (a quarantined repo has no live budget to selectively clear).
            try:
                quarantine.unlink()
            except FileNotFoundError:
                pass
        return 1 if (task_id and count) else count


def _policy_path(repo: Path) -> Path:
    """Path to the repo's drift-policy.toml (same wg-dir resolution as the
    degrade state file: .workgraph preferred, .wg accepted)."""
    wg = repo / ".workgraph"
    if not wg.exists():
        wg = repo / ".wg"
    return wg / "drift-policy.toml"


def _load_ceiling(repo: Path) -> int:
    """Load the degrade ceiling from drift-policy.toml ``[acceptance]``.

    Falls back to ``DEFAULT_DEGRADE_CEILING`` when the policy file, the
    section, or the key is missing, when the file is unreadable/corrupt, or
    when the value is invalid (non-integer or < 1 — a zero ceiling would
    silently make the gate un-degradable, which is not a sane config).
    """
    path = _policy_path(repo)
    if not path.exists():
        return DEFAULT_DEGRADE_CEILING
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_DEGRADE_CEILING
    section = data.get("acceptance")
    if not isinstance(section, dict):
        return DEFAULT_DEGRADE_CEILING
    try:
        value = int(section.get("degrade_ceiling"))
    except (TypeError, ValueError):
        return DEFAULT_DEGRADE_CEILING
    if value < 1:
        return DEFAULT_DEGRADE_CEILING
    return value


def degrade_status(repo: Path) -> dict[str, Any]:
    """Return a summary of degrade state for CLI display."""
    state = load_degrade_state(repo)
    ceiling = _load_ceiling(repo)
    quarantined = state is None
    if state is None:
        state = {}
    return {
        "total_degraded_tasks": len(state),
        "per_task": dict(state),
        "ceiling": ceiling,
        "at_ceiling": [tid for tid, c in state.items() if c >= ceiling],
        "quarantined": quarantined,
        "note": (
            "degrade state corrupt (gate fails closed); run `driftdriver acceptance reset` after inspection"
            if quarantined
            else None
        ),
    }


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------


def evaluate_acceptance(
    task_id: str,
    verify_commands: list[str],
    repo: Path,
    *,
    degrade_ceiling: int | None = None,
    timeout: int = 120,
    record_degrade: bool = True,
) -> GateResult:
    """Evaluate a task's acceptance by running its verify commands.

    Each verify command is run in the repo. If all pass, the gate passes.
    If any fail, the gate blocks unless the task is under its degrade ceiling.

    The degrade ceiling defaults to the repo's drift-policy.toml
    ``[acceptance] degrade_ceiling`` (fallback ``DEFAULT_DEGRADE_CEILING``);
    an explicit ``degrade_ceiling`` argument wins over the policy file.

    Tasks with no verify commands pass with status ``no_criteria`` (the gate
    can't check what it can't run; the critic handles semantic criteria).

    ``record_degrade``: when True (completion path), a failing gate consumes a
    degrade slot. When False (inspection), the result is read-only — the gate
    reports what would happen without consuming the override.
    """
    if degrade_ceiling is None:
        degrade_ceiling = _load_ceiling(repo)
    if not verify_commands:
        return GateResult(
            status="no_criteria",
            task_id=task_id,
            reason="No verify commands in task contract; gate cannot evaluate deterministically.",
        )

    results: list[CriterionResult] = []
    for cmd in verify_commands:
        result = _run_verify(cmd, repo, timeout)
        results.append(result)

    failures = [r for r in results if not r.passed]

    if not failures:
        return GateResult(
            status="pass",
            task_id=task_id,
            results=results,
        )

    # Some commands failed — check degrade ceiling. The whole
    # read-modify-write cycle is serialized under the degrade lock so
    # concurrent completions cannot lose updates or exceed the ceiling
    # (CRIT-2).
    with _degrade_lock(repo):
        state = load_degrade_state(repo)
        if state is None:
            # CRIT-1 remediation: corrupt state fails closed.
            return GateResult(
                status="blocked",
                task_id=task_id,
                results=results,
                reason=(
                    "Degrade state is corrupt (quarantined); the gate fails closed "
                    "rather than silently restoring override budget. Run "
                    "`driftdriver acceptance reset` after inspecting the "
                    "quarantined file."
                ),
            )
        current_degrades = state.get(task_id, 0)

        if current_degrades < degrade_ceiling:
            # Under ceiling: allow with a degrade (operator override)
            if record_degrade:
                state[task_id] = current_degrades + 1
                save_degrade_state(repo, state)
            return GateResult(
                status="degraded",
                task_id=task_id,
                results=results,
                degrade_count=current_degrades + 1,
                degrade_ceiling=degrade_ceiling,
                reason=(
                    f"{len(failures)} of {len(results)} verify commands failed. "
                    f"Degraded (override {current_degrades + 1}/{degrade_ceiling}). "
                    f"Task completes with a warning."
                ),
            )

    # At or above ceiling: hard block
    return GateResult(
        status="blocked",
        task_id=task_id,
        results=results,
        degrade_count=current_degrades,
        degrade_ceiling=degrade_ceiling,
        reason=(
            f"{len(failures)} of {len(results)} verify commands failed. "
            f"Degrade ceiling reached ({current_degrades}/{degrade_ceiling}). "
            f"Completion blocked. Fix the failures or reset the degrade counter."
        ),
    )


def _run_verify(command: str, repo: Path, timeout: int) -> CriterionResult:
    """Run a single verify command and return its result."""
    try:
        proc = subprocess.run(
            command,
            cwd=str(repo),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CriterionResult(
            command=command,
            passed=proc.returncode == 0,
            exit_code=proc.returncode,
            stderr=proc.stderr.strip()[:500] if proc.stderr else "",
        )
    except subprocess.TimeoutExpired:
        return CriterionResult(
            command=command,
            passed=False,
            exit_code=None,
            stderr=f"Timed out after {timeout}s",
        )
    except Exception as exc:
        return CriterionResult(
            command=command,
            passed=False,
            exit_code=None,
            stderr=str(exc)[:500],
        )


# ---------------------------------------------------------------------------
# Task-level integration: read verify commands from a task's contract
# ---------------------------------------------------------------------------


def _extract_verify_commands(description: str) -> list[str]:
    """Extract the verify command list from a task's wg-contract description.

    The contract block contains ``verify = ["cmd1", "cmd2"]`` in TOML-ish
    syntax inside a ```wg-contract fence. This extracts and parses it.
    """
    # Find the verify = [...] line in the description
    match = re.search(r'verify\s*=\s*\[(.*?)\]', description, re.DOTALL)
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw:
        return []
    # Parse the TOML-ish list: items are quoted strings
    commands = re.findall(r'"([^"]*)"', raw)
    return [c for c in commands if c.strip()]


def check_task(wg_dir: Path, task_id: str, *, record_degrade: bool = True) -> GateResult:
    """Read a task's verify commands from graph.jsonl and run the acceptance gate.

    This is the integration point: the coordinator (or speedrift post-task
    check) calls this before accepting a task's completion. It reads the
    task's contract from the graph, extracts the verify commands, and runs
    the deterministic gate.

    Tasks without verify commands pass with ``no_criteria`` — the gate can't
    check what it can't run, and semantic criteria are the critic's job.
    """
    repo = wg_dir.parent if wg_dir.name in (".workgraph", ".wg") else wg_dir
    graph_path = wg_dir / "graph.jsonl"

    if not graph_path.exists():
        return GateResult(
            status="no_criteria",
            task_id=task_id,
            reason=f"graph.jsonl not found at {graph_path}",
        )

    # Find the task in graph.jsonl (each line is a JSON task object)
    description = ""
    try:
        for line in graph_path.read_text().splitlines():
            if not line.strip():
                continue
            task = json.loads(line)
            if task.get("id") == task_id:
                description = task.get("description", "")
                break
    except (json.JSONDecodeError, OSError):
        pass

    verify_commands = _extract_verify_commands(description)
    return evaluate_acceptance(
        task_id, verify_commands, repo, record_degrade=record_degrade
    )
