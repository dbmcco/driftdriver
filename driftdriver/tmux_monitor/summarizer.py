# ABOUTME: Ollama-based LLM summarization for agent panes.
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftdriver.tmux_monitor.config import TmuxMonitorConfig

_DEFAULT_MODEL = "hermes3:8b"
_OLLAMA_URL = "http://localhost:11434"

_SUMMARY_PROMPT = """\
You are a coding agent monitor. Summarize what this agent has been doing based on its recent terminal output.

Rules:
- Write one concise paragraph (2-4 sentences) capturing the agent's trajectory, not just the last action.
- If the agent appears to have drifted between tasks, mention the progression.
- Extract a short "current_task" (5-10 words) for what it's doing right now.
- If you see references to other tmux sessions or panes collaborating, list them in "related_panes".
- If you see references to file paths in other repos that match panes tracked in other sessions, flag those too.

Previous summary (if any):
{previous_summary}

Recent terminal output:
{pane_output}

Respond in JSON only:
{{"summary": "...", "current_task": "...", "related_panes": ["session:window.pane", ...]}}"""


@dataclass
class PaneSummary:
    summary: str
    current_task: str
    related_panes: list[str]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "current_task": self.current_task,
            "related_panes": self.related_panes,
            "llm_summary_at": self.generated_at,
        }


def _call_ollama(model: str, prompt: str, timeout: int = 60) -> str:
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", str(timeout),
                f"{_OLLAMA_URL}/api/generate",
                "-d", json.dumps({
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 512},
                }),
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        return data.get("response", "").strip()
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return ""


def _parse_summary_response(raw: str) -> PaneSummary | None:
    if not raw:
        return None
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None

    return PaneSummary(
        summary=data.get("summary", ""),
        current_task=data.get("current_task", ""),
        related_panes=data.get("related_panes", []),
        generated_at=_iso_now(),
    )


def _iso_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def summarize_pane(
    log_path: Path,
    config: TmuxMonitorConfig,
    previous_summary: str = "",
    max_input_chars: int = 15000,
) -> PaneSummary | None:
    if not log_path.exists():
        return None

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if not content.strip():
        return None

    if len(content) > max_input_chars:
        content = content[-max_input_chars:]

    model = getattr(config, "ollama_model", None) or _DEFAULT_MODEL
    prompt = _SUMMARY_PROMPT.format(
        previous_summary=previous_summary or "(none)",
        pane_output=content,
    )

    raw = _call_ollama(model, prompt)
    return _parse_summary_response(raw)


def run_summarization_cycle(
    config: TmuxMonitorConfig,
    agent_panes: dict[str, Path],
    previous_summaries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    updated: dict[str, dict[str, Any]] = {}

    for pane_id, log_path in agent_panes.items():
        prev = previous_summaries.get(pane_id, {})
        prev_text = prev.get("summary", "")

        summary = summarize_pane(log_path, config, previous_summary=prev_text)
        if summary:
            updated[pane_id] = summary.to_dict()
        elif prev:
            updated[pane_id] = prev

    return updated
