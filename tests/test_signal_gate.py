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
    VOLATILE_FIELDS,
    SignalGate,
    compute_content_hash,
    should_fire,
    record_fire,
    is_gate_enabled,
    check_canary,
    log_canary_decision,
)


# ---------------------------------------------------------------------------
# VOLATILE_FIELDS constant
# ---------------------------------------------------------------------------


class TestVolatileFields:
    def test_contains_expected_fields(self) -> None:
        for field in ("ts", "timestamp", "checked_at", "agent_count", "task_count", "cycle_id"):
            assert field in VOLATILE_FIELDS, f"expected '{field}' in VOLATILE_FIELDS"

    def test_is_frozenset(self) -> None:
        assert isinstance(VOLATILE_FIELDS, frozenset)


# ---------------------------------------------------------------------------
# SignalGate class — volatile-stripped hash suppression
# ---------------------------------------------------------------------------


class TestSignalGate:
    def test_fires_on_first_call(self, tmp_path: Path) -> None:
        gate = SignalGate(tmp_path / "gate.json")
        assert gate.should_fire({"findings": ["a"], "repo": "x"}) is True

    def test_suppresses_when_only_volatile_fields_differ(self, tmp_path: Path) -> None:
        """Same structural content, different ts — gate must suppress on second call."""
        gate = SignalGate(tmp_path / "gate.json")
        inp1 = {"findings": ["a", "b"], "repo": "test", "ts": "2026-04-01T00:00:00"}
        inp2 = {"findings": ["a", "b"], "repo": "test", "ts": "2026-04-01T01:00:00"}
        r1 = gate.should_fire(inp1)
        r2 = gate.should_fire(inp2)
        assert r1 is True
        assert r2 is False

    def test_fires_when_structural_content_changes(self, tmp_path: Path) -> None:
        gate = SignalGate(tmp_path / "gate.json")
        inp1 = {"findings": ["a", "b"], "repo": "test", "ts": "2026-04-01T00:00:00"}
        inp2 = {"findings": ["a", "b", "c"], "repo": "test", "ts": "2026-04-01T01:00:00"}
        r1 = gate.should_fire(inp1)
        r2 = gate.should_fire(inp2)
        assert r1 is True
        assert r2 is True

    def test_all_volatile_fields_stripped(self, tmp_path: Path) -> None:
        """All six canonical volatile fields are ignored."""
        gate = SignalGate(tmp_path / "gate.json")
        base = {"findings": ["x"], "repo": "r"}
        inp1 = {**base, "ts": "t1", "timestamp": "t1", "checked_at": "t1",
                "agent_count": 3, "task_count": 10, "cycle_id": "c1"}
        inp2 = {**base, "ts": "t2", "timestamp": "t2", "checked_at": "t2",
                "agent_count": 7, "task_count": 20, "cycle_id": "c2"}
        gate.should_fire(inp1)
        assert gate.should_fire(inp2) is False

    def test_persists_state_to_disk(self, tmp_path: Path) -> None:
        state_path = tmp_path / "gate.json"
        gate = SignalGate(state_path)
        gate.should_fire({"findings": ["a"], "ts": "t1"})
        assert state_path.exists()
        # New instance reads from same file and suppresses
        gate2 = SignalGate(state_path)
        assert gate2.should_fire({"findings": ["a"], "ts": "t2"}) is False

    def test_non_dict_input_works(self, tmp_path: Path) -> None:
        gate = SignalGate(tmp_path / "gate.json")
        assert gate.should_fire("hello") is True
        assert gate.should_fire("hello") is False


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


# ---------------------------------------------------------------------------
# log_canary_decision (JSONL logging)
# ---------------------------------------------------------------------------


class TestLogCanaryDecision:
    def test_creates_canary_file_on_first_write(self, tmp_path: Path) -> None:
        """First log_canary_decision call creates the JSONL file."""
        log_canary_decision(
            agent_name="northstardrift",
            fired=True,
            content_hash="abc123",
            canary_log=tmp_path / "signal-gate-canary.jsonl",
        )
        canary_file = tmp_path / "signal-gate-canary.jsonl"
        assert canary_file.exists()
        entries = [json.loads(line) for line in canary_file.read_text().strip().split("\n")]
        assert len(entries) == 1
        assert entries[0]["agent"] == "northstardrift"
        assert entries[0]["fired"] is True
        assert entries[0]["content_hash"] == "abc123"
        assert "ts" in entries[0]

    def test_appends_multiple_entries(self, tmp_path: Path) -> None:
        """Multiple calls append separate JSONL lines."""
        canary_file = tmp_path / "signal-gate-canary.jsonl"
        log_canary_decision("agent_a", fired=True, content_hash="h1", canary_log=canary_file)
        log_canary_decision("agent_b", fired=False, content_hash="h2", canary_log=canary_file)
        entries = [json.loads(line) for line in canary_file.read_text().strip().split("\n")]
        assert len(entries) == 2
        assert entries[0]["agent"] == "agent_a"
        assert entries[0]["fired"] is True
        assert entries[1]["agent"] == "agent_b"
        assert entries[1]["fired"] is False

    def test_records_suppression(self, tmp_path: Path) -> None:
        """When fired=False, entry records suppression."""
        canary_file = tmp_path / "signal-gate-canary.jsonl"
        log_canary_decision("attractor", fired=False, content_hash="same", canary_log=canary_file)
        entry = json.loads(canary_file.read_text().strip())
        assert entry["fired"] is False
        assert entry["agent"] == "attractor"

    def test_includes_reason_when_provided(self, tmp_path: Path) -> None:
        """Optional reason field is included."""
        canary_file = tmp_path / "signal-gate-canary.jsonl"
        log_canary_decision("northstardrift", fired=True, content_hash="x", reason="content_changed", canary_log=canary_file)
        entry = json.loads(canary_file.read_text().strip())
        assert entry["reason"] == "content_changed"
