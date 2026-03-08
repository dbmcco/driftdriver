# ABOUTME: Tests that all built-in bundle TOML files load without errors.
# ABOUTME: Validates bundle IDs, required fields, and task template structure.

from __future__ import annotations

from pathlib import Path

from driftdriver.bundles import load_bundles_from_dir, parameterize_bundle


BUNDLES_DIR = Path(__file__).resolve().parent.parent / "driftdriver" / "bundles"


def test_all_builtin_bundles_load():
    bundles = load_bundles_from_dir(BUNDLES_DIR)
    assert len(bundles) >= 8  # scope-drift + 7 new ones
    ids = {b.id for b in bundles}
    assert "scope-drift" in ids
    assert "missing-intervening-tests" in ids
    assert "scaffold-workgraph" in ids


def test_all_builtin_bundles_have_tasks():
    bundles = load_bundles_from_dir(BUNDLES_DIR)
    for b in bundles:
        assert len(b.tasks) >= 1, f"Bundle {b.id} has no tasks"
        assert len(b.finding_kinds) >= 1, f"Bundle {b.id} has no finding_kinds"


def test_all_builtin_bundles_parameterize():
    bundles = load_bundles_from_dir(BUNDLES_DIR)
    context = {
        "finding_id": "test-finding-123",
        "task_title": "Test Task",
        "evidence": "something drifted",
        "file": "src/main.py",
        "repo_name": "test-repo",
    }
    for b in bundles:
        instance = parameterize_bundle(b, context)
        assert instance.bundle_id == b.id
        assert len(instance.tasks) == len(b.tasks)
        for task in instance.tasks:
            assert "{finding_id}" not in task["task_id"], f"Unresolved template in {b.id}"
