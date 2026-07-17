"""Tests for `driftdriver check --gate` severity threshold.

Gate mode inverts the normal check posture so a graph gate node's ``--verify``
passes on advisory info-level findings and only fails (non-zero) when a finding
is at/above warning severity. Lanes emit ``warn`` for real drift, so ``warn``
must be treated as blocking.
"""

from __future__ import annotations

from driftdriver.cli.check import _gate_blocks


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
