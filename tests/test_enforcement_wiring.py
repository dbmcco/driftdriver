# ABOUTME: Tests for enforcement quality gates wired into the check pipeline.
# ABOUTME: Validates finding collection, severity evaluation, and exit code enforcement.

from __future__ import annotations

from pathlib import Path
from typing import Any

from driftdriver.policy import DriftPolicy, _default_enforcement_cfg
from driftdriver.policy_enforcement import (
    collect_enforcement_findings,
    evaluate_enforcement,
    SEVERITY_RANK,
)


def _make_policy(enforcement_overrides: dict[str, Any] | None = None) -> DriftPolicy:
    """Build a real DriftPolicy with enforcement config overrides."""
    cfg = _default_enforcement_cfg()
    if enforcement_overrides:
        cfg.update(enforcement_overrides)
    return DriftPolicy(
        schema=1,
        mode="redirect",
        order=["coredrift"],
        cooldown_seconds=1800,
        max_auto_actions_per_hour=2,
        require_new_evidence=True,
        max_auto_depth=2,
        contracts_auto_ensure=True,
        updates_enabled=False,
        updates_check_interval_seconds=21600,
        updates_create_followup=False,
        loop_max_redrift_depth=2,
        loop_max_ready_drift_followups=20,
        loop_block_followup_creation=True,
        reporting_central_repo="",
        reporting_auto_report=False,
        reporting_include_knowledge=False,
        factory={"enabled": False},
        model={"planner_profile": "default"},
        sourcedrift={"enabled": False},
        syncdrift={"enabled": False},
        stalledrift={"enabled": False},
        servicedrift={"enabled": False},
        federatedrift={"enabled": False},
        secdrift={"enabled": False},
        qadrift={"enabled": False},
        sessiondriver={"enabled": False},
        speedriftd={"enabled": False},
        plandrift={"enabled": False},
        northstardrift={"enabled": False},
        evolverdrift={"enabled": False},
        bridge={"enabled": False},
        enforcement=cfg,
        autonomy_default={"level": "observe"},
        autonomy_repos=[],
    )


# --- collect_enforcement_findings tests ---


def test_collect_finds_severity_from_internal_lanes() -> None:
    """Internal lanes produce findings with severity field; collector extracts them."""
    plugins_json: dict[str, Any] = {
        "secdrift": {
            "ran": True,
            "exit_code": 3,
            "report": {
                "lane": "secdrift",
                "findings": [
                    {"message": "hardcoded secret", "severity": "critical"},
                    {"message": "weak hash", "severity": "warning"},
                ],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    assert len(findings) == 2
    assert findings[0]["severity"] == "critical"
    assert findings[1]["severity"] == "warning"


def test_collect_skips_plugins_that_didnt_run() -> None:
    """Plugins that didn't run are excluded from findings collection."""
    plugins_json: dict[str, Any] = {
        "specdrift": {
            "ran": False,
            "exit_code": 0,
            "report": {
                "findings": [{"message": "stale", "severity": "error"}],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    assert len(findings) == 0


def test_collect_handles_missing_report() -> None:
    """Plugins with report=None produce no findings."""
    plugins_json: dict[str, Any] = {
        "uxdrift": {
            "ran": True,
            "exit_code": 0,
            "report": None,
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    assert len(findings) == 0


def test_collect_defaults_missing_severity_to_info() -> None:
    """Findings without a severity field default to 'info'."""
    plugins_json: dict[str, Any] = {
        "coredrift": {
            "ran": True,
            "exit_code": 3,
            "report": {
                "findings": [{"message": "scope creep", "kind": "scope_creep"}],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    assert len(findings) == 1
    assert findings[0]["severity"] == "info"


def test_collect_multiple_plugins() -> None:
    """Findings aggregated across multiple plugins."""
    plugins_json: dict[str, Any] = {
        "secdrift": {
            "ran": True,
            "exit_code": 3,
            "report": {
                "findings": [
                    {"message": "cred leak", "severity": "critical"},
                ],
            },
        },
        "qadrift": {
            "ran": True,
            "exit_code": 3,
            "report": {
                "findings": [
                    {"message": "test gap", "severity": "warning"},
                    {"message": "flaky test", "severity": "error"},
                ],
            },
        },
        "coredrift": {
            "ran": True,
            "exit_code": 0,
            "report": {
                "findings": [],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    assert len(findings) == 3
    severities = [f["severity"] for f in findings]
    assert "critical" in severities
    assert "warning" in severities
    assert "error" in severities


def test_collect_handles_non_list_findings() -> None:
    """Report with findings that isn't a list is safely ignored."""
    plugins_json: dict[str, Any] = {
        "coredrift": {
            "ran": True,
            "exit_code": 0,
            "report": {
                "findings": "not a list",
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    assert len(findings) == 0


# --- evaluate_enforcement with real policy tests ---


def test_enforcement_disabled_by_default() -> None:
    """Default enforcement config has enabled=False; everything passes."""
    policy = _make_policy()
    findings = [{"severity": "critical"}, {"severity": "error"}]
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is False
    assert result["exit_code"] == 0
    assert result["warnings"] == []
    assert result["counts"] == {}


def test_enforcement_enabled_no_findings() -> None:
    """Enabled enforcement with empty findings returns clean."""
    policy = _make_policy({"enabled": True})
    result = evaluate_enforcement(policy, [])
    assert result["blocked"] is False
    assert result["exit_code"] == 0
    assert result["counts"]["critical"] == 0
    assert result["counts"]["error"] == 0


def test_enforcement_critical_blocks() -> None:
    """A critical finding triggers block when block_on_critical=True."""
    policy = _make_policy({"enabled": True, "block_on_critical": True})
    findings = [{"severity": "critical"}]
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is True
    assert result["exit_code"] == 2
    assert result["counts"]["critical"] == 1


def test_enforcement_critical_no_block_when_disabled() -> None:
    """Critical findings don't block when block_on_critical=False."""
    policy = _make_policy({
        "enabled": True,
        "block_on_critical": False,
        "warn_on_error": False,
        "max_unresolved_warnings": 100,
    })
    findings = [{"severity": "critical"}]
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is False
    # critical still counts as actionable, but threshold is 100
    assert result["exit_code"] == 0


def test_enforcement_error_warns() -> None:
    """Error findings trigger warning when warn_on_error=True."""
    policy = _make_policy({"enabled": True, "warn_on_error": True})
    findings = [{"severity": "error"}, {"severity": "error"}]
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is False
    assert result["exit_code"] == 1
    assert any("2 error" in w for w in result["warnings"])


def test_enforcement_threshold_exceeded() -> None:
    """Exceeding max_unresolved_warnings triggers a threshold warning."""
    policy = _make_policy({
        "enabled": True,
        "block_on_critical": False,
        "warn_on_error": False,
        "max_unresolved_warnings": 3,
    })
    findings = [{"severity": "warning"}] * 4
    result = evaluate_enforcement(policy, findings)
    assert result["exit_code"] == 1
    assert any("threshold" in w.lower() for w in result["warnings"])


def test_enforcement_info_only_is_clean() -> None:
    """Info-only findings don't trigger any warnings or blocks."""
    policy = _make_policy({"enabled": True})
    findings = [{"severity": "info"}] * 5
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is False
    assert result["exit_code"] == 0
    assert result["warnings"] == []


def test_enforcement_unknown_severity_treated_as_info() -> None:
    """Unknown severity values are mapped to info."""
    policy = _make_policy({"enabled": True})
    findings = [{"severity": "catastrophic"}, {"severity": ""}]
    result = evaluate_enforcement(policy, findings)
    assert result["counts"]["info"] == 2
    assert result["exit_code"] == 0


def test_enforcement_mixed_severities() -> None:
    """Mixed severities: critical blocks, errors warn, all counted."""
    policy = _make_policy({
        "enabled": True,
        "block_on_critical": True,
        "warn_on_error": True,
        "max_unresolved_warnings": 100,
    })
    findings = [
        {"severity": "info"},
        {"severity": "warning"},
        {"severity": "error"},
        {"severity": "critical"},
    ]
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is True
    assert result["exit_code"] == 2
    assert result["counts"] == {"info": 1, "warning": 1, "error": 1, "critical": 1}


def test_severity_rank_completeness() -> None:
    """All four severity levels are ranked."""
    assert set(SEVERITY_RANK.keys()) == {"info", "warning", "error", "critical"}
    # Strictly ascending
    ranks = [SEVERITY_RANK[s] for s in ["info", "warning", "error", "critical"]]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == 4  # all distinct


# --- End-to-end: collect + evaluate pipeline ---


def test_pipeline_collect_then_evaluate_clean() -> None:
    """Full pipeline: plugins with info-only findings pass cleanly."""
    policy = _make_policy({"enabled": True})
    plugins_json: dict[str, Any] = {
        "coredrift": {
            "ran": True,
            "exit_code": 0,
            "report": {
                "findings": [{"message": "note", "severity": "info"}],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    result = evaluate_enforcement(policy, findings)
    assert result["exit_code"] == 0
    assert result["blocked"] is False


def test_pipeline_collect_then_evaluate_blocked() -> None:
    """Full pipeline: critical from any lane triggers block."""
    policy = _make_policy({"enabled": True, "block_on_critical": True})
    plugins_json: dict[str, Any] = {
        "coredrift": {
            "ran": True,
            "exit_code": 0,
            "report": {"findings": []},
        },
        "secdrift": {
            "ran": True,
            "exit_code": 3,
            "report": {
                "findings": [
                    {"message": "exposed key", "severity": "critical"},
                ],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is True
    assert result["exit_code"] == 2


def test_pipeline_collect_then_evaluate_warn() -> None:
    """Full pipeline: errors trigger warning exit code 1."""
    policy = _make_policy({
        "enabled": True,
        "block_on_critical": True,
        "warn_on_error": True,
    })
    plugins_json: dict[str, Any] = {
        "qadrift": {
            "ran": True,
            "exit_code": 3,
            "report": {
                "findings": [
                    {"message": "coverage gap", "severity": "error"},
                ],
            },
        },
    }
    findings = collect_enforcement_findings(plugins_json)
    result = evaluate_enforcement(policy, findings)
    assert result["blocked"] is False
    assert result["exit_code"] == 1
    assert result["counts"]["error"] == 1
