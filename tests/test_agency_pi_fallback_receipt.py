from __future__ import annotations

import json
from pathlib import Path

import pytest

import driftdriver.agency_speedrift_wrapper as agency_wrapper
from driftdriver.agency_speedrift_wrapper import record_agency_pi_fallback_receipt
from driftdriver.speedriftd_state import load_control_state, runtime_paths, write_control_state


@pytest.mark.parametrize(
    ("mode", "lease_owner"),
    [("observe", None), ("supervise", "existing-supervisor"), ("autonomous", "existing-autopilot")],
)
def test_agency_pi_fallback_receipt_is_audit_only(
    tmp_path: Path,
    mode: str,
    lease_owner: str | None,
) -> None:
    (tmp_path / ".workgraph" / "graph.jsonl").parent.mkdir(parents=True)
    (tmp_path / ".workgraph" / "graph.jsonl").write_text("", encoding="utf-8")
    if lease_owner:
        write_control_state(tmp_path, mode=mode, lease_owner=lease_owner, source="fixture")
    else:
        write_control_state(tmp_path, mode=mode, release_lease=True, source="fixture")
    before = load_control_state(tmp_path)

    receipt = record_agency_pi_fallback_receipt(
        tmp_path,
        task_id="phase0.workgraph-pi-compat",
        selected_model="anthropic/claude-opus-4-8",
        fallback_model="zai/glm-5.2",
        reason="agency health endpoint unavailable",
        timestamp="2026-07-18T20:00:00+00:00",
    )

    after = load_control_state(tmp_path)
    receipt_path = runtime_paths(tmp_path)["dir"] / "agency-pi-fallback-receipts.jsonl"
    stored = json.loads(receipt_path.read_text(encoding="utf-8").splitlines()[0])

    assert receipt == stored
    assert receipt["repo"] == tmp_path.name
    assert receipt["task_id"] == "phase0.workgraph-pi-compat"
    assert receipt["preferred_runtime"] == "agency"
    assert receipt["preferred_model"] == "anthropic/claude-opus-4-8"
    assert receipt["fallback_runtime"] == "pi"
    assert receipt["fallback_model"] == "zai/glm-5.2"
    assert receipt["reason"] == "agency health endpoint unavailable"
    assert receipt["control_before"] == {"mode": mode, "lease_active": before["lease_active"]}
    assert receipt["control_after"] == receipt["control_before"]
    assert after["mode"] == before["mode"] == mode
    assert after["lease_active"] == before["lease_active"]
    assert after["lease_owner"] == before["lease_owner"]


def test_fallback_receipt_detects_control_change_after_receipt_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".workgraph" / "graph.jsonl").parent.mkdir(parents=True)
    (tmp_path / ".workgraph" / "graph.jsonl").write_text("", encoding="utf-8")
    before = {"mode": "observe", "lease_active": False}
    changed = {"mode": "autonomous", "lease_active": True}
    calls = iter([before, changed])
    monkeypatch.setattr(agency_wrapper, "load_control_state", lambda _path: next(calls))

    with pytest.raises(RuntimeError, match="control changed while recording"):
        record_agency_pi_fallback_receipt(
            tmp_path,
            task_id="task",
            selected_model="anthropic/claude-sonnet-4-5",
            fallback_model="anthropic/claude-haiku-4-5",
            reason="agency unavailable",
            timestamp="2026-07-18T20:00:00+00:00",
        )

    receipt_path = runtime_paths(tmp_path)["dir"] / "agency-pi-fallback-receipts.jsonl"
    stored = json.loads(receipt_path.read_text(encoding="utf-8").splitlines()[0])
    assert stored["control_before"] == before
    assert stored["control_after"] == before


def test_fallback_receipt_rejects_non_live_model_without_touching_control(tmp_path: Path) -> None:
    (tmp_path / ".workgraph" / "graph.jsonl").parent.mkdir(parents=True)
    (tmp_path / ".workgraph" / "graph.jsonl").write_text("", encoding="utf-8")
    before = load_control_state(tmp_path)

    with pytest.raises(ValueError, match="allowed Pi model"):
        record_agency_pi_fallback_receipt(
            tmp_path,
            task_id="task",
            selected_model="agency/unknown",
            fallback_model="zai/glm-5.2",
            reason="unavailable",
        )

    after = load_control_state(tmp_path)
    assert after["mode"] == before["mode"] == "observe"
    assert after["lease_active"] is before["lease_active"] is False


def test_fallback_receipt_writer_is_not_a_mode_setter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".workgraph" / "graph.jsonl").parent.mkdir(parents=True)
    (tmp_path / ".workgraph" / "graph.jsonl").write_text("", encoding="utf-8")

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("fallback receipt must not arm or change speedriftd")

    monkeypatch.setattr(agency_wrapper, "write_control_state", fail_if_called, raising=False)
    receipt = record_agency_pi_fallback_receipt(
        tmp_path,
        task_id="task",
        selected_model="anthropic/claude-sonnet-4-5",
        fallback_model="anthropic/claude-haiku-4-5",
        reason="agency unavailable",
        timestamp="2026-07-18T20:00:00+00:00",
    )

    assert receipt["control_before"] == {"mode": "observe", "lease_active": False}
