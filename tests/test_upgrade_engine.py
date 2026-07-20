# ABOUTME: Tests for the upgrade engine (discovery, state, idempotent apply).
from __future__ import annotations

import importlib.util
from pathlib import Path

from driftdriver.upgrade.engine import (
    Migration,
    apply_pending,
    load_migrations,
    read_state,
    write_state,
)

_MIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "driftdriver/upgrade/migrations/001_strip_ecosystem_hook.py"
)


def _real_block() -> str:
    spec = importlib.util.spec_from_file_location("m001_engine", _MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod._BLOCK


def _make_repo(root: Path, *, with_hook: bool = True) -> Path:
    h = root / ".workgraph/handlers"
    h.mkdir(parents=True, exist_ok=True)
    pre = "#!/usr/bin/env bash\nset -euo pipefail\nfi\n\n"
    post = "# Prime\nif command -v driftdriver >/dev/null 2>&1; then :\nfi\n"
    block = _real_block() if with_hook else ""
    (h / "session-start.sh").write_text(pre + block + post)
    return root


def test_load_migrations_includes_001_and_is_sorted():
    migs = load_migrations()
    ids = [m.id for m in migs]
    assert "001" in ids
    assert ids == sorted(ids)


def test_state_roundtrip(tmp_path):
    assert read_state(tmp_path) == {"applied": []}
    write_state(tmp_path, ["001", "002"])
    st = read_state(tmp_path)
    assert st["applied"] == ["001", "002"]


def test_state_corrupt_returns_empty(tmp_path):
    sp = tmp_path / ".workgraph/upgrade-state.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text("{not valid json")
    assert read_state(tmp_path) == {"applied": []}


def test_apply_pending_stamps_and_changes(tmp_path):
    _make_repo(tmp_path, with_hook=True)

    rep = apply_pending(tmp_path)

    assert "001" in rep.ran
    assert rep.changed_files
    assert "001" in read_state(tmp_path)["applied"]


def test_apply_pending_is_idempotent(tmp_path):
    _make_repo(tmp_path, with_hook=True)
    apply_pending(tmp_path)

    rep2 = apply_pending(tmp_path)

    assert rep2.ran == []
    assert "001" in rep2.skipped
    assert rep2.changed_files == []


def test_apply_pending_dry_run_does_not_stamp(tmp_path):
    _make_repo(tmp_path, with_hook=True)

    rep = apply_pending(tmp_path, dry_run=True)

    assert rep.dry_run is True
    assert rep.changed_files  # would change
    assert read_state(tmp_path)["applied"] == []  # not stamped


def test_apply_pending_with_no_workgraph_stamps_noop(tmp_path):
    # Migration reports no file -> no change, but still stamps (idempotent no-op).
    rep = apply_pending(tmp_path)

    assert rep.errors == []
    assert "001" in rep.ran
    assert read_state(tmp_path)["applied"] == ["001"]


def test_apply_pending_with_injected_migrations(tmp_path):
    calls: list[tuple[str, bool]] = []

    def fake_apply(repo, *, dry_run=False):
        calls.append((str(repo), dry_run))
        return {"id": "099", "changed": True, "files": ["a/b"], "note": "x"}

    migs = [Migration(id="099", description="fake", apply=fake_apply)]

    rep = apply_pending(tmp_path, migrations=migs)

    assert rep.ran == ["099"]
    assert rep.changed_files == ["a/b"]
    assert "099" in read_state(tmp_path)["applied"]
    assert calls and calls[0][1] is False


def test_apply_pending_needs_review_not_stamped(tmp_path):
    def needs_review_apply(repo, *, dry_run=False):
        return {"id": "050", "changed": False, "files": [], "needs_review": True}

    migs = [Migration(id="050", description="review", apply=needs_review_apply)]

    rep = apply_pending(tmp_path, migrations=migs)

    assert "050" in rep.reviews
    assert "050" not in read_state(tmp_path)["applied"]
