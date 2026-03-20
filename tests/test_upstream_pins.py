# ABOUTME: Tests for upstream_pins — TOML read/write/snooze logic.
# ABOUTME: No mocks; uses real tmp_path files.
from __future__ import annotations

from pathlib import Path

import pytest

from driftdriver.upstream_pins import (
    get_sha,
    is_snoozed,
    load_pins,
    save_pins,
    set_sha,
    snooze_branch,
)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    pins = load_pins(tmp_path / "upstream-pins.toml")
    assert pins == {"shas": {}, "snoozed": {}}


def test_set_and_get_sha(tmp_path: Path) -> None:
    path = tmp_path / "upstream-pins.toml"
    pins = load_pins(path)
    pins = set_sha(pins, "graphwork/workgraph", "main", "abc123")
    save_pins(path, pins)
    pins2 = load_pins(path)
    assert get_sha(pins2, "graphwork/workgraph", "main") == "abc123"


def test_get_sha_missing_returns_none(tmp_path: Path) -> None:
    pins = load_pins(tmp_path / "nope.toml")
    assert get_sha(pins, "graphwork/workgraph", "nonexistent") is None


def test_snooze_and_is_snoozed(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    pins = load_pins(path)
    pins = snooze_branch(pins, "graphwork/workgraph", "fix-before-edges", "2099-01-01", "TUI-only")
    save_pins(path, pins)
    pins2 = load_pins(path)
    assert is_snoozed(pins2, "graphwork/workgraph", "fix-before-edges") is True


def test_expired_snooze_not_snoozed(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    pins = load_pins(path)
    pins = snooze_branch(pins, "graphwork/workgraph", "old-branch", "2020-01-01", "old")
    save_pins(path, pins)
    pins2 = load_pins(path)
    assert is_snoozed(pins2, "graphwork/workgraph", "old-branch") is False


def test_set_sha_overwrites_previous(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    pins = load_pins(path)
    pins = set_sha(pins, "graphwork/workgraph", "main", "aaa")
    pins = set_sha(pins, "graphwork/workgraph", "main", "bbb")
    save_pins(path, pins)
    assert get_sha(load_pins(path), "graphwork/workgraph", "main") == "bbb"
