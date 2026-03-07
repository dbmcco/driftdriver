# ABOUTME: Expanded test coverage for plandrift module — contract validation, dependency analysis, run_as_lane
# ABOUTME: Targets 20+ tests using real temp directories with graph.jsonl fixtures (no mocks)
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from driftdriver.plandrift import (
    _build_dependents,
    _downstream_has,
    _fingerprint,
    _is_continuation_task,
    _is_e2e_task,
    _is_implementation_task,
    _is_integration_task,
    _is_loopback_task,
    _is_test_task,
    _map_severity,
    _normalize_cfg,
    _normalize_dependencies,
    _normalize_tags,
    _planning_prompt,
    _read_workgraph_tasks,
    _task_text,
    emit_plan_review_tasks,
    run_as_lane,
    run_workgraph_plan_review,
)
from driftdriver.lane_contract import LaneResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_graph(repo: Path, rows: list[dict]) -> Path:
    """Write a graph.jsonl file into a .workgraph directory and return the repo path."""
    wg = repo / ".workgraph"
    wg.mkdir(parents=True, exist_ok=True)
    graph = wg / "graph.jsonl"
    graph.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return repo


def _make_task(
    task_id: str,
    title: str = "",
    status: str = "open",
    after: list | None = None,
    tags: list | None = None,
    description: str = "",
) -> dict:
    return {
        "type": "task",
        "id": task_id,
        "title": title,
        "description": description,
        "status": status,
        "after": after or [],
        "tags": tags or [],
    }


# ---------------------------------------------------------------------------
# _normalize_cfg
# ---------------------------------------------------------------------------

class TestNormalizeCfg:
    def test_defaults_when_none(self) -> None:
        cfg = _normalize_cfg(None)
        assert cfg["enabled"] is True
        assert cfg["interval_seconds"] == 14400
        assert cfg["max_findings_per_repo"] == 40
        assert cfg["emit_review_tasks"] is True
        assert cfg["continuation_runtime"] == "double-shot-latte"
        assert cfg["review_rounds"] == 2

    def test_defaults_when_empty_dict(self) -> None:
        cfg = _normalize_cfg({})
        assert cfg["enabled"] is True
        assert cfg["require_integration_tests"] is True
        assert cfg["require_e2e_tests"] is True
        assert cfg["fresh_reviewer_required"] is True

    def test_disabled_policy(self) -> None:
        cfg = _normalize_cfg({"enabled": False})
        assert cfg["enabled"] is False

    def test_clamps_negative_interval(self) -> None:
        cfg = _normalize_cfg({"interval_seconds": -100})
        assert cfg["interval_seconds"] == 0

    def test_clamps_max_findings_minimum(self) -> None:
        cfg = _normalize_cfg({"max_findings_per_repo": 0})
        assert cfg["max_findings_per_repo"] == 1

    def test_clamps_review_rounds_minimum(self) -> None:
        cfg = _normalize_cfg({"review_rounds": -5})
        assert cfg["review_rounds"] == 1

    def test_custom_values_preserved(self) -> None:
        cfg = _normalize_cfg({
            "enabled": True,
            "interval_seconds": 9999,
            "max_findings_per_repo": 10,
            "continuation_runtime": "custom-runtime",
            "review_rounds": 5,
            "hard_stop_on_critical": True,
        })
        assert cfg["interval_seconds"] == 9999
        assert cfg["max_findings_per_repo"] == 10
        assert cfg["continuation_runtime"] == "custom-runtime"
        assert cfg["review_rounds"] == 5
        assert cfg["hard_stop_on_critical"] is True


# ---------------------------------------------------------------------------
# _normalize_dependencies
# ---------------------------------------------------------------------------

class TestNormalizeDependencies:
    def test_string_list(self) -> None:
        assert _normalize_dependencies(["a", "b", "c"]) == ["a", "b", "c"]

    def test_dict_list(self) -> None:
        result = _normalize_dependencies([{"id": "task-1"}, {"id": "task-2"}])
        assert result == ["task-1", "task-2"]

    def test_mixed_list(self) -> None:
        result = _normalize_dependencies(["plain", {"id": "dict-dep"}])
        assert result == ["plain", "dict-dep"]

    def test_non_list_returns_empty(self) -> None:
        assert _normalize_dependencies("not-a-list") == []
        assert _normalize_dependencies(None) == []
        assert _normalize_dependencies(42) == []

    def test_strips_whitespace(self) -> None:
        assert _normalize_dependencies(["  spaced  "]) == ["spaced"]

    def test_skips_empty_strings(self) -> None:
        assert _normalize_dependencies(["", "  ", "valid"]) == ["valid"]

    def test_skips_empty_dict_ids(self) -> None:
        assert _normalize_dependencies([{"id": ""}, {"id": "ok"}]) == ["ok"]


# ---------------------------------------------------------------------------
# _normalize_tags
# ---------------------------------------------------------------------------

class TestNormalizeTags:
    def test_basic_tags(self) -> None:
        assert _normalize_tags(["Feature", "BugFix"]) == ["feature", "bugfix"]

    def test_non_list_returns_empty(self) -> None:
        assert _normalize_tags(None) == []
        assert _normalize_tags("not-a-list") == []

    def test_strips_whitespace_and_lowercases(self) -> None:
        assert _normalize_tags(["  TDD  "]) == ["tdd"]

    def test_skips_empty_tags(self) -> None:
        assert _normalize_tags(["", None, "valid"]) == ["valid"]


# ---------------------------------------------------------------------------
# _task_text
# ---------------------------------------------------------------------------

class TestTaskText:
    def test_combines_fields(self) -> None:
        task = {"id": "t1", "title": "My Title", "description": "A desc", "tags": ["test", "api"]}
        text = _task_text(task)
        assert "t1" in text
        assert "My Title" in text
        assert "A desc" in text
        assert "test" in text
        assert "api" in text

    def test_handles_missing_fields(self) -> None:
        text = _task_text({})
        assert isinstance(text, str)


# ---------------------------------------------------------------------------
# Task classification predicates
# ---------------------------------------------------------------------------

class TestTaskClassification:
    def test_is_test_task_positive(self) -> None:
        assert _is_test_task({"id": "t", "title": "Run integration tests"}) is True
        assert _is_test_task({"id": "t", "title": "e2e validation"}) is True
        assert _is_test_task({"id": "t", "title": "regression suite"}) is True

    def test_is_test_task_negative(self) -> None:
        assert _is_test_task({"id": "t", "title": "Build login page"}) is False

    def test_is_integration_task(self) -> None:
        assert _is_integration_task({"id": "t", "title": "Integration check"}) is True
        assert _is_integration_task({"id": "t", "title": "API contract tests"}) is True
        assert _is_integration_task({"id": "t", "title": "Build login"}) is False

    def test_is_e2e_task(self) -> None:
        assert _is_e2e_task({"id": "t", "title": "Run e2e suite"}) is True
        assert _is_e2e_task({"id": "t", "title": "Playwright tests"}) is True
        assert _is_e2e_task({"id": "t", "title": "Smoke test journey"}) is True
        assert _is_e2e_task({"id": "t", "title": "Build login"}) is False

    def test_is_loopback_task(self) -> None:
        assert _is_loopback_task({"id": "t", "title": "Retry on failure"}) is True
        assert _is_loopback_task({"id": "t", "title": "Rollback migration"}) is True
        assert _is_loopback_task({"id": "t", "title": "Recovery path"}) is True
        assert _is_loopback_task({"id": "t", "title": "Build feature"}) is False

    def test_is_continuation_task(self) -> None:
        assert _is_continuation_task({"id": "t", "title": "Double-shot-latte handoff"}) is True
        assert _is_continuation_task({"id": "t", "title": "Session driver resume"}) is True
        assert _is_continuation_task({"id": "t", "title": "Continuation after context limit"}) is True
        assert _is_continuation_task({"id": "t", "title": "Build feature"}) is False

    def test_is_implementation_task(self) -> None:
        assert _is_implementation_task({"id": "t", "title": "Implement auth API"}) is True
        assert _is_implementation_task({"id": "t", "title": "Build login feature"}) is True
        assert _is_implementation_task({"id": "t", "title": "Refactor database layer"}) is True

    def test_is_implementation_excludes_test_tasks(self) -> None:
        # A task with both "implement" and "test" should NOT be classified as implementation
        assert _is_implementation_task({"id": "t", "title": "Test the implementation"}) is False

    def test_classification_via_tags(self) -> None:
        # Tags contribute to _task_text, so classification works through tags too
        assert _is_test_task({"id": "t", "title": "Some task", "tags": ["test"]}) is True
        assert _is_loopback_task({"id": "t", "title": "Handle", "tags": ["loopback"]}) is True


# ---------------------------------------------------------------------------
# _read_workgraph_tasks
# ---------------------------------------------------------------------------

class TestReadWorkgraphTasks:
    def test_reads_valid_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("t1", "Task one"),
                _make_task("t2", "Task two", after=["t1"]),
            ])
            tasks, errors = _read_workgraph_tasks(repo)
            assert len(tasks) == 2
            assert "t1" in tasks
            assert "t2" in tasks
            assert tasks["t2"]["after"] == ["t1"]
            assert len(errors) == 0

    def test_missing_graph_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            tasks, errors = _read_workgraph_tasks(repo)
            assert len(tasks) == 0
            assert len(errors) == 1
            assert "missing" in errors[0]

    def test_invalid_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True)
            graph = wg / "graph.jsonl"
            graph.write_text(
                '{"type":"task","id":"good","title":"Ok","status":"open"}\n'
                'not valid json\n'
                '{"type":"task","id":"also-good","title":"Fine","status":"open"}\n',
                encoding="utf-8",
            )
            tasks, errors = _read_workgraph_tasks(repo)
            assert len(tasks) == 2
            assert len(errors) == 1
            assert "invalid json" in errors[0]

    def test_skips_non_task_types(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                {"type": "task", "id": "t1", "title": "Real task", "status": "open"},
                {"type": "event", "id": "e1", "title": "Not a task"},
                {"type": "meta", "id": "m1"},
            ])
            tasks, errors = _read_workgraph_tasks(repo)
            assert len(tasks) == 1
            assert "t1" in tasks

    def test_empty_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            tasks, errors = _read_workgraph_tasks(repo)
            assert len(tasks) == 0
            assert len(errors) == 0

    def test_skips_rows_without_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True)
            (wg / "graph.jsonl").write_text(
                '{"type":"task","title":"No ID"}\n'
                '{"type":"task","id":"","title":"Empty ID"}\n'
                '{"type":"task","id":"valid","title":"Has ID","status":"open"}\n',
                encoding="utf-8",
            )
            tasks, errors = _read_workgraph_tasks(repo)
            assert len(tasks) == 1
            assert "valid" in tasks

    def test_uses_desc_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True)
            (wg / "graph.jsonl").write_text(
                '{"type":"task","id":"t1","title":"T","desc":"fallback desc","status":"open"}\n',
                encoding="utf-8",
            )
            tasks, _ = _read_workgraph_tasks(repo)
            assert tasks["t1"]["description"] == "fallback desc"

    def test_uses_dependencies_key_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True)
            (wg / "graph.jsonl").write_text(
                '{"type":"task","id":"t1","title":"T","status":"open","dependencies":["dep1"]}\n',
                encoding="utf-8",
            )
            tasks, _ = _read_workgraph_tasks(repo)
            assert tasks["t1"]["after"] == ["dep1"]


# ---------------------------------------------------------------------------
# _build_dependents
# ---------------------------------------------------------------------------

class TestBuildDependents:
    def test_basic_dependents(self) -> None:
        tasks = {
            "a": {"id": "a", "after": []},
            "b": {"id": "b", "after": ["a"]},
            "c": {"id": "c", "after": ["a"]},
        }
        deps, missing = _build_dependents(tasks)
        assert "a" in deps
        assert set(deps["a"]) == {"b", "c"}
        assert missing == 0

    def test_missing_references(self) -> None:
        tasks = {
            "a": {"id": "a", "after": ["nonexistent"]},
        }
        deps, missing = _build_dependents(tasks)
        assert missing == 1
        assert "nonexistent" in deps
        assert deps["nonexistent"] == ["a"]

    def test_empty_tasks(self) -> None:
        deps, missing = _build_dependents({})
        assert deps == {}
        assert missing == 0


# ---------------------------------------------------------------------------
# _downstream_has
# ---------------------------------------------------------------------------

class TestDownstreamHas:
    def _build_chain(self) -> tuple[dict, dict]:
        """Build: a -> b -> c -> d, where d is a test task."""
        tasks = {
            "a": {"id": "a", "title": "Implement feature", "after": []},
            "b": {"id": "b", "title": "Code review", "after": ["a"]},
            "c": {"id": "c", "title": "Prepare release", "after": ["b"]},
            "d": {"id": "d", "title": "Integration test", "after": ["c"]},
        }
        dependents, _ = _build_dependents(tasks)
        return tasks, dependents

    def test_finds_downstream_match(self) -> None:
        tasks, dependents = self._build_chain()
        assert _downstream_has(
            start_task_id="a",
            tasks=tasks,
            dependents=dependents,
            predicate=_is_integration_task,
            max_depth=4,
        ) is True

    def test_respects_max_depth(self) -> None:
        tasks, dependents = self._build_chain()
        # d is 3 hops from a; with max_depth=2 we should NOT find it
        assert _downstream_has(
            start_task_id="a",
            tasks=tasks,
            dependents=dependents,
            predicate=_is_integration_task,
            max_depth=2,
        ) is False

    def test_no_match_returns_false(self) -> None:
        tasks, dependents = self._build_chain()
        assert _downstream_has(
            start_task_id="a",
            tasks=tasks,
            dependents=dependents,
            predicate=_is_e2e_task,
            max_depth=10,
        ) is False

    def test_handles_cycle_without_infinite_loop(self) -> None:
        tasks = {
            "x": {"id": "x", "title": "Task X", "after": ["y"]},
            "y": {"id": "y", "title": "Task Y", "after": ["x"]},
        }
        dependents, _ = _build_dependents(tasks)
        # Should terminate without infinite recursion
        result = _downstream_has(
            start_task_id="x",
            tasks=tasks,
            dependents=dependents,
            predicate=_is_e2e_task,
            max_depth=10,
        )
        assert result is False


# ---------------------------------------------------------------------------
# _fingerprint
# ---------------------------------------------------------------------------

class TestFingerprint:
    def test_deterministic(self) -> None:
        a = _fingerprint(["repo", "task-1", "category"])
        b = _fingerprint(["repo", "task-1", "category"])
        assert a == b

    def test_different_inputs_differ(self) -> None:
        a = _fingerprint(["repo", "task-1", "cat-a"])
        b = _fingerprint(["repo", "task-1", "cat-b"])
        assert a != b

    def test_case_insensitive(self) -> None:
        a = _fingerprint(["Repo", "Task-1"])
        b = _fingerprint(["repo", "task-1"])
        assert a == b

    def test_strips_whitespace(self) -> None:
        a = _fingerprint(["  repo  ", "task"])
        b = _fingerprint(["repo", "task"])
        assert a == b

    def test_handles_none_parts(self) -> None:
        result = _fingerprint([None, "task"])
        assert isinstance(result, str)
        assert len(result) == 40  # SHA1 hex


# ---------------------------------------------------------------------------
# _planning_prompt
# ---------------------------------------------------------------------------

class TestPlanningPrompt:
    def test_includes_repo_and_finding_details(self) -> None:
        cfg = _normalize_cfg(None)
        finding = {
            "fingerprint": "abc123",
            "category": "missing-intervening-tests",
            "severity": "high",
            "evidence": "task=impl-auth",
            "recommendation": "add integration tests",
        }
        prompt = _planning_prompt("my-repo", finding, cfg)
        assert "my-repo" in prompt
        assert "abc123" in prompt
        assert "high" in prompt
        assert "missing-intervening-tests" in prompt
        assert "task=impl-auth" in prompt
        assert "double-shot-latte" in prompt


# ---------------------------------------------------------------------------
# _map_severity
# ---------------------------------------------------------------------------

class TestMapSeverity:
    def test_all_mappings(self) -> None:
        assert _map_severity({"severity": "critical"}) == "critical"
        assert _map_severity({"severity": "high"}) == "error"
        assert _map_severity({"severity": "medium"}) == "warning"
        assert _map_severity({"severity": "low"}) == "info"

    def test_unknown_defaults_to_info(self) -> None:
        assert _map_severity({"severity": "banana"}) == "info"
        assert _map_severity({}) == "info"


# ---------------------------------------------------------------------------
# run_workgraph_plan_review — expanded scenarios
# ---------------------------------------------------------------------------

class TestRunWorkgraphPlanReview:
    def test_disabled_policy_returns_early(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"enabled": False},
            )
            assert report["enabled"] is False
            assert report["summary"]["findings_total"] == 0
            assert report["summary"]["narrative"] == "plandrift disabled by policy"
            assert report["findings"] == []

    def test_missing_workgraph_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
            )
            assert len(report["errors"]) > 0
            assert "missing" in report["errors"][0]

    def test_missing_intervening_tests_finding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement new feature", status="in-progress", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"require_integration_tests": True, "require_e2e_tests": False},
            )
            categories = {f["category"] for f in report["findings"]}
            assert "missing-intervening-tests" in categories
            # In-progress tasks get "high" severity
            for f in report["findings"]:
                if f["category"] == "missing-intervening-tests":
                    assert f["severity"] == "high"

    def test_no_finding_when_integration_test_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
                _make_task("int-test", "Integration test for feature", status="open", after=["impl-1"], tags=["test", "integration"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": True,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": False,
                },
            )
            categories = {f["category"] for f in report["findings"]}
            assert "missing-intervening-tests" not in categories

    def test_dependency_integrity_gap(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("t1", "Task one", after=["nonexistent-dep"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": False,
                },
            )
            categories = {f["category"] for f in report["findings"]}
            assert "dependency-integrity-gap" in categories

    def test_dependency_integrity_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [_make_task("t1", "Task")])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                repo_snapshot={"blocked_open": 5, "missing_dependencies": 0},
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": False,
                },
            )
            categories = {f["category"] for f in report["findings"]}
            assert "dependency-integrity-gap" in categories

    def test_continuation_bridge_gap_high_severity_when_no_ready(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Build something", status="in-progress", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": True,
                },
            )
            cont_findings = [f for f in report["findings"] if f["category"] == "continuation-bridge-gap"]
            assert len(cont_findings) >= 1
            assert cont_findings[0]["severity"] == "high"

    def test_continuation_bridge_gap_medium_when_ready_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Build something", status="in-progress", tags=["feature"]),
                _make_task("next-task", "Next task", status="ready"),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": True,
                },
            )
            cont_findings = [f for f in report["findings"] if f["category"] == "continuation-bridge-gap"]
            assert len(cont_findings) >= 1
            assert cont_findings[0]["severity"] == "medium"

    def test_session_hook_missing_finding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Build feature", status="in-progress", tags=["feature"]),
            ])
            # No handlers/session-start.sh
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": True,
                },
            )
            categories = {f["category"] for f in report["findings"]}
            assert "session-driver-hook-missing" in categories

    def test_no_session_hook_finding_when_hook_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Build feature", status="in-progress", tags=["feature"]),
            ])
            handlers = repo / ".workgraph" / "handlers"
            handlers.mkdir(parents=True, exist_ok=True)
            (handlers / "session-start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": True,
                },
            )
            categories = {f["category"] for f in report["findings"]}
            assert "session-driver-hook-missing" not in categories

    def test_e2e_check_for_web_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Make it look like a web repo
            (repo / "package.json").write_text("{}", encoding="utf-8")
            (repo / "next.config.mjs").write_text("", encoding="utf-8")
            _write_graph(repo, [
                _make_task("impl-ui", "Implement UI feature", status="open", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": True,
                    "require_e2e_tests": True,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": False,
                },
            )
            # Should detect missing e2e tests for web repo
            evidence_strs = [f["evidence"] for f in report["findings"] if f["category"] == "missing-intervening-tests"]
            assert any("e2e" in e for e in evidence_strs)

    def test_no_e2e_finding_for_non_web_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="open", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": False,
                    "require_e2e_tests": True,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": False,
                },
            )
            # Not a web repo, so no e2e findings even with require_e2e_tests=True
            evidence_strs = [f["evidence"] for f in report["findings"] if f["category"] == "missing-intervening-tests"]
            assert not any("e2e" in e for e in evidence_strs)

    def test_findings_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": True,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": False,
                    "require_continuation_edges": False,
                },
            )
            fingerprints = [f["fingerprint"] for f in report["findings"]]
            assert len(fingerprints) == len(set(fingerprints)), "Findings should have unique fingerprints"

    def test_at_risk_when_critical_findings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={
                    "require_integration_tests": True,
                    "require_e2e_tests": False,
                },
            )
            summary = report["summary"]
            # high findings present => at_risk
            if summary["high"] > 0:
                assert summary["at_risk"] is True

    def test_model_contract_structure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [_make_task("t1", "Task")])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
            )
            mc = report["model_contract"]
            assert mc["decision_owner"] == "model"
            assert "required_outputs" in mc
            assert isinstance(mc["required_outputs"], list)
            assert "dependency_updates" in mc["required_outputs"]
            assert "prompt_seed" in mc

    def test_top_findings_have_model_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
            ])
            report = run_workgraph_plan_review(
                repo_name="test",
                repo_path=repo,
                policy_cfg={"require_integration_tests": True, "require_e2e_tests": False},
            )
            for f in report["top_findings"]:
                assert "model_prompt" in f
                assert isinstance(f["model_prompt"], str)
                assert len(f["model_prompt"]) > 0


# ---------------------------------------------------------------------------
# run_as_lane — expanded scenarios
# ---------------------------------------------------------------------------

class TestRunAsLaneExpanded:
    def test_findings_have_correct_severity_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
                _make_task("test-1", "Test the feature", status="open", after=["impl-1"], tags=["test"]),
            ])
            result = run_as_lane(repo)
            assert isinstance(result, LaneResult)
            assert result.lane == "plandrift"
            # Verify severity values are from the lane contract vocabulary
            valid_severities = {"info", "warning", "error", "critical"}
            for f in result.findings:
                assert f.severity in valid_severities, f"Unexpected severity: {f.severity}"

    def test_clean_workgraph_no_findings(self) -> None:
        """A fully gated workgraph with no drift should produce zero findings."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # Build a "clean" graph: implementation -> integration test -> loopback, plus continuation
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
                _make_task("int-test", "Integration test feature", status="open", after=["impl-1"], tags=["test", "integration"]),
                _make_task("recovery", "Loopback on failure", status="open", after=["int-test"], tags=["loopback"]),
                _make_task("cont-1", "Continuation handoff", status="open", tags=["continuation"]),
            ])
            # Install the session hook
            handlers = repo / ".workgraph" / "handlers"
            handlers.mkdir(parents=True, exist_ok=True)
            (handlers / "session-start.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            result = run_as_lane(repo)
            assert result.exit_code == 0
            assert len(result.findings) == 0

    def test_summary_contains_narrative(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
            ])
            result = run_as_lane(repo)
            assert "plandrift reviewed" in result.summary

    def test_finding_tags_include_category(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [
                _make_task("impl-1", "Implement feature", status="in-progress", tags=["feature"]),
            ])
            result = run_as_lane(repo)
            for f in result.findings:
                assert len(f.tags) > 0, "Each finding should have at least one tag"


# ---------------------------------------------------------------------------
# emit_plan_review_tasks — no-wg scenario (no mocks)
# ---------------------------------------------------------------------------

class TestEmitPlanReviewTasksNoWg:
    def test_missing_workgraph_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            report = {"recommended_reviews": [{"fingerprint": "abc", "severity": "high", "category": "test"}]}
            out = emit_plan_review_tasks(repo_path=repo, report=report, max_tasks=1)
            assert len(out["errors"]) > 0
            assert "missing" in out["errors"][0]

    def test_empty_reviews_list(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph").mkdir()
            report = {"recommended_reviews": []}
            out = emit_plan_review_tasks(repo_path=repo, report=report, max_tasks=5)
            assert out["attempted"] == 0
            assert out["created"] == 0

    def test_skips_findings_without_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph").mkdir()
            report = {"recommended_reviews": [{"severity": "high", "category": "test"}]}
            out = emit_plan_review_tasks(repo_path=repo, report=report, max_tasks=5)
            assert out["skipped"] == 1
            assert out["attempted"] == 0
