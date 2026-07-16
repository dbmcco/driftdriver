# ABOUTME: Tests for workgraph loading and directory discovery.
# ABOUTME: Covers happy path, filtering, and missing-id guard.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from driftdriver.workgraph import (
    WorkgraphDirectoryConflictError,
    find_workgraph_dir,
    load_workgraph,
    parse_workgraph_status,
    resolve_workgraph_dir,
)


def test_parse_workgraph_status_requires_expected_section_types():
    status = {
        "service": {},
        "coordinator": {"executor": "pi", "model": "zai/glm-5.2"},
        "agents": {},
        "tasks": {},
        "recent": [],
    }
    assert parse_workgraph_status(status) == status

    for section, value in (
        ("coordinator", []),
        ("agents", []),
        ("tasks", []),
        ("recent", {}),
    ):
        invalid = {**status, section: value}
        with pytest.raises(ValueError, match="invalid types"):
            parse_workgraph_status(invalid)


def test_parse_workgraph_status_rejects_coordinator_without_required_keys():
    status = {
        "service": {},
        "coordinator": {"executor": "pi"},
        "agents": {},
        "tasks": {},
        "recent": [],
    }
    with pytest.raises(ValueError, match="executor/model"):
        parse_workgraph_status(status)


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


def test_find_workgraph_dir_walk_up(tmp_path):
    """Test that find_workgraph_dir walks up from a subdirectory."""
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    (wg / "graph.jsonl").touch()
    subdir = tmp_path / "src" / "deep"
    subdir.mkdir(parents=True)
    # find_workgraph_dir should find .workgraph by walking up
    found = find_workgraph_dir(subdir)
    assert found == wg


def test_find_workgraph_dir_does_not_cross_git_root(tmp_path):
    parent_wg = tmp_path / ".workgraph"
    parent_wg.mkdir()
    (parent_wg / "graph.jsonl").touch()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".workgraph" / "service").mkdir(parents=True)

    with pytest.raises(FileNotFoundError):
        find_workgraph_dir(repo)


def test_load_workgraph_kind_field(tmp_path):
    """Test that 'kind' field works as alternative to 'type' (wg CLI uses 'kind')."""
    graph = tmp_path / ".workgraph" / "graph.jsonl"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        '{"kind":"task","id":"k1","title":"Kind task","status":"open"}\n'
        '{"type":"task","id":"t1","title":"Type task","status":"open"}\n'
        '{"kind":"log","id":"l1","title":"Not a task"}\n'
    )
    result = load_workgraph(tmp_path / ".workgraph")
    assert "k1" in result.tasks
    assert "t1" in result.tasks
    assert "l1" not in result.tasks
    assert result.tasks["k1"]["title"] == "Kind task"


def test_load_workgraph_malformed_json(tmp_path):
    graph = tmp_path / ".workgraph" / "graph.jsonl"
    graph.parent.mkdir(parents=True)
    graph.write_text(
        "not json at all\n"
        '{"type":"task","id":"t1","title":"Valid task","status":"open"}\n'
        "{broken json\n"
    )
    result = load_workgraph(tmp_path / ".workgraph")
    assert "t1" in result.tasks
    assert len(result.tasks) == 1


class GraphDirectoryResolutionTests(unittest.TestCase):
    def test_resolves_initialized_legacy_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            graph = repo / ".workgraph"
            graph.mkdir()
            (graph / "graph.jsonl").write_text("", encoding="utf-8")
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, graph)
            self.assertTrue(result.initialized)
            self.assertEqual(result.source, "legacy")

    def test_resolves_initialized_current_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            graph = repo / ".wg"
            graph.mkdir()
            (graph / "graph.jsonl").write_text("", encoding="utf-8")
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, graph)
            self.assertTrue(result.initialized)
            self.assertEqual(result.source, "current")

    def test_rejects_two_initialized_graphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            for name in (".workgraph", ".wg"):
                graph = repo / name
                graph.mkdir()
                (graph / "graph.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(
                WorkgraphDirectoryConflictError,
                r"\.workgraph.*\.wg",
            ):
                resolve_workgraph_dir(repo)

    def test_partial_legacy_directory_is_not_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            graph = repo / ".workgraph"
            graph.mkdir()
            (graph / "drift-policy.toml").write_text("", encoding="utf-8")
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, graph)
            self.assertFalse(result.initialized)
            self.assertEqual(result.source, "existing")

    def test_new_speedrift_repo_uses_legacy_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            result = resolve_workgraph_dir(repo)
            self.assertEqual(result.path, repo / ".workgraph")
            self.assertFalse(result.initialized)
            self.assertEqual(result.source, "default")
