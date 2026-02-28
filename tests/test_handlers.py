# ABOUTME: Tests for driftdriver handler scripts installed into .workgraph/handlers/
# ABOUTME: Verifies existence, executability, sourcing of common.sh, and ABOUTME headers

import os
import stat
from pathlib import Path

HANDLERS_DIR = Path(__file__).parent.parent / "driftdriver" / "templates" / "handlers"

HANDLER_SCRIPTS = [
    "common.sh",
    "session-start.sh",
    "pre-compact.sh",
    "task-claimed.sh",
    "task-completing.sh",
    "progress-check.sh",
    "agent-stop.sh",
    "agent-error.sh",
]

NON_COMMON_SCRIPTS = [s for s in HANDLER_SCRIPTS if s != "common.sh"]

COMMON_FUNCTIONS = ["lessons_mcp", "wg_log", "current_task_id"]


def test_handlers_dir_exists():
    assert HANDLERS_DIR.exists(), f"handlers dir not found: {HANDLERS_DIR}"
    assert HANDLERS_DIR.is_dir()


def test_all_handler_scripts_exist():
    for script in HANDLER_SCRIPTS:
        path = HANDLERS_DIR / script
        assert path.exists(), f"Missing handler script: {script}"


def test_all_handler_scripts_are_executable():
    for script in HANDLER_SCRIPTS:
        path = HANDLERS_DIR / script
        if not path.exists():
            continue
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"Script not executable: {script}"


def test_all_scripts_have_bash_shebang():
    for script in HANDLER_SCRIPTS:
        path = HANDLERS_DIR / script
        if not path.exists():
            continue
        first_line = path.read_text().splitlines()[0]
        assert first_line == "#!/usr/bin/env bash", (
            f"{script} missing bash shebang, got: {first_line!r}"
        )


def test_all_scripts_have_aboutme_headers():
    for script in HANDLER_SCRIPTS:
        path = HANDLERS_DIR / script
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        aboutme_lines = [l for l in lines if l.startswith("# ABOUTME:")]
        assert len(aboutme_lines) >= 2, (
            f"{script} needs at least 2 ABOUTME header lines, found {len(aboutme_lines)}"
        )


def test_non_common_scripts_source_common_sh():
    for script in NON_COMMON_SCRIPTS:
        path = HANDLERS_DIR / script
        if not path.exists():
            continue
        content = path.read_text()
        assert '. "$HANDLER_DIR/common.sh"' in content, (
            f"{script} does not source common.sh via '. \"$HANDLER_DIR/common.sh\"'"
        )


def test_common_sh_defines_lessons_mcp():
    common = HANDLERS_DIR / "common.sh"
    if not common.exists():
        return
    content = common.read_text()
    assert "lessons_mcp()" in content, "common.sh missing lessons_mcp() function"


def test_common_sh_defines_wg_log():
    common = HANDLERS_DIR / "common.sh"
    if not common.exists():
        return
    content = common.read_text()
    assert "wg_log()" in content, "common.sh missing wg_log() function"


def test_common_sh_defines_current_task_id():
    common = HANDLERS_DIR / "common.sh"
    if not common.exists():
        return
    content = common.read_text()
    assert "current_task_id()" in content, "common.sh missing current_task_id() function"


def test_scripts_are_under_80_lines():
    for script in HANDLER_SCRIPTS:
        path = HANDLERS_DIR / script
        if not path.exists():
            continue
        lines = path.read_text().splitlines()
        assert len(lines) <= 80, (
            f"{script} exceeds 80 lines: {len(lines)} lines"
        )


# Fix 1: JSON injection via unescaped variables
JSON_BUILDING_SCRIPTS = ["agent-error.sh", "task-claimed.sh", "task-completing.sh", "agent-stop.sh"]


def test_handler_scripts_use_jq_for_json_construction():
    """Handler scripts must use 'jq -n' to build JSON safely, not string interpolation."""
    for script in JSON_BUILDING_SCRIPTS:
        path = HANDLERS_DIR / script
        if not path.exists():
            continue
        content = path.read_text()
        assert "jq -n" in content, (
            f"{script} must use 'jq -n' for safe JSON construction, not bare string interpolation"
        )


# Fix 2: lessons_mcp() must write to JSONL file, not pipe to node
def test_lessons_mcp_writes_to_jsonl_file():
    """lessons_mcp() must write events to .lessons-events/pending.jsonl, not pipe to node."""
    common = HANDLERS_DIR / "common.sh"
    if not common.exists():
        return
    content = common.read_text()
    assert ".lessons-events" in content, (
        "common.sh lessons_mcp() must write to .lessons-events/ directory"
    )
    assert "pending.jsonl" in content, (
        "common.sh lessons_mcp() must append to pending.jsonl"
    )
    assert "lessons-mcp" not in content, (
        "common.sh lessons_mcp() must not invoke node lessons-mcp process"
    )


# Fix 3: wg_log must use positional arg, not --message flag
def test_wg_log_uses_positional_arg():
    """wg_log() must call 'wg log' with positional message arg, not --message flag."""
    common = HANDLERS_DIR / "common.sh"
    if not common.exists():
        return
    content = common.read_text()
    assert "--message" not in content, (
        "common.sh wg_log() must not use --message flag; pass message as positional arg"
    )


# Fix 4: progress-check.sh must guard against empty TASK_ID
def test_progress_check_guards_empty_task_id():
    """progress-check.sh must exit 0 early when TASK_ID is empty."""
    path = HANDLERS_DIR / "progress-check.sh"
    if not path.exists():
        return
    content = path.read_text()
    assert '[[ -z "$TASK_ID" ]]' in content, (
        "progress-check.sh must check if TASK_ID is empty"
    )
    assert "exit 0" in content, (
        "progress-check.sh must exit 0 when TASK_ID is empty"
    )
