# ABOUTME: Tests for the fleet runner (discovery + resilient apply across repos).
from __future__ import annotations

import importlib.util
from pathlib import Path

from driftdriver.upgrade.fleet import discover_repos, run_fleet

_MIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "driftdriver/upgrade/migrations/001_strip_ecosystem_hook.py"
)


def _real_block() -> str:
    spec = importlib.util.spec_from_file_location("m001_fleet", _MIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod._BLOCK


def _make_repo(root: Path, name: str, *, with_hook: bool = True) -> Path:
    repo = root / name
    h = repo / ".workgraph/handlers"
    h.mkdir(parents=True, exist_ok=True)
    if with_hook:
        (h / "session-start.sh").write_text(
            "#!/usr/bin/env bash\nfi\n\n" + _real_block() + "# Prime\n"
        )
    else:
        (h / "session-start.sh").write_text("#!/usr/bin/env bash\nfi\n\n# Prime\n")
    return repo


def test_discover_finds_workgraph_repos_and_excludes_noise(tmp_path):
    _make_repo(tmp_path, "alpha")
    _make_repo(tmp_path, "beta")
    # a deeply nested repo (depth 4) must still be found
    _make_repo(tmp_path, "a/b/c/delta")
    # noise directories that must be excluded
    (tmp_path / "gamma/node_modules/pkg/.workgraph").mkdir(parents=True)
    (tmp_path / "delta/.wg-worktrees/x/.workgraph").mkdir(parents=True)
    (tmp_path / "epsilon/.worktrees/y/.workgraph").mkdir(parents=True)
    # a repo's own internals should not be re-discovered as a separate repo
    _make_repo(tmp_path, "zeta")
    (tmp_path / "zeta/.workgraph/inner/.workgraph").mkdir(parents=True)

    repos = discover_repos(tmp_path)
    names = sorted(p.relative_to(tmp_path).as_posix() for p in repos)

    assert names == ["a/b/c/delta", "alpha", "beta", "zeta"]


def test_run_fleet_applies_all_changed(tmp_path):
    _make_repo(tmp_path, "alpha")
    _make_repo(tmp_path, "beta")

    fr = run_fleet(tmp_path)

    assert fr.total == 2
    assert len(fr.changed) == 2
    assert fr.with_errors == []


def test_run_fleet_dry_run_reports_would_change(tmp_path):
    _make_repo(tmp_path, "alpha")

    fr = run_fleet(tmp_path, dry_run=True)

    assert fr.dry_run is True
    assert len(fr.changed) == 1


def test_run_fleet_second_pass_is_noop(tmp_path):
    _make_repo(tmp_path, "alpha")
    run_fleet(tmp_path)

    fr2 = run_fleet(tmp_path)

    assert fr2.changed == []
    assert fr2.with_errors == []


def test_run_fleet_handles_clean_repos(tmp_path):
    _make_repo(tmp_path, "clean", with_hook=False)

    fr = run_fleet(tmp_path)

    assert fr.total == 1
    assert fr.changed == []
    assert fr.with_errors == []
