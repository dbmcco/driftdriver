# ABOUTME: Tests for the zombie process reaper that kills stale claude --print processes.
# ABOUTME: Verifies kill/skip logic, coordinator protection, age filtering, and logging.

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from driftdriver.reaper import (
    ProcessInfo,
    ReaperResult,
    classify_process,
    parse_claude_processes,
    reap_zombies,
    read_reaper_status,
)


def _write_graph(repo: Path, tasks: list[dict]) -> None:
    wg_dir = repo / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in tasks:
        row = {
            "kind": "task",
            "id": task["id"],
            "title": task.get("title", task["id"]),
            "status": task.get("status", "open"),
        }
        rows.append(json.dumps(row))
    (wg_dir / "graph.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


class TestParseClaudeProcesses:
    def test_extracts_pid_and_task_id(self):
        ps_line = "12345 300 /usr/bin/claude --print --task build-foo --output-format stream-json"
        procs = parse_claude_processes(
            [ps_line], min_age_seconds=0
        )
        assert len(procs) == 1
        assert procs[0].pid == 12345
        assert procs[0].task_id == "build-foo"

    def test_skips_coordinator_with_input_format_stream_json(self):
        ps_line = "12345 300 /usr/bin/claude --print --input-format stream-json --task coord-1"
        procs = parse_claude_processes(
            [ps_line], min_age_seconds=0
        )
        assert len(procs) == 0

    def test_skips_process_under_max_age(self):
        ps_line = "12345 60 /usr/bin/claude --print --task build-foo"
        procs = parse_claude_processes(
            [ps_line], min_age_seconds=14400  # 4 hours
        )
        assert len(procs) == 0

    def test_includes_process_over_max_age(self):
        ps_line = "12345 50000 /usr/bin/claude --print --task build-foo"
        procs = parse_claude_processes(
            [ps_line], min_age_seconds=14400
        )
        assert len(procs) == 1

    def test_no_task_flag_still_parsed(self):
        ps_line = "12345 50000 /usr/bin/claude --print"
        procs = parse_claude_processes(
            [ps_line], min_age_seconds=0
        )
        assert len(procs) == 1
        assert procs[0].task_id is None


class TestClassifyProcess:
    def test_done_task_gets_killed(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "done"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "kill"

    def test_failed_task_gets_killed(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "failed"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "kill"

    def test_abandoned_task_gets_killed(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "abandoned"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "kill"

    def test_in_progress_task_is_skipped(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "in-progress"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "skip"

    def test_open_task_is_skipped(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "open"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "skip"

    def test_unknown_task_id_gets_killed(self, tmp_path):
        _write_graph(tmp_path, [{"id": "other-task", "status": "open"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "kill"

    def test_no_workgraph_dir_gets_killed(self, tmp_path):
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id="build-foo", cmdline="claude --print --task build-foo")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "kill"

    def test_no_task_id_gets_killed(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "open"}])
        proc = ProcessInfo(pid=1234, elapsed_seconds=50000, task_id=None, cmdline="claude --print")
        action = classify_process(proc, tmp_path / ".workgraph")
        assert action == "kill"


class TestReapZombies:
    def test_kills_done_task_and_logs(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "done"}])
        ps_output = ["1234 50000 /usr/bin/claude --print --task build-foo"]
        log_path = tmp_path / ".workgraph" / "zombie-reaper.log"

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process") as mock_kill:
            result = reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=0,
                dry_run=False,
            )

        assert result.killed == 1
        assert result.skipped == 0
        mock_kill.assert_called_once_with(1234)
        assert log_path.exists()
        log_content = log_path.read_text()
        assert "1234" in log_content
        assert "build-foo" in log_content

    def test_skips_in_progress_task(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "in-progress"}])
        ps_output = ["1234 50000 /usr/bin/claude --print --task build-foo"]

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process") as mock_kill:
            result = reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=0,
                dry_run=False,
            )

        assert result.killed == 0
        assert result.skipped == 1
        mock_kill.assert_not_called()

    def test_coordinator_never_killed(self, tmp_path):
        _write_graph(tmp_path, [{"id": "coord-1", "status": "done"}])
        ps_output = ["1234 50000 /usr/bin/claude --print --input-format stream-json --task coord-1"]

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process") as mock_kill:
            result = reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=0,
                dry_run=False,
            )

        assert result.killed == 0
        mock_kill.assert_not_called()

    def test_young_process_skipped(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "done"}])
        ps_output = ["1234 60 /usr/bin/claude --print --task build-foo"]

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process") as mock_kill:
            result = reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=14400,
                dry_run=False,
            )

        assert result.killed == 0
        mock_kill.assert_not_called()

    def test_unknown_task_killed_after_max_age(self, tmp_path):
        _write_graph(tmp_path, [{"id": "other-task", "status": "open"}])
        ps_output = ["1234 50000 /usr/bin/claude --print --task unknown-task"]

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process") as mock_kill:
            result = reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=0,
                dry_run=False,
            )

        assert result.killed == 1
        mock_kill.assert_called_once_with(1234)

    def test_log_entry_written_on_each_kill(self, tmp_path):
        _write_graph(tmp_path, [
            {"id": "task-a", "status": "done"},
            {"id": "task-b", "status": "failed"},
        ])
        ps_output = [
            "1001 50000 /usr/bin/claude --print --task task-a",
            "1002 50000 /usr/bin/claude --print --task task-b",
        ]
        log_path = tmp_path / ".workgraph" / "zombie-reaper.log"

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process"):
            result = reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=0,
                dry_run=False,
            )

        assert result.killed == 2
        log_lines = [l for l in log_path.read_text().splitlines() if l.strip()]
        assert len(log_lines) == 2
        assert "1001" in log_lines[0]
        assert "1002" in log_lines[1]


class TestReaperStatus:
    def test_status_no_log(self, tmp_path):
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        status = read_reaper_status(wg_dir)
        assert status["last_run"] is None
        assert status["total_killed"] == 0

    def test_status_after_run(self, tmp_path):
        _write_graph(tmp_path, [{"id": "build-foo", "status": "done"}])
        ps_output = ["1234 50000 /usr/bin/claude --print --task build-foo"]

        with patch("driftdriver.reaper._get_ps_output", return_value=ps_output), \
             patch("driftdriver.reaper._kill_process"):
            reap_zombies(
                wg_dir=tmp_path / ".workgraph",
                min_age_seconds=0,
                dry_run=False,
            )

        status = read_reaper_status(tmp_path / ".workgraph")
        assert status["last_run"] is not None
        assert status["total_killed"] == 1
        assert status["total_skipped"] >= 0
