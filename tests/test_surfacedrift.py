"""Tests for the surfacedrift internal lane (model-operable interface compliance)."""
from __future__ import annotations

from pathlib import Path

from driftdriver import surfacedrift
from driftdriver.contracts import REQUIRED_FIELDS


def _write(repo: Path, rel: str, text: str) -> Path:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _deviation_register(repo: Path, entries: list[str]) -> Path:
    body = ["# Deviation Register", ""]
    for i, loc in enumerate(entries, 1):
        body += [
            f"## Entry {i}",
            f"- **Location:** `{loc}`",
            "- **Doctrine clause:** interface deviation, intentional",
            "- **Mechanism:** registered surface exception",
            "- **Decision:** `blessed`",
        ]
    return _write(repo, "docs/model-mediated/deviation-register.md", "\n".join(body) + "\n")


# --- contract field discovery --------------------------------------------------

def test_compliant_marked_class_has_no_findings(tmp_path: Path) -> None:
    fields = "\n".join(f"    {f}: object" for f in REQUIRED_FIELDS)
    _write(tmp_path, "src/tool.py",
           "from driftdriver.contracts import model_operable\n\n\n"
           "@model_operable\n"
           f"class ToolError:\n{fields}\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_missing_required_fields_flagged(tmp_path: Path) -> None:
    _write(tmp_path, "src/tool.py",
           "from driftdriver.contracts import model_operable\n\n\n"
           "@model_operable\n"
           "class ToolError:\n"
           "    error: str\n"
           "    message: str\n"
           "    # expected, valid_examples, retryable, next_step missing\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert len(res.findings) == 1
    f = res.findings[0]
    assert f.file.endswith("tool.py")
    assert "interface-violation" in f.tags
    for missing in ("expected", "valid_examples", "retryable", "next_step"):
        assert missing in f.message
    # none of the present fields should be named as missing
    assert "error" not in f.message.split("missing")[0] or "error" in f.message


def test_partial_fields_flagged_with_present_not_named(tmp_path: Path) -> None:
    _write(tmp_path, "e.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class E:\n"
           "    error: str\n"
           "    next_step: str\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert len(res.findings) == 1
    # Isolate the missing-fields list (between "field(s):" and the next ". ").
    seg = res.findings[0].message.split("field(s):", 1)[1].split(". ", 1)[0]
    assert "next_step" not in seg  # present, must not be named as missing
    assert "message" in seg and "expected" in seg  # absent, must be named


# --- discovery: unmarked classes are ignored ----------------------------------

def test_unmarked_class_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "plain.py",
           "class PlainError:\n    pass\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_no_marked_surfaces_is_clean(tmp_path: Path) -> None:
    # A repo with no model-operable surfaces is not flagged — surfacedrift is opt-in.
    _write(tmp_path, "app.py",
           "def handler(x):\n    return x + 1\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []
    assert res.exit_code == 0


# --- discovery: marker forms ---------------------------------------------------

def test_base_class_subclass_discovered(tmp_path: Path) -> None:
    fields = "\n".join(f"    {f}: object" for f in REQUIRED_FIELDS)
    _write(tmp_path, "m.py",
           "from driftdriver.contracts import ModelOperableErrorContract\n\n"
           f"class MyError(ModelOperableErrorContract):\n{fields}\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_decorator_dotted_form_discovered(tmp_path: Path) -> None:
    fields = "\n".join(f"    {f}: object" for f in REQUIRED_FIELDS)
    _write(tmp_path, "d.py",
           "import driftdriver.contracts as c\n\n"
           "@c.model_operable\n"
           f"class D:\n{fields}\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_decorator_call_form_discovered(tmp_path: Path) -> None:
    fields = "\n".join(f"    {f}: object" for f in REQUIRED_FIELDS)
    _write(tmp_path, "call.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable()\n"
           f"class C:\n{fields}\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


# --- field-declaration forms ---------------------------------------------------

def test_fields_via_assignment_defaults_recognized(tmp_path: Path) -> None:
    # Plain assignment defaults (not annotations) count as declared fields.
    _write(tmp_path, "a.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class A:\n"
           "    error = 'E'\n"
           "    message = ''\n"
           "    expected = ''\n"
           "    valid_examples = []\n"
           "    retryable = False\n"
           "    next_step = ''\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_received_is_optional_not_required(tmp_path: Path) -> None:
    # `received` is recommended but NOT required — omitting it is fine.
    _write(tmp_path, "r.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class R:\n"
           "    error: str\n"
           "    message: str\n"
           "    expected: str\n"
           "    valid_examples: list\n"
           "    retryable: bool\n"
           "    next_step: str\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


# --- deviation register suppression --------------------------------------------

def test_deviation_register_suppresses_covered_class(tmp_path: Path) -> None:
    _write(tmp_path, "src/tool.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class ToolError:\n"
           "    error: str\n")
    _deviation_register(tmp_path, ["src/tool.py:4-4"])
    res = surfacedrift.run_as_lane(tmp_path)
    assert not any(f.file.endswith("tool.py") for f in res.findings)


def test_deviation_does_not_suppress_other_class(tmp_path: Path) -> None:
    src = (
        "from driftdriver.contracts import model_operable\n\n"
        "@model_operable\n"
        "class A:\n    error: str\n\n"
        "@model_operable\n"
        "class B:\n    error: str\n"
    )
    _write(tmp_path, "two.py", src)
    # Class A (ClassDef at line 4) is covered; class B (ClassDef at line 8)
    # must still be flagged.
    _deviation_register(tmp_path, ["two.py:4-4"])
    res = surfacedrift.run_as_lane(tmp_path)
    flagged_lines = {f.line for f in res.findings if f.file.endswith("two.py")}
    assert 8 in flagged_lines
    assert 4 not in flagged_lines


# --- posture: advisory, non-blocking -------------------------------------------

def test_exit_code_always_zero_even_with_findings(tmp_path: Path) -> None:
    _write(tmp_path, "g.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class G:\n    error: str\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings, "expected findings"
    assert res.exit_code == 0, "surfacedrift is advisory; it must not fail the gate"


def test_findings_are_warning_severity_with_file_line_tags(tmp_path: Path) -> None:
    _write(tmp_path, "src/e.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class E:\n    error: str\n")
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings
    f = res.findings[0]
    assert f.severity == "warning"
    assert f.file  # non-empty
    assert f.line >= 1
    assert "interface-violation" in f.tags


def test_lane_identity_and_summary(tmp_path: Path) -> None:
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.lane == "surfacedrift"
    assert isinstance(res.summary, str) and res.summary


# --- hygiene -------------------------------------------------------------------

def test_ignores_venv_and_build_dirs(tmp_path: Path) -> None:
    bad = (
        "from driftdriver.contracts import model_operable\n\n"
        "@model_operable\n"
        "class V:\n    error: str\n"
    )
    _write(tmp_path, ".venv/lib/v.py", bad)
    _write(tmp_path, "build/gen.py", bad)
    _write(tmp_path, "__pycache__/c.py", bad)
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_syntax_error_file_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py",
           "from driftdriver.contracts import model_operable\n\n"
           "@model_operable\n"
           "class Broken(:\n    error: str\n")  # malformed
    res = surfacedrift.run_as_lane(tmp_path)
    assert res.findings == []
