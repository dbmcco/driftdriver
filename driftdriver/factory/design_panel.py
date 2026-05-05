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

from driftdriver.model_routes import model_for_route

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_API_VERSION = "2023-06-01"
_HAIKU_MODEL = model_for_route("driftdriver.design_panel_specialist")
_SONNET_MODEL = model_for_route("driftdriver.design_panel_moderator")
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


def _anthropic_api_key() -> str:
    return (
        os.environ.get("DRIFTDRIVER_ANTHROPIC_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
        or ""
    )


def _default_specialist_caller(role: str, north_star: str) -> str:
    """Call Haiku to get a specialist perspective."""
    api_key = _anthropic_api_key()
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
    api_key = _anthropic_api_key()
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
    current_ns = north_star

    for role in SPECIALIST_ROLES:
        transcript = ""
        for attempt in range(_MAX_RETRIES + 1):
            try:
                transcript = s_caller(role, current_ns)
            except Exception as exc:
                transcript = f"[{role} call failed: {exc}]"
                break
            if _quality_gate(transcript):
                break
            if attempt < _MAX_RETRIES:
                current_ns = (
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
