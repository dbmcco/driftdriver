# ABOUTME: Tests for cli.py fixes - _repair_wrappers NameError and new subcommands
# ABOUTME: Verifies bug fix and wire/profile/ready subcommand wiring

from __future__ import annotations

import tempfile
from pathlib import Path


def test_repair_wrappers_no_name_error(tmp_path):
    """_repair_wrappers must not raise NameError for project_dir."""
    from driftdriver.cli import _repair_wrappers

    wg_dir = tmp_path / ".workgraph"
    wg_dir.mkdir()
    # Should not raise NameError (or any other exception from undefined project_dir)
    try:
        _repair_wrappers(wg_dir=wg_dir)
    except NameError as e:
        raise AssertionError(f"NameError raised: {e}") from e
    except Exception:
        # Other errors (missing binaries, etc.) are acceptable
        pass


def test_wire_subcommands_exist():
    """Argparse must have verify/loop-check/enrich/bridge/distill/rollback-eval subcommands."""
    from driftdriver.cli import _build_parser

    p = _build_parser()
    # Extract registered subcommand names
    subparsers_actions = [
        a for a in p._actions if hasattr(a, "_name_parser_map")
    ]
    assert subparsers_actions, "No subparsers found"
    choices = set(subparsers_actions[0]._name_parser_map.keys())

    for cmd in ("verify", "loop-check", "enrich", "bridge", "distill", "rollback-eval"):
        assert cmd in choices, f"Subcommand '{cmd}' not found in argparse choices: {choices}"


def test_profile_subcommand_exists():
    """Argparse must have a 'profile' subcommand."""
    from driftdriver.cli import _build_parser

    p = _build_parser()
    subparsers_actions = [
        a for a in p._actions if hasattr(a, "_name_parser_map")
    ]
    choices = set(subparsers_actions[0]._name_parser_map.keys())
    assert "profile" in choices, f"Subcommand 'profile' not found in: {choices}"


def test_ready_subcommand_exists():
    """Argparse must have a 'ready' subcommand."""
    from driftdriver.cli import _build_parser

    p = _build_parser()
    subparsers_actions = [
        a for a in p._actions if hasattr(a, "_name_parser_map")
    ]
    choices = set(subparsers_actions[0]._name_parser_map.keys())
    assert "ready" in choices, f"Subcommand 'ready' not found in: {choices}"
