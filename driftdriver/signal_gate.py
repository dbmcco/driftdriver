# ABOUTME: Shared signal-gate library for content-hash-based LLM call deduplication.
# ABOUTME: Pure functions, disk-persisted state, per-agent config, canary alerting.

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_GATE_DIR = ".workgraph/.signal-gates"

VOLATILE_FIELDS: frozenset[str] = frozenset({
    "ts", "timestamp", "checked_at", "agent_count", "task_count", "cycle_id"
})


def strip_volatile(data: Any) -> Any:
    """Return *data* with all VOLATILE_FIELDS keys removed (shallow, dict only)."""
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k not in VOLATILE_FIELDS}
    return data


class SignalGate:
    """File-backed signal gate that suppresses LLM calls when only volatile fields change.

    Volatile fields (ts, timestamp, checked_at, agent_count, task_count, cycle_id) are
    stripped before hashing so that routine timestamp churn doesn't cause repeated fires.
    State is persisted to *state_path* so suppression survives across process restarts.
    """

    def __init__(self, state_path: str | Path) -> None:
        self._state_path = Path(state_path)

    def should_fire(self, input_data: Any) -> bool:
        """Return True when structural content changed since last fire; False to suppress.

        Automatically records the new hash when returning True.
        """
        structural = strip_volatile(input_data)
        current_hash = compute_content_hash(structural)
        stored_hash = self._load_hash()

        if stored_hash is None or current_hash != stored_hash:
            self._save_hash(current_hash)
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_hash(self) -> str | None:
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data.get("content_hash") if isinstance(data, dict) else None
        except Exception:
            return None

    def _save_hash(self, content_hash: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "content_hash": content_hash,
            "last_fire_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._state_path)


def compute_content_hash(input_data: Any) -> str:
    """Compute a stable SHA-256 hex digest of arbitrary input data.

    Dicts are serialized with sorted keys for deterministic output.
    """
    if isinstance(input_data, (dict, list)):
        payload = json.dumps(input_data, sort_keys=True, default=str)
    else:
        payload = str(input_data)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def should_fire(
    agent_name: str,
    input_data: Any,
    *,
    gate_dir: Path | None = None,
) -> bool:
    """Return True when *input_data* differs from the last recorded hash for *agent_name*.

    On first call (no persisted state) always returns True.
    """
    gate_dir = _resolve_gate_dir(gate_dir)
    current_hash = compute_content_hash(input_data)
    state = _load_state(gate_dir, agent_name)

    # Derive canary log path from gate_dir so tests stay isolated
    canary_log = gate_dir.parent / "signal-gate-canary.jsonl"

    if state is None:
        log.info("[signal-gate] %s — no prior state, firing", agent_name)
        log_canary_decision(agent_name, fired=True, content_hash=current_hash, reason="no_prior_state", canary_log=canary_log)
        return True

    stored_hash = state.get("content_hash", "")
    if current_hash != stored_hash:
        log.info("[signal-gate] %s — content changed, firing (new=%s)", agent_name, current_hash[:12])
        log_canary_decision(agent_name, fired=True, content_hash=current_hash, reason="content_changed", canary_log=canary_log)
        return True

    log.info("[signal-gate] %s — content unchanged, suppressing (hash=%s)", agent_name, current_hash[:12])
    log_canary_decision(agent_name, fired=False, content_hash=current_hash, reason="content_unchanged", canary_log=canary_log)
    return False


def record_fire(
    agent_name: str,
    input_data: Any,
    *,
    gate_dir: Path | None = None,
) -> None:
    """Persist the content hash after a successful LLM call for *agent_name*."""
    gate_dir = _resolve_gate_dir(gate_dir)
    current_hash = compute_content_hash(input_data)
    state = _load_state(gate_dir, agent_name) or {}

    state["content_hash"] = current_hash
    state["last_fire_at"] = datetime.now(timezone.utc).isoformat()
    state["fire_count"] = state.get("fire_count", 0) + 1

    _save_state(gate_dir, agent_name, state)
    log.info("[signal-gate] %s — recorded fire (hash=%s, count=%d)", agent_name, current_hash[:12], state["fire_count"])


def is_gate_enabled(agent_name: str, policy_path: Path) -> bool:
    """Check whether the signal gate is enabled for *agent_name* in drift-policy.toml.

    Checks two locations:
    1. [signal_gates] <agent_name> = true  (new canonical form)
    2. [<agent_name>] signal_gate_enabled = true  (legacy per-section flag)
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        return False

    if not policy_path.exists():
        return False

    try:
        data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    # Canonical: [signal_gates] section
    signal_gates = data.get("signal_gates")
    if isinstance(signal_gates, dict):
        val = signal_gates.get(agent_name)
        if val is not None:
            return bool(val)

    # Legacy fallback: [<agent_name>] signal_gate_enabled
    section = data.get(agent_name)
    if isinstance(section, dict):
        return bool(section.get("signal_gate_enabled", False))

    return False


def log_canary_decision(
    agent_name: str,
    *,
    fired: bool,
    content_hash: str,
    reason: str = "",
    canary_log: Path | None = None,
) -> None:
    """Append a gate hit/miss entry to the canary JSONL log.

    Each entry records whether the gate fired (content changed) or suppressed
    (content unchanged), enabling 7-day monitoring for misconfigurations.
    """
    if canary_log is None:
        canary_log = Path(_DEFAULT_GATE_DIR).parent / "signal-gate-canary.jsonl"

    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent_name,
        "fired": fired,
        "content_hash": content_hash,
    }
    if reason:
        entry["reason"] = reason

    try:
        canary_log.parent.mkdir(parents=True, exist_ok=True)
        with open(canary_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        log.warning("[signal-gate] failed to write canary log for %s", agent_name)


def check_canary(
    agent_name: str,
    *,
    gate_dir: Path | None = None,
    silent_threshold_seconds: int = 3600,
) -> str | None:
    """Return an alert message if *agent_name* has been silent longer than *silent_threshold_seconds*.

    Returns None when everything is fine (recently fired or no prior state).
    """
    gate_dir = _resolve_gate_dir(gate_dir)
    state = _load_state(gate_dir, agent_name)

    if state is None:
        return None

    last_fire = state.get("last_fire_at")
    if not last_fire:
        return None

    try:
        dt = datetime.fromisoformat(last_fire)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None

    elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
    if elapsed > silent_threshold_seconds:
        hours = elapsed / 3600
        msg = f"{agent_name}: silent for {hours:.1f}h (no fire since {last_fire}) — gate may be stuck"
        log.warning("[signal-gate-canary] %s", msg)
        return msg

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_gate_dir(gate_dir: Path | None) -> Path:
    if gate_dir is not None:
        return gate_dir
    return Path(_DEFAULT_GATE_DIR)


def _state_path(gate_dir: Path, agent_name: str) -> Path:
    return gate_dir / f"{agent_name}.json"


def _load_state(gate_dir: Path, agent_name: str) -> dict[str, Any] | None:
    path = _state_path(gate_dir, agent_name)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_state(gate_dir: Path, agent_name: str, state: dict[str, Any]) -> None:
    gate_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(gate_dir, agent_name)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
