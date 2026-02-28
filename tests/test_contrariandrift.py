# ABOUTME: Tests for contrariandrift — contrarian code review speedrift lane.
# ABOUTME: Covers dead imports, JSON safety, error swallowing, scoring, and report formatting.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driftdriver.contrariandrift import (
    ContrarianFinding,
    ContrarianReport,
    check_dead_imports,
    check_error_swallowing,
    check_json_safety,
    format_report,
    run_contrarian_check,
)


class TestCheckDeadImports(unittest.TestCase):
    def test_check_dead_imports_finds_unused_function(self) -> None:
        """Finds a function defined in a module but never imported by any other module."""
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            pkg = project_dir / "mypkg"
            pkg.mkdir()

            # Define a function in one module
            (pkg / "utils.py").write_text(
                "def unused_helper():\n    pass\n\ndef used_helper():\n    pass\n",
                encoding="utf-8",
            )
            # Only import used_helper elsewhere
            (pkg / "main.py").write_text(
                "from mypkg.utils import used_helper\n",
                encoding="utf-8",
            )

            findings = check_dead_imports(project_dir)

            fn_names = [f.description for f in findings]
            # unused_helper should appear, used_helper should not
            # Use quoted names to avoid substring false-positives ('used_helper' ⊂ 'unused_helper')
            self.assertTrue(
                any("'unused_helper'" in d for d in fn_names),
                f"Expected unused_helper in findings: {fn_names}",
            )
            self.assertFalse(
                any("'used_helper'" in d for d in fn_names),
                f"used_helper should not appear in findings: {fn_names}",
            )
            for f in findings:
                self.assertEqual(f.category, "dead-code")


class TestCheckJsonSafety(unittest.TestCase):
    def test_check_json_safety_flags_interpolation(self) -> None:
        """Flags shell scripts that build JSON via string interpolation with $ variables."""
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            scripts = project_dir / "scripts"
            scripts.mkdir()

            # Unsafe: inline JSON with variable expansion
            (scripts / "bad.sh").write_text(
                '#!/usr/bin/env bash\n'
                'echo "{\\"key\\": \\"$VALUE\\"}" | curl -d @- http://example.com\n',
                encoding="utf-8",
            )
            # Safe: uses jq
            (scripts / "good.sh").write_text(
                '#!/usr/bin/env bash\n'
                'jq -n --arg key "$VALUE" \'{"key": $key}\' | curl -d @- http://example.com\n',
                encoding="utf-8",
            )

            findings = check_json_safety(project_dir)

            self.assertTrue(len(findings) >= 1, "Expected at least one finding")
            sources = [f.file for f in findings]
            self.assertTrue(
                any("bad.sh" in s for s in sources),
                f"Expected bad.sh in sources: {sources}",
            )
            self.assertFalse(
                any("good.sh" in s for s in sources),
                f"good.sh should not appear: {sources}",
            )
            for f in findings:
                self.assertEqual(f.category, "security")


class TestCheckErrorSwallowing(unittest.TestCase):
    def test_check_error_swallowing_detects_pass(self) -> None:
        """Detects bare except-pass patterns and shell || true silencing."""
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)

            # Python: bare except pass
            (project_dir / "risky.py").write_text(
                "try:\n    do_thing()\nexcept Exception:\n    pass\n",
                encoding="utf-8",
            )
            # Shell: || true silencing
            (project_dir / "risky.sh").write_text(
                "#!/usr/bin/env bash\nrm -rf /tmp/stuff 2>/dev/null || true\n",
                encoding="utf-8",
            )
            # Clean file
            (project_dir / "clean.py").write_text(
                "try:\n    do_thing()\nexcept ValueError as e:\n    log(e)\n",
                encoding="utf-8",
            )

            findings = check_error_swallowing(project_dir)

            self.assertTrue(len(findings) >= 2, f"Expected at least 2 findings, got {findings}")
            sources = [f.file for f in findings]
            self.assertTrue(any("risky.py" in s for s in sources))
            self.assertTrue(any("risky.sh" in s for s in sources))
            self.assertFalse(any("clean.py" in s for s in sources))
            for f in findings:
                self.assertEqual(f.category, "bug")


class TestRunContrarianCheck(unittest.TestCase):
    def test_run_contrarian_check_calculates_score(self) -> None:
        """run_contrarian_check returns a report with a non-zero score when issues exist."""
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            # Plant a swallowed error to guarantee at least one finding
            (project_dir / "bad.py").write_text(
                "try:\n    x()\nexcept Exception:\n    pass\n",
                encoding="utf-8",
            )

            report = run_contrarian_check(project_dir)

            self.assertIsInstance(report, ContrarianReport)
            self.assertGreater(report.drift_score, 0.0)
            self.assertGreaterEqual(len(report.findings), 1)

    def test_drift_score_capped_at_one(self) -> None:
        """drift_score never exceeds 1.0 regardless of finding count."""
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            # Plant many CRITICAL-level findings (JSON safety in shell scripts)
            scripts = project_dir / "scripts"
            scripts.mkdir()
            for i in range(20):
                (scripts / f"bad{i}.sh").write_text(
                    f'echo "{{\\\"k\\\": \\\"${i}_VAR\\\"}}" | curl http://x\n',
                    encoding="utf-8",
                )

            report = run_contrarian_check(project_dir)

            self.assertLessEqual(report.drift_score, 1.0)


class TestFormatReport(unittest.TestCase):
    def test_format_report_groups_by_severity(self) -> None:
        """format_report includes severity headers and lists findings grouped by severity."""
        report = ContrarianReport(
            findings=[
                ContrarianFinding(
                    file="a.py",
                    line=10,
                    severity="CRITICAL",
                    category="security",
                    description="SQL injection risk",
                ),
                ContrarianFinding(
                    file="b.py",
                    line=5,
                    severity="HIGH",
                    category="bug",
                    description="Null dereference",
                ),
                ContrarianFinding(
                    file="c.py",
                    line=None,
                    severity="CRITICAL",
                    category="dead-code",
                    description="Unused export",
                ),
            ],
            drift_score=0.7,
            summary="3 issues found",
        )

        output = format_report(report)

        self.assertIn("CRITICAL", output)
        self.assertIn("HIGH", output)
        self.assertIn("SQL injection risk", output)
        self.assertIn("Null dereference", output)
        self.assertIn("Unused export", output)
        # CRITICAL should appear before HIGH in the output
        self.assertLess(output.index("CRITICAL"), output.index("HIGH"))
