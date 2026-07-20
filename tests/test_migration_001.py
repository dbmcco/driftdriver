# ABOUTME: Tests for migration 001 (strip ECOSYSTEM_HUB_AUTOSTART block).
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "driftdriver/upgrade/migrations/001_strip_ecosystem_hook.py"
)


def load_m001():
    spec = importlib.util.spec_from_file_location("m001_under_test", MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _write_sh(repo: Path, *, block: str | None, marker_variant: bool = False) -> Path:
    h = repo / ".workgraph/handlers"
    h.mkdir(parents=True, exist_ok=True)
    pre = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "\n"
        'wg service start 2>/dev/null || true\n'
        "fi\n"
        "\n"
    )
    if marker_variant:
        middle = (
            "# Ensure ecosystem hub automation only starts when explicitly requested.\n"
            'echo "an unusual variant of the block"\n'
            "fi\n"
            "\n"
        )
    elif block is None:
        middle = ""
    else:
        middle = block
    post = (
        "# Prime agent with project knowledge from lessons.db (real-time path)\n"
        "if command -v driftdriver >/dev/null 2>&1; then\n"
        "  :\n"
        "fi\n"
    )
    target = h / "session-start.sh"
    target.write_text(pre + middle + post)
    return target


def test_id_and_description_present():
    m = load_m001()
    assert m.ID == "001"
    assert isinstance(m.DESCRIPTION, str) and m.DESCRIPTION.strip()


def test_strips_exact_block(tmp_path):
    m = load_m001()
    target = _write_sh(tmp_path, block=m._BLOCK)
    before = target.read_text()
    assert "ECOSYSTEM_HUB_AUTOSTART" in before
    assert "ecosystem-hub" in before

    res = m.apply(tmp_path, dry_run=False)

    assert res["changed"] is True
    assert res["id"] == "001"
    after = target.read_text()
    assert "ECOSYSTEM_HUB_AUTOSTART" not in after
    assert "ecosystem-hub" not in after
    # surrounding live blocks survive
    assert "# Prime agent" in after
    assert 'wg service start' in after
    # clean single-blank separation between the preceding block and Prime
    assert "fi\n\n# Prime agent" in after


def test_idempotent_second_run_is_noop(tmp_path):
    m = load_m001()
    _write_sh(tmp_path, block=m._BLOCK)
    m.apply(tmp_path, dry_run=False)

    res2 = m.apply(tmp_path, dry_run=False)

    assert res2["changed"] is False
    assert "already clean" in res2["note"]


def test_dry_run_writes_nothing(tmp_path):
    m = load_m001()
    target = _write_sh(tmp_path, block=m._BLOCK)
    before = target.read_text()

    res = m.apply(tmp_path, dry_run=True)

    assert res["changed"] is True
    assert target.read_text() == before  # file untouched


def test_variant_block_flags_review(tmp_path):
    m = load_m001()
    _write_sh(tmp_path, block=None, marker_variant=True)

    res = m.apply(tmp_path, dry_run=False)

    assert res["changed"] is False
    assert res.get("needs_review") is True


def test_missing_file_is_noop(tmp_path):
    m = load_m001()
    res = m.apply(tmp_path, dry_run=False)
    assert res["changed"] is False
    assert "no session-start.sh" in res["note"]


def test_clean_file_is_noop(tmp_path):
    m = load_m001()
    target = _write_sh(tmp_path, block=None)
    res = m.apply(tmp_path, dry_run=False)
    assert res["changed"] is False
    assert "already clean" in res["note"]
