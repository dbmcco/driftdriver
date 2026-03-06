# ABOUTME: Speedriftd subcommand delegation for driftdriver CLI.
# ABOUTME: cmd_speedriftd lives in __init__.py to preserve test patch targets on driftdriver.cli namespace.

from __future__ import annotations

# cmd_speedriftd is defined in driftdriver/cli/__init__.py because existing tests
# patch driftdriver.cli.write_control_state, driftdriver.cli.load_runtime_snapshot,
# and driftdriver.cli.run_runtime_cycle at the driftdriver.cli namespace level.
# Keeping cmd_speedriftd in __init__.py ensures those patches affect the function's
# name lookups at call time.
#
# This module exists as an organizational marker per the subpackage layout spec.
# Import cmd_speedriftd from the parent package:
#   from driftdriver.cli import cmd_speedriftd
