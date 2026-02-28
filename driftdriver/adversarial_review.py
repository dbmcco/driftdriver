# ABOUTME: Adversarial review gate — binary PASS/FAIL spec compliance check
# ABOUTME: Inspired by metaswarm pattern: fresh reviewer, evidence-required, no suggestions

from dataclasses import dataclass, field
from pathlib import Path
import subprocess


@dataclass
class Finding:
    """A single review finding with evidence."""
    severity: str  # "BLOCKING" or "WARNING"
    description: str
    file_path: str
    line: int | None = None
    evidence: str = ""


@dataclass
class AdversarialResult:
    """Result of an adversarial review."""
    verdict: str  # "PASS" or "FAIL"
    findings: list[Finding] = field(default_factory=list)
    blocking_count: int = 0
    warning_count: int = 0


def get_task_diff(project_dir: Path, base_ref: str = "HEAD~1") -> str:
    """Get the git diff for the current task."""
    result = subprocess.run(
        ["git", "diff", base_ref, "HEAD"],
        capture_output=True, text=True, cwd=str(project_dir)
    )
    return result.stdout if result.returncode == 0 else ""


def get_task_spec(wg_dir: Path, task_id: str) -> str:
    """Extract the spec/DoD from the workgraph task description."""
    result = subprocess.run(
        ["wg", "show", task_id],
        capture_output=True, text=True, cwd=str(wg_dir.parent)
    )
    return result.stdout if result.returncode == 0 else ""


def parse_review_findings(review_text: str) -> list[Finding]:
    """Parse structured review output into Finding objects."""
    findings = []
    current = None
    for line in review_text.strip().splitlines():
        line = line.strip()
        if line.startswith("BLOCKING:") or line.startswith("WARNING:"):
            if current:
                findings.append(current)
            severity = "BLOCKING" if line.startswith("BLOCKING") else "WARNING"
            description = line.split(":", 1)[1].strip()
            current = Finding(severity=severity, description=description, file_path="")
        elif current and line.startswith("File:"):
            parts = line.split(":", 2)
            if len(parts) >= 2:
                current.file_path = parts[1].strip()
                if len(parts) >= 3:
                    try:
                        current.line = int(parts[2].strip())
                    except ValueError:
                        pass
        elif current and line.startswith("Evidence:"):
            current.evidence = line.split(":", 1)[1].strip()
    if current:
        findings.append(current)
    return findings


def evaluate_review(findings: list[Finding]) -> AdversarialResult:
    """Determine PASS/FAIL from findings."""
    blocking = [f for f in findings if f.severity == "BLOCKING"]
    warnings = [f for f in findings if f.severity == "WARNING"]
    verdict = "FAIL" if blocking else "PASS"
    return AdversarialResult(
        verdict=verdict,
        findings=findings,
        blocking_count=len(blocking),
        warning_count=len(warnings),
    )


def format_review_prompt(diff: str, spec: str) -> str:
    """Build the prompt for an adversarial reviewer agent."""
    return f"""You are an adversarial code reviewer. Your ONLY job is to verify that the implementation matches the specification. You cannot make suggestions — only PASS or FAIL with evidence.

## Specification
{spec}

## Implementation Diff
{diff}

## Instructions
For each item in the specification/DoD:
1. Verify it is implemented in the diff
2. If implemented: note as verified
3. If NOT implemented or incorrectly implemented: mark as BLOCKING

Output format (one per finding):
BLOCKING: <description>
File: <path>:<line>
Evidence: <what's wrong or missing>

or:

WARNING: <description>
File: <path>:<line>
Evidence: <minor concern>

End with:
VERDICT: PASS or VERDICT: FAIL
"""


def format_result_report(result: AdversarialResult) -> str:
    """Format the review result as a human-readable report."""
    lines = [f"## Adversarial Review: {result.verdict}"]
    lines.append(f"Blocking: {result.blocking_count} | Warnings: {result.warning_count}")
    lines.append("")
    for f in result.findings:
        prefix = "BLOCK" if f.severity == "BLOCKING" else "WARN"
        loc = f"{f.file_path}:{f.line}" if f.line else f.file_path
        lines.append(f"[{prefix}] {f.description}")
        if loc:
            lines.append(f"  at {loc}")
        if f.evidence:
            lines.append(f"  evidence: {f.evidence}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    project_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    diff = get_task_diff(project_dir)
    if not diff:
        print("No diff found — nothing to review")
        sys.exit(0)
    # In actual use, this would be called by the orchestrator with a spec
    print(format_review_prompt(diff, ""))
