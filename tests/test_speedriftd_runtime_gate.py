from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from driftdriver.cli import main
from driftdriver.speedriftd_state import load_control_state, write_control_state


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    workgraph = tmp_path / ".workgraph"
    workgraph.mkdir()
    (workgraph / "graph.jsonl").write_text("", encoding="utf-8")
    return tmp_path


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
