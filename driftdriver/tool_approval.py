# ABOUTME: Tool approval gate logic for session-driver workers
# ABOUTME: Auto-approves reads, gates destructive ops through PM review

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApprovalDecision:
    action: str       # "allow", "deny"
    reason: str
    requires_review: bool = False


_SAFE_TOOLS = {"Read", "Glob", "Grep"}

_SAFE_BASH_PATTERNS = [
    r"^\s*ls(\s|$)",
    r"^\s*cat(\s|$)",
    r"^\s*grep(\s|$)",
    r"^\s*find(\s|$)",
    r"^\s*git\s+status(\s|$)",
    r"^\s*git\s+log(\s|$)",
    r"^\s*git\s+diff(\s|$)",
    r"^\s*pytest(\s|$)",
    r"^\s*pip(\s|$)",
    r"^\s*pip3(\s|$)",
    r"^\s*wg(\s|$)",
    r"^\s*coredrift(\s|$)",
    r"^\s*specdrift(\s|$)",
    r"^\s*driftdriver(\s|$)",
    r"^\s*vitest(\s|$)",
    r"^\s*jest(\s|$)",
    r"^\s*echo(\s|$)",
    r"^\s*source\s+\./",          # source local files only
    r"^\s*\.\s+\./",              # dot-source local files only
    r"^\s*which(\s|$)",
    r"^\s*type(\s|$)",
    r"^\s*cd(\s|$)",
    r"^\s*pwd(\s|$)",
    # Specific safe interpreter invocations (bare interpreters removed to prevent -c/-e bypass)
    r"^\s*python3?\s+-m\s+pytest",
    r"^\s*python3?\s+-m\s+black",
    r"^\s*python3?\s+-m\s+flake8",
    r"^\s*python3?\s+-m\s+mypy",
    r"^\s*python3?\s+-m\s+pip\s+install",
    r"^\s*node\s+--version",
    r"^\s*npm\s+(test|run|install|ci)",
    r"^\s*npx\s+",
    r"^\s*cargo\s+(build|test|check|clippy|fmt)",
    r"^\s*make\s+\w+",
    r"^\s*uv\s+run\s+",
]

_DANGEROUS_BASH_PATTERNS = [
    r"^\s*rm(\s|$)",
    r"^\s*git\s+push(\s|$)",
    r"^\s*git\s+reset(\s|$)",
    r"^\s*docker(\s|$)",
    r"curl\s+.*-X\s+(POST|PUT|DELETE|PATCH)",
    r"curl\s+.*--request\s+(POST|PUT|DELETE|PATCH)",
    r"^\s*chmod(\s|$)",
    r"^\s*chown(\s|$)",
]

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}


def is_safe_bash(command: str) -> bool:
    """Check if a bash command is safe by evaluating each segment independently."""
    cmd = command.strip()
    # Reject command substitution before pattern matching
    if "$(" in cmd or "`" in cmd:
        return False
    segments = re.split(r'\s*(?:&&|\|\||[;|])\s*', cmd)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        for pattern in _DANGEROUS_BASH_PATTERNS:
            if re.search(pattern, segment, re.IGNORECASE):
                return False
        is_segment_safe = False
        for pattern in _SAFE_BASH_PATTERNS:
            if re.search(pattern, segment):
                is_segment_safe = True
                break
        if not is_segment_safe:
            return False

    return True


def is_in_scope(file_path: str, allowed_paths: list[str]) -> bool:
    """Return True if file_path is within any of the allowed_paths."""
    resolved = Path(file_path).resolve()
    for allowed in allowed_paths:
        allowed_resolved = Path(allowed).resolve()
        try:
            resolved.relative_to(allowed_resolved)
            return True
        except ValueError:
            continue
    return False


def format_review_request(tool_name: str, tool_input: dict, reason: str) -> dict:
    """Format a human-readable review request for the PM."""
    return {
        "tool_name": tool_name,
        "input_summary": {k: str(v)[:200] for k, v in tool_input.items()},
        "reason": reason,
    }


def evaluate_tool_call(
    tool_name: str,
    tool_input: dict,
    task_contract: dict | None = None,
) -> ApprovalDecision:
    """Evaluate whether a tool call should be allowed or denied."""
    contract = task_contract or {}

    # Auto-approve safe read-only tools
    if tool_name in _SAFE_TOOLS:
        return ApprovalDecision(action="allow", reason="safe read-only tool")

    # Handle Bash
    if tool_name == "Bash":
        command = tool_input.get("command", "")

        # Check contract blocked_commands first
        blocked = contract.get("blocked_commands", [])
        for blocked_cmd in blocked:
            if blocked_cmd in command:
                return ApprovalDecision(
                    action="deny",
                    reason=f"command matches contract blocked pattern: {blocked_cmd!r}",
                    requires_review=True,
                )

        if is_safe_bash(command):
            return ApprovalDecision(action="allow", reason="read-only bash command")
        return ApprovalDecision(
            action="deny",
            reason="potentially destructive bash command",
            requires_review=True,
        )

    # Handle Write/Edit/MultiEdit tools — fail-secure when no contract or allowed_paths
    if tool_name in _WRITE_TOOLS:
        if task_contract is None:
            return ApprovalDecision(
                action="deny",
                reason="no task contract — write requires explicit scope",
            )
        allowed_paths = task_contract.get("allowed_paths")
        if not allowed_paths:
            return ApprovalDecision(
                action="deny",
                reason="no allowed_paths in contract — write requires explicit scope",
            )
        file_path = tool_input.get("file_path", "")
        if not is_in_scope(file_path, allowed_paths):
            return ApprovalDecision(
                action="deny",
                reason=f"write to {file_path!r} is outside allowed paths",
                requires_review=True,
            )
        return ApprovalDecision(action="allow", reason="write within allowed scope")

    # Default: deny unknown tools, require review
    return ApprovalDecision(action="deny", reason="unrecognized tool, requires review", requires_review=True)
