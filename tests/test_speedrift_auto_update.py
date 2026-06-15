from __future__ import annotations

from pathlib import Path

from driftdriver.speedrift_auto_update import auto_update_for_repo_changes


def test_auto_update_refreshes_once_then_waits_for_repo_change(tmp_path: Path) -> None:
    project_dir = tmp_path / "repo"
    wg_dir = project_dir / ".workgraph"
    wg_dir.mkdir(parents=True)
    (project_dir / "AGENTS.md").write_text("managed guidance\n", encoding="utf-8")
    (project_dir / "README.md").write_text("# Demo\n", encoding="utf-8")

    calls: list[tuple[Path, Path]] = []

    def refresher(project: Path, wg: Path) -> dict[str, bool]:
        calls.append((project, wg))
        return {"wrote_agents_md": True}

    first = auto_update_for_repo_changes(project_dir, wg_dir, refresher=refresher)
    assert first["changed"] is True
    assert first["refreshed"] is True
    assert first["refresh_result"] == {"wrote_agents_md": True}
    assert calls == [(project_dir, wg_dir)]

    second = auto_update_for_repo_changes(project_dir, wg_dir, refresher=refresher)
    assert second["changed"] is False
    assert second["refreshed"] is False
    assert len(calls) == 1

    (project_dir / "README.md").write_text("# Demo\n\nChanged\n", encoding="utf-8")
    third = auto_update_for_repo_changes(project_dir, wg_dir, refresher=refresher)
    assert third["changed"] is True
    assert third["refreshed"] is True
    assert len(calls) == 2


def test_auto_update_can_be_disabled_by_environment(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "repo"
    wg_dir = project_dir / ".workgraph"
    wg_dir.mkdir(parents=True)
    monkeypatch.setenv("DRIFTDRIVER_DISABLE_SPEEDRIFT_AUTO_UPDATE", "1")

    def refresher(project: Path, wg: Path) -> dict[str, bool]:
        raise AssertionError("refresher should not run when auto-update is disabled")

    result = auto_update_for_repo_changes(project_dir, wg_dir, refresher=refresher)
    assert result["enabled"] is False
    assert result["skipped_reason"] == "disabled_by_environment"


def test_auto_update_records_post_refresh_signature(tmp_path: Path) -> None:
    project_dir = tmp_path / "repo"
    wg_dir = project_dir / ".workgraph"
    wg_dir.mkdir(parents=True)
    guidance = project_dir / "AGENTS.md"
    guidance.write_text("old guidance\n", encoding="utf-8")

    calls = 0

    def refresher(project: Path, wg: Path) -> dict[str, bool]:
        nonlocal calls
        calls += 1
        guidance.write_text("new guidance\n", encoding="utf-8")
        return {"wrote_agents_md": True}

    first = auto_update_for_repo_changes(project_dir, wg_dir, refresher=refresher)
    assert first["changed"] is True
    assert first["refreshed"] is True

    second = auto_update_for_repo_changes(project_dir, wg_dir, refresher=refresher)
    assert second["changed"] is False
    assert second["refreshed"] is False
    assert calls == 1
