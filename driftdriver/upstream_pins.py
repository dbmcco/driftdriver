# ABOUTME: Read/write .driftdriver/upstream-pins.toml for upstream branch tracking.
# ABOUTME: Tracks true upstream SHAs, adopted local/fork SHAs, and a snooze list with expiry dates.
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

try:
    import tomli_w
    _HAS_TOMLI_W = True
except ImportError:
    _HAS_TOMLI_W = False


def load_pins(pins_path: Path) -> dict[str, Any]:
    """Load upstream-pins.toml.

    The file tracks two independent lines:
    - ``shas``: the last observed external upstream SHA per repo+branch
    - ``adopted_shas``: the last observed local/fork line actually adopted per repo+branch
    """
    if not pins_path.exists():
        return {"shas": {}, "adopted_shas": {}, "snoozed": {}}
    try:
        data = tomllib.loads(pins_path.read_text(encoding="utf-8"))
    except Exception:
        return {"shas": {}, "adopted_shas": {}, "snoozed": {}}
    return {
        "shas": dict(data.get("shas") or {}),
        "adopted_shas": dict(data.get("adopted_shas") or {}),
        "snoozed": dict(data.get("snoozed") or {}),
    }


def save_pins(pins_path: Path, pins: dict[str, Any]) -> None:
    """Write pins back to disk atomically."""
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    if _HAS_TOMLI_W:
        content = tomli_w.dumps(pins)
    else:
        # Fallback: hand-write minimal TOML
        lines = ["[shas]\n"]
        for key, val in (pins.get("shas") or {}).items():
            lines.append(f'"{key}" = "{val}"\n')
        lines.append("\n[adopted_shas]\n")
        for key, val in (pins.get("adopted_shas") or {}).items():
            lines.append(f'"{key}" = "{val}"\n')
        lines.append("\n[snoozed]\n")
        for key, val in (pins.get("snoozed") or {}).items():
            until = val.get("until", "")
            reason = val.get("reason", "")
            lines.append(f'"{key}" = {{ until = "{until}", reason = "{reason}" }}\n')
        content = "".join(lines)
    tmp = pins_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(pins_path)


def _branch_key(repo: str, branch: str) -> str:
    return f"{repo}:{branch}"


def get_sha(pins: dict[str, Any], repo: str, branch: str) -> str | None:
    """Return pinned SHA or None if not tracked yet."""
    return (pins.get("shas") or {}).get(_branch_key(repo, branch))


def get_adopted_sha(pins: dict[str, Any], repo: str, branch: str) -> str | None:
    """Return pinned adopted SHA or None if not tracked yet."""
    return (pins.get("adopted_shas") or {}).get(_branch_key(repo, branch))


def set_sha(pins: dict[str, Any], repo: str, branch: str, sha: str) -> dict[str, Any]:
    """Return updated pins dict with new SHA (immutable-style)."""
    updated = {
        "shas": dict(pins.get("shas") or {}),
        "adopted_shas": dict(pins.get("adopted_shas") or {}),
        "snoozed": dict(pins.get("snoozed") or {}),
    }
    updated["shas"][_branch_key(repo, branch)] = sha
    return updated


def set_adopted_sha(pins: dict[str, Any], repo: str, branch: str, sha: str) -> dict[str, Any]:
    """Return updated pins dict with new adopted SHA (immutable-style)."""
    updated = {
        "shas": dict(pins.get("shas") or {}),
        "adopted_shas": dict(pins.get("adopted_shas") or {}),
        "snoozed": dict(pins.get("snoozed") or {}),
    }
    updated["adopted_shas"][_branch_key(repo, branch)] = sha
    return updated


def is_snoozed(pins: dict[str, Any], repo: str, branch: str) -> bool:
    """Return True if this branch is snoozed and the snooze hasn't expired."""
    entry = (pins.get("snoozed") or {}).get(_branch_key(repo, branch))
    if not isinstance(entry, dict):
        return False
    until_str = entry.get("until", "")
    try:
        until = date.fromisoformat(str(until_str))
        return until >= date.today()
    except (ValueError, TypeError):
        return False


def snooze_branch(
    pins: dict[str, Any],
    repo: str,
    branch: str,
    until: str,
    reason: str,
) -> dict[str, Any]:
    """Return updated pins dict with snooze entry added."""
    updated = {
        "shas": dict(pins.get("shas") or {}),
        "adopted_shas": dict(pins.get("adopted_shas") or {}),
        "snoozed": dict(pins.get("snoozed") or {}),
    }
    updated["snoozed"][_branch_key(repo, branch)] = {"until": until, "reason": reason}
    return updated
