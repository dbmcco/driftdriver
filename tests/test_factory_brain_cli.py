# ABOUTME: Tests for factory brain CLI handler functions.
# ABOUTME: Validates status, roster, enroll, and unenroll via tmp_path isolation.
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from driftdriver.factory_brain.cli import (
    handle_brain_enroll,
    handle_brain_roster,
    handle_brain_status,
    handle_brain_unenroll,
)
from driftdriver.factory_brain.roster import load_roster


def _make_args(**kwargs: object) -> argparse.Namespace:
    """Build an argparse.Namespace with arbitrary attributes."""
    return argparse.Namespace(**kwargs)


def test_handle_brain_roster_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty roster prints a 'no repos' message."""
    args = _make_args(hub_data_dir=str(tmp_path))
    rc = handle_brain_roster(args, hub_data_dir=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No repos enrolled" in out


def test_handle_brain_enroll_and_roster(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Enroll a repo, then verify roster shows it."""
    repo_dir = tmp_path / "my-project"
    repo_dir.mkdir()

    # Enroll
    args = _make_args(path=str(repo_dir), target="onboarded", hub_data_dir=str(tmp_path))
    rc = handle_brain_enroll(args, hub_data_dir=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Enrolled" in out
    assert "my-project" in out

    # Roster should now show the repo
    args = _make_args(hub_data_dir=str(tmp_path))
    rc = handle_brain_roster(args, hub_data_dir=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "my-project" in out
    assert "active" in out
    assert "onboarded" in out


def test_handle_brain_unenroll(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Enroll then unenroll — verify status becomes inactive."""
    repo_dir = tmp_path / "temp-repo"
    repo_dir.mkdir()

    # Enroll
    args = _make_args(path=str(repo_dir), target="production-ready", hub_data_dir=str(tmp_path))
    handle_brain_enroll(args, hub_data_dir=tmp_path)
    capsys.readouterr()  # clear output

    # Unenroll
    args = _make_args(name="temp-repo", hub_data_dir=str(tmp_path))
    rc = handle_brain_unenroll(args, hub_data_dir=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Unenrolled" in out
    assert "temp-repo" in out

    # Verify roster shows inactive
    roster = load_roster(tmp_path / "roster.json")
    assert roster.repos["temp-repo"]["status"] == "inactive"


def test_handle_brain_unenroll_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Unenrolling a nonexistent repo returns error code 1."""
    args = _make_args(name="ghost-repo", hub_data_dir=str(tmp_path))
    rc = handle_brain_unenroll(args, hub_data_dir=tmp_path)
    assert rc == 1
    out = capsys.readouterr().out
    assert "not found" in out


def test_handle_brain_status_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Status with no roster or invocations prints zeros."""
    args = _make_args(hub_data_dir=str(tmp_path))
    rc = handle_brain_status(args, hub_data_dir=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 active / 0 total" in out
    assert "Invocations: 0" in out
    assert "(none)" in out


def test_handle_brain_status_with_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Status reflects enrolled repos and invocation data."""
    import json

    # Enroll a repo
    args = _make_args(path=str(tmp_path / "proj"), target="onboarded", hub_data_dir=str(tmp_path))
    (tmp_path / "proj").mkdir()
    handle_brain_enroll(args, hub_data_dir=tmp_path)
    capsys.readouterr()

    # Write a fake invocation
    invocations_file = tmp_path / "brain-invocations.jsonl"
    record = {"tier": 2, "model": "claude-sonnet-4-20250514", "reasoning": "test"}
    invocations_file.write_text(json.dumps(record) + "\n")

    # Check status
    args = _make_args(hub_data_dir=str(tmp_path))
    rc = handle_brain_status(args, hub_data_dir=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 active / 1 total" in out
    assert "Invocations: 1" in out
    assert "tier 2" in out
