# ABOUTME: Central brain module — assembles prompts, invokes Claude CLI at tiered models,
# ABOUTME: and parses responses into actionable directives for the factory supervisor.
from __future__ import annotations

import json
import logging
import subprocess
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
from driftdriver.llm_meter import (
    extract_usage_from_claude_json,
    record_spend,
)
from driftdriver.signal_gate import record_fire, should_fire

logger = logging.getLogger(__name__)


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
    dry_run: bool = False
    backend: str = "claude"


def _noop_response(reason: str) -> BrainResponse:
    """Build a fallback noop BrainResponse."""
    return BrainResponse(
        reasoning=reason,
        directives=[Directive(action="noop", params={"reason": reason})],
        telegram=None,
        escalate=False,
    )


def _try_invoke(prompt: str, tier: int) -> tuple[dict, str, tuple[int, int]]:
    """Call Claude CLI with prompt and tier. Returns (parsed_dict, model_used, (in_tokens, out_tokens)).

    This is the actual CLI boundary — monkeypatch this in tests to avoid real calls.
    """
    model = TIER_MODELS.get(tier, TIER_MODELS[2])

    result = subprocess.run(
        [
            "claude",
            "--model", model,
            "--output-format", "json",
            "--max-turns", "1",
            "-p", prompt,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        return {
            "reasoning": f"CLI exit {result.returncode}: {result.stderr.strip()[:200]}",
            "directives": [{"action": "noop", "params": {"reason": f"CLI exit {result.returncode}"}}],
            "telegram": None,
            "escalate": False,
        }, model, (0, 0)

    cli_output = json.loads(result.stdout)
    structured = cli_output.get("structured_output", {})
    usage = extract_usage_from_claude_json(cli_output) or (0, 0)
    return structured, model, usage



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
    dry_run: bool = False,
    spend_log_path: Path | None = None,
    gate_enabled: bool = False,
    gate_dir: Path | None = None,
    gate_dry_run: bool = False,
) -> BrainResponse:
    """Invoke the brain at the given tier, returning parsed directives."""
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

    full_prompt = f"{system_prompt}\n\n{user_prompt}"

    # Signal gate: check whether this input has been seen before
    gate_agent = f"factory-brain-tier{tier}"
    gate_input = {"trigger": trigger_event, "snapshot": snapshot, "tier": tier}

    if gate_enabled and not gate_dry_run:
        if not should_fire(gate_agent, gate_input, gate_dir=gate_dir):
            return _noop_response("Signal gate suppressed — content unchanged")

    if gate_enabled and gate_dry_run:
        would_fire = should_fire(gate_agent, gate_input, gate_dir=gate_dir)
        if log_dir is not None:
            _write_gate_shadow_log(log_dir, gate_agent, would_fire, gate_input)

    input_tokens = 0
    output_tokens = 0
    backend = "claude"
    try:
        tool_input, model, (input_tokens, output_tokens) = _try_invoke(full_prompt, tier)
        brain_response = parse_brain_response(tool_input)
    except subprocess.TimeoutExpired:
        brain_response = _noop_response("CLI timed out")
    except FileNotFoundError as exc:
        brain_response = _noop_response(f"CLI not found: {exc}")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        brain_response = _noop_response(f"Failed to parse CLI output: {exc}")

    # Record fire after successful LLM call (also in dry_run to track hashes)
    if gate_enabled:
        record_fire(gate_agent, gate_input, gate_dir=gate_dir)

    invocation = BrainInvocation(
        tier=tier,
        model=model,
        trigger=trigger_event,
        reasoning=brain_response.reasoning,
        directives=[{"action": d.action, "params": d.params} for d in brain_response.directives],
        telegram=brain_response.telegram,
        escalate=brain_response.escalate,
        timestamp=time.time(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        dry_run=dry_run,
        backend=backend,
    )

    if log_dir is not None:
        if dry_run:
            _write_dryrun_log(log_dir, invocation)
        else:
            _write_brain_log(log_dir, invocation)

    if input_tokens > 0 or output_tokens > 0:
        record_spend(
            agent="factory-brain",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            log_path=spend_log_path,
        )

    return brain_response


def _write_gate_shadow_log(
    log_dir: Path,
    agent_name: str,
    would_fire: bool,
    gate_input: dict,
) -> None:
    """Write a shadow log entry for gate dry-run mode."""
    log_dir.mkdir(parents=True, exist_ok=True)
    shadow_path = log_dir / "brain-gate-shadow.jsonl"
    entry = {
        "agent": agent_name,
        "gate_would_fire": would_fire,
        "input_hash": json.dumps(gate_input, sort_keys=True, default=str)[:200],
        "timestamp": time.time(),
    }
    with shadow_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _write_dryrun_log(log_dir: Path, invocation: BrainInvocation) -> None:
    """Write brain invocation to brain-dryruns.jsonl for dry-run mode."""
    log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = log_dir / "brain-dryruns.jsonl"
    record = {
        "tier": invocation.tier,
        "model": invocation.model,
        "trigger": invocation.trigger,
        "reasoning": invocation.reasoning,
        "directives": invocation.directives,
        "telegram": invocation.telegram,
        "escalate": invocation.escalate,
        "timestamp": invocation.timestamp,
        "dry_run": True,
    }
    with jsonl_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _write_brain_log(log_dir: Path, invocation: BrainInvocation) -> None:
    """Write brain invocation to both JSONL and markdown logs."""
    log_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = log_dir / "brain-invocations.jsonl"
    record = {
        "tier": invocation.tier,
        "model": invocation.model,
        "backend": invocation.backend,
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
