# ABOUTME: Tests for bundle loading, validation, and parameterization.
# ABOUTME: Covers TOML parsing, template interpolation, and bundle registry.

from __future__ import annotations

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.bundles import (
    TaskTemplate,
    Bundle,
    BundleInstance,
    load_bundle,
    load_bundles_from_dir,
    parameterize_bundle,
)


def test_task_template_fields():
    t = TaskTemplate(
        id_template="{finding_id}-write-test",
        title_template="Write test for {task_title}",
        description_template="Cover {evidence}",
        tags=["drift", "attractor"],
        after=[],
        verify="pytest tests/ -x -q",
    )
    assert t.id_template == "{finding_id}-write-test"
    assert t.verify == "pytest tests/ -x -q"


def test_bundle_fields():
    b = Bundle(
        id="scope-drift",
        finding_kinds=["scope_drift", "scope-drift"],
        description="Fix scope drift",
        tasks=[],
    )
    assert b.id == "scope-drift"
    assert "scope_drift" in b.finding_kinds


def test_load_bundle_from_toml():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "test-bundle.toml"
        p.write_text("""
[bundle]
id = "test-bundle"
finding_kinds = ["test_finding"]
description = "A test bundle"

[[tasks]]
id_template = "{finding_id}-fix"
title_template = "Fix {task_title}"
description_template = "Address {evidence}"
tags = ["drift"]

[[tasks]]
id_template = "{finding_id}-verify"
title_template = "Verify {task_title}"
after = ["{finding_id}-fix"]
verify = "pytest -x"
""")
        bundle = load_bundle(p)
        assert bundle.id == "test-bundle"
        assert len(bundle.tasks) == 2
        assert bundle.tasks[1].after == ["{finding_id}-fix"]
        assert bundle.tasks[1].verify == "pytest -x"


def test_load_bundle_missing_fields():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.toml"
        p.write_text("[bundle]\nid = 'bad'\n")
        with pytest.raises(ValueError, match="finding_kinds"):
            load_bundle(p)


def test_load_bundles_from_dir():
    with TemporaryDirectory() as tmp:
        (Path(tmp) / "a.toml").write_text("""
[bundle]
id = "a"
finding_kinds = ["a_finding"]
description = "Bundle A"

[[tasks]]
id_template = "{finding_id}-fix"
title_template = "Fix"
""")
        (Path(tmp) / "b.toml").write_text("""
[bundle]
id = "b"
finding_kinds = ["b_finding"]
description = "Bundle B"

[[tasks]]
id_template = "{finding_id}-fix"
title_template = "Fix"
""")
        (Path(tmp) / "not-toml.txt").write_text("ignore me")
        bundles = load_bundles_from_dir(Path(tmp))
        assert len(bundles) == 2
        ids = {b.id for b in bundles}
        assert ids == {"a", "b"}


def test_parameterize_bundle():
    bundle = Bundle(
        id="scope-drift",
        finding_kinds=["scope_drift"],
        description="Fix scope drift",
        tasks=[
            TaskTemplate(
                id_template="{finding_id}-update-contract",
                title_template="Update contract for {task_title}",
                description_template="Scope drifted: {evidence}",
                tags=["drift", "attractor"],
            ),
            TaskTemplate(
                id_template="{finding_id}-verify",
                title_template="Verify {task_title}",
                after=["{finding_id}-update-contract"],
                verify="pytest -x",
            ),
        ],
    )
    context = {
        "finding_id": "coredrift-scope-task42",
        "task_title": "Implement auth",
        "evidence": "touched files outside contract scope",
        "file": "src/auth.py",
        "repo_name": "paia-shell",
    }
    instance = parameterize_bundle(bundle, context)
    assert instance.bundle_id == "scope-drift"
    assert instance.finding_id == "coredrift-scope-task42"
    assert len(instance.tasks) == 2
    assert instance.tasks[0]["task_id"] == "coredrift-scope-task42-update-contract"
    assert instance.tasks[0]["title"] == "Update contract for Implement auth"
    assert instance.tasks[1]["after"] == ["coredrift-scope-task42-update-contract"]
    assert instance.confidence == "high"


def test_parameterize_bundle_missing_context_key():
    bundle = Bundle(
        id="test",
        finding_kinds=["x"],
        description="test",
        tasks=[
            TaskTemplate(
                id_template="{finding_id}-fix",
                title_template="Fix {nonexistent_key}",
            ),
        ],
    )
    instance = parameterize_bundle(bundle, {"finding_id": "f1"})
    # Missing keys should be left as-is (not crash)
    assert "{nonexistent_key}" in instance.tasks[0]["title"]
