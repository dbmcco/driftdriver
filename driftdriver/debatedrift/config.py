# ABOUTME: Parse debatedrift fenced TOML block from workgraph task descriptions.
# ABOUTME: Returns DebateDriftConfig or None if no fence present.
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field


_VALID_TYPES = {"planning", "troubleshoot", "usecase"}
_FENCE_RE = re.compile(r"```debatedrift\n(.*?)```", re.DOTALL)


@dataclass
class DebateDriftConfig:
    type: str
    max_rounds: int = 5
    watchdog_timeout: int = 90
    context_files: list[str] = field(default_factory=list)


def parse_debatedrift_config(description: str) -> DebateDriftConfig | None:
    """Extract and parse a debatedrift fenced TOML block from a task description.

    Returns None if no fence is present. Raises ValueError for invalid values.
    """
    match = _FENCE_RE.search(description)
    if not match:
        return None

    raw = match.group(1)
    try:
        data = tomllib.loads(raw)
    except Exception as exc:
        raise ValueError(f"debatedrift fence is not valid TOML: {exc}") from exc

    debate_type = str(data.get("type", "")).strip()
    if debate_type not in _VALID_TYPES:
        raise ValueError(
            f"debatedrift type={debate_type!r} is not valid; "
            f"must be one of {sorted(_VALID_TYPES)}"
        )

    context_files_raw = data.get("context_files", [])
    context_files = [str(f) for f in context_files_raw] if isinstance(context_files_raw, list) else []

    return DebateDriftConfig(
        type=debate_type,
        max_rounds=max(1, int(data.get("max_rounds", 5))),
        watchdog_timeout=max(10, int(data.get("watchdog_timeout", 90))),
        context_files=context_files,
    )
