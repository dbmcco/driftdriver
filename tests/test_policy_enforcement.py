# ABOUTME: Tests for the enforcement evaluation module
# ABOUTME: Verifies block/warn/pass verdicts based on finding severity

from unittest.mock import MagicMock

from driftdriver.policy_enforcement import evaluate_enforcement, SEVERITY_RANK


def test_severity_rank_ordering():
    assert SEVERITY_RANK["info"] < SEVERITY_RANK["warning"]
    assert SEVERITY_RANK["warning"] < SEVERITY_RANK["error"]
    assert SEVERITY_RANK["error"] < SEVERITY_RANK["critical"]


def test_enforcement_disabled_returns_clean():
    policy = MagicMock()
    policy.enforcement = {"enabled": False}
    result = evaluate_enforcement(policy, [{"severity": "critical"}])
    assert result["blocked"] is False
    assert result["exit_code"] == 0
    assert result["warnings"] == []


def test_critical_finding_blocks():
    policy = MagicMock()
    policy.enforcement = {
        "enabled": True,
        "block_on_critical": True,
        "warn_on_error": True,
        "max_unresolved_warnings": 10,
    }
    result = evaluate_enforcement(policy, [{"severity": "critical"}])
    assert result["blocked"] is True
    assert result["exit_code"] == 2
    assert any("BLOCKED" in w for w in result["warnings"])


def test_error_finding_warns():
    policy = MagicMock()
    policy.enforcement = {
        "enabled": True,
        "block_on_critical": True,
        "warn_on_error": True,
        "max_unresolved_warnings": 10,
    }
    result = evaluate_enforcement(policy, [{"severity": "error"}])
    assert result["blocked"] is False
    assert result["exit_code"] == 1
    assert any("WARNING" in w for w in result["warnings"])


def test_info_finding_clean():
    policy = MagicMock()
    policy.enforcement = {
        "enabled": True,
        "block_on_critical": True,
        "warn_on_error": True,
        "max_unresolved_warnings": 10,
    }
    result = evaluate_enforcement(policy, [{"severity": "info"}])
    assert result["blocked"] is False
    assert result["exit_code"] == 0


def test_threshold_exceeded_warns():
    policy = MagicMock()
    policy.enforcement = {
        "enabled": True,
        "block_on_critical": False,
        "warn_on_error": False,
        "max_unresolved_warnings": 2,
    }
    findings = [{"severity": "warning"}] * 3
    result = evaluate_enforcement(policy, findings)
    assert any("threshold" in w.lower() for w in result["warnings"])
