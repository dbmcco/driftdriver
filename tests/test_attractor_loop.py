# ABOUTME: Tests for the attractor loop — convergence detection, pass orchestration, circuit breakers.
# ABOUTME: Uses mocked lane runs and task creation to test loop logic without real wg calls.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
from typing import Any

from driftdriver.attractor_loop import (
    AttractorRun,
    PassResult,
    CircuitBreakers,
    run_attractor_pass,
    check_convergence,
    run_attractor_loop,
    compute_gate_hash,
    load_gate_state,
    save_gate_state,
    _write_gate_log,
    _check_attractor_canary,
)
from driftdriver.attractors import Attractor, AttractorCriterion, AttractorGap
from driftdriver.attractor_planner import ConvergencePlan, EscalationRecord
from driftdriver.bundles import BundleInstance
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_circuit_breakers_defaults():
    cb = CircuitBreakers()
    assert cb.max_passes == 3
    assert cb.plateau_threshold == 2
    assert cb.max_tasks_per_cycle == 30


def test_check_convergence_converged():
    passes = [
        PassResult(pass_number=0, findings_before=3, findings_after=0, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers())
    assert status == "converged"


def test_check_convergence_plateau():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=3, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=2, findings_before=3, findings_after=3, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers(plateau_threshold=2))
    assert status == "plateau"


def test_check_convergence_max_passes():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=4, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=4, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=2, findings_before=3, findings_after=2, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers(max_passes=3))
    assert status == "max_passes"


def test_check_convergence_improving():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=3, findings_after=1, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers())
    assert status == "continue"


def test_attractor_run_fields():
    run = AttractorRun(
        repo="test-repo",
        attractor="production-ready",
        status="converged",
    )
    assert run.repo == "test-repo"
    assert run.passes == []


def test_pass_result_fields():
    pr = PassResult(
        pass_number=0,
        findings_before=5,
        findings_after=2,
        duration_seconds=15.5,
        bundles_applied=["scope-drift"],
        bundle_outcomes={"scope-drift": "resolved"},
    )
    assert pr.findings_before == 5
    assert pr.findings_after == 2


# ---------------------------------------------------------------------------
# Signal gate tests — all named with 'attractor' and 'gate' for pytest -k
# ---------------------------------------------------------------------------


def _make_lane_results(specs: list[tuple[str, str, str]]) -> dict:
    """Helper: build lane_results from [(lane, severity, message), ...]."""
    from driftdriver.lane_contract import LaneFinding, LaneResult

    lanes: dict = {}
    for lane, severity, message in specs:
        if lane not in lanes:
            lanes[lane] = LaneResult(lane=lane, findings=[], exit_code=0, summary="")
        lanes[lane].findings.append(LaneFinding(message=message, severity=severity))
    return lanes


def test_attractor_gate_compute_hash_stable():
    """Same findings produce the same hash."""
    lr = _make_lane_results([("coredrift", "error", "missing contract")])
    h1 = compute_gate_hash(lr)
    h2 = compute_gate_hash(lr)
    assert h1 == h2


def test_attractor_gate_compute_hash_changes_on_new_finding():
    """Adding a finding changes the hash."""
    lr1 = _make_lane_results([("coredrift", "error", "missing contract")])
    lr2 = _make_lane_results([
        ("coredrift", "error", "missing contract"),
        ("coredrift", "warning", "stale task"),
    ])
    assert compute_gate_hash(lr1) != compute_gate_hash(lr2)


def test_attractor_gate_compute_hash_empty():
    """Empty findings produce a stable hash."""
    h = compute_gate_hash({})
    assert len(h) == 64  # sha256 hex


def test_attractor_gate_load_state_missing_file():
    """Missing gate state file returns empty dict."""
    with TemporaryDirectory() as tmpdir:
        state = load_gate_state(Path(tmpdir))
        assert state == {}


def test_attractor_gate_load_state_existing_file():
    """Existing gate state file is parsed correctly."""
    with TemporaryDirectory() as tmpdir:
        wg_dir = Path(tmpdir) / ".workgraph"
        wg_dir.mkdir()
        (wg_dir / "attractor-gate-state.json").write_text(
            json.dumps({"myrepo::onboarded": "abc123"}), encoding="utf-8"
        )
        state = load_gate_state(Path(tmpdir))
        assert state == {"myrepo::onboarded": "abc123"}


def test_attractor_gate_save_state():
    """State is written to .workgraph/attractor-gate-state.json."""
    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        save_gate_state(repo_path, {"myrepo::onboarded": "deadbeef"})
        gate_file = repo_path / ".workgraph" / "attractor-gate-state.json"
        assert gate_file.exists()
        data = json.loads(gate_file.read_text())
        assert data["myrepo::onboarded"] == "deadbeef"


def _make_attractor() -> "Attractor":
    return Attractor(id="onboarded", description="Onboarded state")


def _make_diagnose_fn(findings_count: int):
    """Return a diagnose function that produces N findings."""
    from driftdriver.lane_contract import LaneFinding, LaneResult

    def _diagnose(repo_path):
        lane = LaneResult(lane="coredrift", findings=[], exit_code=0, summary="")
        for i in range(findings_count):
            lane.findings.append(LaneFinding(message=f"finding-{i}", severity="error"))
        return {"coredrift": lane}

    return _diagnose


def test_attractor_gate_skips_when_hash_matches():
    """Loop returns 'signal_gated' when findings hash matches last run."""
    diagnose_fn = _make_diagnose_fn(2)
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        wg_dir = repo_path / ".workgraph"
        wg_dir.mkdir()

        # Pre-compute and persist the hash for the findings we'll return
        initial_lr = diagnose_fn(repo_path)
        current_hash = compute_gate_hash(initial_lr)
        save_gate_state(repo_path, {"test-repo::onboarded": current_hash})

        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
            signal_gate_enabled=True,
        )

        assert run.status == "signal_gated"
        execute_fn.assert_not_called()
        assert run.passes == []


def test_attractor_gate_runs_when_hash_differs():
    """Loop proceeds normally when findings hash differs from last run."""
    diagnose_fn = _make_diagnose_fn(0)  # 0 findings -> converges immediately
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        wg_dir = repo_path / ".workgraph"
        wg_dir.mkdir()

        # Save a stale hash
        save_gate_state(repo_path, {"test-repo::onboarded": "stale_hash_0000"})

        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
            signal_gate_enabled=True,
        )

        assert run.status == "converged"
        assert len(run.passes) >= 1


def test_attractor_gate_disabled_by_default():
    """Gate does not fire when signal_gate_enabled=False (default)."""
    diagnose_fn = _make_diagnose_fn(0)
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)

        # Even with no state file at all, loop runs normally
        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
        )

        assert run.status != "signal_gated"


def test_attractor_gate_saves_hash_after_run():
    """After a gated run completes, new hash is persisted to disk."""
    diagnose_fn = _make_diagnose_fn(0)
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / ".workgraph").mkdir()

        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
            signal_gate_enabled=True,
        )

        assert run.status == "converged"
        state = load_gate_state(repo_path)
        assert "test-repo::onboarded" in state
        entry = state["test-repo::onboarded"]
        assert isinstance(entry, dict)
        assert len(entry["hash"]) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Gate log + canary tests — all named with 'attractor' and 'gate' for pytest -k
# ---------------------------------------------------------------------------


def test_attractor_gate_writes_log_on_skip():
    """Gate skip writes an entry to gate-log.jsonl with agent=attractor."""
    diagnose_fn = _make_diagnose_fn(2)
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        wg_dir = repo_path / ".workgraph"
        wg_dir.mkdir()

        # Pre-seed hash so gate fires (skip)
        initial_lr = diagnose_fn(repo_path)
        current_hash = compute_gate_hash(initial_lr)
        save_gate_state(repo_path, {"test-repo::onboarded": current_hash})

        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
            signal_gate_enabled=True,
        )

        assert run.status == "signal_gated"
        gate_log = wg_dir / "gate-log.jsonl"
        assert gate_log.exists()
        entries = [json.loads(line) for line in gate_log.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["agent"] == "attractor"
        assert entries[0]["skipped"] is True
        assert entries[0]["reason"] == "no_signal"


def test_attractor_gate_writes_log_on_proceed():
    """Gate proceed writes an entry to gate-log.jsonl with fired=True."""
    diagnose_fn = _make_diagnose_fn(0)
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        wg_dir = repo_path / ".workgraph"
        wg_dir.mkdir()

        # Stale hash so gate lets it through
        save_gate_state(repo_path, {"test-repo::onboarded": "stale_hash_0000"})

        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
            signal_gate_enabled=True,
        )

        assert run.status == "converged"
        gate_log = wg_dir / "gate-log.jsonl"
        assert gate_log.exists()
        entries = [json.loads(line) for line in gate_log.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["agent"] == "attractor"
        assert entries[0]["fired"] is True
        assert entries[0]["skipped"] is False


def test_attractor_gate_state_tracks_timestamps():
    """Gate state includes last_evidence_at and last_checked_at after a real run."""
    diagnose_fn = _make_diagnose_fn(0)
    execute_fn = MagicMock(return_value={})

    with TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir)
        (repo_path / ".workgraph").mkdir()

        run = run_attractor_loop(
            repo="test-repo",
            repo_path=repo_path,
            attractor=_make_attractor(),
            bundles=[],
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
            signal_gate_enabled=True,
        )

        state = load_gate_state(repo_path)
        gate_key = "test-repo::onboarded"
        # State is now a dict with hash + timestamps
        assert gate_key in state
        assert isinstance(state[gate_key], dict)
        assert "hash" in state[gate_key]
        assert "last_evidence_at" in state[gate_key]
        assert "last_checked_at" in state[gate_key]


def test_attractor_gate_canary_no_alert_when_recent():
    """Canary does not alert when last_evidence_at is recent."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    gate_state = {"last_evidence_at": now}
    # Should not raise or call wg notify
    _check_attractor_canary(gate_state, alert_hours=4)


def test_attractor_gate_canary_alerts_when_stale():
    """Canary alerts via wg notify when no evidence in alert_hours."""
    from datetime import datetime, timezone, timedelta
    stale = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    gate_state = {"last_evidence_at": stale}
    with patch("subprocess.run") as mock_run:
        _check_attractor_canary(gate_state, alert_hours=4)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "wg" in args[0]
        assert "notify" in args[1]
        assert "attractor" in args[2].lower()


def test_attractor_gate_write_log_unit():
    """_write_gate_log writes a valid JSONL entry."""
    with TemporaryDirectory() as tmpdir:
        wg_dir = Path(tmpdir)
        _write_gate_log(wg_dir, agent="attractor", skipped=True, reason="no_signal", gate_key="r::a")
        log_path = wg_dir / "gate-log.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["agent"] == "attractor"
        assert entry["skipped"] is True
        assert entry["gate_key"] == "r::a"
        assert "ts" in entry
