# ABOUTME: Tests for factory brain roster — enrollment, unenrollment, discovery.
# ABOUTME: Validates repo lifecycle tracking and workspace scanning for .workgraph dirs.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from driftdriver.factory_brain.roster import (
    Roster,
    active_repos,
    discover_repos,
    enroll_repo,
    load_roster,
    save_roster,
    unenroll_repo,
)


def test_load_roster_missing_file(tmp_path: Path) -> None:
    """Loading from a nonexistent file returns an empty Roster."""
    roster = load_roster(tmp_path / "no-such-file.json")
    assert isinstance(roster, Roster)
    assert roster.repos == {}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """A roster saved to disk round-trips back identically."""
    roster_file = tmp_path / "roster.json"
    roster = Roster(repos={"myrepo": {"path": "/tmp/myrepo", "target": "onboarded", "status": "active"}})
    save_roster(roster, roster_file)
    loaded = load_roster(roster_file)
    assert loaded.repos == roster.repos


def test_enroll_repo(tmp_path: Path) -> None:
    """enroll_repo adds a repo with correct metadata."""
    roster = Roster(repos={})
    name = enroll_repo(roster, path="/home/dev/cool-project", target="onboarded")
    assert name == "cool-project"
    entry = roster.repos["cool-project"]
    assert entry["path"] == "/home/dev/cool-project"
    assert entry["target"] == "onboarded"
    assert entry["status"] == "active"
    assert "enrolled_at" in entry


def test_unenroll_repo() -> None:
    """unenroll_repo sets status to inactive and adds unenrolled_at."""
    roster = Roster(repos={
        "myrepo": {"path": "/tmp/myrepo", "target": "onboarded", "status": "active", "enrolled_at": "2026-01-01T00:00:00+00:00"},
    })
    unenroll_repo(roster, name="myrepo")
    entry = roster.repos["myrepo"]
    assert entry["status"] == "inactive"
    assert "unenrolled_at" in entry


def test_unenroll_preserves_history() -> None:
    """unenroll_repo does NOT delete the entry — it preserves history."""
    roster = Roster(repos={
        "myrepo": {"path": "/tmp/myrepo", "target": "onboarded", "status": "active", "enrolled_at": "2026-01-01T00:00:00+00:00"},
    })
    unenroll_repo(roster, name="myrepo")
    assert "myrepo" in roster.repos
    assert roster.repos["myrepo"]["path"] == "/tmp/myrepo"
    assert roster.repos["myrepo"]["enrolled_at"] == "2026-01-01T00:00:00+00:00"


def test_discover_repos(tmp_path: Path) -> None:
    """discover_repos finds directories containing .workgraph/."""
    # Create two repos with .workgraph
    (tmp_path / "repo-a" / ".workgraph").mkdir(parents=True)
    (tmp_path / "repo-b" / ".workgraph").mkdir(parents=True)
    # Create a non-repo directory (no .workgraph)
    (tmp_path / "not-a-repo").mkdir()

    found = discover_repos(tmp_path)
    names = {p.name for p in found}
    assert "repo-a" in names
    assert "repo-b" in names
    assert "not-a-repo" not in names


def test_discover_repos_excludes_enrolled(tmp_path: Path) -> None:
    """discover_repos filters out names in the exclude set."""
    (tmp_path / "repo-a" / ".workgraph").mkdir(parents=True)
    (tmp_path / "repo-b" / ".workgraph").mkdir(parents=True)

    found = discover_repos(tmp_path, exclude={"repo-a"})
    names = {p.name for p in found}
    assert "repo-a" not in names
    assert "repo-b" in names


def test_active_repos_filters_inactive() -> None:
    """active_repos returns only repos with status=active."""
    roster = Roster(repos={
        "alive": {"path": "/tmp/alive", "target": "onboarded", "status": "active"},
        "dead": {"path": "/tmp/dead", "target": "onboarded", "status": "inactive"},
    })
    result = active_repos(roster)
    assert "alive" in result
    assert "dead" not in result
