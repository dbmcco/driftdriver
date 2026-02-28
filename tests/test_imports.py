# ABOUTME: Tests module imports with callable behavioral checks for core driftdriver functions
# ABOUTME: Verifies behavioral contracts, not just import success
from __future__ import annotations


def test_import_workgraph() -> None:
    from driftdriver.workgraph import find_workgraph_dir, load_workgraph
    assert callable(find_workgraph_dir)
    assert callable(load_workgraph)


def test_import_wire() -> None:
    from driftdriver.wire import cmd_verify, cmd_loop_check, cmd_enrich, cmd_bridge, cmd_distill, cmd_rollback_eval
    assert callable(cmd_verify)


def test_import_health() -> None:
    from driftdriver.health import compute_scoreboard
    assert callable(compute_scoreboard)
