# ABOUTME: Tests for smart lane routing evidence gathering
# ABOUTME: Verifies evidence package assembly from git diff, task contracts, and project context

import pytest
from pathlib import Path
from driftdriver.smart_routing import EvidencePackage, compute_lane_weights, gather_evidence, parse_git_diff_stat, load_pattern_hints
from driftdriver.outcome import DriftOutcome, write_outcome


class TestEvidencePackage:
    """Tests for the EvidencePackage dataclass."""

    def test_evidence_package_creation(self):
        """Basic EvidencePackage should hold all fields."""
        pkg = EvidencePackage(
            changed_files={"src/auth.py": "modified", "tests/test_auth.py": "added"},
            file_classifications={"src/auth.py": ["coredrift"], "tests/test_auth.py": ["coredrift"]},
            task_description="Implement JWT authentication",
            task_contract={"lanes": [], "verify": "tests pass"},
            project_context=[],
            prior_drift_findings=[],
            installed_lanes=["coredrift", "uxdrift", "datadrift", "depsdrift"],
            pattern_hints={"coredrift": ["*.py"], "uxdrift": ["*.tsx", "*.css"]},
        )
        assert len(pkg.changed_files) == 2
        assert "coredrift" in pkg.installed_lanes

    def test_file_classification_from_patterns(self):
        """Files should be classified into lanes based on extension patterns."""
        pkg = EvidencePackage(
            changed_files={
                "src/App.tsx": "modified",
                "migrations/001.sql": "added",
                "package.json": "modified",
                "src/api/auth.py": "modified",
            },
            file_classifications={},
            task_description="",
            task_contract={},
            project_context=[],
            prior_drift_findings=[],
            installed_lanes=["coredrift", "uxdrift", "datadrift", "depsdrift"],
            pattern_hints={
                "uxdrift": ["*.tsx", "*.jsx", "*.css"],
                "datadrift": ["**/migrations/**", "*.sql"],
                "depsdrift": ["package.json", "*.lock"],
            },
        )
        classifications = pkg.classify_files()
        assert "uxdrift" in classifications.get("src/App.tsx", [])
        assert "datadrift" in classifications.get("migrations/001.sql", [])
        assert "depsdrift" in classifications.get("package.json", [])

    def test_suggested_lanes_from_classification(self):
        """Should return unique set of lanes suggested by file patterns."""
        pkg = EvidencePackage(
            changed_files={"src/App.tsx": "modified", "package.json": "modified"},
            file_classifications={},
            task_description="",
            task_contract={},
            project_context=[],
            prior_drift_findings=[],
            installed_lanes=["coredrift", "uxdrift", "depsdrift"],
            pattern_hints={
                "uxdrift": ["*.tsx"],
                "depsdrift": ["package.json"],
            },
        )
        suggested = pkg.suggest_lanes()
        assert "uxdrift" in suggested
        assert "depsdrift" in suggested


class TestGatherEvidence:
    """Tests for gather_evidence lane detection."""

    def test_gather_evidence_finds_installed_lanes(self, tmp_path):
        """Executable lane scripts in .workgraph/ root should be detected."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        # Executable coredrift wrapper — should be detected
        coredrift = wg_dir / "coredrift"
        coredrift.write_text("#!/bin/bash\necho coredrift")
        coredrift.chmod(0o755)
        # Non-executable specdrift — should NOT be detected
        specdrift = wg_dir / "specdrift"
        specdrift.write_text("#!/bin/bash\necho specdrift")
        # specdrift left as non-executable (default mode)

        evidence = gather_evidence(wg_dir)
        assert "coredrift" in evidence.installed_lanes
        assert "specdrift" not in evidence.installed_lanes

    def test_gather_evidence_ignores_non_lane_files(self, tmp_path):
        """Files not in KNOWN_LANES should be excluded even if executable."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        # Known lane — should be detected
        coredrift = wg_dir / "coredrift"
        coredrift.write_text("#!/bin/bash")
        coredrift.chmod(0o755)
        # Unknown name — should be excluded even though executable
        unknown = wg_dir / "some_random_script"
        unknown.write_text("#!/bin/bash")
        unknown.chmod(0o755)

        evidence = gather_evidence(wg_dir)
        assert "coredrift" in evidence.installed_lanes
        assert "some_random_script" not in evidence.installed_lanes


class TestParseGitDiffStat:
    """Tests for parse_git_diff_stat."""

    def test_parse_git_diff_stat_basic(self):
        raw = "M\tsrc/foo.py\nA\tsrc/bar.py\nD\told.py\n"
        result = parse_git_diff_stat(raw)
        assert isinstance(result, dict)
        assert len(result) >= 3
        assert "src/foo.py" in result
        assert "src/bar.py" in result

    def test_parse_git_diff_stat_rename(self):
        raw = "R100\told.py\tnew.py\n"
        result = parse_git_diff_stat(raw)
        assert len(result) >= 1

    def test_parse_git_diff_stat_empty(self):
        result = parse_git_diff_stat("")
        assert result == {}


class TestLoadPatternHints:
    """Tests for load_pattern_hints."""

    def test_load_pattern_hints_missing_file(self, tmp_path):
        result = load_pattern_hints(tmp_path / "nonexistent.toml")
        assert result == {}

    def test_load_pattern_hints_valid(self, tmp_path):
        toml_file = tmp_path / "lane-routing.toml"
        toml_file.write_text('[lane-routing.patterns]\ncoredrift = ["*.py"]\n')
        result = load_pattern_hints(toml_file)
        assert isinstance(result, dict)
        assert "coredrift" in result


class TestComputeLaneWeights:
    """Tests for outcome-based lane weight computation."""

    def _write_outcomes(self, path, outcomes):
        for o in outcomes:
            write_outcome(path, DriftOutcome(
                task_id=o.get("task_id", "t1"),
                lane=o["lane"],
                finding_key="f1",
                recommendation="fix it",
                action_taken="did something",
                outcome=o["outcome"],
            ))

    def test_no_history_returns_neutral(self, tmp_path):
        ledger = tmp_path / "drift-outcomes.jsonl"
        weights = compute_lane_weights(ledger, ["coredrift", "specdrift"])
        assert weights == {"coredrift": 1.0, "specdrift": 1.0}

    def test_high_ignored_rate_escalates(self, tmp_path):
        ledger = tmp_path / "drift-outcomes.jsonl"
        self._write_outcomes(ledger, [
            {"lane": "secdrift", "outcome": "ignored"},
            {"lane": "secdrift", "outcome": "ignored"},
            {"lane": "secdrift", "outcome": "ignored"},
            {"lane": "secdrift", "outcome": "resolved"},
        ])
        weights = compute_lane_weights(ledger, ["secdrift"])
        # 75% ignored, 25% resolved → 1.0 + 0.75 - 0.125 = 1.625
        assert weights["secdrift"] > 1.5

    def test_high_resolved_rate_demotes(self, tmp_path):
        ledger = tmp_path / "drift-outcomes.jsonl"
        self._write_outcomes(ledger, [
            {"lane": "coredrift", "outcome": "resolved"},
            {"lane": "coredrift", "outcome": "resolved"},
            {"lane": "coredrift", "outcome": "resolved"},
            {"lane": "coredrift", "outcome": "resolved"},
        ])
        weights = compute_lane_weights(ledger, ["coredrift"])
        # 100% resolved → 1.0 + 0 - 0.5 = 0.5
        assert weights["coredrift"] < 0.6

    def test_worsened_rate_strongly_escalates(self, tmp_path):
        ledger = tmp_path / "drift-outcomes.jsonl"
        self._write_outcomes(ledger, [
            {"lane": "qadrift", "outcome": "worsened"},
            {"lane": "qadrift", "outcome": "worsened"},
        ])
        weights = compute_lane_weights(ledger, ["qadrift"])
        # 100% worsened → 1.0 + 1.0 = 2.0
        assert weights["qadrift"] >= 2.0

    def test_weights_clamped_to_bounds(self, tmp_path):
        ledger = tmp_path / "drift-outcomes.jsonl"
        # All resolved → should clamp at 0.2 minimum
        self._write_outcomes(ledger, [
            {"lane": "x", "outcome": "resolved"},
        ] * 10)
        weights = compute_lane_weights(ledger, ["x"])
        assert weights["x"] >= 0.2

    def test_mixed_lanes_weighted_independently(self, tmp_path):
        ledger = tmp_path / "drift-outcomes.jsonl"
        self._write_outcomes(ledger, [
            {"lane": "coredrift", "outcome": "resolved"},
            {"lane": "coredrift", "outcome": "resolved"},
            {"lane": "secdrift", "outcome": "ignored"},
            {"lane": "secdrift", "outcome": "ignored"},
        ])
        weights = compute_lane_weights(ledger, ["coredrift", "secdrift"])
        assert weights["coredrift"] < weights["secdrift"]

    def test_evidence_package_includes_lane_weights(self, tmp_path):
        """EvidencePackage should accept and render lane weights."""
        pkg = EvidencePackage(
            changed_files={"src/app.py": "modified"},
            file_classifications={},
            task_description="test",
            task_contract={},
            project_context=[],
            prior_drift_findings=[],
            installed_lanes=["coredrift", "secdrift"],
            lane_weights={"coredrift": 0.5, "secdrift": 1.8},
        )
        prompt = pkg.to_prompt_context()
        assert "Escalated Lanes" in prompt
        assert "secdrift" in prompt
