# ABOUTME: Executor shim — translates Speedrift directives into wg CLI calls.
# ABOUTME: Intentionally dumb. No judgment, no filtering. Dies when Erik ships portfolio coordinator.

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from driftdriver.directive_schemas import DirectiveValidationError, validate_params
from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.speedriftd_state import load_dispatch_authority

# Directive actions that mutate Workgraph dispatch/service state. These reach
# the executor as raw effects and are gated by lease authority: a directive
# arriving here without an active supervise/autonomous lease is denied (fail
# closed) so neither ``wg claim`` nor ``wg service start`` can run without one,
# regardless of which caller built the directive.
_DISPATCH_ACTIONS: frozenset[Action] = frozenset({Action.CLAIM_TASK, Action.START_SERVICE})


@dataclass
class ExecutorShim:
    wg_dir: Path
    log: DirectiveLog
    timeout: float = 30.0

    def execute(self, directive: Directive) -> str:
        self.log.append(directive)
        try:
            validate_params(directive.action, directive.params)
        except DirectiveValidationError as exc:
            payload = exc.to_payload()
            self.log.mark_failed(
                directive.id,
                exit_code=2,
                error=str(exc),
                directive=directive,
                details=payload,
            )
            return "failed"
        admitted, reason = self._admit(directive)
        if not admitted:
            self.log.mark_failed(
                directive.id,
                exit_code=0,
                error=reason,
                directive=directive,
                details={
                    "error_code": "directive_admission_denied",
                    "retryable": True,
                    "repairable": False,
                    "admission_reason": reason,
                    "retryability_basis": "lease_may_become_active",
                    "next_step": "Acquire an active supervise/autonomous lease before retrying.",
                },
            )
            return "blocked"
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
                    directive=directive,
                    details={
                        "error_code": "directive_execution_failed",
                        "retryable": None,
                        "repairable": False,
                        "observed_exit_code": result.returncode,
                        "retryability_basis": "not_classified_by_executor",
                        "next_step": "Inspect stderr and verify the target Workgraph before deciding whether to retry.",
                    },
                )
                return "failed"
        except subprocess.TimeoutExpired:
            self.log.mark_failed(
                directive.id,
                exit_code=-1,
                error="timeout",
                directive=directive,
                details={
                    "error_code": "directive_execution_timeout",
                    "retryable": None,
                    "repairable": False,
                    "observed_timeout": True,
                    "retryability_basis": "not_classified_by_executor",
                    "next_step": "Inspect the Workgraph and verify whether the effect occurred before deciding whether to retry.",
                },
            )
            return "failed"

    def _resolve_cwd(self, directive: Directive) -> str:
        if directive.action in {Action.START_SERVICE, Action.STOP_SERVICE}:
            return directive.params.get("repo", str(self.wg_dir.parent))
        if directive.action == Action.CREATE_UPSTREAM_PR:
            return directive.params.get("repo", str(self.wg_dir.parent))
        return str(self.wg_dir.parent)

    def _admit(self, directive: Directive) -> tuple[bool, str]:
        """Lease-gate dispatch-mutating actions; fail closed without a lease.

        Returns ``(admitted, reason)``. Non-dispatch actions are always admitted.
        For CLAIM_TASK / START_SERVICE the repo is resolved from the directive's
        ``repo`` param (a ``.workgraph`` path is collapsed to its repo root) or
        falls back to the shim's ``wg_dir`` parent.
        """
        if directive.action not in _DISPATCH_ACTIONS:
            return True, "action does not require lease admission"
        raw_repo = directive.params.get("repo")
        if raw_repo:
            candidate = Path(str(raw_repo))
            project_dir = candidate.parent if candidate.name == ".workgraph" else candidate
        else:
            project_dir = self.wg_dir.parent
        authority = load_dispatch_authority(project_dir)
        if authority.get("enabled"):
            return True, str(authority.get("reason") or "admitted")
        return False, str(authority.get("reason") or "dispatch not authorized")

    def _build_command(self, directive: Directive) -> list[str]:
        p = directive.params
        wg = ["wg", "--dir", str(self.wg_dir)]

        match directive.action:
            case Action.CREATE_TASK:
                cmd = wg + ["add", p["title"], "--id", p["task_id"], "--no-place"]
                if p.get("description"):
                    cmd += ["-d", p["description"]]
                if p.get("assign"):
                    cmd += ["--assign", p["assign"]]
                if p.get("model"):
                    cmd += ["--model", p["model"]]
                for tag in p.get("tags", []):
                    cmd += ["-t", tag]
                for dep in p.get("after", []):
                    cmd += ["--after", dep]
                return cmd

            case Action.CLAIM_TASK:
                cmd = wg + ["claim", p["task_id"]]
                if p.get("agent"):
                    cmd += ["--actor", p["agent"]]
                return cmd

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
                return wg + ["evolve", "run"]

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
                    "--no-place",
                    "--after", p["parent_task_id"],
                    "-t", "validation",
                    "-d", p.get("criteria", "Verify task deliverables"),
                ]

            case Action.CREATE_UPSTREAM_PR:
                cmd = [
                    "gh", "pr", "create", "--draft",
                    "--title", p.get("title", "upstream contribution"),
                    "--body", p.get("body", ""),
                ]
                if p.get("base"):
                    cmd += ["--base", p["base"]]
                if p.get("head"):
                    cmd += ["--head", p["head"]]
                return cmd

            case Action.ABANDON_TASK:
                return wg + ["abandon", p["task_id"]]

            case Action.RESCHEDULE_TASK:
                cmd = wg + ["reschedule", p["task_id"]]
                if p.get("after_hours"):
                    cmd += ["--after", str(p["after_hours"])]
                return cmd

            case _:
                return ["echo", f"unknown action: {directive.action}"]
