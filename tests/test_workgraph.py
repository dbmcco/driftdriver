# ABOUTME: Tests for workgraph loading and directory discovery.
# ABOUTME: Covers happy path, filtering, and missing-id guard.
from __future__ import annotations

import pytest

from driftdriver.workgraph import find_workgraph_dir, load_workgraph


def test_load_workgraph_happy_path(tmp_path):
    graph = tmp_path / ".workgraph" / "graph.jsonl"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"type":"task","id":"t1","title":"Test task","status":"open"}\n')
    result = load_workgraph(tmp_path / ".workgraph")
    assert "t1" in result.tasks
    assert result.tasks["t1"]["title"] == "Test task"


def test_load_workgraph_skips_non_tasks(tmp_path):
    graph = tmp_path / ".workgraph" / "graph.jsonl"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"type":"meta","version":"1"}\n{"type":"task","id":"t1","title":"T"}\n')
    result = load_workgraph(tmp_path / ".workgraph")
    assert len(result.tasks) == 1


def test_load_workgraph_skips_missing_id(tmp_path):
    graph = tmp_path / ".workgraph" / "graph.jsonl"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"type":"task","title":"No ID"}\n{"type":"task","id":"t1","title":"T"}\n')
    result = load_workgraph(tmp_path / ".workgraph")
    assert "None" not in result.tasks
    assert "t1" in result.tasks


def test_find_workgraph_dir_explicit(tmp_path):
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    (wg / "graph.jsonl").touch()
    assert find_workgraph_dir(tmp_path) == wg


def test_find_workgraph_dir_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_workgraph_dir(tmp_path)
