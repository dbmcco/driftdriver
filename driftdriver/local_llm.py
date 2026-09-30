# ABOUTME: Shared model caller for model-mediated lanes (Lunaroute GLM flash by default).
# ABOUTME: Code owns evidence and policy; the model owns interpretation.
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# Owner ruling 2026-09-30: local Ollama models are prohibited in the wg/speedrift
# stack — resident local models degrade machine performance. Default lanes call
# the remote Lunaroute gateway (glm-5.3-flash, zero cost through the gateway).
_DEFAULT_MODEL = "glm-5.3-flash"
_DEFAULT_BASE_URL = "https://gw.lunaroute.com/v1"
_PI_MODELS_JSON = Path.home() / ".pi" / "agent" / "models.json"

_OLLAMA_URL = "http://localhost:11434"


def _lunaroute_api_key() -> str:
    """Resolve the Lunaroute API key: LUNAROUTE_API_KEY env, then pi models.json."""
    key = os.environ.get("LUNAROUTE_API_KEY", "").strip()
    if key:
        return key
    try:
        data = json.loads(_PI_MODELS_JSON.read_text(encoding="utf-8"))
        key = str(data["providers"]["lunaroute"]["apiKey"] or "").strip()
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return ""
    return key


def call_lunaroute(model: str, prompt: str, timeout: int = 60) -> str:
    """Call a remote OpenAI-compatible chat endpoint (Lunaroute GLM) and return
    the raw response text.

    Returns an empty string on any failure (timeout, network, parse error, or
    missing API key). The caller is responsible for interpreting the response —
    this function never substitutes a default judgment.
    """
    key = _lunaroute_api_key()
    if not key:
        return ""
    base_url = os.environ.get("DRIFTDRIVER_LLM_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", str(timeout),
                f"{base_url}/chat/completions",
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {key}",
                "-d", json.dumps({
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1024,
                }),
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        return str(data["choices"][0]["message"]["content"]).strip()
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError, KeyError, IndexError, TypeError):
        return ""


def call_ollama(model: str, prompt: str, timeout: int = 60) -> str:
    """Call a local Ollama model. Retained ONLY for explicit operator opt-in —
    the wg/speedrift stack must not use local models by default (owner ruling
    2026-09-30).

    Returns an empty string on any failure (timeout, network, parse error).
    The caller is responsible for interpreting the response — this function
    never substitutes a default judgment.
    """
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", str(timeout),
                f"{_OLLAMA_URL}/api/generate",
                "-d", json.dumps({
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1024},
                    # task-68 keep_alive trial (owner GO 2026-09-29): unload the chat
                    # model shortly after each call instead of parking in VRAM alongside
                    # the embedding model (VRAM contention -> /api/embeddings 500s).
                    # Rollback: delete this line.
                    "keep_alive": "3m",
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


# Default caller for model-mediated lanes. Lanes inject their own caller in
# tests; production code should use this alias.
default_call = call_lunaroute
