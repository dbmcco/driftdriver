# ABOUTME: Tests for smart lane routing evidence gathering
# ABOUTME: Verifies evidence package assembly from git diff, task contracts, and project context

import pytest
from pathlib import Path
from driftdriver.smart_routing import EvidencePackage, gather_evidence


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
