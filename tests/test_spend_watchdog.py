# ABOUTME: Tests for the spend watchdog — threshold detection, warn/kill actions, canary, idempotency.
# ABOUTME: Validates rolling rate computation, per-agent kill via policy rewrite, SIGTERM to hub, and double-fire guard.

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.spend_watchdog import (
    WatchdogAction,
    WatchdogConfig,
    compute_rolling_rate,
    load_watchdog_config,
    run_watchdog,
    top_offending_agent,
)


# --- Helpers ---


def _write_spend_log(log_path: Path, records: list[dict]) -> None:
    with log_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _write_policy(policy_path: Path, watchdog_section: str = "") -> None:
    content = 'schema = 1\nmode = "redirect"\n'
    if watchdog_section:
        content += "\n" + watchdog_section
    policy_path.write_text(content)


def _make_records(
    agent: str, cost_each: float, count: int, *, span_seconds: float = 3600
) -> list[dict]:
    """Create spend records evenly spaced over span_seconds ending at now.

    The first record starts 1s inside the window to avoid boundary races.
    """
    now = time.time()
    window_start = now - span_seconds + 1  # 1s inside window
    records = []
    for i in range(count):
        ts = window_start + (i * (now - window_start) / max(count - 1, 1))
        records.append(
            {
                "ts": ts,
                "agent": agent,
                "model": "sonnet",
                "input_tokens": 1000,
                "output_tokens": 500,
                "estimated_cost_usd": cost_each,
            }
        )
    return records


# --- load_watchdog_config ---


def test_load_config_defaults(tmp_path: Path):
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy)
    cfg = load_watchdog_config(policy)
    assert isinstance(cfg, WatchdogConfig)
    assert cfg.warn_per_hour_usd == 1.0
    assert cfg.kill_per_hour_usd == 5.0
    assert cfg.kill_per_5min_usd == 2.0


def test_load_config_custom_values(tmp_path: Path):
    policy = tmp_path / "drift-policy.toml"
    _write_policy(
        policy,
        "[watchdog]\nwarn_per_hour_usd = 2.5\nkill_per_hour_usd = 10.0\nkill_per_5min_usd = 4.0\n",
    )
    cfg = load_watchdog_config(policy)
    assert cfg.warn_per_hour_usd == 2.5
    assert cfg.kill_per_hour_usd == 10.0
    assert cfg.kill_per_5min_usd == 4.0


def test_load_config_missing_file(tmp_path: Path):
    policy = tmp_path / "nonexistent.toml"
    cfg = load_watchdog_config(policy)
    # Falls back to defaults
    assert cfg.warn_per_hour_usd == 1.0


# --- compute_rolling_rate ---


def test_rolling_rate_1h(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    # 10 records at $0.50 each in the last hour = $5.00/hr
    records = _make_records("brain", 0.50, 10, span_seconds=3600)
    _write_spend_log(log_path, records)
    rate = compute_rolling_rate(log_path, window_seconds=3600)
    assert rate == pytest.approx(5.0, abs=0.01)


def test_rolling_rate_5min(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    # 5 records at $0.40 each in the last 5 minutes = $2.00 in 5min
    records = _make_records("brain", 0.40, 5, span_seconds=300)
    _write_spend_log(log_path, records)
    rate = compute_rolling_rate(log_path, window_seconds=300)
    assert rate == pytest.approx(2.0, abs=0.01)


def test_rolling_rate_excludes_old_records(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    now = time.time()
    records = [
        {"ts": now - 7200, "agent": "old", "model": "haiku", "input_tokens": 1000, "output_tokens": 500, "estimated_cost_usd": 100.0},
        {"ts": now - 1800, "agent": "recent", "model": "haiku", "input_tokens": 1000, "output_tokens": 500, "estimated_cost_usd": 0.50},
    ]
    _write_spend_log(log_path, records)
    rate = compute_rolling_rate(log_path, window_seconds=3600)
    assert rate == pytest.approx(0.50, abs=0.01)


def test_rolling_rate_empty_log(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    log_path.touch()
    rate = compute_rolling_rate(log_path, window_seconds=3600)
    assert rate == 0.0


def test_rolling_rate_missing_log(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    rate = compute_rolling_rate(log_path, window_seconds=3600)
    assert rate == 0.0


# --- top_offending_agent ---


def test_top_offending_agent(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    now = time.time()
    records = [
        {"ts": now - 100, "agent": "brain", "model": "haiku", "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.50},
        {"ts": now - 50, "agent": "northstar", "model": "sonnet", "input_tokens": 20, "output_tokens": 10, "estimated_cost_usd": 2.00},
        {"ts": now - 30, "agent": "brain", "model": "haiku", "input_tokens": 30, "output_tokens": 15, "estimated_cost_usd": 0.30},
    ]
    _write_spend_log(log_path, records)
    agent = top_offending_agent(log_path, window_seconds=3600)
    assert agent == "northstar"


def test_top_offending_agent_empty(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    log_path.touch()
    assert top_offending_agent(log_path, window_seconds=3600) is None


# --- run_watchdog: warn threshold ---


def test_warn_threshold_fires_alert_not_kill(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    # $1.50/hr — above warn, below kill
    records = _make_records("brain", 0.15, 10, span_seconds=3600)
    _write_spend_log(log_path, records)

    result = run_watchdog(
        log_path=log_path,
        policy_path=policy,
        watchdog_log_path=watchdog_log,
    )

    assert result.action == WatchdogAction.WARN
    assert watchdog_log.exists()
    log_text = watchdog_log.read_text()
    assert "WARN" in log_text
    assert "KILL" not in log_text


# --- run_watchdog: kill threshold (hourly) ---


def test_kill_threshold_disables_agent_and_signals(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    # $6.00/hr — above kill
    records = _make_records("brain", 0.60, 10, span_seconds=3600)
    _write_spend_log(log_path, records)

    with patch("driftdriver.spend_watchdog._send_sigterm_to_hub") as mock_kill:
        result = run_watchdog(
            log_path=log_path,
            policy_path=policy,
            watchdog_log_path=watchdog_log,
        )

    assert result.action == WatchdogAction.KILL
    mock_kill.assert_called_once()

    # Verify watchdog.log has KILL entry
    log_text = watchdog_log.read_text()
    assert "KILL" in log_text

    # Verify policy was rewritten to disable offending agent's signal_gate
    import tomllib
    policy_data = tomllib.loads(policy.read_text())
    assert policy_data["watchdog"].get("disabled_agents") is not None
    assert "brain" in policy_data["watchdog"]["disabled_agents"]


# --- run_watchdog: 5-min burst ---


def test_5min_burst_catches_spike(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    # $3.00 in 5 minutes — above 5min kill but under hourly kill
    records = _make_records("brain", 0.60, 5, span_seconds=300)
    _write_spend_log(log_path, records)

    with patch("driftdriver.spend_watchdog._send_sigterm_to_hub") as mock_kill:
        result = run_watchdog(
            log_path=log_path,
            policy_path=policy,
            watchdog_log_path=watchdog_log,
        )

    assert result.action == WatchdogAction.KILL
    mock_kill.assert_called_once()
    log_text = watchdog_log.read_text()
    assert "KILL" in log_text
    assert "5min" in log_text.lower() or "burst" in log_text.lower()


# --- canary ---


def test_canary_fires_when_ledger_silent(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    # Records that are all >2h old
    now = time.time()
    records = [
        {"ts": now - 8000, "agent": "brain", "model": "haiku", "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.001},
    ]
    _write_spend_log(log_path, records)

    result = run_watchdog(
        log_path=log_path,
        policy_path=policy,
        watchdog_log_path=watchdog_log,
    )

    assert result.action == WatchdogAction.CANARY
    log_text = watchdog_log.read_text()
    assert "CANARY" in log_text


def test_canary_does_not_fire_on_empty_log(tmp_path: Path):
    """Empty log (no entries ever) should be OK, not a canary."""
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    log_path.touch()

    result = run_watchdog(
        log_path=log_path,
        policy_path=policy,
        watchdog_log_path=watchdog_log,
    )
    assert result.action == WatchdogAction.OK


# --- idempotency ---


def test_idempotent_no_double_fire(tmp_path: Path):
    """Running twice on same ledger doesn't double-fire."""
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    # Above warn threshold
    records = _make_records("brain", 0.15, 10, span_seconds=3600)
    _write_spend_log(log_path, records)

    state_path = tmp_path / "watchdog-state.json"

    run_watchdog(
        log_path=log_path,
        policy_path=policy,
        watchdog_log_path=watchdog_log,
        state_path=state_path,
    )
    first_log = watchdog_log.read_text()
    warn_count_1 = first_log.count("WARN")

    # Run again — same ledger, no new records
    run_watchdog(
        log_path=log_path,
        policy_path=policy,
        watchdog_log_path=watchdog_log,
        state_path=state_path,
    )
    second_log = watchdog_log.read_text()
    warn_count_2 = second_log.count("WARN")

    assert warn_count_2 == warn_count_1  # No additional WARN entry


def test_idempotent_kill_not_doubled(tmp_path: Path):
    """Running kill twice doesn't send SIGTERM again or duplicate log entries."""
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    records = _make_records("brain", 0.60, 10, span_seconds=3600)
    _write_spend_log(log_path, records)

    state_path = tmp_path / "watchdog-state.json"

    with patch("driftdriver.spend_watchdog._send_sigterm_to_hub") as mock_kill:
        run_watchdog(
            log_path=log_path,
            policy_path=policy,
            watchdog_log_path=watchdog_log,
            state_path=state_path,
        )
        run_watchdog(
            log_path=log_path,
            policy_path=policy,
            watchdog_log_path=watchdog_log,
            state_path=state_path,
        )

    # SIGTERM sent only once
    assert mock_kill.call_count == 1


# --- OK path ---


def test_ok_when_below_thresholds(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    watchdog_log = tmp_path / "watchdog.log"
    policy = tmp_path / "drift-policy.toml"
    _write_policy(policy, "[watchdog]\nwarn_per_hour_usd = 1.0\nkill_per_hour_usd = 5.0\nkill_per_5min_usd = 2.0\n")

    # $0.50/hr — well below warn
    records = _make_records("brain", 0.05, 10, span_seconds=3600)
    _write_spend_log(log_path, records)

    result = run_watchdog(
        log_path=log_path,
        policy_path=policy,
        watchdog_log_path=watchdog_log,
    )
    assert result.action == WatchdogAction.OK
