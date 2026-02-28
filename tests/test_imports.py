# ABOUTME: Tests module imports with behavioral smoke tests for core driftdriver functions
# ABOUTME: Verifies behavioral contracts, not just import success
from __future__ import annotations

import pytest
from pathlib import Path


def test_import_workgraph() -> None:
    from driftdriver.workgraph import find_workgraph_dir, load_workgraph
    # Verify they raise on bad input, not just that they exist
    with pytest.raises(Exception):
        find_workgraph_dir(Path("/tmp/no-workgraph-xyzzy-driftdriver"))


def test_import_wire() -> None:
    from driftdriver.wire import cmd_verify, cmd_loop_check, cmd_enrich, cmd_bridge, cmd_distill, cmd_rollback_eval
    # cmd_verify needs a Path argument — None should raise
    with pytest.raises(Exception):
        cmd_verify(None)


def test_import_health() -> None:
    from driftdriver.health import compute_scoreboard
    # compute_scoreboard with empty input should return a scoreboard structure
    result = compute_scoreboard([])
    assert isinstance(result, (dict, list, str))
