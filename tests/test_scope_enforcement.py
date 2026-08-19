# ABOUTME: Tests for file scope enforcement module
# ABOUTME: Verifies that agent changes stay within declared scope

from driftdriver.plan_preflight import path_matches
from driftdriver.scope_enforcement import (
    check_file_scope,
    _matches_any_pattern,
    extract_scope_from_contract,
    format_scope_report,
    ScopeResult,
    ScopeViolation,
)


def test_check_scope_all_in_scope():
    changes = [("modified", "driftdriver/cli.py"), ("added", "driftdriver/new.py")]
    result = check_file_scope(changes, ["driftdriver/*.py"])
    assert result.in_scope is True
    assert len(result.violations) == 0


def test_check_scope_violation():
    changes = [("modified", "driftdriver/cli.py"), ("modified", "README.md")]
    result = check_file_scope(changes, ["driftdriver/*.py"])
    assert result.in_scope is False
    assert len(result.violations) == 1
    assert result.violations[0].file_path == "README.md"


def test_check_scope_empty_patterns():
    changes = [("modified", "anything.py")]
    result = check_file_scope(changes, [])
    assert result.in_scope is True  # no constraints


def test_matches_pattern_glob():
    assert _matches_any_pattern("driftdriver/cli.py", ["driftdriver/*.py"]) is True


def test_matches_pattern_directory():
    assert _matches_any_pattern("tests/test_foo.py", ["tests/*"]) is True


def test_matches_pattern_exact():
    assert _matches_any_pattern("setup.py", ["setup.py"]) is True


def test_no_match():
    assert _matches_any_pattern("other/file.py", ["driftdriver/*"]) is False


def test_extract_scope_from_contract():
    contract = {"allowed_paths": ["driftdriver/*"], "scope": ["tests/*"]}
    patterns = extract_scope_from_contract(contract)
    assert "driftdriver/*" in patterns
    assert "tests/*" in patterns


def test_format_scope_report_pass():
    result = ScopeResult(in_scope=True, checked_files=3)
    report = format_scope_report(result)
    assert "PASSED" in report


def test_format_scope_report_fail():
    result = ScopeResult(
        in_scope=False,
        violations=[ScopeViolation(file_path="bad.py", change_type="modified")],
        allowed_patterns=["driftdriver/*"],
    )
    report = format_scope_report(result)
    assert "FAILED" in report
    assert "bad.py" in report


def test_scope_double_star_glob():
    """src/**/*.py should match deeply nested paths like src/foo/bar.py."""
    assert _matches_any_pattern("src/foo/bar.py", ["src/**/*.py"]) is True
    assert _matches_any_pattern("src/a/b/c.py", ["src/**/*.py"]) is True


def test_check_scope_touch_src_double_star_covers_src():
    """A touch=["src/**"] scope covers src/evaluator.py."""
    result = check_file_scope([("modified", "src/evaluator.py")], ["src/**"])
    assert result.in_scope is True
    assert result.violations == []


def test_check_scope_touch_src_double_star_excludes_tests():
    """A touch=["src/**"] scope does not cover tests/evaluator_test.py."""
    result = check_file_scope([("modified", "tests/evaluator_test.py")], ["src/**"])
    assert result.in_scope is False
    assert [v.file_path for v in result.violations] == ["tests/evaluator_test.py"]


def test_scope_matching_agrees_with_preflight_path_matcher():
    """Post-completion scope checks must use the preflight path semantics.

    Preflight and post-completion scope checks must agree on glob coverage,
    so every case below must hold for both matchers. The semantics are
    fnmatch's: ``*`` and ``**`` cross separators, a bare directory pattern
    is not subtree coverage, and paths never match on basename alone.
    """
    cases = [
        ("src/evaluator.py", "src/**", True),
        ("src/deep/nested/mod.py", "src/**", True),
        ("tests/evaluator_test.py", "src/**", False),
        ("docs/a/b.md", "docs/*", True),
        ("src/evaluator.py", "src", False),
        ("src/evaluator.py", "src/", False),
        ("nested/evaluator.py", "evaluator.py", False),
    ]
    for file_path, pattern, expected in cases:
        assert _matches_any_pattern(file_path, [pattern]) is expected, (
            file_path,
            pattern,
            "scope matcher disagrees",
        )
        assert path_matches(file_path, pattern) is expected, (
            file_path,
            pattern,
            "preflight matcher disagrees",
        )
