# driftdriver/paia_agent_health/analyzer.py
# ABOUTME: Two-pass LLM analysis — Haiku detects patterns, Sonnet designs fixes.
# ABOUTME: Uses subprocess Claude CLI directly (same pattern as factory_brain/chat.py).

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from driftdriver.paia_agent_health.collector import SignalBundle

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6
SMALL_FIX_MAX_LINES = 20
SMALL_FIX_MIN_EVIDENCE = 3

_EXPERIMENTS = os.environ.get("EXPERIMENTS_DIR", os.path.expanduser("~/projects/experiments"))

_STRIPPED_ENV = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}


def _clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    path = env.get("PATH", "")
    for extra in [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin"]:
        if extra not in path:
            path = f"{extra}:{path}"
    env["PATH"] = path
    return env


@dataclass
class Finding:
    agent: str
    pattern_type: str       # "tool_failure" | "behavioral_loop" | "task_stall" | "conversation_correction"
    evidence: list[str]
    evidence_count: int
    affected_component: str # skill file path, tool name, or config key
    severity: str           # "low" | "medium" | "high"
    confidence: float       # 0.0–1.0


@dataclass
class FixProposal:
    finding: Finding
    change_summary: str
    diff: str
    auto_apply: bool
    risk: str


_PASS1_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["agent", "pattern_type", "evidence", "evidence_count",
                             "affected_component", "severity", "confidence"],
                "properties": {
                    "agent": {"type": "string"},
                    "pattern_type": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "evidence_count": {"type": "integer"},
                    "affected_component": {"type": "string"},
                    "severity": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}

_PASS2_SCHEMA = {
    "type": "object",
    "required": ["change_summary", "diff", "auto_apply", "risk"],
    "properties": {
        "change_summary": {"type": "string"},
        "diff": {"type": "string"},
        "auto_apply": {"type": "boolean"},
        "risk": {"type": "string"},
    },
}


def _invoke_claude(prompt: str, schema: dict, model: str) -> dict:
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--max-budget-usd", "1.00",
    ]
    result = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=120, env=_clean_env()
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude exit {result.returncode}: {result.stderr[:200]}")
    cli_out = json.loads(result.stdout)
    return cli_out.get("structured_output") or {}


def _bundle_to_context(bundle: SignalBundle) -> str:
    lines: list[str] = ["## Agent Signal Bundle\n"]
    for name, signals in bundle.agents.items():
        lines.append(f"### Agent: {name}")
        if signals.conversation_turns:
            lines.append("Conversation turns (recent):")
            for t in signals.conversation_turns[:20]:
                lines.append(f"  - {str(t.get('content', ''))[:200]}")
        if signals.tool_events:
            failures = [e for e in signals.tool_events if not e.get("data", {}).get("success", True)]
            lines.append(f"Tool call failures (last 24h): {len(failures)}")
            for e in failures[:5]:
                d = e.get("data", {})
                lines.append(f"  - {d.get('tool')}.{d.get('action')}: {d.get('error')}")
        if signals.task_events:
            failed = [e for e in signals.task_events if "failed" in e.get("event_type", "")]
            lines.append(f"Task failures: {len(failed)}")
        if signals.errors:
            lines.append(f"Collection errors: {signals.errors}")
        lines.append("")
    return "\n".join(lines)


def _read_component(component: str) -> str:
    """Read the current content of a skill file or config. Returns empty string if missing."""
    paths_to_try = [
        Path(_EXPERIMENTS) / component,
    ]
    for agent in ("samantha", "derek", "ingrid", "caroline"):
        paths_to_try.append(Path(_EXPERIMENTS) / agent / component)
        paths_to_try.append(Path(_EXPERIMENTS) / agent / "skills" / Path(component).name)
    for p in paths_to_try:
        if p.exists():
            return p.read_text()[:3000]
    return ""


def _is_small_fix(proposal_raw: dict, finding: Finding) -> bool:
    """True if the fix meets all auto-apply criteria."""
    diff = proposal_raw.get("diff", "")
    added_lines = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed_lines = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    changed_lines = added_lines + removed_lines
    if changed_lines > SMALL_FIX_MAX_LINES:
        return False
    if finding.evidence_count < SMALL_FIX_MIN_EVIDENCE:
        return False
    if proposal_raw.get("risk", "low") not in ("low",):
        return False
    return True


def run_analysis(bundle: SignalBundle) -> list[FixProposal]:
    """Run two-pass analysis on a SignalBundle. Returns list of FixProposals."""
    context = _bundle_to_context(bundle)

    # Pass 1: Haiku — pattern detection
    pass1_prompt = (
        "You are an agent quality analyst. Review these agent signal bundles and identify "
        "recurring failure patterns — tool failures, behavioral loops, task stalls, and "
        "explicit user corrections. Only report patterns with clear evidence. Be conservative.\n\n"
        f"{context}"
    )
    try:
        pass1_raw = _invoke_claude(pass1_prompt, _PASS1_SCHEMA, "claude-haiku-4-5-20251001")
    except Exception as exc:
        logger.warning("paia_agent_health.pass1_failed: %s", exc)
        return []

    findings: list[Finding] = []
    for raw in pass1_raw.get("findings", []):
        try:
            f = Finding(
                agent=raw["agent"],
                pattern_type=raw["pattern_type"],
                evidence=raw["evidence"],
                evidence_count=raw["evidence_count"],
                affected_component=raw["affected_component"],
                severity=raw["severity"],
                confidence=float(raw["confidence"]),
            )
            if f.confidence >= CONFIDENCE_THRESHOLD:
                findings.append(f)
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("malformed finding: %s", exc)

    if not findings:
        return []

    # Pass 2: Sonnet — fix design per finding
    proposals: list[FixProposal] = []
    for finding in findings:
        current_content = _read_component(finding.affected_component)
        pass2_prompt = (
            f"You are a PAIA agent improvement engineer. Design a specific fix for this finding.\n\n"
            f"Agent: {finding.agent}\n"
            f"Pattern: {finding.pattern_type}\n"
            f"Component: {finding.affected_component}\n"
            f"Evidence: {finding.evidence}\n"
            f"Evidence count: {finding.evidence_count}\n\n"
            f"Current component content:\n{current_content}\n\n"
            "Produce a specific diff that addresses the failure pattern. "
            "Set auto_apply=true only if: diff is ≤20 lines changed, risk is 'low', "
            "and no agent restart is needed."
        )
        try:
            pass2_raw = _invoke_claude(pass2_prompt, _PASS2_SCHEMA, "claude-sonnet-4-6")
            auto_apply = bool(pass2_raw.get("auto_apply")) and _is_small_fix(pass2_raw, finding)
            proposals.append(FixProposal(
                finding=finding,
                change_summary=pass2_raw.get("change_summary", ""),
                diff=pass2_raw.get("diff", ""),
                auto_apply=auto_apply,
                risk=pass2_raw.get("risk", "unknown"),
            ))
        except Exception as exc:
            logger.warning("paia_agent_health.pass2_failed for %s/%s: %s",
                           finding.agent, finding.affected_component, exc)

    return proposals
