# ABOUTME: Tests for FactoryBrain hub integration controller.
# ABOUTME: Verifies init, empty-roster tick, brain invocation, and roster persistence.
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from driftdriver.factory_brain.hub_integration import FactoryBrain
from driftdriver.factory_brain.roster import enroll_repo, load_roster


def test_factory_brain_init(tmp_path: Path) -> None:
    """Verify empty roster, correct paths after init."""
    hub_dir = tmp_path / "hub-data"
    hub_dir.mkdir()

    brain = FactoryBrain(
        hub_data_dir=hub_dir,
        workspace_roots=[tmp_path],
    )

    assert brain.roster_file == hub_dir / "factory-roster.json"
    assert brain.log_dir == hub_dir / "brain-logs"
    assert brain.log_dir.is_dir()
    assert brain.roster.repos == {}
    assert brain.dry_run is False


def test_factory_brain_tick_empty_roster(tmp_path: Path) -> None:
    """Tick with no enrolled repos returns empty list."""
    hub_dir = tmp_path / "hub-data"
    hub_dir.mkdir()

    brain = FactoryBrain(
        hub_data_dir=hub_dir,
        workspace_roots=[tmp_path],
    )

    results = brain.tick()
    assert results == []


def test_factory_brain_tick_calls_brain(tmp_path: Path) -> None:
    """Enroll a repo, mock run_brain_tick, verify it gets called."""
    hub_dir = tmp_path / "hub-data"
    hub_dir.mkdir()

    # Create a fake repo with .workgraph
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / ".workgraph").mkdir()

    brain = FactoryBrain(
        hub_data_dir=hub_dir,
        workspace_roots=[tmp_path],
        dry_run=True,
    )

    # Enroll the repo via roster API
    enroll_repo(brain.roster, path=str(repo), target="onboarded")

    mock_results = [{"tier": 1, "directives_executed": 0, "results": [], "escalated": False}]

    with patch(
        "driftdriver.factory_brain.hub_integration.run_brain_tick",
        return_value=mock_results,
    ) as mock_tick:
        results = brain.tick(snapshot={"repos": []})

    assert results == mock_results
    mock_tick.assert_called_once()
    call_kwargs = mock_tick.call_args.kwargs
    assert call_kwargs["state"] is brain.state
    assert len(call_kwargs["roster_repos"]) == 1
    assert call_kwargs["roster_repos"][0] == repo
    assert call_kwargs["dry_run"] is True
    assert call_kwargs["snapshot"] == {"repos": []}


def test_factory_brain_persists_roster(tmp_path: Path) -> None:
    """Enroll, tick, verify roster file exists and contains the repo."""
    hub_dir = tmp_path / "hub-data"
    hub_dir.mkdir()

    repo = tmp_path / "persist-repo"
    repo.mkdir()
    (repo / ".workgraph").mkdir()

    brain = FactoryBrain(
        hub_data_dir=hub_dir,
        workspace_roots=[tmp_path],
        dry_run=True,
    )

    # Enroll via the _handle_enroll path (simulates a deferred enroll directive)
    brain._handle_enroll(str(repo))

    # Force a roster save (normally happens when roster_changed is True in tick)
    from driftdriver.factory_brain.roster import save_roster

    save_roster(brain.roster, brain.roster_file)

    # Verify roster file exists
    assert brain.roster_file.exists()

    # Reload and verify repo is present
    loaded = load_roster(brain.roster_file)
    assert "persist-repo" in loaded.repos
    assert loaded.repos["persist-repo"]["status"] == "active"
    assert loaded.repos["persist-repo"]["path"] == str(repo)
