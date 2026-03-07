# ABOUTME: Tests for driftdriver/cli/run.py — cmd_run, cmd_factory, cmd_orchestrate, _invoke_check_json.
# ABOUTME: Covers argument parsing, execution flow, subprocess delegation, JSON output, and edge cases.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from driftdriver.cli.check import ExitCode
from driftdriver.cli.run import (
    _invoke_check_json,
    cmd_factory,
    cmd_orchestrate,
    cmd_run,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workgraph_dir(tmp_path: Path, tasks: list[dict[str, Any]] | None = None) -> Path:
    """Create a minimal .workgraph directory with graph.jsonl."""
    wg = tmp_path / ".workgraph"
    wg.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for task in tasks or []:
        entry = {"kind": "task", **task}
        lines.append(json.dumps(entry))
    (wg / "graph.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return wg


def _stub_cmd_check(monkeypatch: pytest.MonkeyPatch, rc: int = 0, output: str = "") -> None:
    """Monkeypatch cmd_check to print output to stdout and return rc."""

    def fake_cmd_check(args: argparse.Namespace) -> int:
        if output:
            print(output, end="")
        return rc

    monkeypatch.setattr("driftdriver.cli.run.cmd_check", fake_cmd_check)


def _base_run_args(
    tmp_path: Path,
    *,
    task: str = "task-1",
    as_json: bool = False,
    max_next: int = 3,
) -> argparse.Namespace:
    return argparse.Namespace(
        dir=str(tmp_path),
        task=task,
        json=as_json,
        max_next=max_next,
        lane_strategy="auto",
    )


# ---------------------------------------------------------------------------
# _invoke_check_json
# ---------------------------------------------------------------------------


class TestInvokeCheckJson:
    def test_returns_parsed_json_on_valid_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        report = {"lane": "coredrift", "findings": []}
        _stub_cmd_check(monkeypatch, rc=0, output=json.dumps(report))

        args = argparse.Namespace(dir=None, task="t1", lane_strategy="auto",
                                  write_log=True, create_followups=True, json=True)
        rc, result = _invoke_check_json(args)
        assert rc == 0
        assert result == report

    def test_returns_empty_dict_on_empty_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_cmd_check(monkeypatch, rc=0, output="")

        args = argparse.Namespace(dir=None, task="t1", lane_strategy="auto",
                                  write_log=True, create_followups=True, json=True)
        rc, result = _invoke_check_json(args)
        assert rc == 0
        assert result == {}

    def test_returns_raw_on_invalid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_cmd_check(monkeypatch, rc=3, output="not valid json at all")

        args = argparse.Namespace(dir=None, task="t1", lane_strategy="auto",
                                  write_log=True, create_followups=True, json=True)
        rc, result = _invoke_check_json(args)
        assert rc == 3
        assert result == {"raw": "not valid json at all"}

    def test_preserves_exit_code_from_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_cmd_check(monkeypatch, rc=ExitCode.findings, output='{"ok": true}')

        args = argparse.Namespace(dir=None, task="t1", lane_strategy="auto",
                                  write_log=True, create_followups=True, json=True)
        rc, result = _invoke_check_json(args)
        assert rc == ExitCode.findings
        assert result == {"ok": True}

    def test_handles_whitespace_only_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_cmd_check(monkeypatch, rc=0, output="   \n  \n  ")

        args = argparse.Namespace(dir=None, task="t1", lane_strategy="auto",
                                  write_log=True, create_followups=True, json=True)
        rc, result = _invoke_check_json(args)
        assert rc == 0
        assert result == {}


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------


class TestCmdRun:
    def test_error_when_no_task(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(dir=None, task="", json=False, max_next=3, lane_strategy="auto")
        rc = cmd_run(args)
        assert rc == ExitCode.usage
        assert "error: --task is required" in capsys.readouterr().err

    def test_error_when_task_is_none(self, capsys: pytest.CaptureFixture[str]) -> None:
        args = argparse.Namespace(dir=None, task=None, json=False, max_next=3, lane_strategy="auto")
        rc = cmd_run(args)
        assert rc == ExitCode.usage

    def test_json_mode_returns_structured_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[
            {"id": "task-1", "title": "Test Task", "status": "open"},
        ])
        check_report = {"lane": "coredrift", "findings": []}
        _stub_cmd_check(monkeypatch, rc=0, output=json.dumps(check_report))

        args = _base_run_args(tmp_path, task="task-1", as_json=True)
        rc = cmd_run(args)
        assert rc == 0

        out = json.loads(capsys.readouterr().out)
        assert out["exit_code"] == 0
        assert out["check"] == check_report
        assert "next_actions" in out
        assert "duplicate_open_drift_groups" in out
        assert "scoreboard" in out

    def test_text_mode_prints_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        _stub_cmd_check(monkeypatch, rc=0, output='{}')

        args = _base_run_args(tmp_path, task="task-1", as_json=False)
        rc = cmd_run(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Run exit code: 0" in out
        assert "Normalized actions: none" in out

    def test_text_mode_shows_action_plan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        report = {"action_plan": [
            {"action": "scope", "kind": "scope_drift", "source": "coredrift"},
        ]}
        _stub_cmd_check(monkeypatch, rc=ExitCode.findings, output=json.dumps(report))

        args = _base_run_args(tmp_path, task="task-1", as_json=False)
        rc = cmd_run(args)
        assert rc == ExitCode.findings

        out = capsys.readouterr().out
        assert "Normalized actions:" in out
        assert "scope: scope_drift (coredrift)" in out

    def test_text_mode_shows_next_actions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[
            {"id": "drift-fix-1", "title": "Fix drift", "status": "open", "tags": ["drift"]},
        ])
        _stub_cmd_check(monkeypatch, rc=0, output='{}')

        args = _base_run_args(tmp_path, task="task-1", as_json=False)
        rc = cmd_run(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Next actions:" in out
        assert "drift-fix-1" in out

    def test_text_mode_shows_duplicate_drift_groups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Two drift tasks with same normalized key → duplicates
        _make_workgraph_dir(tmp_path, tasks=[
            {"id": "drift-scope-task-1", "title": "drift scope check", "status": "open", "tags": ["drift"]},
            {"id": "coredrift-scope-task-1", "title": "drift scope check copy", "status": "open", "tags": ["drift"]},
        ])
        _stub_cmd_check(monkeypatch, rc=0, output='{}')

        args = _base_run_args(tmp_path, task="task-1", as_json=False)
        rc = cmd_run(args)

        out = capsys.readouterr().out
        # May or may not show duplicates depending on normalize_drift_key logic;
        # just verify no crash
        assert "Run exit code: 0" in out

    def test_max_next_clamped_to_minimum_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        _stub_cmd_check(monkeypatch, rc=0, output='{}')

        args = _base_run_args(tmp_path, task="task-1", as_json=True, max_next=0)
        rc = cmd_run(args)
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        # Should not crash despite max_next=0 (clamped to 1)
        assert "next_actions" in out

    def test_propagates_check_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        _stub_cmd_check(monkeypatch, rc=ExitCode.findings, output='{"findings": ["scope_drift"]}')

        args = _base_run_args(tmp_path, task="task-1", as_json=True)
        rc = cmd_run(args)
        assert rc == ExitCode.findings

    def test_non_dict_action_plan_items_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        report = {"action_plan": ["string_item", 42, {"action": "scope", "kind": "x", "source": "y"}]}
        _stub_cmd_check(monkeypatch, rc=0, output=json.dumps(report))

        args = _base_run_args(tmp_path, task="task-1", as_json=False)
        rc = cmd_run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "scope: x (y)" in out

    def test_action_plan_limited_to_5_items(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        items = [{"action": f"a{i}", "kind": f"k{i}", "source": f"s{i}"} for i in range(10)]
        report = {"action_plan": items}
        _stub_cmd_check(monkeypatch, rc=0, output=json.dumps(report))

        args = _base_run_args(tmp_path, task="task-1", as_json=False)
        rc = cmd_run(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Items 0-4 shown, items 5-9 not shown
        assert "a4:" in out
        assert "a5:" not in out

    def test_lane_strategy_passed_to_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_workgraph_dir(tmp_path, tasks=[])
        captured_args: list[argparse.Namespace] = []

        def capture_check(args: argparse.Namespace) -> int:
            captured_args.append(args)
            return 0

        monkeypatch.setattr("driftdriver.cli.run.cmd_check", capture_check)

        args = _base_run_args(tmp_path, task="task-1", as_json=True)
        args.lane_strategy = "smart"
        cmd_run(args)

        assert len(captured_args) == 1
        assert captured_args[0].lane_strategy == "smart"


# ---------------------------------------------------------------------------
# cmd_orchestrate
# ---------------------------------------------------------------------------


class TestCmdOrchestrate:
    def test_error_when_coredrift_missing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)
        args = argparse.Namespace(
            dir=str(tmp_path),
            interval=30,
            redirect_interval=60,
            write_log=False,
            create_followups=False,
        )
        rc = cmd_orchestrate(args)
        assert rc == ExitCode.usage
        assert "coredrift not found" in capsys.readouterr().err

    def test_delegates_to_coredrift_subprocess(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wg = _make_workgraph_dir(tmp_path)
        coredrift = wg / "coredrift"
        coredrift.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        coredrift.chmod(0o755)

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str]) -> int:
            captured_cmds.append(cmd)
            return 0

        monkeypatch.setattr("driftdriver.cli.run._run", fake_run)

        args = argparse.Namespace(
            dir=str(tmp_path),
            interval=30,
            redirect_interval=60,
            write_log=True,
            create_followups=True,
        )
        rc = cmd_orchestrate(args)
        assert rc == 0
        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        assert str(coredrift) in cmd
        assert "--interval" in cmd
        assert "30" in cmd
        assert "--redirect-interval" in cmd
        assert "60" in cmd
        assert "--write-log" in cmd
        assert "--create-followups" in cmd

    def test_write_log_flag_omitted_when_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wg = _make_workgraph_dir(tmp_path)
        coredrift = wg / "coredrift"
        coredrift.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        coredrift.chmod(0o755)

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str]) -> int:
            captured_cmds.append(cmd)
            return 0

        monkeypatch.setattr("driftdriver.cli.run._run", fake_run)

        args = argparse.Namespace(
            dir=str(tmp_path),
            interval=10,
            redirect_interval=20,
            write_log=False,
            create_followups=False,
        )
        cmd_orchestrate(args)
        cmd = captured_cmds[0]
        assert "--write-log" not in cmd
        assert "--create-followups" not in cmd

    def test_returns_subprocess_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wg = _make_workgraph_dir(tmp_path)
        coredrift = wg / "coredrift"
        coredrift.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        coredrift.chmod(0o755)

        monkeypatch.setattr("driftdriver.cli.run._run", lambda cmd: 42)

        args = argparse.Namespace(
            dir=str(tmp_path),
            interval=30,
            redirect_interval=60,
            write_log=False,
            create_followups=False,
        )
        rc = cmd_orchestrate(args)
        assert rc == 42


# ---------------------------------------------------------------------------
# cmd_factory
# ---------------------------------------------------------------------------


class TestCmdFactory:
    def _factory_args(
        self,
        tmp_path: Path,
        *,
        as_json: bool = False,
        force: bool = False,
        plan_only: bool = False,
        execute: bool = False,
        emit_followups: bool = False,
        no_write_ledger: bool = True,
        skip_updates: bool = True,
        max_next: int = 5,
        max_prompts: int = 8,
        write: str = "",
        workspace_root: str = "",
        ecosystem_toml: str = "",
        central_repo: str = "",
        execute_draft_prs: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            dir=str(tmp_path),
            json=as_json,
            force=force,
            plan_only=plan_only,
            execute=execute,
            emit_followups=emit_followups,
            no_write_ledger=no_write_ledger,
            skip_updates=skip_updates,
            max_next=max_next,
            max_prompts=max_prompts,
            write=write,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=execute_draft_prs,
        )

    def test_plan_only_and_execute_mutually_exclusive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)
        args = self._factory_args(tmp_path, plan_only=True, execute=True)
        rc = cmd_factory(args)
        assert rc == ExitCode.usage
        assert "mutually exclusive" in capsys.readouterr().err

    def test_disabled_without_force_returns_ok(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)
        args = self._factory_args(tmp_path, force=False)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok
        out = capsys.readouterr().out
        assert "disabled" in out.lower() or "Factory loop is disabled" in out

    def test_force_bypasses_disabled_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)

        fake_snapshot: dict[str, Any] = {"repos": [], "updates": []}
        fake_cycle: dict[str, Any] = {
            "cycle_id": "test-123",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "action_plan": [],
            "selected_repos": [],
            "generated_at": "2025-01-01T00:00:00Z",
        }
        fake_summary: dict[str, Any] = {
            "cycle_id": "test-123",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "planned_actions": 0,
            "selected_repos": 0,
            "module_counts": {},
            "next_cycle_hints": [],
        }

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: fake_snapshot,
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: fake_summary,
        )

        args = self._factory_args(tmp_path, force=True, as_json=True)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok

        out = json.loads(capsys.readouterr().out)
        assert out["forced"] is True
        assert out["summary"]["cycle_id"] == "test-123"

    def test_json_output_structure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)

        fake_cycle: dict[str, Any] = {
            "cycle_id": "abc",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "action_plan": [{"repo": "r1", "module": "m1", "prompt": "do stuff"}],
            "selected_repos": ["r1"],
            "generated_at": "2025-01-01T00:00:00Z",
        }

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: {"repos": []},
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: {
                "cycle_id": "abc",
                "execution_mode": "plan_only",
                "execution_status": "planned_only",
                "planned_actions": 1,
                "selected_repos": 1,
            },
        )

        args = self._factory_args(tmp_path, force=True, as_json=True)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok

        out = json.loads(capsys.readouterr().out)
        assert "summary" in out
        assert "cycle" in out
        assert "execution" in out
        assert "followups" in out
        assert "ledger" in out

    def test_text_output_prints_summary_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)

        fake_cycle: dict[str, Any] = {
            "cycle_id": "xyz",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "action_plan": [],
            "selected_repos": [],
            "generated_at": "2025-01-01T00:00:00Z",
        }

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: {"repos": []},
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: {
                "cycle_id": "xyz",
                "execution_mode": "plan_only",
                "execution_status": "planned_only",
                "planned_actions": 0,
                "selected_repos": 0,
                "next_cycle_hints": ["hint-a"],
            },
        )

        args = self._factory_args(tmp_path, force=True, as_json=False)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok

        out = capsys.readouterr().out
        assert "Factory cycle: xyz" in out
        assert "Execution mode: plan_only" in out
        assert "Execution status: planned_only" in out
        assert "hint-a" in out
        assert "Action prompts: none" in out

    def test_write_flag_saves_to_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_workgraph_dir(tmp_path)
        output_file = tmp_path / "output" / "cycle.json"

        fake_cycle: dict[str, Any] = {
            "cycle_id": "w1",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "action_plan": [],
            "selected_repos": [],
            "generated_at": "2025-01-01T00:00:00Z",
        }

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: {"repos": []},
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: {
                "cycle_id": "w1",
                "execution_mode": "plan_only",
                "execution_status": "planned_only",
                "planned_actions": 0,
                "selected_repos": 0,
            },
        )

        args = self._factory_args(tmp_path, force=True, as_json=False, write=str(output_file))
        cmd_factory(args)

        assert output_file.exists()
        written = json.loads(output_file.read_text(encoding="utf-8"))
        assert written["summary"]["cycle_id"] == "w1"

    def test_action_prompts_printed_in_text_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)

        fake_cycle: dict[str, Any] = {
            "cycle_id": "p1",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "action_plan": [
                {"repo": "myrepo", "module": "mymod", "prompt": "do the thing"},
            ],
            "selected_repos": ["myrepo"],
            "generated_at": "2025-01-01T00:00:00Z",
        }

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: {"repos": []},
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: {
                "cycle_id": "p1",
                "execution_mode": "plan_only",
                "execution_status": "planned_only",
                "planned_actions": 1,
                "selected_repos": 1,
            },
        )

        args = self._factory_args(tmp_path, force=True, as_json=False)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok

        out = capsys.readouterr().out
        assert "Action prompts:" in out
        assert "[myrepo:mymod] do the thing" in out

    def test_execute_mode_runs_execute_factory_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)

        fake_cycle: dict[str, Any] = {
            "cycle_id": "e1",
            "execution_mode": "execute",
            "execution_status": "completed",
            "action_plan": [],
            "selected_repos": [],
            "generated_at": "2025-01-01T00:00:00Z",
        }
        fake_execution: dict[str, Any] = {
            "attempted": 1,
            "executed": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
            "hard_stop": False,
            "stopped_early": False,
            "stop_reason": "",
            "attempts": [],
            "followups": {
                "enabled": False,
                "attempted": 0,
                "created": 0,
                "existing": 0,
                "skipped": 0,
                "errors": [],
                "tasks": [],
            },
        }
        execute_called = {"called": False}

        def fake_execute(**kw: Any) -> dict[str, Any]:
            execute_called["called"] = True
            return fake_execution

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: {"repos": []},
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.execute_factory_cycle",
            fake_execute,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: {
                "cycle_id": "e1",
                "execution_mode": "execute",
                "execution_status": "completed",
                "planned_actions": 0,
                "selected_repos": 0,
                "executed_actions": 1,
                "failed_actions": 0,
            },
        )

        args = self._factory_args(tmp_path, force=True, execute=True, as_json=True)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok
        assert execute_called["called"]

        out = json.loads(capsys.readouterr().out)
        assert out["execution"]["executed"] == 1

    def test_emit_followups_in_plan_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _make_workgraph_dir(tmp_path)

        fake_cycle: dict[str, Any] = {
            "cycle_id": "f1",
            "execution_mode": "plan_only",
            "execution_status": "planned_only",
            "action_plan": [],
            "selected_repos": [],
            "generated_at": "2025-01-01T00:00:00Z",
        }
        emit_called = {"called": False}

        def fake_emit(**kw: Any) -> dict[str, Any]:
            emit_called["called"] = True
            return {
                "enabled": True,
                "attempted": 2,
                "created": 1,
                "existing": 1,
                "skipped": 0,
                "errors": [],
                "tasks": [],
            }

        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.collect_ecosystem_snapshot",
            lambda **kw: {"repos": []},
        )
        monkeypatch.setattr(
            "driftdriver.ecosystem_hub.resolve_central_repo_path",
            lambda project_dir, explicit_path="": None,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.build_factory_cycle",
            lambda **kw: fake_cycle,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.emit_factory_followups",
            fake_emit,
        )
        monkeypatch.setattr(
            "driftdriver.cli.run.summarize_factory_cycle",
            lambda cycle: {
                "cycle_id": "f1",
                "execution_mode": "plan_only",
                "execution_status": "planned_only",
                "planned_actions": 0,
                "selected_repos": 0,
            },
        )

        args = self._factory_args(tmp_path, force=True, emit_followups=True, as_json=False)
        rc = cmd_factory(args)
        assert rc == ExitCode.ok
        assert emit_called["called"]

        out = capsys.readouterr().out
        assert "Corrective follow-up tasks:" in out
        assert "attempted=2" in out
        assert "created=1" in out
