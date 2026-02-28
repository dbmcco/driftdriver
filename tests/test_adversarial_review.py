# ABOUTME: Tests for adversarial_review — binary PASS/FAIL spec compliance gate.
# ABOUTME: Covers finding parsing, verdict evaluation, prompt formatting, and KNOWN_LANES registration.
from __future__ import annotations

import unittest

from driftdriver.adversarial_review import (
    AdversarialResult,
    Finding,
    evaluate_review,
    format_result_report,
    format_review_prompt,
    parse_review_findings,
)


class TestParseReviewFindings(unittest.TestCase):
    def test_parse_review_findings_blocking(self) -> None:
        text = "BLOCKING: Missing error handling\nFile: src/foo.py:42\nEvidence: no try/except"
        findings = parse_review_findings(text)
        assert len(findings) == 1
        assert findings[0].severity == "BLOCKING"
        assert findings[0].file_path == "src/foo.py"
        assert findings[0].line == 42

    def test_parse_review_findings_mixed(self) -> None:
        text = "BLOCKING: Missing test\nFile: tests/test.py\nWARNING: Could use better name\nFile: src/foo.py:10"
        findings = parse_review_findings(text)
        assert len(findings) == 2


class TestEvaluateReview(unittest.TestCase):
    def test_evaluate_review_pass(self) -> None:
        findings = [Finding(severity="WARNING", description="minor", file_path="f.py")]
        result = evaluate_review(findings)
        assert result.verdict == "PASS"

    def test_evaluate_review_fail(self) -> None:
        findings = [Finding(severity="BLOCKING", description="critical", file_path="f.py")]
        result = evaluate_review(findings)
        assert result.verdict == "FAIL"
        assert result.blocking_count == 1


class TestFormatReviewPrompt(unittest.TestCase):
    def test_format_review_prompt_includes_spec_and_diff(self) -> None:
        prompt = format_review_prompt("+ new code", "must have tests")
        assert "must have tests" in prompt
        assert "+ new code" in prompt


class TestFormatResultReport(unittest.TestCase):
    def test_format_result_report(self) -> None:
        result = AdversarialResult(verdict="FAIL", findings=[], blocking_count=1, warning_count=0)
        report = format_result_report(result)
        assert "FAIL" in report


class TestKnownLanes(unittest.TestCase):
    def test_reviewdrift_in_known_lanes(self) -> None:
        from driftdriver.routing_models import KNOWN_LANES
        assert "reviewdrift" in KNOWN_LANES
