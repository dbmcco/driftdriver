# ABOUTME: Central brain module — assembles prompts, invokes model CLIs (claude or codex),
# ABOUTME: with automatic fallback. Parses responses into directives for the factory supervisor.
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from driftdriver.factory_brain.directives import BrainResponse, Directive, parse_brain_response
from driftdriver.factory_brain.prompts import (
    TIER_MODELS,
    build_system_prompt,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

# JSON schema dict for structured output (shared across CLIs)
_DIRECTIVE_SCHEMA = {
    "type": "object",
    "required": ["reasoning", "directives", "escalate"],
    "properties": {
        "reasoning": {
            "type": "string",
            "description": "Your adversarial analysis of the situation.",
        },
        "directives": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["action"],
                "properties": {
                    "action": {"type": "string"},
                    "params": {"type": "object"},
                },
            },
        },
        "telegram": {
            "type": ["string", "null"],
            "description": "Optional Telegram message to the operator.",
        },
        "escalate": {
            "type": "boolean",
            "description": "Whether to escalate to a higher tier.",
        },
    },
}

# Claude CLI model aliases per tier
_CLAUDE_TIER_ALIASES: dict[int, str] = {
    1: "haiku",
    2: "sonnet",
    3: "opus",
}

# Codex CLI model names per tier
_CODEX_TIER_MODELS: dict[int, str] = {
    1: "o4-mini",
    2: "o3",
    3: "o3",
}

# Env vars to clear for clean subprocess invocation (matches clauded alias)
_STRIPPED_ENV_VARS = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}


@dataclass
class BrainInvocation:
    tier: int
    model: str
    cli: str
    trigger: dict | None
    reasoning: str
    directives: list[dict]
    telegram: str | None
    escalate: bool
    timestamp: float


def _clean_env() -> dict[str, str]:
    """Return env dict safe for spawning CLI subprocesses (matches clauded alias)."""
    return {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_VARS}


def _invoke_claude(full_prompt: str, tier: int, timeout: int = 120) -> dict:
    """Invoke claude CLI (clauded equivalent) and return parsed directive data."""
    model_alias = _CLAUDE_TIER_ALIASES.get(tier, "sonnet")

    cmd = [
        "claude",
        "-p",
        "--model", model_alias,
        "--output-format", "json",
        "--json-schema", json.dumps(_DIRECTIVE_SCHEMA),
        "--no-session-persistence",
        "--dangerously-skip-permissions",
        "--max-budget-usd", "0.50",
    ]

    result = subprocess.run(
        cmd,
        input=full_prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_clean_env(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"claude exit {result.returncode}: {result.stderr[:300]}")

    cli_output = json.loads(result.stdout)

    # --json-schema puts structured data in "structured_output"
    data = cli_output.get("structured_output") if isinstance(cli_output, dict) else None
    if data is not None:
        return data

    # Fallback: parse "result" field as JSON
    raw = cli_output.get("result", "") if isinstance(cli_output, dict) else result.stdout
    return json.loads(raw) if isinstance(raw, str) else raw


def _invoke_codex(full_prompt: str, tier: int, timeout: int = 120) -> dict:
    """Invoke codex CLI (codexd equivalent) and return parsed directive data."""
    model = _CODEX_TIER_MODELS.get(tier, "o4-mini")

    # Codex --output-schema requires a file path
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="brain-schema-", delete=False
    ) as schema_file:
        json.dump(_DIRECTIVE_SCHEMA, schema_file)
        schema_path = schema_file.name

    # Codex -o writes last message to a file
    with tempfile.NamedTemporaryFile(
        suffix=".json", prefix="brain-output-", delete=False
    ) as output_file:
        output_path = output_file.name

    try:
        cmd = [
            "codex",
            "exec",
            "-m", model,
            "--output-schema", schema_path,
            "-o", output_path,
            "--ephemeral",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-",  # read prompt from stdin
        ]

        result = subprocess.run(
            cmd,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_clean_env(),
        )

        if result.returncode != 0:
            raise RuntimeError(f"codex exit {result.returncode}: {result.stderr[:300]}")

        # Read structured output from the -o file
        output_content = Path(output_path).read_text().strip()
        return json.loads(output_content)

    finally:
        for p in (schema_path, output_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _noop_response(reason: str) -> BrainResponse:
    """Build a noop BrainResponse for error cases."""
    return BrainResponse(
        reasoning=reason,
        directives=[Directive(action="noop", params={"reason": "cli error"})],
        telegram=None,
        escalate=False,
    )


def _try_invoke(full_prompt: str, tier: int) -> tuple[dict, str]:
    """Try claude first, fall back to codex. Returns (directive_data, cli_used).

    Preference order can be overridden via FACTORY_BRAIN_CLI=codex to try codex first.
    """
    preferred = os.environ.get("FACTORY_BRAIN_CLI", "claude").lower()
    order = (
        [("codex", _invoke_codex), ("claude", _invoke_claude)]
        if preferred == "codex"
        else [("claude", _invoke_claude), ("codex", _invoke_codex)]
    )

    last_error = None
    for cli_name, invoke_fn in order:
        try:
            data = invoke_fn(full_prompt, tier)
            logger.info("Brain tier %d: %s succeeded", tier, cli_name)
            return data, cli_name
        except FileNotFoundError:
            logger.info("Brain tier %d: %s not found, trying next", tier, cli_name)
            last_error = FileNotFoundError(f"{cli_name} not found")
        except subprocess.TimeoutExpired:
            logger.warning("Brain tier %d: %s timed out, trying next", tier, cli_name)
            last_error = subprocess.TimeoutExpired(cmd=cli_name, timeout=120)
        except (RuntimeError, json.JSONDecodeError, TypeError, OSError) as exc:
            logger.warning("Brain tier %d: %s failed (%s), trying next", tier, cli_name, exc)
            last_error = exc

    # Both failed
    raise last_error or RuntimeError("No CLI available")


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
    """Invoke the brain at the given tier via CLI (auto-selects claude or codex)."""
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

    full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

    try:
        directive_data, cli_used = _try_invoke(full_prompt, tier)
    except subprocess.TimeoutExpired:
        return _noop_response("All CLIs timed out.")
    except FileNotFoundError:
        return _noop_response("No CLI (claude/codex) found in PATH.")
    except Exception as exc:
        return _noop_response(f"All CLIs failed: {exc}")

    # Resolve model ID for logging
    if cli_used == "codex":
        model_id = _CODEX_TIER_MODELS.get(tier, "o4-mini")
    else:
        model_id = TIER_MODELS.get(tier, TIER_MODELS[2])

    brain_response = parse_brain_response(directive_data)

    invocation = BrainInvocation(
        tier=tier,
        model=model_id,
        cli=cli_used,
        trigger=trigger_event,
        reasoning=brain_response.reasoning,
        directives=[{"action": d.action, "params": d.params} for d in brain_response.directives],
        telegram=brain_response.telegram,
        escalate=brain_response.escalate,
        timestamp=time.time(),
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
        "cli": invocation.cli,
        "trigger": invocation.trigger,
        "reasoning": invocation.reasoning,
        "directives": invocation.directives,
        "telegram": invocation.telegram,
        "escalate": invocation.escalate,
        "timestamp": invocation.timestamp,
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
        f"### Tier {invocation.tier} — {invocation.model} (via {invocation.cli})\n"
        f"**Time:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(invocation.timestamp))}\n\n"
        f"**Reasoning:**\n{invocation.reasoning}\n\n"
        f"**Directives:**\n{directives_section}\n"
    )
    if invocation.telegram:
        entry += f"\n**Telegram:** {invocation.telegram}\n"
    if invocation.escalate:
        entry += f"\n**ESCALATION REQUESTED**\n"

    with md_path.open("a") as f:
        f.write(entry)
