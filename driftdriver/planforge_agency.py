# ABOUTME: Compose planforge debate agent prompts via Agency with speedrift wrapping.
# ABOUTME: Falls back to built-in prompts when Agency is unavailable.
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


PLANFORGE_ROLES = (
    "synthesis",
    "yagni",
    "analogy",
    "ideation",
    "technical",
    "validation",
    "scoring",
)

_ROLE_DESCRIPTIONS: dict[str, str] = {
    "synthesis": (
        "You are the Synthesis agent in a PlanForge planning session.\n\n"
        "Your job: Read the discovery context and validate whether the problem is "
        "well-defined. Find gaps, ambiguities, and hidden assumptions. Do NOT propose solutions."
    ),
    "yagni": (
        "You are the Constraints agent in a PlanForge planning session.\n\n"
        "Your job: Given the discovery context, aggressively identify what should be cut, "
        "deferred, or explicitly excluded. Apply YAGNI. Challenge every assumption. "
        "What is the minimum viable scope?"
    ),
    "analogy": (
        "You are the Analogy agent in a PlanForge planning session.\n\n"
        "Your job: (1) Find 2-3 parallel problems from other domains that were solved "
        "well — what did those solutions have in common? (2) Invert the problem: what "
        "would make this problem WORSE? What does that tell us about what matters?"
    ),
    "ideation": (
        "You are the Ideation agent in a PlanForge planning session.\n\n"
        "Your job: Propose exactly 2-3 distinct solution approaches. Each must be a real "
        "option with genuine tradeoffs — not variations of the same idea. For each: what "
        "is it, what does it do well, what does it sacrifice, and what is the implementation shape?"
    ),
    "technical": (
        "You are the Technical agent in a PlanForge planning session.\n\n"
        "Your job: Evaluate each proposed approach for technical feasibility. Consider: "
        "complexity, risk, reversibility, dependencies, and fit with the existing system."
    ),
    "validation": (
        "You are the Validation agent in a PlanForge planning session.\n\n"
        "Your job: For each proposed solution approach, identify the realistic ways it fails. "
        "What are the edge cases? What assumptions does each approach make that might be wrong? "
        "Be specific — \"it might not scale\" is not useful. \"It breaks when X because Y\" is."
    ),
    "scoring": (
        "You are the Scoring agent in a PlanForge planning session.\n\n"
        "Your job: Read all Phase 2 agent outputs and produce a final scored recommendation. "
        "Score each approach across: problem fit, simplicity, technical risk, YAGNI alignment, "
        "reversibility. Produce a clear recommendation with rationale."
    ),
}


@dataclass
class AgencyResult:
    """Parsed result from an Agency task assign call."""

    prompt: str

    @classmethod
    def from_json(cls, raw: str) -> AgencyResult | None:
        """Parse Agency assignment JSON, returning None if invalid or empty."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        if isinstance(data, list):
            if not data:
                return None
            assignment = data[0]
        elif isinstance(data, dict):
            assignments = data.get("assignments", [data])
            if not assignments:
                return None
            assignment = assignments[0]
        else:
            return None

        prompt = (
            assignment.get("system_prompt")
            or assignment.get("prompt")
            or assignment.get("composed_prompt")
            or assignment.get("instructions")
            or ""
        )
        if not prompt.strip():
            return None

        return cls(prompt=prompt)


@dataclass
class ComposeResult:
    """Result of compose_debate_prompt — the prompt and whether Agency was used."""

    prompt: str
    used_agency: bool


def builtin_prompt(
    *,
    role: str,
    context: dict[str, Any],
    desired_outcomes: list[str],
) -> str:
    """Build the default prompt for a planforge debate role (no Agency)."""
    role_desc = _ROLE_DESCRIPTIONS.get(role, f"You are the {role} agent in a PlanForge planning session.")

    parts = [role_desc, ""]

    # Embed discovery context
    if context:
        parts.append("## Discovery Context")
        parts.append(json.dumps(context, indent=2))
        parts.append("")

    # Embed desired outcomes
    if desired_outcomes:
        parts.append("## Desired Outcomes")
        for outcome in desired_outcomes:
            parts.append(f"- {outcome}")
        parts.append("")

    return "\n".join(parts)


def wrap_with_speedrift(agency_prompt: str, original_prompt: str) -> str:
    """Wrap Agency-composed prompt with speedrift protocol envelope.

    If agency_prompt is empty/whitespace, returns original_prompt unchanged.
    Uses the same merge logic as agency-speedrift-wrap.py.
    """
    if not agency_prompt.strip():
        return original_prompt

    parts = []
    parts.append("## Agency-Composed Agent Identity\n")
    parts.append(agency_prompt.strip())
    parts.append("\n\n---\n")
    parts.append(original_prompt)
    return "\n".join(parts)


def _call_agency(
    *,
    role: str,
    task_id: str,
    context: dict[str, Any],
    desired_outcomes: list[str],
    agency_host: str,
    agency_port: int,
    timeout: float,
) -> AgencyResult | None:
    """Call Agency to compose a debate agent prompt. Returns None on failure."""
    # Build a task description that includes role, context, and desired outcomes
    description = json.dumps({
        "planforge_role": role,
        "context": context,
        "desired_outcomes": desired_outcomes,
    })

    task_json = json.dumps([{"id": task_id, "description": description}])

    try:
        result = subprocess.run(
            [
                "agency", "task", "assign",
                "--tasks", task_json,
                "--format", "json",
                "--timeout", str(int(timeout)),
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
            env={"AGENCY_HOST": agency_host, "AGENCY_PORT": str(agency_port)},
        )
        if result.returncode != 0:
            return None
        return AgencyResult.from_json(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def compose_debate_prompt(
    *,
    role: str,
    context: dict[str, Any],
    desired_outcomes: list[str],
    session_dir: str,
    agency_host: str = "127.0.0.1",
    agency_port: int = 8765,
    agency_timeout: float = 5.0,
) -> ComposeResult:
    """Compose a planforge debate agent prompt, trying Agency first.

    If Agency is reachable: calls Agency with role + context + desired outcomes,
    wraps the result with the speedrift protocol envelope around the built-in prompt.

    If Agency is unavailable: returns the built-in prompt unchanged.
    """
    fallback = builtin_prompt(role=role, context=context, desired_outcomes=desired_outcomes)
    task_id = f"planforge-{role}"

    # Try Agency
    agency_result = _call_agency(
        role=role,
        task_id=task_id,
        context=context,
        desired_outcomes=desired_outcomes,
        agency_host=agency_host,
        agency_port=agency_port,
        timeout=agency_timeout,
    )

    if agency_result is not None:
        wrapped = wrap_with_speedrift(agency_result.prompt, fallback)
        return ComposeResult(prompt=wrapped, used_agency=True)

    return ComposeResult(prompt=fallback, used_agency=False)


def main() -> None:
    """CLI: compose a planforge debate prompt with Agency fallback.

    Usage: python3 -m driftdriver.planforge_agency \\
             --role synthesis \\
             --context-file .workgraph/planforge/SESSION/discovery.json \\
             --session-dir .workgraph/planforge/SESSION \\
             --desired-outcomes "validate problem" "find gaps"
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Compose planforge debate prompt via Agency")
    parser.add_argument("--role", required=True, choices=PLANFORGE_ROLES)
    parser.add_argument("--context-file", required=True, help="Path to discovery JSON")
    parser.add_argument("--session-dir", required=True, help="Planforge session directory")
    parser.add_argument("--desired-outcomes", nargs="+", default=[], help="Desired outcomes")
    parser.add_argument("--agency-host", default="127.0.0.1")
    parser.add_argument("--agency-port", type=int, default=8765)
    args = parser.parse_args()

    try:
        with open(args.context_file, "r", encoding="utf-8") as f:
            context = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read context file: {e}", file=sys.stderr)
        sys.exit(1)

    result = compose_debate_prompt(
        role=args.role,
        context=context,
        desired_outcomes=args.desired_outcomes,
        session_dir=args.session_dir,
        agency_host=args.agency_host,
        agency_port=args.agency_port,
    )

    if result.used_agency:
        print("agency: composed", file=sys.stderr)
    else:
        print("agency: fallback (built-in)", file=sys.stderr)

    print(result.prompt, end="")


if __name__ == "__main__":
    main()
