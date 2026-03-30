# ABOUTME: Tests for the shared signal-gate library (should_fire / record_fire / canary).
# ABOUTME: Validates content-hash gating, per-agent config, canary alerting, and disk persistence.

from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.signal_gate import (
    compute_content_hash,
    should_fire,
    record_fire,
    is_gate_enabled,
    check_canary,
)


# ---------------------------------------------------------------------------
# compute_content_hash
# ---------------------------------------------------------------------------


class TestComputeContentHash:
    def test_stable_for_same_input(self) -> None:
        data = {"repos": [{"name": "a"}], "scores": [1, 2, 3]}
        assert compute_content_hash(data) == compute_content_hash(data)

    def test_differs_for_different_input(self) -> None:
        a = {"key": "value1"}
        b = {"key": "value2"}
        assert compute_content_hash(a) != compute_content_hash(b)

    def test_order_independent_for_dicts(self) -> None:
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        assert compute_content_hash(a) == compute_content_hash(b)

    def test_handles_string_input(self) -> None:
        h = compute_content_hash("plain string data")
        assert isinstance(h, str) and len(h) > 0

    def test_handles_list_input(self) -> None:
        h = compute_content_hash([1, 2, 3])
        assert isinstance(h, str) and len(h) > 0

    def test_handles_empty_dict(self) -> None:
        h = compute_content_hash({})
        assert isinstance(h, str) and len(h) > 0


# ---------------------------------------------------------------------------
# should_fire
# ---------------------------------------------------------------------------


class TestShouldFire:
    def test_fires_on_first_call(self, tmp_path: Path) -> None:
        """First call for an agent should always fire (no prior hash)."""
        result = should_fire("northstardrift", {"data": "hello"}, gate_dir=tmp_path)
        assert result is True

    def test_does_not_fire_on_identical_input(self, tmp_path: Path) -> None:
        """Same input twice — second call should not fire."""
        data = {"snapshot": [1, 2, 3]}
        should_fire("northstardrift", data, gate_dir=tmp_path)
        record_fire("northstardrift", data, gate_dir=tmp_path)
        assert should_fire("northstardrift", data, gate_dir=tmp_path) is False

    def test_fires_on_changed_input(self, tmp_path: Path) -> None:
        """Changed input after a recorded fire should fire again."""
        should_fire("northstardrift", {"v": 1}, gate_dir=tmp_path)
        record_fire("northstardrift", {"v": 1}, gate_dir=tmp_path)
        assert should_fire("northstardrift", {"v": 2}, gate_dir=tmp_path) is True

    def test_agents_are_independent(self, tmp_path: Path) -> None:
        """Different agents maintain separate hashes."""
        data = {"same": "data"}
        should_fire("agent_a", data, gate_dir=tmp_path)
        record_fire("agent_a", data, gate_dir=tmp_path)
        # agent_b has never seen this data
        assert should_fire("agent_b", data, gate_dir=tmp_path) is True

    def test_persists_across_calls(self, tmp_path: Path) -> None:
        """Gate state persists to disk and is read back on subsequent calls."""
        data = {"persist": True}
        should_fire("northstardrift", data, gate_dir=tmp_path)
        record_fire("northstardrift", data, gate_dir=tmp_path)

        # Verify the file exists on disk
        state_file = tmp_path / "northstardrift.json"
        assert state_file.exists()

        # A fresh call should read from disk and not fire
        assert should_fire("northstardrift", data, gate_dir=tmp_path) is False

    def test_corrupted_state_file_fires(self, tmp_path: Path) -> None:
        """If the state file is corrupted, treat as first call and fire."""
        state_file = tmp_path / "northstardrift.json"
        state_file.write_text("NOT VALID JSON", encoding="utf-8")
        assert should_fire("northstardrift", {"x": 1}, gate_dir=tmp_path) is True


# ---------------------------------------------------------------------------
# record_fire
# ---------------------------------------------------------------------------


class TestRecordFire:
    def test_writes_state_file(self, tmp_path: Path) -> None:
        record_fire("attractor", {"findings": [1]}, gate_dir=tmp_path)
        state_file = tmp_path / "attractor.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert "content_hash" in state
        assert "last_fire_at" in state

    def test_updates_hash_on_new_data(self, tmp_path: Path) -> None:
        record_fire("attractor", {"v": 1}, gate_dir=tmp_path)
        state1 = json.loads((tmp_path / "attractor.json").read_text(encoding="utf-8"))

        record_fire("attractor", {"v": 2}, gate_dir=tmp_path)
        state2 = json.loads((tmp_path / "attractor.json").read_text(encoding="utf-8"))

        assert state1["content_hash"] != state2["content_hash"]

    def test_preserves_fire_count(self, tmp_path: Path) -> None:
        record_fire("agent_x", {"v": 1}, gate_dir=tmp_path)
        record_fire("agent_x", {"v": 2}, gate_dir=tmp_path)
        state = json.loads((tmp_path / "agent_x.json").read_text(encoding="utf-8"))
        assert state["fire_count"] == 2


# ---------------------------------------------------------------------------
# is_gate_enabled
# ---------------------------------------------------------------------------


class TestIsGateEnabled:
    def test_returns_false_when_no_section(self, tmp_path: Path) -> None:
        """No [signal_gates] section means disabled."""
        policy = tmp_path / "drift-policy.toml"
        policy.write_text('[factory]\nenabled = true\n', encoding="utf-8")
        assert is_gate_enabled("northstardrift", policy) is False

    def test_returns_true_when_agent_enabled(self, tmp_path: Path) -> None:
        policy = tmp_path / "drift-policy.toml"
        policy.write_text(
            '[signal_gates]\nnorthstardrift = true\nattractor = false\n',
            encoding="utf-8",
        )
        assert is_gate_enabled("northstardrift", policy) is True

    def test_returns_false_when_agent_disabled(self, tmp_path: Path) -> None:
        policy = tmp_path / "drift-policy.toml"
        policy.write_text(
            '[signal_gates]\nnorthstardrift = false\n',
            encoding="utf-8",
        )
        assert is_gate_enabled("northstardrift", policy) is False

    def test_returns_false_when_agent_missing(self, tmp_path: Path) -> None:
        """Agent not listed in [signal_gates] defaults to disabled."""
        policy = tmp_path / "drift-policy.toml"
        policy.write_text(
            '[signal_gates]\nattractor = true\n',
            encoding="utf-8",
        )
        assert is_gate_enabled("northstardrift", policy) is False

    def test_returns_false_when_no_policy_file(self, tmp_path: Path) -> None:
        policy = tmp_path / "drift-policy.toml"
        assert is_gate_enabled("northstardrift", policy) is False

    def test_falls_back_to_section_signal_gate_enabled(self, tmp_path: Path) -> None:
        """Legacy: [northstardrift] signal_gate_enabled = true should also work."""
        policy = tmp_path / "drift-policy.toml"
        policy.write_text(
            '[northstardrift]\nsignal_gate_enabled = true\n',
            encoding="utf-8",
        )
        assert is_gate_enabled("northstardrift", policy) is True


# ---------------------------------------------------------------------------
# check_canary
# ---------------------------------------------------------------------------


class TestCheckCanary:
    def test_no_alert_when_recently_fired(self, tmp_path: Path) -> None:
        """No alert when agent fired within the threshold."""
        record_fire("canary_agent", {"v": 1}, gate_dir=tmp_path)
        alert = check_canary("canary_agent", gate_dir=tmp_path, silent_threshold_seconds=3600)
        assert alert is None

    def test_alert_when_silent_too_long(self, tmp_path: Path) -> None:
        """Alert when agent hasn't fired in longer than threshold."""
        # Write state with a last_fire_at in the past
        state_file = tmp_path / "stale_agent.json"
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state = {
            "content_hash": "abc123",
            "last_fire_at": old_time,
            "fire_count": 1,
        }
        state_file.write_text(json.dumps(state), encoding="utf-8")

        alert = check_canary("stale_agent", gate_dir=tmp_path, silent_threshold_seconds=3600)
        assert alert is not None
        assert "stale_agent" in alert
        assert "silent" in alert.lower() or "no fire" in alert.lower()

    def test_no_alert_when_no_state_file(self, tmp_path: Path) -> None:
        """No state file = agent never ran, not a canary concern."""
        alert = check_canary("unknown_agent", gate_dir=tmp_path, silent_threshold_seconds=3600)
        assert alert is None

    def test_no_alert_when_threshold_not_exceeded(self, tmp_path: Path) -> None:
        """Fired 30 min ago, threshold is 1h — no alert."""
        state_file = tmp_path / "recent_agent.json"
        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        state = {
            "content_hash": "def456",
            "last_fire_at": recent_time,
            "fire_count": 5,
        }
        state_file.write_text(json.dumps(state), encoding="utf-8")

        alert = check_canary("recent_agent", gate_dir=tmp_path, silent_threshold_seconds=3600)
        assert alert is None
