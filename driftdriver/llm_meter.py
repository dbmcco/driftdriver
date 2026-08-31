# ABOUTME: LLM spend meter — captures token usage from claude/codex CLI and Anthropic API calls.
# ABOUTME: Appends to .workgraph/llm-spend.jsonl; exposes query_spend() for the CLI.

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cost per million tokens (input, output) — drawn from the live Pi model
# catalog (~/.pi/agent/models-store.json). Anthropic / legacy OpenAI o-series
# entries removed; the ecosystem no longer routes through them.
_COST_PER_MTOK: dict[str, tuple[float, float]] = {
    # zai (callable from both layers)
    "zai/glm-5.3": (1.40, 4.40),
    "zai/glm-5.3-flash": (0.075, 0.25),
    "zai/glm-5.2": (1.40, 4.40),
    # lunaroute (Pi-layer only; same underlying glm-5.3 family pricing)
    "lunaroute/glm-5.3": (1.40, 4.40),
    "lunaroute/glm-5.3-flash": (0.075, 0.25),
    # openai-codex (Pi-layer only)
    "openai-codex/gpt-5.6-luna": (0.20, 1.20),
    "openai-codex/gpt-5.5": (5.00, 30.00),
    "openai-codex/gpt-5.4-mini": (0.75, 4.50),
    # Local Ollama models are free
    "ollama": (0.0, 0.0),
}

_DEFAULT_RATE = _COST_PER_MTOK["zai/glm-5.3"]

_DEFAULT_LOG_PATH = Path(".workgraph/llm-spend.jsonl")


@dataclass
class SpendRecord:
    ts: float
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


def _resolve_rate(model: str) -> tuple[float, float]:
    """Look up cost rate by model name, falling back to checking for known substrings."""
    lower = model.lower()
    if lower in _COST_PER_MTOK:
        return _COST_PER_MTOK[lower]
    for key in (
        "glm-5.3-flash",
        "glm-5.3",
        "glm-5.2",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
        "ollama",
    ):
        if key in lower:
            return _COST_PER_MTOK[key]
    return _DEFAULT_RATE


def estimate_cost(model: str, *, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a given model and token counts."""
    if input_tokens == 0 and output_tokens == 0:
        return 0.0
    input_rate, output_rate = _resolve_rate(model)
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def extract_usage_from_claude_json(cli_output: Any) -> tuple[int, int] | None:
    """Extract (input_tokens, output_tokens) from claude CLI --output-format json output."""
    if not isinstance(cli_output, dict):
        return None
    usage = cli_output.get("usage")
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if inp is None or out is None:
        return None
    return (int(inp), int(out))


def extract_usage_from_api_response(body: dict[str, Any]) -> tuple[int, int] | None:
    """Extract (input_tokens, output_tokens) from an Anthropic Messages API
    response body. Kept for any residual Anthropic-shaped callers; new zai
    callers should use :func:`extract_usage_from_openai_compat`."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    if inp is None or out is None:
        return None
    return (int(inp), int(out))


def extract_usage_from_openai_compat(body: dict[str, Any]) -> tuple[int, int] | None:
    """Extract (input_tokens, output_tokens) from an OpenAI-compatible response
    body (zai, openai, openrouter). Handles both the ``prompt_tokens`` /
    ``completion_tokens`` shape and the Anthropic-style ``input_tokens`` /
    ``output_tokens`` shape as a fallback."""
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    inp = usage.get("prompt_tokens", usage.get("input_tokens"))
    out = usage.get("completion_tokens", usage.get("output_tokens"))
    if inp is None or out is None:
        return None
    return (int(inp), int(out))


def record_spend(
    *,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    log_path: Path | None = None,
) -> SpendRecord:
    """Record an LLM spend entry to the JSONL log and return the record."""
    path = log_path or _DEFAULT_LOG_PATH
    cost = estimate_cost(model, input_tokens=input_tokens, output_tokens=output_tokens)
    rec = SpendRecord(
        ts=time.time(),
        agent=agent,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=round(cost, 8),
    )
    entry = {
        "ts": rec.ts,
        "agent": rec.agent,
        "model": rec.model,
        "input_tokens": rec.input_tokens,
        "output_tokens": rec.output_tokens,
        "estimated_cost_usd": rec.estimated_cost_usd,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        logger.warning("Failed to write LLM spend log: %s", exc)
    return rec


def query_spend(
    *,
    log_path: Path | None = None,
    tail_hours: float = 24,
    by_agent: bool = False,
) -> dict[str, Any]:
    """Query the LLM spend log. Returns summary dict."""
    path = log_path or _DEFAULT_LOG_PATH
    cutoff = time.time() - (tail_hours * 3600)

    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ts", 0) >= cutoff:
                records.append(rec)

    total_cost = sum(r.get("estimated_cost_usd", 0) for r in records)
    total_input = sum(r.get("input_tokens", 0) for r in records)
    total_output = sum(r.get("output_tokens", 0) for r in records)

    result: dict[str, Any] = {
        "records": records,
        "total_cost_usd": total_cost,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "tail_hours": tail_hours,
    }

    if by_agent:
        agents: dict[str, dict[str, Any]] = {}
        for r in records:
            agent = r.get("agent", "unknown")
            if agent not in agents:
                agents[agent] = {
                    "total_cost_usd": 0.0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "call_count": 0,
                }
            agents[agent]["total_cost_usd"] += r.get("estimated_cost_usd", 0)
            agents[agent]["total_input_tokens"] += r.get("input_tokens", 0)
            agents[agent]["total_output_tokens"] += r.get("output_tokens", 0)
            agents[agent]["call_count"] += 1
        result["by_agent"] = agents

    return result
