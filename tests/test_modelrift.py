"""Tests for the modelrift internal lane (model-agency-violation drift)."""
from __future__ import annotations

from pathlib import Path

from driftdriver import modelrift


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
            "- **Doctrine clause:** model owns semantic judgment",
            "- **Mechanism:** registered heuristic",
            "- **Decision:** `pending-decision`",
        ]
    return _write(repo, "docs/model-mediated/deviation-register.md", "\n".join(body) + "\n")


# --- Detector A: keyword / marker intent gates ---------------------------------

def test_keyword_marker_constant_detected(tmp_path: Path) -> None:
    _write(tmp_path, "src/gate.py",
           '_TOOL_BACKED_WORK_MARKERS = ("schedule", "task", "send", "email")\n'
           "def wants(msg):\n"
           "    return any(m in msg.lower() for m in _TOOL_BACKED_WORK_MARKERS)\n")
    res = modelrift.run_as_lane(tmp_path)
    kinds = {tuple(f.tags) for f in res.findings}
    assert any("keyword-gate" in t for t in kinds)
    assert any(f.line == 1 for f in res.findings if "keyword-gate" in f.tags)


def test_lowercase_intent_list_detected(tmp_path: Path) -> None:
    _write(tmp_path, "r.py", "intent_triggers = [\"book\", \"cancel\"]\n")
    res = modelrift.run_as_lane(tmp_path)
    assert any("keyword-gate" in f.tags for f in res.findings)


def test_marker_definition_without_strings_not_flagged(tmp_path: Path) -> None:
    # A name ending in 'markers' but bound to a non-string collection is not an
    # intent gate.
    _write(tmp_path, "m.py", "POSITION_MARKERS = [(1, 2), (3, 4)]\n")
    res = modelrift.run_as_lane(tmp_path)
    assert not any("keyword-gate" in f.tags for f in res.findings)


# --- Detector B: hardcoded semantic thresholds ---------------------------------

def test_float_threshold_on_semantic_var_detected(tmp_path: Path) -> None:
    _write(tmp_path, "rank.py", "def passes(item):\n    return item.score >= 0.7\n")
    res = modelrift.run_as_lane(tmp_path)
    assert any("semantic-threshold" in f.tags for f in res.findings)
    assert any(f.line == 2 for f in res.findings if "semantic-threshold" in f.tags)


def test_threshold_on_plain_counter_not_flagged(tmp_path: Path) -> None:
    # `i > 5` is mechanical, not a semantic judgment — must not fire.
    _write(tmp_path, "loop.py", "for i in range(10):\n    if i > 5:\n        pass\n")
    res = modelrift.run_as_lane(tmp_path)
    assert not any("semantic-threshold" in f.tags for f in res.findings)


# --- Deviation register suppression --------------------------------------------

def test_deviation_register_suppresses_covered_location(tmp_path: Path) -> None:
    _write(tmp_path, "src/gate.py",
           "_MARKERS = (\"schedule\", \"task\")\n"
           "def wants(msg):\n    return any(m in msg for m in _MARKERS)\n")
    _deviation_register(tmp_path, ["src/gate.py:1-1"])
    res = modelrift.run_as_lane(tmp_path)
    assert not any(f.file.endswith("gate.py") for f in res.findings)


def test_deviation_register_does_not_suppress_other_lines(tmp_path: Path) -> None:
    _write(tmp_path, "src/gate.py",
           "_MARKERS = (\"schedule\", \"task\")\n"
           "_OTHER_MARKERS = (\"x\",)\n")
    # Deviation covers only line 1; line 2 must still be flagged.
    _deviation_register(tmp_path, ["src/gate.py:1-1"])
    res = modelrift.run_as_lane(tmp_path)
    flagged_lines = {f.line for f in res.findings if f.file.endswith("gate.py")}
    assert 2 in flagged_lines
    assert 1 not in flagged_lines


# --- Posture: advisory, non-blocking -------------------------------------------

def test_exit_code_always_zero_even_with_findings(tmp_path: Path) -> None:
    _write(tmp_path, "g.py", "_INTENT_KEYWORDS = (\"go\",)\n")
    res = modelrift.run_as_lane(tmp_path)
    assert res.findings, "expected findings"
    assert res.exit_code == 0, "modelrift is advisory; it must not fail the gate"


def test_findings_are_warning_severity_with_file_line_tags(tmp_path: Path) -> None:
    _write(tmp_path, "src/a.py", "if score > 0.8:\n    pass\n")
    res = modelrift.run_as_lane(tmp_path)
    assert res.findings
    f = res.findings[0]
    assert f.severity == "warning"
    assert f.file  # non-empty
    assert f.line >= 1
    assert "model-agency" in f.tags


def test_lane_identity_and_summary(tmp_path: Path) -> None:
    res = modelrift.run_as_lane(tmp_path)
    assert res.lane == "modelrift"
    assert isinstance(res.summary, str) and res.summary


# --- Hygiene -------------------------------------------------------------------

def test_clean_code_has_no_findings(tmp_path: Path) -> None:
    _write(tmp_path, "clean.py",
           "def add(a, b):\n    return a + b\n\n"
           "MAX_CONNECTIONS = 10\n"
           "for i in range(MAX_CONNECTIONS):\n    print(i)\n")
    res = modelrift.run_as_lane(tmp_path)
    assert res.findings == []


def test_ignores_venv_and_build_dirs(tmp_path: Path) -> None:
    _write(tmp_path, ".venv/lib/junk.py", "_MARKERS = (\"x\",)\n")
    _write(tmp_path, "build/gen.py", "if confidence >= 0.9:\n    pass\n")
    _write(tmp_path, "__pycache__/c.py", "_TRIGGERS = (\"y\",)\n")
    res = modelrift.run_as_lane(tmp_path)
    assert res.findings == []
