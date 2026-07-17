"""Tests for `driftdriver check --gate` severity threshold and escape hatch.

Gate mode inverts the normal check posture so a graph gate node's ``--exec``
passes on advisory info-level findings and only fails (non-zero) when a finding
is at/above warning severity. Lanes emit ``warn`` for real drift, so ``warn``
must be treated as blocking.

The escape hatch degrades to advisory after N consecutive failures so a
perpetually-failing gate cannot deadlock its ``--after`` dependents.
"""

from __future__ import annotations

import pytest

from driftdriver.cli.check import (
    ExitCode,
    _apply_gate_with_escape,
    _gate_blocks,
    _gate_failures_path,
    _read_gate_failures,
)


def _plugins(findings_by_lane: dict[str, list[dict]]) -> dict:
    return {
        lane: {"ran": True, "exit_code": 3, "report": {"findings": fs}}
        for lane, fs in findings_by_lane.items()
    }


def test_gate_passes_with_no_findings():
    blocks, n = _gate_blocks(_plugins({"coredrift": []}))
    assert blocks is False
    assert n == 0


def test_gate_passes_on_info_only():
    blocks, n = _gate_blocks(_plugins({"specdrift": [{"severity": "info"}, {"severity": "note"}]}))
    assert blocks is False
    assert n == 0


def test_gate_blocks_on_warn_realised_as_warning():
    # depsdrift/specdrift emit "warn" for real drift; the gate must catch it.
    blocks, n = _gate_blocks(
        _plugins({"depsdrift": [{"severity": "warn"}, {"severity": "info"}]})
    )
    assert blocks is True
    assert n == 1


def test_gate_blocks_on_error_and_critical():
    blocks, n = _gate_blocks(
        _plugins(
            {
                "coredrift": [{"severity": "error"}],
                "specdrift": [{"severity": "critical"}],
            }
        )
    )
    assert blocks is True
    assert n == 2


def test_gate_tolerates_malformed_reports():
    blocks, n = _gate_blocks(
        {"x": {"report": "not-a-dict"}, "y": {}, "z": {"report": {"findings": "nope"}}}
    )
    assert blocks is False
    assert n == 0


def test_gate_handles_explicit_warning_token():
    blocks, n = _gate_blocks(_plugins({"archdrift": [{"severity": "warning"}]}))
    assert blocks is True
    assert n == 1


def test_gate_none_input_is_safe():
    blocks, n = _gate_blocks(None)  # type: ignore[arg-type]
    assert blocks is False
    assert n == 0


# ===== Escape-hatch (degrade-to-advisory) tests =====


def _record_degrade_side_effects(monkeypatch):
    """Patch the degrade side effects to record calls instead of hitting wg."""
    calls = {"log": [], "followup": []}
    monkeypatch.setattr(
        "driftdriver.cli.check._wg_log_message",
        lambda **kw: calls["log"].append(kw),
    )
    monkeypatch.setattr(
        "driftdriver.cli.check._ensure_gate_waived_followup",
        lambda **kw: calls["followup"].append(kw),
    )
    return calls


def test_gate_under_threshold_still_fails(tmp_path, monkeypatch):
    calls = _record_degrade_side_effects(monkeypatch)
    r1 = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-A", blocks=True, blocking_count=1, max_failures=3
    )
    assert r1["exit_code"] == ExitCode.findings
    assert r1["degraded"] is False
    assert r1["consecutive_failures"] == 1
    r2 = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-A", blocks=True, blocking_count=1, max_failures=3
    )
    assert r2["exit_code"] == ExitCode.findings
    assert r2["degraded"] is False
    assert r2["consecutive_failures"] == 2
    assert calls["log"] == []
    assert calls["followup"] == []


def test_gate_degrades_at_threshold(tmp_path, monkeypatch):
    calls = _record_degrade_side_effects(monkeypatch)
    for _ in range(2):
        _apply_gate_with_escape(
            wg_dir=tmp_path, task_id="task-B", blocks=True, blocking_count=2, max_failures=3
        )
    r3 = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-B", blocks=True, blocking_count=2, max_failures=3
    )
    assert r3["exit_code"] == ExitCode.ok
    assert r3["degraded"] is True
    assert r3["consecutive_failures"] == 3
    assert len(calls["log"]) == 1
    assert "degraded" in calls["log"][0]["message"]
    assert len(calls["followup"]) == 1
    assert calls["followup"][0]["task_id"] == "task-B"
    # Counter persists at 3 after degrade (stays degraded until a real pass).
    assert _read_gate_failures(tmp_path, "task-B") == 3


def test_gate_resets_on_pass(tmp_path, monkeypatch):
    _record_degrade_side_effects(monkeypatch)
    _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-C", blocks=True, blocking_count=1, max_failures=3
    )
    _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-C", blocks=True, blocking_count=1, max_failures=3
    )
    r = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-C", blocks=False, blocking_count=0, max_failures=3
    )
    assert r["exit_code"] == ExitCode.ok
    assert r["degraded"] is False
    assert _read_gate_failures(tmp_path, "task-C") == 0
    assert not _gate_failures_path(tmp_path, "task-C").exists()


def test_gate_max_failures_1_degrades_immediately(tmp_path, monkeypatch):
    calls = _record_degrade_side_effects(monkeypatch)
    r = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-D", blocks=True, blocking_count=1, max_failures=1
    )
    assert r["exit_code"] == ExitCode.ok
    assert r["degraded"] is True
    assert r["consecutive_failures"] == 1
    assert len(calls["followup"]) == 1


def test_gate_per_task_isolation(tmp_path, monkeypatch):
    _record_degrade_side_effects(monkeypatch)
    for _ in range(3):
        _apply_gate_with_escape(
            wg_dir=tmp_path, task_id="task-A", blocks=True, blocking_count=1, max_failures=3
        )
    assert _read_gate_failures(tmp_path, "task-B") == 0
    rB = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-B", blocks=True, blocking_count=1, max_failures=3
    )
    assert rB["exit_code"] == ExitCode.findings
    assert rB["degraded"] is False
    assert rB["consecutive_failures"] == 1


def test_gate_no_task_id_pure_threshold(tmp_path, monkeypatch):
    calls = _record_degrade_side_effects(monkeypatch)
    r = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id=None, blocks=True, blocking_count=1, max_failures=3
    )
    assert r["exit_code"] == ExitCode.findings
    assert r["degraded"] is False
    assert r["consecutive_failures"] == 0
    assert calls["log"] == []
    assert calls["followup"] == []


def test_gate_failures_path_sanitizes(tmp_path):
    p = _gate_failures_path(tmp_path, "task/with/slashes")
    assert p.parent == tmp_path / ".gate-failures"
    # Slashes must be sanitized to avoid path traversal.
    assert "/" not in p.name
    assert p.suffix == ".json"


def test_gate_degrade_survives_side_effect_failure(tmp_path, monkeypatch):
    """MAJOR-1: the degrade MUST exit 0 even if its log/followup side-effects
    throw. Otherwise the escape hatch deadlocks the graph via its own "loud"
    side-effect (a thrown followup -> non-zero exit -> wg parks in Failed)."""

    def _raise(**kw):
        raise RuntimeError("side-effect boom")

    monkeypatch.setattr("driftdriver.cli.check._wg_log_message", _raise)
    monkeypatch.setattr("driftdriver.cli.check._ensure_gate_waived_followup", _raise)
    for _ in range(2):
        _apply_gate_with_escape(
            wg_dir=tmp_path, task_id="task-throw", blocks=True, blocking_count=2, max_failures=3
        )
    r3 = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-throw", blocks=True, blocking_count=2, max_failures=3
    )
    assert r3["exit_code"] == ExitCode.ok
    assert r3["degraded"] is True
    assert r3["consecutive_failures"] == 3
    assert r3["waived_followup"] is None


def test_gate_degrade_side_effects_fire_only_on_crossing(tmp_path, monkeypatch):
    """NIT-2: side-effects fire only on the threshold crossing, not on every
    subsequent degraded run (avoids log spam; the followup is idempotent)."""
    calls = _record_degrade_side_effects(monkeypatch)
    for _ in range(3):  # 3rd run crosses the threshold
        _apply_gate_with_escape(
            wg_dir=tmp_path, task_id="task-x", blocks=True, blocking_count=1, max_failures=3
        )
    assert len(calls["log"]) == 1
    assert len(calls["followup"]) == 1
    # 4th run: still degraded, but side-effects must NOT re-fire.
    r4 = _apply_gate_with_escape(
        wg_dir=tmp_path, task_id="task-x", blocks=True, blocking_count=1, max_failures=3
    )
    assert r4["exit_code"] == ExitCode.ok
    assert r4["degraded"] is True
    assert len(calls["log"]) == 1
    assert len(calls["followup"]) == 1
