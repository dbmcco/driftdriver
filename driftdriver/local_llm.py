# ABOUTME: Shared local-model (Ollama) caller for model-mediated lanes.
# ABOUTME: Code owns evidence and policy; the model owns interpretation.
from __future__ import annotations

import json
import subprocess

_DEFAULT_MODEL = "qwen3:8b"
_OLLAMA_URL = "http://localhost:11434"


def call_ollama(model: str, prompt: str, timeout: int = 60) -> str:
    """Call a local Ollama model and return the raw response text.

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
