# ABOUTME: Central brain module — assembles prompts, calls the Anthropic API at tiered models,
# ABOUTME: and parses responses into actionable directives for the factory supervisor.
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from driftdriver.factory_brain.directives import BrainResponse, Directive, parse_brain_response
from driftdriver.factory_brain.prompts import (
    DIRECTIVE_TOOL,
    TIER_MODELS,
    build_system_prompt,
    build_user_prompt,
)


@dataclass
class BrainInvocation:
    tier: int
    model: str
    trigger: dict | None
    reasoning: str
    directives: list[dict]
    telegram: str | None
    escalate: bool
    timestamp: float
    input_tokens: int
    output_tokens: int


def invoke_brain(
    *,
    tier: int,
    trigger_event: dict | None = None,
    recent_events: list[dict] | None = None,
    snapshot: dict | None = None,
    heuristic_recommendation: str | None = None,
    recent_directives: list[dict] | None = None,
    roster: dict | None = None,
    escalation_reason: str | None = None,
    tier1_reasoning: str | None = None,
    tier2_reasoning: str | None = None,
    log_dir: Path | None = None,
) -> BrainResponse:
    """Invoke the brain at the given tier, returning parsed directives."""
    import anthropic

    model = TIER_MODELS.get(tier, TIER_MODELS[2])
    system_prompt = build_system_prompt(tier)
    user_prompt = build_user_prompt(
        trigger_event=trigger_event,
        recent_events=recent_events,
        snapshot=snapshot,
        heuristic_recommendation=heuristic_recommendation,
        recent_directives=recent_directives,
        roster=roster,
        escalation_reason=escalation_reason,
        tier1_reasoning=tier1_reasoning,
        tier2_reasoning=tier2_reasoning,
    )

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        tools=[DIRECTIVE_TOOL],
        tool_choice={"type": "tool", "name": "issue_directives"},
    )

    # Extract the tool_use block
    tool_input = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "issue_directives":
            tool_input = block.input
            break

    if tool_input is None:
        # No tool_use block — return a noop with error reasoning
        noop_response = BrainResponse(
            reasoning="Model did not return a tool_use block for issue_directives.",
            directives=[Directive(action="noop", params={"reason": "no tool_use in response"})],
            telegram=None,
            escalate=False,
        )
        return noop_response

    brain_response = parse_brain_response(tool_input)

    # Build invocation record for logging
    invocation = BrainInvocation(
        tier=tier,
        model=model,
        trigger=trigger_event,
        reasoning=brain_response.reasoning,
        directives=[{"action": d.action, "params": d.params} for d in brain_response.directives],
        telegram=brain_response.telegram,
        escalate=brain_response.escalate,
        timestamp=time.time(),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    if log_dir is not None:
        _write_brain_log(log_dir, invocation)

    return brain_response


def _write_brain_log(log_dir: Path, invocation: BrainInvocation) -> None:
    """Write brain invocation to both JSONL and markdown logs."""
    log_dir.mkdir(parents=True, exist_ok=True)

    # Machine-readable JSONL
    jsonl_path = log_dir / "brain-invocations.jsonl"
    record = {
        "tier": invocation.tier,
        "model": invocation.model,
        "trigger": invocation.trigger,
        "reasoning": invocation.reasoning,
        "directives": invocation.directives,
        "telegram": invocation.telegram,
        "escalate": invocation.escalate,
        "timestamp": invocation.timestamp,
        "input_tokens": invocation.input_tokens,
        "output_tokens": invocation.output_tokens,
    }
    with jsonl_path.open("a") as f:
        f.write(json.dumps(record) + "\n")

    # Human-readable markdown
    md_path = log_dir / "brain-log.md"
    directive_lines = []
    for d in invocation.directives:
        params_str = json.dumps(d.get("params", {}))
        directive_lines.append(f"- **{d['action']}** {params_str}")
    directives_section = "\n".join(directive_lines) if directive_lines else "- (none)"

    entry = (
        f"\n---\n"
        f"### Tier {invocation.tier} — {invocation.model}\n"
        f"**Time:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(invocation.timestamp))}\n"
        f"**Tokens:** {invocation.input_tokens} in / {invocation.output_tokens} out\n\n"
        f"**Reasoning:**\n{invocation.reasoning}\n\n"
        f"**Directives:**\n{directives_section}\n"
    )
    if invocation.telegram:
        entry += f"\n**Telegram:** {invocation.telegram}\n"
    if invocation.escalate:
        entry += f"\n**ESCALATION REQUESTED**\n"

    with md_path.open("a") as f:
        f.write(entry)
