# ABOUTME: Expanded test suite for secdrift.py covering internal helpers, all scan
# ABOUTME: functions, policy normalization, severity mapping, and run_as_lane edge cases.

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from driftdriver.secdrift import (
    _fingerprint,
    _is_text_candidate,
    _iter_repo_files,
    _map_severity,
    _normalize_policy,
    _placeholder_value,
    _read_text,
    _scan_dependency_posture,
    _scan_secret_patterns,
    _scan_sensitive_artifacts,
    _security_prompt,
    run_as_lane,
    run_secdrift_scan,
)


class TestIsTextCandidate(unittest.TestCase):
    """Tests for _is_text_candidate file filtering."""

    def test_python_suffix_accepted(self) -> None:
        self.assertTrue(_is_text_candidate(Path("app.py")))

    def test_typescript_suffix_accepted(self) -> None:
        self.assertTrue(_is_text_candidate(Path("index.ts")))

    def test_json_suffix_accepted(self) -> None:
        self.assertTrue(_is_text_candidate(Path("config.json")))

    def test_env_suffix_accepted(self) -> None:
        self.assertTrue(_is_text_candidate(Path(".env")))

    def test_binary_suffix_rejected(self) -> None:
        self.assertFalse(_is_text_candidate(Path("image.png")))

    def test_object_file_rejected(self) -> None:
        self.assertFalse(_is_text_candidate(Path("module.o")))

    def test_sensitive_filename_id_rsa(self) -> None:
        self.assertTrue(_is_text_candidate(Path("id_rsa")))

    def test_sensitive_filename_id_ed25519(self) -> None:
        self.assertTrue(_is_text_candidate(Path("id_ed25519")))

    def test_keyword_secret_in_name(self) -> None:
        self.assertTrue(_is_text_candidate(Path("my_secret_config")))

    def test_keyword_token_in_name(self) -> None:
        self.assertTrue(_is_text_candidate(Path("auth_token")))

    def test_keyword_credential_in_name(self) -> None:
        self.assertTrue(_is_text_candidate(Path("credential_store")))

    def test_keyword_password_in_name(self) -> None:
        self.assertTrue(_is_text_candidate(Path("password_file")))

    def test_unrelated_extensionless_file_rejected(self) -> None:
        self.assertFalse(_is_text_candidate(Path("Makefile")))


class TestIterRepoFiles(unittest.TestCase):
    """Tests for _iter_repo_files directory walking."""

    def test_collects_files_from_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "a.py").write_text("x", encoding="utf-8")
            (repo / "sub").mkdir()
            (repo / "sub" / "b.ts").write_text("y", encoding="utf-8")
            result = _iter_repo_files(repo, max_files=100)
            names = {p.name for p in result}
            self.assertIn("a.py", names)
            self.assertIn("b.ts", names)

    def test_ignores_git_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".git").mkdir()
            (repo / ".git" / "config").write_text("x", encoding="utf-8")
            (repo / "main.py").write_text("y", encoding="utf-8")
            result = _iter_repo_files(repo, max_files=100)
            names = {p.name for p in result}
            self.assertIn("main.py", names)
            self.assertNotIn("config", names)

    def test_ignores_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "pkg.js").write_text("x", encoding="utf-8")
            result = _iter_repo_files(repo, max_files=100)
            self.assertEqual(len(result), 0)

    def test_max_files_limit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            for i in range(20):
                (repo / f"file_{i}.txt").write_text("x", encoding="utf-8")
            result = _iter_repo_files(repo, max_files=5)
            self.assertEqual(len(result), 5)

    def test_nonexistent_path_returns_empty(self) -> None:
        result = _iter_repo_files(Path("/nonexistent_path_xyz"), max_files=100)
        self.assertEqual(result, [])


class TestReadText(unittest.TestCase):
    """Tests for _read_text file reading."""

    def test_reads_utf8_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.txt"
            p.write_text("hello world", encoding="utf-8")
            self.assertEqual(_read_text(p, max_bytes=1024), "hello world")

    def test_truncates_at_max_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "big.txt"
            p.write_text("A" * 200, encoding="utf-8")
            result = _read_text(p, max_bytes=50)
            self.assertEqual(len(result), 50)

    def test_nonexistent_file_returns_empty(self) -> None:
        result = _read_text(Path("/nonexistent_xyz.txt"), max_bytes=1024)
        self.assertEqual(result, "")

    def test_binary_file_uses_replace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "binary.bin"
            p.write_bytes(b"\x80\x81\x82valid\xff")
            result = _read_text(p, max_bytes=4096)
            self.assertIn("valid", result)


class TestPlaceholderValue(unittest.TestCase):
    """Tests for _placeholder_value detection."""

    def test_example_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value("example-api-key"))

    def test_changeme_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value("changeme"))

    def test_dummy_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value("dummy_value"))

    def test_empty_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value(""))

    def test_none_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value(None))  # type: ignore[arg-type]

    def test_real_value_is_not_placeholder(self) -> None:
        self.assertFalse(_placeholder_value("sk-live-abc123xyz456"))

    def test_test_key_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value("test-key-value"))

    def test_test_token_is_placeholder(self) -> None:
        self.assertTrue(_placeholder_value("my_test_token"))


class TestFingerprint(unittest.TestCase):
    """Tests for _fingerprint deterministic hashing."""

    def test_deterministic(self) -> None:
        fp1 = _fingerprint(["repo", "cat", "file.py", "10", "secret"])
        fp2 = _fingerprint(["repo", "cat", "file.py", "10", "secret"])
        self.assertEqual(fp1, fp2)

    def test_different_inputs_differ(self) -> None:
        fp1 = _fingerprint(["repo", "cat", "a.py", "10", "secret"])
        fp2 = _fingerprint(["repo", "cat", "b.py", "10", "secret"])
        self.assertNotEqual(fp1, fp2)

    def test_case_insensitive(self) -> None:
        fp1 = _fingerprint(["Repo", "CAT", "File.py"])
        fp2 = _fingerprint(["repo", "cat", "file.py"])
        self.assertEqual(fp1, fp2)

    def test_handles_none_parts(self) -> None:
        fp = _fingerprint([None, "", "test"])  # type: ignore[list-item]
        self.assertIsInstance(fp, str)
        self.assertEqual(len(fp), 40)  # SHA1 hex


class TestScanSecretPatterns(unittest.TestCase):
    """Tests for _scan_secret_patterns pattern detection."""

    def test_detects_aws_access_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "config.py").write_text(
                'aws_key = "AKIAIOSFODNN7REALKEY"\n', encoding="utf-8"
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            categories = {f["category"] for f in findings}
            self.assertIn("aws-access-key", categories)

    def test_detects_github_token(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "deploy.sh").write_text(
                'GITHUB_TOKEN="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"\n',
                encoding="utf-8",
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            categories = {f["category"] for f in findings}
            self.assertIn("github-token", categories)

    def test_detects_private_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "key.py").write_text(
                '-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n',
                encoding="utf-8",
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            categories = {f["category"] for f in findings}
            self.assertIn("private-key-material", categories)
            severity = next(f["severity"] for f in findings if f["category"] == "private-key-material")
            self.assertEqual(severity, "critical")

    def test_detects_generic_secret_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "settings.py").write_text(
                'password = "my-super-secret-password-value"\n', encoding="utf-8"
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            categories = {f["category"] for f in findings}
            self.assertIn("generic-secret-assignment", categories)

    def test_skips_placeholder_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "config.py").write_text(
                'api_key = "example-placeholder-value"\n', encoding="utf-8"
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            generic = [f for f in findings if f["category"] == "generic-secret-assignment"]
            self.assertEqual(len(generic), 0)

    def test_clean_repo_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "app.py").write_text(
                "print('hello world')\n", encoding="utf-8"
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            self.assertEqual(len(findings), 0)

    def test_finding_has_line_number(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "main.py").write_text(
                'import os\n\napi_key = "real-production-secret-key-12345"\n',
                encoding="utf-8",
            )
            findings = _scan_secret_patterns(
                repo_name="test", repo_path=repo, max_files=100, max_file_bytes=65536
            )
            self.assertGreater(len(findings), 0)
            self.assertEqual(findings[0]["line"], 3)


class TestScanSensitiveArtifacts(unittest.TestCase):
    """Tests for _scan_sensitive_artifacts file detection."""

    def test_detects_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".env").write_text("SECRET=x\n", encoding="utf-8")
            findings = _scan_sensitive_artifacts(
                repo_name="test", repo_path=repo, max_files=100
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["category"], "sensitive-artifact")

    def test_detects_pem_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "cert.pem").write_text("PEM DATA\n", encoding="utf-8")
            findings = _scan_sensitive_artifacts(
                repo_name="test", repo_path=repo, max_files=100
            )
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["severity"], "high")

    def test_detects_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "server.key").write_text("KEY DATA\n", encoding="utf-8")
            findings = _scan_sensitive_artifacts(
                repo_name="test", repo_path=repo, max_files=100
            )
            self.assertEqual(len(findings), 1)

    def test_detects_p12_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "cert.p12").write_bytes(b"\x00\x01\x02")
            findings = _scan_sensitive_artifacts(
                repo_name="test", repo_path=repo, max_files=100
            )
            self.assertEqual(len(findings), 1)

    def test_detects_id_rsa(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "id_rsa").write_text("private key\n", encoding="utf-8")
            findings = _scan_sensitive_artifacts(
                repo_name="test", repo_path=repo, max_files=100
            )
            self.assertEqual(len(findings), 1)

    def test_clean_repo_no_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "main.py").write_text("pass\n", encoding="utf-8")
            findings = _scan_sensitive_artifacts(
                repo_name="test", repo_path=repo, max_files=100
            )
            self.assertEqual(len(findings), 0)


class TestScanDependencyPosture(unittest.TestCase):
    """Tests for _scan_dependency_posture lockfile detection."""

    def test_node_missing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "package.json").write_text("{}", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            categories = {f["category"] for f in findings}
            self.assertIn("node-lock-missing", categories)

    def test_node_with_lockfile_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "package.json").write_text("{}", encoding="utf-8")
            (repo / "package-lock.json").write_text("{}", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            node_findings = [f for f in findings if f["category"] == "node-lock-missing"]
            self.assertEqual(len(node_findings), 0)

    def test_python_missing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            categories = {f["category"] for f in findings}
            self.assertIn("python-lock-missing", categories)

    def test_python_with_uv_lock_clean(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            (repo / "uv.lock").write_text("lock\n", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            py_findings = [f for f in findings if f["category"] == "python-lock-missing"]
            self.assertEqual(len(py_findings), 0)

    def test_cargo_missing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            categories = {f["category"] for f in findings}
            self.assertIn("cargo-lock-missing", categories)

    def test_no_manifests_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "main.py").write_text("pass\n", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            self.assertEqual(len(findings), 0)

    def test_yarn_lock_satisfies_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "package.json").write_text("{}", encoding="utf-8")
            (repo / "yarn.lock").write_text("lock\n", encoding="utf-8")
            findings = _scan_dependency_posture(repo_name="test", repo_path=repo)
            node_findings = [f for f in findings if f["category"] == "node-lock-missing"]
            self.assertEqual(len(node_findings), 0)


class TestNormalizePolicy(unittest.TestCase):
    """Tests for _normalize_policy configuration defaults and clamping."""

    def test_none_returns_defaults(self) -> None:
        cfg = _normalize_policy(None)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["max_findings_per_repo"], 40)
        self.assertEqual(cfg["scan_max_files"], 320)
        self.assertFalse(cfg["run_pentest"])
        self.assertFalse(cfg["allow_network_scans"])
        self.assertEqual(cfg["target_urls"], [])

    def test_empty_dict_returns_defaults(self) -> None:
        cfg = _normalize_policy({})
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["scan_max_files"], 320)

    def test_custom_values_propagate(self) -> None:
        cfg = _normalize_policy({
            "enabled": False,
            "max_findings_per_repo": 10,
            "scan_max_files": 50,
            "run_pentest": True,
            "allow_network_scans": True,
            "target_urls": ["https://example.com"],
        })
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["max_findings_per_repo"], 10)
        self.assertEqual(cfg["scan_max_files"], 50)
        self.assertTrue(cfg["run_pentest"])
        self.assertTrue(cfg["allow_network_scans"])
        self.assertEqual(cfg["target_urls"], ["https://example.com"])

    def test_clamps_min_values(self) -> None:
        cfg = _normalize_policy({
            "max_findings_per_repo": 0,
            "scan_max_files": 1,
            "scan_max_file_bytes": 100,
        })
        self.assertGreaterEqual(cfg["max_findings_per_repo"], 1)
        self.assertGreaterEqual(cfg["scan_max_files"], 20)
        self.assertGreaterEqual(cfg["scan_max_file_bytes"], 2048)

    def test_target_urls_truncated_to_20(self) -> None:
        urls = [f"https://example.com/{i}" for i in range(30)]
        cfg = _normalize_policy({"target_urls": urls})
        self.assertEqual(len(cfg["target_urls"]), 20)

    def test_target_urls_strips_empty_strings(self) -> None:
        cfg = _normalize_policy({"target_urls": ["", "  ", "https://valid.com"]})
        self.assertEqual(cfg["target_urls"], ["https://valid.com"])


class TestSecurityPrompt(unittest.TestCase):
    """Tests for _security_prompt template generation."""

    def test_produces_string_with_repo_name(self) -> None:
        finding = {
            "fingerprint": "abc123",
            "category": "aws-access-key",
            "severity": "high",
            "file": "config.py",
            "evidence": "AKIA...",
            "recommendation": "Rotate key",
        }
        prompt = _security_prompt("my-repo", finding)
        self.assertIn("my-repo", prompt)
        self.assertIn("abc123", prompt)
        self.assertIn("high", prompt)
        self.assertIn("aws-access-key", prompt)
        self.assertIn("config.py", prompt)

    def test_handles_missing_fields(self) -> None:
        prompt = _security_prompt("repo", {})
        self.assertIn("repo", prompt)
        self.assertIsInstance(prompt, str)


class TestRunSecdriftScan(unittest.TestCase):
    """Tests for the top-level run_secdrift_scan orchestrator."""

    def test_disabled_policy_returns_zero_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=Path(td),
                policy_cfg={"enabled": False},
            )
            self.assertFalse(report["enabled"])
            self.assertEqual(report["summary"]["findings_total"], 0)
            self.assertEqual(report["findings"], [])
            self.assertIn("disabled", report["summary"]["narrative"])

    def test_risk_score_increases_with_severity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Create a critical finding (private key) and a medium finding (missing lock)
            (repo / "key.py").write_text(
                "-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text("{}", encoding="utf-8")
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"run_pentest": False, "allow_network_scans": False},
            )
            summary = report["summary"]
            self.assertGreater(summary["risk_score"], 0)
            self.assertTrue(summary["at_risk"])

    def test_deduplication_by_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Same secret on same line should produce one finding, not duplicates
            (repo / "config.py").write_text(
                'api_key = "real-production-secret-key-value"\n', encoding="utf-8"
            )
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"run_pentest": False, "allow_network_scans": False},
            )
            fps = [f["fingerprint"] for f in report["findings"]]
            self.assertEqual(len(fps), len(set(fps)), "Duplicate fingerprints found")

    def test_findings_sorted_by_severity_desc(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "key.py").write_text(
                '-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n'
                'api_key = "some-real-production-api-key-1234"\n',
                encoding="utf-8",
            )
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"run_pentest": False, "allow_network_scans": False},
            )
            from driftdriver.secdrift import _SEVERITY_ORDER
            severities = [f.get("severity", "") for f in report["findings"]]
            severity_values = [_SEVERITY_ORDER.get(s, 0) for s in severities]
            self.assertEqual(severity_values, sorted(severity_values, reverse=True))

    def test_model_contract_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=Path(td),
            )
            mc = report["model_contract"]
            self.assertIn("decision_owner", mc)
            self.assertIn("required_outputs", mc)
            self.assertIn("prompt_seed", mc)

    def test_top_findings_limited_by_max(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Create many findings
            for i in range(10):
                (repo / f"secret_{i}.py").write_text(
                    f'token = "production-real-secret-value-{i:04d}"\n',
                    encoding="utf-8",
                )
            (repo / "package.json").write_text("{}", encoding="utf-8")
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "max_findings_per_repo": 3,
                    "run_pentest": False,
                    "allow_network_scans": False,
                },
            )
            self.assertLessEqual(len(report["top_findings"]), 3)

    def test_model_prompt_added_to_top_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "config.py").write_text(
                'api_key = "real-production-secret-key-value"\n', encoding="utf-8"
            )
            report = run_secdrift_scan(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"run_pentest": False, "allow_network_scans": False},
            )
            for f in report["top_findings"]:
                self.assertIn("model_prompt", f)
                self.assertIsInstance(f["model_prompt"], str)


class TestRunAsLaneExpanded(unittest.TestCase):
    """Expanded tests for run_as_lane integration."""

    def test_findings_have_correct_tags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".env").write_text("SECRET=x\n", encoding="utf-8")
            result = run_as_lane(repo)
            for f in result.findings:
                self.assertIsInstance(f.tags, list)
                self.assertGreater(len(f.tags), 0)

    def test_summary_contains_repo_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "config.py").write_text(
                'password = "real-production-secret-pass1234"\n', encoding="utf-8"
            )
            result = run_as_lane(repo)
            # Summary should contain the repo directory name
            self.assertIn(repo.name, result.summary)

    def test_severity_mapping_in_findings(self) -> None:
        """All findings from run_as_lane use lane contract severity values."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "key.py").write_text(
                "-----BEGIN RSA PRIVATE KEY-----\ndata\n-----END RSA PRIVATE KEY-----\n",
                encoding="utf-8",
            )
            (repo / "package.json").write_text("{}", encoding="utf-8")
            result = run_as_lane(repo)
            valid_severities = {"info", "warning", "error", "critical"}
            for f in result.findings:
                self.assertIn(f.severity, valid_severities)


class TestMapSeverityExpanded(unittest.TestCase):
    """Additional _map_severity edge case tests."""

    def test_case_insensitive(self) -> None:
        self.assertEqual(_map_severity({"severity": "HIGH"}), "error")
        self.assertEqual(_map_severity({"severity": "Critical"}), "critical")

    def test_none_severity(self) -> None:
        self.assertEqual(_map_severity({"severity": None}), "info")


if __name__ == "__main__":
    unittest.main()
