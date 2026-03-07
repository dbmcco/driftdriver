# ABOUTME: Tests for lane plugin contract enforcement wired into check.py.
# ABOUTME: Validates _count_contract_compliance and _run_optional_plugin_json contract annotations.

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

from driftdriver.cli.check import _count_contract_compliance, _run_optional_plugin_json


def test_count_compliance_all_valid() -> None:
    """All plugins with _contract_valid=True are counted as valid."""
    plugins_json: dict[str, Any] = {
        "specdrift": {
            "ran": True,
            "exit_code": 0,
            "report": {"_contract_valid": True, "lane": "specdrift"},
        },
        "datadrift": {
            "ran": True,
            "exit_code": 0,
            "report": {"_contract_valid": True, "lane": "datadrift"},
        },
    }
    result = _count_contract_compliance(plugins_json)
    assert result["total_checked"] == 2
    assert result["contract_valid"] == 2
    assert result["contract_invalid"] == 0
    assert result["invalid_lanes"] == []


def test_count_compliance_mixed_valid_invalid() -> None:
    """Mix of valid and invalid plugins counted correctly."""
    plugins_json: dict[str, Any] = {
        "specdrift": {
            "ran": True,
            "exit_code": 0,
            "report": {"_contract_valid": True},
        },
        "datadrift": {
            "ran": True,
            "exit_code": 0,
            "report": {"_contract_valid": False},
        },
        "archdrift": {
            "ran": True,
            "exit_code": 0,
            "report": {"_contract_valid": True},
        },
    }
    result = _count_contract_compliance(plugins_json)
    assert result["total_checked"] == 3
    assert result["contract_valid"] == 2
    assert result["contract_invalid"] == 1
    assert result["invalid_lanes"] == ["datadrift"]


def test_count_compliance_no_plugins_ran() -> None:
    """Plugins that did not run are excluded from compliance count."""
    plugins_json: dict[str, Any] = {
        "specdrift": {
            "ran": False,
            "exit_code": 0,
            "report": {"_contract_valid": True},
        },
        "datadrift": {
            "ran": False,
            "exit_code": 0,
            "report": None,
        },
    }
    result = _count_contract_compliance(plugins_json)
    assert result["total_checked"] == 0
    assert result["contract_valid"] == 0
    assert result["contract_invalid"] == 0
    assert result["invalid_lanes"] == []


def test_count_compliance_report_none_skipped() -> None:
    """Plugins with report=None are skipped (not a dict)."""
    plugins_json: dict[str, Any] = {
        "uxdrift": {
            "ran": True,
            "exit_code": 0,
            "report": None,
        },
    }
    result = _count_contract_compliance(plugins_json)
    assert result["total_checked"] == 0


def test_contract_valid_annotation(tmp_path: Path, monkeypatch: Any) -> None:
    """Plugin output matching lane contract gets _contract_valid=True annotation."""
    import subprocess

    wg_dir = tmp_path / ".workgraph"
    wg_dir.mkdir()
    plugin_bin = wg_dir / "specdrift"
    plugin_bin.write_text("#!/bin/sh\necho '{}'")
    plugin_bin.chmod(0o755)

    valid_output = json.dumps({
        "lane": "specdrift",
        "findings": [{"message": "spec mismatch", "severity": "warning"}],
        "exit_code": 0,
        "summary": "1 finding",
    })

    fake_result = types.SimpleNamespace(returncode=0, stdout=valid_output, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    result = _run_optional_plugin_json(
        plugin="specdrift",
        enabled=True,
        wg_dir=wg_dir,
        project_dir=tmp_path,
        task_id="t1",
        mode="redirect",
        force_write_log=False,
        force_create_followups=False,
    )
    assert result["ran"] is True
    assert result["report"]["_contract_valid"] is True
    assert result["report"]["_lane_result"]["lane"] == "specdrift"
    assert result["report"]["_lane_result"]["findings_count"] == 1
    assert result["report"]["_lane_result"]["summary"] == "1 finding"


def test_contract_invalid_annotation(tmp_path: Path, monkeypatch: Any) -> None:
    """Plugin output missing required 'lane' field gets _contract_valid=False."""
    import subprocess

    wg_dir = tmp_path / ".workgraph"
    wg_dir.mkdir()
    plugin_bin = wg_dir / "datadrift"
    plugin_bin.write_text("#!/bin/sh\necho '{}'")
    plugin_bin.chmod(0o755)

    invalid_output = json.dumps({
        "some_key": "some_value",
        "findings": [],
    })

    fake_result = types.SimpleNamespace(returncode=0, stdout=invalid_output, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    result = _run_optional_plugin_json(
        plugin="datadrift",
        enabled=True,
        wg_dir=wg_dir,
        project_dir=tmp_path,
        task_id="t2",
        mode="redirect",
        force_write_log=False,
        force_create_followups=False,
    )
    assert result["ran"] is True
    assert result["report"]["_contract_valid"] is False
    assert "_lane_result" not in result["report"]


def test_contract_validation_preserves_original_report(tmp_path: Path, monkeypatch: Any) -> None:
    """Contract validation adds metadata without removing any original report keys."""
    import subprocess

    wg_dir = tmp_path / ".workgraph"
    wg_dir.mkdir()
    plugin_bin = wg_dir / "archdrift"
    plugin_bin.write_text("#!/bin/sh\necho '{}'")
    plugin_bin.chmod(0o755)

    original_output = json.dumps({
        "lane": "archdrift",
        "findings": [{"message": "coupling too high", "severity": "error"}],
        "exit_code": 3,
        "summary": "architecture concern",
        "custom_field": "preserved",
        "metrics": {"complexity": 42},
    })

    fake_result = types.SimpleNamespace(returncode=3, stdout=original_output, stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

    result = _run_optional_plugin_json(
        plugin="archdrift",
        enabled=True,
        wg_dir=wg_dir,
        project_dir=tmp_path,
        task_id="t3",
        mode="redirect",
        force_write_log=False,
        force_create_followups=False,
    )
    report = result["report"]
    # Original keys preserved
    assert report["lane"] == "archdrift"
    assert report["custom_field"] == "preserved"
    assert report["metrics"] == {"complexity": 42}
    assert len(report["findings"]) == 1
    # Contract metadata added
    assert report["_contract_valid"] is True
    assert report["_lane_result"]["exit_code"] == 3
