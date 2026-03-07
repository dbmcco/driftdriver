# ABOUTME: Comprehensive tests for driftdriver/cli/_helpers.py utility functions.
# ABOUTME: Covers findings collection, loop safety, action normalization, parsing, deduplication, and preflight logic.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from driftdriver.cli._helpers import (
    _collect_findings,
    _compute_loop_safety,
    _dedupe_strings,
    _ensure_update_followup_task,
    _maybe_auto_ensure_contracts,
    _normalize_actions,
    _parse_watch_repo,
    _parse_watch_report,
    _resolve_update_sources,
    _run_update_preflight,
    _update_errors,
    _wg_log_message,
    _wrapper_commands_available,
)


# ---------------------------------------------------------------------------
# _update_errors
# ---------------------------------------------------------------------------


class TestUpdateErrors:
    def test_extracts_errors_from_repos_section(self) -> None:
        result: dict[str, Any] = {
            "repos": [
                {"tool": "coredrift", "error": "timeout"},
                {"tool": "specdrift", "sha": "abc123"},
            ],
        }
        errors = _update_errors(result)
        assert errors == ["coredrift: timeout"]

    def test_extracts_errors_from_user_checks(self) -> None:
        result: dict[str, Any] = {
            "user_checks": [
                {"user": "alice", "error": "rate limited"},
            ],
        }
        errors = _update_errors(result)
        assert errors == ["alice: rate limited"]

    def test_extracts_errors_from_report_checks(self) -> None:
        result: dict[str, Any] = {
            "report_checks": [
                {"name": "changelog", "error": "404 not found"},
            ],
        }
        errors = _update_errors(result)
        assert errors == ["changelog: 404 not found"]

    def test_multiple_sections_combined(self) -> None:
        result: dict[str, Any] = {
            "repos": [{"tool": "coredrift", "error": "fail1"}],
            "user_checks": [{"user": "bob", "error": "fail2"}],
            "report_checks": [{"name": "rpt", "error": "fail3"}],
        }
        errors = _update_errors(result)
        assert len(errors) == 3
        assert "coredrift: fail1" in errors
        assert "bob: fail2" in errors
        assert "rpt: fail3" in errors

    def test_empty_result_returns_no_errors(self) -> None:
        assert _update_errors({}) == []

    def test_non_list_sections_skipped(self) -> None:
        result: dict[str, Any] = {
            "repos": "not a list",
            "user_checks": 42,
        }
        assert _update_errors(result) == []

    def test_non_dict_entries_skipped(self) -> None:
        result: dict[str, Any] = {
            "repos": ["string_entry", None, {"tool": "x", "error": "boom"}],
        }
        errors = _update_errors(result)
        assert errors == ["x: boom"]

    def test_blank_and_none_errors_skipped(self) -> None:
        result: dict[str, Any] = {
            "repos": [
                {"tool": "a", "error": ""},
                {"tool": "b", "error": None},
                {"tool": "c", "error": "   "},
                {"tool": "d", "error": "real error"},
            ],
        }
        errors = _update_errors(result)
        assert errors == ["d: real error"]

    def test_missing_label_key_defaults_to_unknown(self) -> None:
        result: dict[str, Any] = {
            "repos": [{"error": "orphan"}],
        }
        errors = _update_errors(result)
        assert errors == ["unknown: orphan"]


# ---------------------------------------------------------------------------
# _dedupe_strings
# ---------------------------------------------------------------------------


class TestDedupeStrings:
    def test_removes_duplicates_preserving_order(self) -> None:
        assert _dedupe_strings(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]

    def test_strips_whitespace(self) -> None:
        assert _dedupe_strings(["  hello ", "hello", " hello"]) == ["hello"]

    def test_removes_empty_and_whitespace_only(self) -> None:
        assert _dedupe_strings(["", "  ", "ok", ""]) == ["ok"]

    def test_empty_input(self) -> None:
        assert _dedupe_strings([]) == []

    def test_all_unique(self) -> None:
        assert _dedupe_strings(["x", "y", "z"]) == ["x", "y", "z"]

    def test_coerces_non_strings(self) -> None:
        # The function calls str(raw) so non-string values should work.
        assert _dedupe_strings([1, 2, 1]) == ["1", "2"]  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# _parse_watch_repo
# ---------------------------------------------------------------------------


class TestParseWatchRepo:
    def test_tool_equals_owner_repo(self) -> None:
        tool, remote = _parse_watch_repo("mytool=owner/repo")
        assert tool == "mytool"
        assert remote == "owner/repo"

    def test_bare_owner_repo_infers_tool(self) -> None:
        tool, remote = _parse_watch_repo("owner/myrepo")
        assert tool == "myrepo"
        assert remote == "owner/myrepo"

    def test_whitespace_stripped(self) -> None:
        tool, remote = _parse_watch_repo("  mytool = owner/repo  ")
        assert tool == "mytool"
        assert remote == "owner/repo"

    def test_empty_spec_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _parse_watch_repo("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _parse_watch_repo("   ")

    def test_no_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid"):
            _parse_watch_repo("noslash")

    def test_equals_but_no_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid"):
            _parse_watch_repo("tool=noslash")

    def test_nested_org_path(self) -> None:
        tool, remote = _parse_watch_repo("org/suborg/repo")
        assert tool == "repo"
        assert remote == "org/suborg/repo"


# ---------------------------------------------------------------------------
# _parse_watch_report
# ---------------------------------------------------------------------------


class TestParseWatchReport:
    def test_name_equals_url(self) -> None:
        report = _parse_watch_report("changelog=https://example.com/log")
        assert report["name"] == "changelog"
        assert report["url"] == "https://example.com/log"
        assert report["keywords"] == []

    def test_bare_url(self) -> None:
        report = _parse_watch_report("https://example.com/rss")
        assert report["name"] == "https://example.com/rss"
        assert report["url"] == "https://example.com/rss"

    def test_empty_spec_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _parse_watch_report("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            _parse_watch_report("   ")

    def test_equals_with_empty_name_uses_url(self) -> None:
        report = _parse_watch_report("=https://example.com/feed")
        assert report["name"] == "https://example.com/feed"
        assert report["url"] == "https://example.com/feed"


# ---------------------------------------------------------------------------
# _collect_findings
# ---------------------------------------------------------------------------


class TestCollectFindings:
    def test_extracts_findings_from_plugin_reports(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [
                        {"kind": "scope_drift", "severity": "high"},
                        {"kind": "missing_contract"},
                    ],
                },
            },
            "specdrift": {
                "report": {
                    "findings": [
                        {"kind": "dependency_drift"},
                    ],
                },
            },
        }
        result = _collect_findings(plugins)
        assert ("coredrift", "scope_drift") in result
        assert ("coredrift", "missing_contract") in result
        assert ("specdrift", "dependency_drift") in result
        assert len(result) == 3

    def test_skips_non_dict_payload(self) -> None:
        plugins: dict[str, Any] = {
            "broken": "not a dict",
        }
        assert _collect_findings(plugins) == []

    def test_skips_missing_report(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {"status": "ok"},
        }
        assert _collect_findings(plugins) == []

    def test_skips_non_list_findings(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {"report": {"findings": "not a list"}},
        }
        assert _collect_findings(plugins) == []

    def test_skips_non_dict_findings(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {"report": {"findings": ["str_finding"]}},
        }
        assert _collect_findings(plugins) == []

    def test_skips_empty_kind(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {"report": {"findings": [{"kind": ""}, {"kind": "  "}]}},
        }
        assert _collect_findings(plugins) == []

    def test_empty_plugins(self) -> None:
        assert _collect_findings({}) == []


# ---------------------------------------------------------------------------
# _normalize_actions
# ---------------------------------------------------------------------------


class TestNormalizeActions:
    def test_maps_known_kinds_to_actions(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [
                        {"kind": "scope_drift"},
                        {"kind": "hardening_in_core"},
                        {"kind": "dependency_drift"},
                    ],
                },
            },
        }
        actions = _normalize_actions(plugins)
        action_map = {a["kind"]: a["action"] for a in actions}
        assert action_map["scope_drift"] == "scope"
        assert action_map["hardening_in_core"] == "harden"
        assert action_map["dependency_drift"] == "respec"

    def test_unknown_kind_maps_to_ignore(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [{"kind": "unknown_finding_type"}],
                },
            },
        }
        actions = _normalize_actions(plugins)
        assert len(actions) == 1
        assert actions[0]["action"] == "ignore-with-rationale"
        assert actions[0]["source"] == "coredrift"

    def test_deduplicates_by_action_and_kind(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [
                        {"kind": "scope_drift"},
                        {"kind": "scope_drift"},
                    ],
                },
            },
            "specdrift": {
                "report": {
                    "findings": [
                        {"kind": "scope_drift"},
                    ],
                },
            },
        }
        actions = _normalize_actions(plugins)
        scope_actions = [a for a in actions if a["kind"] == "scope_drift"]
        assert len(scope_actions) == 1
        # First occurrence wins — source should be coredrift.
        assert scope_actions[0]["source"] == "coredrift"

    def test_empty_plugins_returns_empty(self) -> None:
        assert _normalize_actions({}) == []

    def test_fix_kind_variants(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [
                        {"kind": "repeated_fix_attempts"},
                        {"kind": "unresolved_fix_followups"},
                        {"kind": "missing_repro_evidence"},
                        {"kind": "missing_root_cause_evidence"},
                        {"kind": "missing_regression_evidence"},
                    ],
                },
            },
        }
        actions = _normalize_actions(plugins)
        for a in actions:
            assert a["action"] == "fix"

    def test_respec_kind_variants(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [
                        {"kind": "missing_redrift_artifacts"},
                        {"kind": "phase_incomplete_analyze"},
                        {"kind": "phase_incomplete_respec"},
                        {"kind": "phase_incomplete_design"},
                        {"kind": "phase_incomplete_build"},
                    ],
                },
            },
        }
        actions = _normalize_actions(plugins)
        for a in actions:
            assert a["action"] == "respec"

    def test_harden_kind_variants(self) -> None:
        plugins: dict[str, Any] = {
            "coredrift": {
                "report": {
                    "findings": [
                        {"kind": "repeated_drift_signals"},
                        {"kind": "unresolved_drift_followups"},
                        {"kind": "missing_recovery_plan"},
                    ],
                },
            },
        }
        actions = _normalize_actions(plugins)
        for a in actions:
            assert a["action"] == "harden"


# ---------------------------------------------------------------------------
# _wrapper_commands_available
# ---------------------------------------------------------------------------


class TestWrapperCommandsAvailable:
    def test_nonexistent_wrapper_returns_empty(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "nonexistent"
        assert _wrapper_commands_available(wrapper=wrapper) == []

    def test_detects_commands_from_help_output(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "fakewrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            'echo "Available commands: install, check, updates, doctor, queue, run, orchestrate"\n'
        )
        wrapper.chmod(0o755)
        result = _wrapper_commands_available(wrapper=wrapper)
        assert "install" in result
        assert "check" in result
        assert "updates" in result
        assert "doctor" in result
        assert "queue" in result
        assert "run" in result
        assert "orchestrate" in result

    def test_partial_commands(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "partialwrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            'echo "commands: install, check"\n'
        )
        wrapper.chmod(0o755)
        result = _wrapper_commands_available(wrapper=wrapper)
        assert "install" in result
        assert "check" in result
        assert "updates" not in result

    def test_no_matching_commands(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "emptywrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            'echo "no recognized commands here"\n'
        )
        wrapper.chmod(0o755)
        result = _wrapper_commands_available(wrapper=wrapper)
        # "run" appears in "no recognized commands here" — let's check
        # Actually "run" is a word in the output. Let's verify.
        # The regex is \brun\b — and "no recognized commands here" does NOT have "run".
        assert result == []

    def test_help_on_stderr(self, tmp_path: Path) -> None:
        wrapper = tmp_path / "stderrwrapper"
        wrapper.write_text(
            "#!/bin/sh\n"
            'echo "install check" >&2\n'
        )
        wrapper.chmod(0o755)
        result = _wrapper_commands_available(wrapper=wrapper)
        assert "install" in result
        assert "check" in result


# ---------------------------------------------------------------------------
# _compute_loop_safety
# ---------------------------------------------------------------------------


class TestComputeLoopSafety:
    @staticmethod
    def _make_wg_dir(tmp_path: Path, tasks: list[dict[str, Any]]) -> Path:
        """Create a minimal .workgraph directory with a graph.jsonl file."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for task in tasks:
            obj = dict(task)
            obj.setdefault("kind", "task")
            lines.append(json.dumps(obj))
        (wg_dir / "graph.jsonl").write_text("\n".join(lines) + "\n")
        return wg_dir

    def test_clean_graph_no_blocks(self, tmp_path: Path) -> None:
        tasks = [
            {"id": "task-1", "kind": "task", "status": "open"},
        ]
        wg_dir = self._make_wg_dir(tmp_path, tasks)

        class FakePolicy:
            loop_max_redrift_depth = 2
            loop_max_ready_drift_followups = 20
            loop_block_followup_creation = True

        result = _compute_loop_safety(wg_dir=wg_dir, task_id="task-1", policy=FakePolicy())
        assert result["followups_blocked"] is False
        assert result["reasons"] == []
        assert result["blocked_by_cycle"] is False

    def test_redrift_depth_exceeded(self, tmp_path: Path) -> None:
        tasks = [
            {"id": "redrift-redrift-redrift-task", "kind": "task", "status": "open"},
        ]
        wg_dir = self._make_wg_dir(tmp_path, tasks)

        class FakePolicy:
            loop_max_redrift_depth = 2
            loop_max_ready_drift_followups = 20
            loop_block_followup_creation = True

        result = _compute_loop_safety(
            wg_dir=wg_dir,
            task_id="redrift-redrift-redrift-task",
            policy=FakePolicy(),
        )
        assert result["observed_redrift_depth"] == 3
        assert result["followups_blocked"] is True
        assert any("redrift_depth_exceeded" in r for r in result["reasons"])

    def test_cycle_detected(self, tmp_path: Path) -> None:
        tasks = [
            {"id": "drift-a", "kind": "task", "status": "open", "blocked_by": ["drift-b"]},
            {"id": "drift-b", "kind": "task", "status": "open", "blocked_by": ["drift-a"]},
        ]
        wg_dir = self._make_wg_dir(tmp_path, tasks)

        class FakePolicy:
            loop_max_redrift_depth = 2
            loop_max_ready_drift_followups = 20
            loop_block_followup_creation = True

        result = _compute_loop_safety(wg_dir=wg_dir, task_id="drift-a", policy=FakePolicy())
        assert result["blocked_by_cycle"] is True
        assert result["followups_blocked"] is True
        assert "blocked_by_cycle_detected" in result["reasons"]

    def test_non_redrift_task_depth_zero(self, tmp_path: Path) -> None:
        tasks = [
            {"id": "drift-scope-xyz", "kind": "task", "status": "open"},
        ]
        wg_dir = self._make_wg_dir(tmp_path, tasks)

        class FakePolicy:
            loop_max_redrift_depth = 2
            loop_max_ready_drift_followups = 20
            loop_block_followup_creation = True

        result = _compute_loop_safety(
            wg_dir=wg_dir, task_id="drift-scope-xyz", policy=FakePolicy()
        )
        assert result["observed_redrift_depth"] == 0

    def test_ready_queue_exceeded(self, tmp_path: Path) -> None:
        # Create enough ready drift tasks to exceed max_ready=2.
        tasks = [
            {"id": "parent", "kind": "task", "status": "done"},
        ]
        for i in range(5):
            tasks.append(
                {
                    "id": f"drift-fix-{i}",
                    "kind": "task",
                    "status": "open",
                    "blocked_by": ["parent"],
                    "created_at": f"2026-01-01T0{i}:00:00+00:00",
                }
            )
        wg_dir = self._make_wg_dir(tmp_path, tasks)

        class FakePolicy:
            loop_max_redrift_depth = 2
            loop_max_ready_drift_followups = 2
            loop_block_followup_creation = True

        result = _compute_loop_safety(wg_dir=wg_dir, task_id="parent", policy=FakePolicy())
        assert result["ready_drift_followups"] == 5
        assert result["followups_blocked"] is True
        assert any("ready_drift_queue_exceeded" in r for r in result["reasons"])

    def test_block_disabled_by_policy(self, tmp_path: Path) -> None:
        # Even with reasons, blocking can be disabled.
        tasks = [
            {"id": "redrift-redrift-redrift-task", "kind": "task", "status": "open"},
        ]
        wg_dir = self._make_wg_dir(tmp_path, tasks)

        class FakePolicy:
            loop_max_redrift_depth = 1
            loop_max_ready_drift_followups = 20
            loop_block_followup_creation = False

        result = _compute_loop_safety(
            wg_dir=wg_dir,
            task_id="redrift-redrift-redrift-task",
            policy=FakePolicy(),
        )
        assert result["reasons"]  # reasons exist
        assert result["followups_blocked"] is False  # but blocking disabled


# ---------------------------------------------------------------------------
# _maybe_auto_ensure_contracts
# ---------------------------------------------------------------------------


class TestMaybeAutoEnsureContracts:
    def test_disabled_by_policy(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        class FakePolicy:
            contracts_auto_ensure = False

        result = _maybe_auto_ensure_contracts(
            wg_dir=wg_dir, project_dir=tmp_path, policy=FakePolicy()
        )
        assert result["enabled"] is False
        assert result["attempted"] is False

    def test_coredrift_wrapper_missing(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        class FakePolicy:
            contracts_auto_ensure = True

        result = _maybe_auto_ensure_contracts(
            wg_dir=wg_dir, project_dir=tmp_path, policy=FakePolicy()
        )
        assert result["enabled"] is True
        assert result["attempted"] is False
        assert result["error"] == "coredrift wrapper not found"

    def test_coredrift_success(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        coredrift = wg_dir / "coredrift"
        coredrift.write_text("#!/bin/sh\nexit 0\n")
        coredrift.chmod(0o755)

        class FakePolicy:
            contracts_auto_ensure = True

        result = _maybe_auto_ensure_contracts(
            wg_dir=wg_dir, project_dir=tmp_path, policy=FakePolicy()
        )
        assert result["enabled"] is True
        assert result["attempted"] is True
        assert result["applied"] is True
        assert result["error"] is None

    def test_coredrift_failure(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        coredrift = wg_dir / "coredrift"
        coredrift.write_text("#!/bin/sh\necho 'something went wrong' >&2\nexit 1\n")
        coredrift.chmod(0o755)

        class FakePolicy:
            contracts_auto_ensure = True

        result = _maybe_auto_ensure_contracts(
            wg_dir=wg_dir, project_dir=tmp_path, policy=FakePolicy()
        )
        assert result["enabled"] is True
        assert result["attempted"] is True
        assert result["applied"] is False
        assert result["error"] is not None
        assert "something went wrong" in result["error"]


# ---------------------------------------------------------------------------
# _resolve_update_sources
# ---------------------------------------------------------------------------


class TestResolveUpdateSources:
    def test_defaults_when_no_config(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        # Should have the default ECOSYSTEM_REPOS.
        assert "driftdriver" in result["repos"]
        assert "coredrift" in result["repos"]
        assert result["users"] == []
        assert result["reports"] == []
        assert result["config_exists"] is False

    def test_watch_repo_specs_add_repos(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=["custom=org/custom-repo"],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        assert result["repos"]["custom"] == "org/custom-repo"

    def test_watch_user_specs_deduped(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=[],
            watch_user_specs=["@alice", "bob", "alice"],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        assert result["users"] == ["alice", "bob"]

    def test_watch_report_specs_added(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=["myreport=https://example.com/report"],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        assert len(result["reports"]) == 1
        assert result["reports"][0]["name"] == "myreport"
        assert result["reports"][0]["url"] == "https://example.com/report"

    def test_user_repo_limit_clamped(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=200,
        )
        assert result["user_repo_limit"] == 100  # clamped to max 100

    def test_user_repo_limit_min_is_1(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=0,
        )
        assert result["user_repo_limit"] == 1  # clamped to min 1

    def test_config_file_repos_override_defaults(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        config_path = tmp_path / "review.json"
        config_path.write_text(json.dumps({
            "repos": {"only-this": "org/only-this"},
        }))
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=str(config_path),
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        # When config provides "repos", ECOSYSTEM_REPOS should NOT be included.
        assert "driftdriver" not in result["repos"]
        assert "only-this" in result["repos"]

    def test_config_file_extra_repos_merged(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        config_path = tmp_path / "review.json"
        config_path.write_text(json.dumps({
            "extra_repos": {"myextra": "org/myextra"},
        }))
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=str(config_path),
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        # extra_repos merged with ECOSYSTEM_REPOS
        assert "driftdriver" in result["repos"]
        assert "myextra" in result["repos"]

    def test_report_keywords_merged_and_deduped(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        config_path = tmp_path / "review.json"
        config_path.write_text(json.dumps({
            "report_keywords": ["drift", "scope"],
        }))
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=str(config_path),
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=["scope", "new_kw"],
            user_repo_limit=None,
        )
        assert "drift" in result["report_keywords"]
        assert "scope" in result["report_keywords"]
        assert "new_kw" in result["report_keywords"]
        # "scope" appears in both config and CLI but should be deduped.
        assert result["report_keywords"].count("scope") == 1

    def test_duplicate_report_urls_deduped(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        config_path = tmp_path / "review.json"
        config_path.write_text(json.dumps({
            "reports": [
                {"name": "r1", "url": "https://example.com/feed"},
                {"name": "r2", "url": "https://example.com/feed"},
            ],
        }))
        result = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=str(config_path),
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
        assert len(result["reports"]) == 1
        assert result["reports"][0]["name"] == "r1"


# ---------------------------------------------------------------------------
# _wg_log_message (subprocess boundary — monkeypatch)
# ---------------------------------------------------------------------------


class TestWgLogMessage:
    def test_calls_wg_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_check_call(cmd: list[str], **kwargs: Any) -> None:
            calls.append(cmd)

        monkeypatch.setattr(subprocess, "check_call", fake_check_call)
        _wg_log_message(wg_dir=tmp_path, task_id="t-1", message="hello world")
        assert len(calls) == 1
        assert calls[0] == ["wg", "--dir", str(tmp_path), "log", "t-1", "hello world"]

    def test_swallows_exception(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fail(*args: Any, **kwargs: Any) -> None:
            raise OSError("wg not found")

        monkeypatch.setattr(subprocess, "check_call", fail)
        _wg_log_message(wg_dir=tmp_path, task_id="t-1", message="msg")
        captured = capsys.readouterr()
        assert "could not write" in captured.err


# ---------------------------------------------------------------------------
# _ensure_update_followup_task (subprocess boundary — monkeypatch)
# ---------------------------------------------------------------------------


class TestEnsureUpdateFollowupTask:
    def test_returns_expected_task_id_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "driftdriver.drift_task_guard.guarded_add_drift_task",
            lambda **kw: "existing",
        )
        result = _ensure_update_followup_task(
            wg_dir=tmp_path, task_id="t-1", summary="updates found"
        )
        assert result == "drift-self-update-t-1"

    def test_creates_new_task_via_guard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard_calls: list[dict[str, Any]] = []

        def fake_guard(**kwargs: Any) -> str:
            guard_calls.append(kwargs)
            return "created"

        monkeypatch.setattr(
            "driftdriver.drift_task_guard.guarded_add_drift_task",
            fake_guard,
        )
        result = _ensure_update_followup_task(
            wg_dir=tmp_path, task_id="t-1", summary="updates found"
        )
        assert result == "drift-self-update-t-1"
        assert len(guard_calls) == 1
        call = guard_calls[0]
        assert call["task_id"] == "drift-self-update-t-1"
        assert call["lane_tag"] == "updates"
        assert call["after"] == "t-1"


# ---------------------------------------------------------------------------
# _run_update_preflight (integration-ish — uses monkeypatch to avoid network)
# ---------------------------------------------------------------------------


class TestRunUpdatePreflight:
    def test_disabled_by_policy(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        class FakePolicy:
            updates_enabled = False

        result = _run_update_preflight(
            wg_dir=wg_dir,
            policy=FakePolicy(),
            task_id="t-1",
            write_log=False,
            create_followups=False,
        )
        assert result["enabled"] is False
        assert result["checked"] is False

    def test_enabled_no_updates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        class FakePolicy:
            updates_enabled = True
            updates_check_interval_seconds = 0

        def fake_check_ecosystem_updates(**kwargs: Any) -> dict[str, Any]:
            return {
                "skipped": False,
                "has_updates": False,
                "has_discoveries": False,
                "updates": [],
                "repos": [],
            }

        monkeypatch.setattr(
            "driftdriver.cli._helpers.check_ecosystem_updates",
            fake_check_ecosystem_updates,
        )
        result = _run_update_preflight(
            wg_dir=wg_dir,
            policy=FakePolicy(),
            task_id="t-1",
            write_log=False,
            create_followups=False,
        )
        assert result["enabled"] is True
        assert result["checked"] is True
        assert result["has_updates"] is False

    def test_negative_interval_clamped_to_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        captured_kwargs: dict[str, Any] = {}

        class FakePolicy:
            updates_enabled = True
            updates_check_interval_seconds = -5

        def fake_check_ecosystem_updates(**kwargs: Any) -> dict[str, Any]:
            captured_kwargs.update(kwargs)
            return {"skipped": False, "has_updates": False, "updates": [], "repos": []}

        monkeypatch.setattr(
            "driftdriver.cli._helpers.check_ecosystem_updates",
            fake_check_ecosystem_updates,
        )
        _run_update_preflight(
            wg_dir=wg_dir,
            policy=FakePolicy(),
            task_id="t-1",
            write_log=False,
            create_followups=False,
        )
        assert captured_kwargs["interval_seconds"] == 0


# ---------------------------------------------------------------------------
# Parametrized edge cases
# ---------------------------------------------------------------------------


class TestParametrized:
    @pytest.mark.parametrize(
        "task_id, expected_depth",
        [
            ("task-1", 0),
            ("redrift-build-task", 1),
            ("redrift-redrift-task", 2),
            ("redrift-redrift-redrift-task", 3),
            ("drift-scope-xyz", 0),
        ],
    )
    def test_redrift_depth_via_loop_safety(
        self, tmp_path: Path, task_id: str, expected_depth: int
    ) -> None:
        """Confirm _compute_loop_safety correctly extracts redrift depth."""
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        (wg_dir / "graph.jsonl").write_text(
            json.dumps({"id": task_id, "kind": "task", "status": "open"}) + "\n"
        )

        class FakePolicy:
            loop_max_redrift_depth = 100
            loop_max_ready_drift_followups = 10000
            loop_block_followup_creation = True

        result = _compute_loop_safety(wg_dir=wg_dir, task_id=task_id, policy=FakePolicy())
        assert result["observed_redrift_depth"] == expected_depth

    @pytest.mark.parametrize(
        "spec, expected_tool, expected_remote",
        [
            ("tool=org/repo", "tool", "org/repo"),
            ("org/repo", "repo", "org/repo"),
            ("my-tool=github.com/org/repo", "my-tool", "github.com/org/repo"),
        ],
    )
    def test_parse_watch_repo_parametrized(
        self, spec: str, expected_tool: str, expected_remote: str
    ) -> None:
        tool, remote = _parse_watch_repo(spec)
        assert tool == expected_tool
        assert remote == expected_remote

    @pytest.mark.parametrize(
        "spec",
        ["", "   ", "noslash", "tool=noslash"],
    )
    def test_parse_watch_repo_invalid_specs(self, spec: str) -> None:
        with pytest.raises(ValueError):
            _parse_watch_repo(spec)
