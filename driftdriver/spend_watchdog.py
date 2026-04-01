# ABOUTME: LLM spend watchdog — rolling rate checks against policy thresholds.
# ABOUTME: Warns, kills (SIGTERM hub + disable agent), or fires canary on silence.

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class WatchdogAction(Enum):
    OK = "ok"
    WARN = "warn"
    KILL = "kill"
    CANARY = "canary"


@dataclass
class WatchdogConfig:
    warn_per_hour_usd: float = 1.0
    kill_per_hour_usd: float = 5.0
    kill_per_5min_usd: float = 2.0


@dataclass
class WatchdogResult:
    action: WatchdogAction
    rate_1h: float
    rate_5m: float
    top_agent: str | None
    message: str


def load_watchdog_config(policy_path: Path) -> WatchdogConfig:
    """Load watchdog thresholds from drift-policy.toml [watchdog] section."""
    try:
        import tomllib
    except ModuleNotFoundError:
        return WatchdogConfig()

    if not policy_path.exists():
        return WatchdogConfig()

    try:
        data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return WatchdogConfig()

    section = data.get("watchdog", {})
    return WatchdogConfig(
        warn_per_hour_usd=float(section.get("warn_per_hour_usd", 1.0)),
        kill_per_hour_usd=float(section.get("kill_per_hour_usd", 5.0)),
        kill_per_5min_usd=float(section.get("kill_per_5min_usd", 2.0)),
    )


def compute_rolling_rate(log_path: Path, window_seconds: int = 3600) -> float:
    """Sum estimated_cost_usd for records within the rolling window."""
    if not log_path.exists():
        return 0.0

    cutoff = time.time() - window_seconds
    total = 0.0
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("ts", 0) >= cutoff:
            total += rec.get("estimated_cost_usd", 0)
    return total


def top_offending_agent(log_path: Path, window_seconds: int = 3600) -> str | None:
    """Return the agent with the highest spend in the window, or None."""
    if not log_path.exists():
        return None

    cutoff = time.time() - window_seconds
    agent_spend: dict[str, float] = {}
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("ts", 0) >= cutoff:
            agent = rec.get("agent", "unknown")
            agent_spend[agent] = agent_spend.get(agent, 0) + rec.get("estimated_cost_usd", 0)

    if not agent_spend:
        return None
    return max(agent_spend, key=agent_spend.get)  # type: ignore[arg-type]


def _last_n_records(log_path: Path, n: int = 10) -> list[dict]:
    """Return the last n records from the spend log."""
    if not log_path.exists():
        return []
    records: list[dict] = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records[-n:]


def _write_kill_diagnostic(
    log_path: Path,
    diagnostic_path: Path,
    *,
    rate_1h: float,
    rate_5m: float,
    top_agent: str | None,
    reason: str,
) -> None:
    """Write last 10 invocations with token counts to kill-diagnostic.jsonl."""
    recent = _last_n_records(log_path, n=10)
    entry = {
        "ts": time.time(),
        "reason": reason,
        "rate_1h_usd": rate_1h,
        "rate_5m_usd": rate_5m,
        "top_agent": top_agent,
        "last_invocations": recent,
    }
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    with diagnostic_path.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _send_sigterm_to_hub() -> None:
    """Send SIGTERM to the ecosystem hub daemon (if running)."""
    hub_pid_path = Path.home() / ".config" / "workgraph" / "factory-brain" / "hub.pid"
    try:
        if hub_pid_path.exists():
            pid = int(hub_pid_path.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to hub pid %d", pid)
    except (ValueError, OSError, ProcessLookupError) as exc:
        logger.warning("Failed to send SIGTERM to hub: %s", exc)


def _disable_agent_in_policy(policy_path: Path, agent: str) -> None:
    """Add agent to [watchdog] disabled_agents list in policy TOML."""
    if not policy_path.exists():
        return

    content = policy_path.read_text(encoding="utf-8")

    try:
        import tomllib
        data = tomllib.loads(content)
    except Exception:
        return

    watchdog = data.get("watchdog", {})
    disabled = watchdog.get("disabled_agents", [])
    if agent not in disabled:
        disabled.append(agent)

    # Rewrite policy — append disabled_agents to [watchdog] section
    if "disabled_agents" not in content:
        # Add disabled_agents to existing [watchdog] section
        lines = content.splitlines(keepends=True)
        new_lines: list[str] = []
        in_watchdog = False
        inserted = False
        for line in lines:
            new_lines.append(line)
            stripped = line.strip()
            if stripped == "[watchdog]":
                in_watchdog = True
            elif in_watchdog and not inserted and (stripped.startswith("[") or stripped == ""):
                # Insert before next section or blank line
                agents_str = json.dumps(disabled)
                new_lines.insert(-1, f"disabled_agents = {agents_str}\n")
                inserted = True
                in_watchdog = False
        if not inserted:
            # Append at end
            agents_str = json.dumps(disabled)
            new_lines.append(f"disabled_agents = {agents_str}\n")
        policy_path.write_text("".join(new_lines), encoding="utf-8")
    else:
        # Update existing disabled_agents line
        import re
        agents_str = json.dumps(disabled)
        content = re.sub(
            r'disabled_agents\s*=\s*\[.*?\]',
            f'disabled_agents = {agents_str}',
            content,
        )
        policy_path.write_text(content, encoding="utf-8")


def _load_state(state_path: Path | None) -> dict[str, Any]:
    """Load watchdog state for idempotency."""
    if state_path is None or not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: Path | None, state: dict[str, Any]) -> None:
    """Save watchdog state."""
    if state_path is None:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def run_watchdog(
    *,
    log_path: Path,
    policy_path: Path,
    watchdog_log_path: Path,
    state_path: Path | None = None,
    diagnostic_path: Path | None = None,
) -> WatchdogResult:
    """Run one watchdog check cycle. Returns the action taken."""
    cfg = load_watchdog_config(policy_path)
    rate_1h = compute_rolling_rate(log_path, window_seconds=3600)
    rate_5m = compute_rolling_rate(log_path, window_seconds=300)
    agent = top_offending_agent(log_path, window_seconds=3600)

    state = _load_state(state_path)
    last_action = state.get("last_action")
    last_rate_1h = state.get("last_rate_1h", 0)

    # Resolve diagnostic path
    if diagnostic_path is None:
        diagnostic_path = log_path.parent / "kill-diagnostic.jsonl"

    # Check canary: records exist but all are old (no recent activity)
    if rate_1h == 0 and rate_5m == 0:
        # Check if there are any records at all (old ones outside window)
        has_old_records = False
        if log_path.exists():
            for line in log_path.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        json.loads(line)
                        has_old_records = True
                        break
                    except json.JSONDecodeError:
                        continue

        if has_old_records:
            msg = "Canary: no recent LLM spend — system may be silent"
            _append_watchdog_log(watchdog_log_path, "CANARY", msg)
            return WatchdogResult(
                action=WatchdogAction.CANARY,
                rate_1h=rate_1h,
                rate_5m=rate_5m,
                top_agent=agent,
                message=msg,
            )

        return WatchdogResult(
            action=WatchdogAction.OK,
            rate_1h=0,
            rate_5m=0,
            top_agent=None,
            message="",
        )

    # Check kill thresholds (5min burst or hourly)
    kill_reason = None
    if rate_5m >= cfg.kill_per_5min_usd:
        kill_reason = f"5min burst: ${rate_5m:.4f} >= ${cfg.kill_per_5min_usd:.4f}"
    elif rate_1h >= cfg.kill_per_hour_usd:
        kill_reason = f"hourly rate: ${rate_1h:.4f} >= ${cfg.kill_per_hour_usd:.4f}"

    if kill_reason:
        # Idempotency: don't re-fire kill if already done for same rate
        if last_action == "kill":
            return WatchdogResult(
                action=WatchdogAction.KILL,
                rate_1h=rate_1h,
                rate_5m=rate_5m,
                top_agent=agent,
                message=f"KILL (already fired): {kill_reason}",
            )

        msg = f"KILL: {kill_reason} (top agent: {agent})"
        _append_watchdog_log(watchdog_log_path, "KILL", msg)

        # Write kill diagnostic before stopping
        _write_kill_diagnostic(
            log_path, diagnostic_path,
            rate_1h=rate_1h, rate_5m=rate_5m,
            top_agent=agent, reason=kill_reason,
        )

        if agent:
            _disable_agent_in_policy(policy_path, agent)

        _send_sigterm_to_hub()

        _save_state(state_path, {"last_action": "kill", "last_rate_1h": rate_1h})

        return WatchdogResult(
            action=WatchdogAction.KILL,
            rate_1h=rate_1h,
            rate_5m=rate_5m,
            top_agent=agent,
            message=msg,
        )

    # Check warn threshold
    if rate_1h >= cfg.warn_per_hour_usd:
        # Idempotency: don't re-warn for same rate
        if last_action == "warn" and abs(last_rate_1h - rate_1h) < 0.001:
            return WatchdogResult(
                action=WatchdogAction.WARN,
                rate_1h=rate_1h,
                rate_5m=rate_5m,
                top_agent=agent,
                message=f"WARN (already notified): ${rate_1h:.4f}/hr",
            )

        msg = f"WARN: ${rate_1h:.4f}/hr >= ${cfg.warn_per_hour_usd:.4f}/hr (top agent: {agent})"
        _append_watchdog_log(watchdog_log_path, "WARN", msg)

        _save_state(state_path, {"last_action": "warn", "last_rate_1h": rate_1h})

        return WatchdogResult(
            action=WatchdogAction.WARN,
            rate_1h=rate_1h,
            rate_5m=rate_5m,
            top_agent=agent,
            message=msg,
        )

    # OK — below all thresholds
    _save_state(state_path, {"last_action": "ok", "last_rate_1h": rate_1h})
    return WatchdogResult(
        action=WatchdogAction.OK,
        rate_1h=rate_1h,
        rate_5m=rate_5m,
        top_agent=agent,
        message="",
    )


def _append_watchdog_log(log_path: Path, level: str, message: str) -> None:
    """Append a timestamped entry to the watchdog log."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a") as f:
        f.write(f"[{ts}] {level}: {message}\n")
