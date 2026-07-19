from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.cli import main
from driftdriver.speedriftd import run_runtime_cycle
from driftdriver.speedriftd_state import load_control_state, runtime_paths, write_control_state


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    workgraph = tmp_path / ".workgraph"
    workgraph.mkdir()
    (workgraph / "graph.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def _expire_control_file(repo: Path) -> None:
    control_path = runtime_paths(repo)["control"]
    control = json.loads(control_path.read_text(encoding="utf-8"))
    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    control["lease_acquired_at"] = expired
    control["lease_expires_at"] = expired
    control["lease_active"] = False
    control_path.write_text(json.dumps(control), encoding="utf-8")


def test_runtime_defaults_to_observe_without_a_lease(repo: Path) -> None:
    control = load_control_state(repo)

    assert control["mode"] == "observe"
    assert control["dispatch_enabled"] is False
    assert control["interactive_service_start"] is False


def test_supervise_requires_explicit_lease_owner_and_reason(repo: Path) -> None:
    assert main(["--dir", str(repo), "speedriftd", "status", "--set-mode", "supervise"]) == 2

    control = write_control_state(
        repo,
        mode="supervise",
        lease_owner="human-supervisor",
        reason="approved supervised runtime",
    )

    assert control["mode"] == "supervise"
    assert control["lease_owner"] == "human-supervisor"
    assert control["reason"] == "approved supervised runtime"
    assert control["dispatch_enabled"] is True
    assert control["interactive_service_start"] is True


def test_autonomous_requires_explicit_lease_owner_and_reason(repo: Path) -> None:
    assert main(
        ["--dir", str(repo), "speedriftd", "status", "--set-mode", "autonomous", "--lease-owner", "human"]
    ) == 2

    control = write_control_state(
        repo,
        mode="autonomous",
        lease_owner="human",
        reason="explicit autonomous approval",
    )

    assert control["mode"] == "autonomous"
    assert control["dispatch_enabled"] is True
    assert control["lease_active"] is True


def test_expired_lease_fails_closed_to_observe_dispatch(repo: Path) -> None:
    control_path = repo / ".workgraph" / "service" / "runtime" / "control.json"
    control_path.parent.mkdir(parents=True)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    control_path.write_text(
        json.dumps(
            {
                "mode": "autonomous",
                "lease_owner": "stale-supervisor",
                "lease_acquired_at": expired,
                "lease_expires_at": expired,
                "lease_ttl_seconds": 60,
                "reason": "old approval",
            }
        ),
        encoding="utf-8",
    )

    control = load_control_state(repo)

    assert control["mode"] == "autonomous"
    assert control["lease_active"] is False
    assert control["dispatch_enabled"] is False
    assert control["interactive_service_start"] is False


def test_runtime_cycle_requires_previous_active_snapshot_for_expiry_stop(repo: Path) -> None:
    write_control_state(
        repo,
        mode="autonomous",
        lease_owner="stale-supervisor",
        lease_ttl_seconds=60,
        reason="runtime gate test",
    )
    _expire_control_file(repo)

    with (
        patch("driftdriver.speedriftd.get_ready_tasks", return_value=[]),
        patch("driftdriver.speedriftd._stop_workgraph_service") as stop,
    ):
        snapshot = run_runtime_cycle(repo)

    assert "last_lease_expiry_stop" not in snapshot
    stop.assert_not_called()


def test_runtime_cycle_records_active_to_expired_stop(repo: Path) -> None:
    write_control_state(
        repo,
        mode="supervise",
        lease_owner="supervisor",
        lease_ttl_seconds=60,
        reason="runtime gate test",
    )
    with patch("driftdriver.speedriftd.get_ready_tasks", return_value=[]):
        run_runtime_cycle(repo)
        _expire_control_file(repo)
        with patch(
            "driftdriver.speedriftd._stop_workgraph_service",
            return_value={"exit_code": 3, "stdout": "", "stderr": "denied"},
        ) as stop:
            snapshot = run_runtime_cycle(repo)

    stop.assert_called_once_with(repo)
    assert snapshot["last_lease_expiry_stop"]["stop_exit_code"] == 3


def test_runtime_cycle_does_not_expiry_stop_on_active_to_manual_or_observe(repo: Path) -> None:
    write_control_state(
        repo,
        mode="autonomous",
        lease_owner="supervisor",
        lease_ttl_seconds=60,
        reason="runtime gate test",
    )
    with patch("driftdriver.speedriftd.get_ready_tasks", return_value=[]):
        run_runtime_cycle(repo)
        for mode in ("manual", "observe"):
            write_control_state(repo, mode=mode, reason=f"switch to {mode}")
            with patch("driftdriver.speedriftd._stop_workgraph_service") as stop:
                snapshot = run_runtime_cycle(repo)
            assert "last_lease_expiry_stop" not in snapshot
            stop.assert_not_called()
            # Re-establish an active snapshot for the next mode transition.
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="supervisor",
                lease_ttl_seconds=60,
                reason="runtime gate test",
            )
            run_runtime_cycle(repo)


def test_session_start_template_requires_active_lease_before_service_start() -> None:
    template = (
        Path(__file__).parents[1]
        / "driftdriver"
        / "templates"
        / "handlers"
        / "session-start.sh"
    ).read_text(encoding="utf-8")

    assert "lease_active" in template
    assert '[[ "$LEASE_ACTIVE" == "true" ]]' in template
    assert 'wg service start' in template
