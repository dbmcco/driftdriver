# ABOUTME: Tests for existdrift — model-mediated pre-build grounding lane.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from driftdriver.existdrift import (
    build_evidence_bundle,
    collect_evidence,
    interpret_evidence,
    run_existdrift_check,
    scan_grounding,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_graph(wg_dir: Path, rows: list[dict]) -> None:
    """Write a fake graph.jsonl into the given .workgraph directory."""
    wg_dir.mkdir(parents=True, exist_ok=True)
    (wg_dir / "graph.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def _contract(
    title: str = "",
    touch: list[str] | None = None,
    creates: list[str] | None = None,
) -> str:
    """Build a wg-contract fence block."""
    lines = [
        "```wg-contract",
        'schema = 1',
        'mode = "core"',
        f'objective = "{title}"',
    ]
    if touch is not None:
        lines.append("touch = [" + ", ".join(f'"{p}"' for p in touch) + "]")
    if creates is not None:
        lines.append("creates = [" + ", ".join(f'"{p}"' for p in creates) + "]")
    lines.append("```")
    return "\n".join(lines)


def _task(
    tid: str,
    title: str = "",
    status: str = "open",
    touch: list[str] | None = None,
    creates: list[str] | None = None,
    description: str = "",
) -> dict:
    desc = _contract(title=title, touch=touch, creates=creates) + "\n" + description
    return {
        "type": "task",
        "id": tid,
        "title": title,
        "status": status,
        "after": [],
        "description": desc,
    }


def _valid_interp_response(task_id: str, item: str, judgment: str, fix: str = "") -> str:
    """A valid model response string for one item."""
    return json.dumps([{
        "task_id": task_id,
        "item": item,
        "judgment": judgment,
        "rationale": "test rationale",
        "suggested_fix": fix,
    }])


class _CallRecorder:
    """A fake caller that records all calls and returns a canned response."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[str] = []

    def __call__(self, model: str, prompt: str, timeout: int = 60) -> str:
        self.calls.append(prompt)
        if self._responses:
            return self._responses.pop(0)
        return ""


# ---------------------------------------------------------------------------
# Evidence layer — purity tests
# ---------------------------------------------------------------------------


class CollectEvidenceTests(unittest.TestCase):
    def test_output_has_no_severity_or_recommendation(self) -> None:
        """collect_evidence must produce pure facts, never judgments."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "real.py").write_text("x = 1\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Mixed", touch=["src/real.py", "src/fake.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": True, "min_symbol_len": 4})
            self.assertTrue(len(rows) >= 1)
            for row in rows:
                self.assertNotIn("severity", row)
                self.assertNotIn("recommendation", row)
                for tf in row.get("touch_facts", []):
                    self.assertNotIn("severity", tf)
                    self.assertNotIn("recommendation", tf)
                for cf in row.get("creates_facts", []):
                    self.assertNotIn("severity", cf)

    def test_creates_declared_missing_path_produces_no_touch_gap(self) -> None:
        """When a missing path is declared in creates, it should NOT appear as a touch_fact with exists=False."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Create module",
                      touch=[], creates=["src/new.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            row = rows[0]
            # creates_facts should have the file with already_exists=False
            creates = [c for c in row["creates_facts"] if c["path"] == "src/new.py"]
            self.assertEqual(len(creates), 1)
            self.assertFalse(creates[0]["already_exists"])

    def test_creates_collision_detected(self) -> None:
        """A creates path that already exists is a collision fact."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "existing.py").write_text("x = 1\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Duplicate",
                      creates=["src/existing.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            collisions = [c for c in rows[0]["creates_facts"] if c["already_exists"]]
            self.assertEqual(len(collisions), 1)
            self.assertEqual(collisions[0]["path"], "src/existing.py")

    def test_nearest_existing_parent_recorded_as_fact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "sibling.py").write_text("x = 1\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Gap", touch=["src/missing.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            tf = rows[0]["touch_facts"][0]
            self.assertFalse(tf["exists"])
            self.assertIn("src", tf["nearest_existing_parent"])

    def test_outside_repo_fact_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Cross-repo", touch=["../other/x.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            tf = rows[0]["touch_facts"][0]
            self.assertTrue(tf["outside_repo"])

    def test_symbol_found_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "mod.py").write_text("def my_func(): pass\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Sym", touch=["src/mod.py"],
                      description="Calls `my_func` and `invented_thing`."),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": True, "min_symbol_len": 4})
            syms = {s["symbol"]: s for s in rows[0]["symbol_facts"]}
            self.assertTrue(syms["my_func"]["found"])
            self.assertFalse(syms["invented_thing"]["found"])


# ---------------------------------------------------------------------------
# Interpretation layer — model-mediated tests
# ---------------------------------------------------------------------------


class InterpretEvidenceTests(unittest.TestCase):
    def test_creates_declared_skips_interpretation(self) -> None:
        """A missing path declared in creates must NOT need interpretation."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Create", creates=["src/new.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            caller = _CallRecorder([])
            result = interpret_evidence(rows, cfg={"interpretation_model": "test"}, caller=caller)
            # No items needed interpretation → model never called
            self.assertEqual(len(caller.calls), 0)
            self.assertEqual(result, {})

    def test_undeclared_missing_path_triggers_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Gap", touch=["src/fake.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            caller = _CallRecorder([
                _valid_interp_response("t1", "src/fake.py", "grounding-error", "check path"),
            ])
            result = interpret_evidence(rows, cfg={"interpretation_model": "test"}, caller=caller)
            self.assertEqual(len(caller.calls), 1)
            self.assertIn("t1", result)
            self.assertEqual(result["t1"][0]["judgment"], "grounding-error")

    def test_creates_collision_triggers_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "dup.py").write_text("x = 1\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Dup", creates=["src/dup.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            caller = _CallRecorder([
                _valid_interp_response("t1", "src/dup.py", "collision-risk"),
            ])
            result = interpret_evidence(rows, cfg={"interpretation_model": "test"}, caller=caller)
            self.assertEqual(result["t1"][0]["judgment"], "collision-risk")

    def test_unknown_symbol_triggers_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "mod.py").write_text("def real(): pass\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Sym", touch=["src/mod.py"],
                      description="Uses `invented_helper`."),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": True, "min_symbol_len": 4})
            caller = _CallRecorder([
                _valid_interp_response("t1", "invented_helper", "acceptable"),
            ])
            result = interpret_evidence(rows, cfg={"interpretation_model": "test"}, caller=caller)
            self.assertEqual(result["t1"][0]["judgment"], "acceptable")

    def test_valid_json_maps_to_findings_severities(self) -> None:
        """scan_grounding maps model judgments to cfg-driven severities."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "dup.py").write_text("x = 1\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Mixed",
                      touch=["src/fake.py"],
                      creates=["src/dup.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            caller = _CallRecorder([
                json.dumps([
                    {"task_id": "t1", "item": "src/fake.py", "judgment": "grounding-error",
                     "rationale": "wrong path", "suggested_fix": "check real path"},
                    {"task_id": "t1", "item": "src/dup.py", "judgment": "collision-risk",
                     "rationale": "exists", "suggested_fix": "use touch"},
                ]),
            ])
            report = scan_grounding(repo, cfg={"enabled": True, "symbol_check": False},
                                     caller=caller)
            cats = {f["category"] for f in report["findings"]}
            self.assertIn("grounding-error", cats)
            self.assertIn("creates-collision", cats)
            ge = [f for f in report["findings"] if f["category"] == "grounding-error"]
            cc = [f for f in report["findings"] if f["category"] == "creates-collision"]
            self.assertEqual(ge[0]["severity"], "warning")
            self.assertEqual(cc[0]["severity"], "high")

    def test_garbage_response_repair_then_uninterpreted(self) -> None:
        """Garbage → repair attempted → still garbage → uninterpreted."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Gap", touch=["src/fake.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})
            caller = _CallRecorder(["total garbage", "still garbage"])
            result = interpret_evidence(rows, cfg={"interpretation_model": "test"}, caller=caller)
            # Two calls: initial + repair
            self.assertEqual(len(caller.calls), 2)
            self.assertIn("t1", result)
            self.assertEqual(result["t1"][0]["judgment"], "uninterpreted")

    def test_caller_raising_yields_uninterpreted_no_crash(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Gap", touch=["src/fake.py"]),
            ])
            tasks, _ = _read_tasks(repo)
            rows = collect_evidence(repo, tasks, {"symbol_check": False, "min_symbol_len": 4})

            def raising_caller(model, prompt, timeout=60):
                raise ConnectionError("ollama down")

            result = interpret_evidence(rows, cfg={"interpretation_model": "test"},
                                         caller=raising_caller)
            self.assertIn("t1", result)
            self.assertEqual(result["t1"][0]["judgment"], "uninterpreted")

    def test_uninterpreted_produces_info_finding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Gap", touch=["src/fake.py"]),
            ])
            caller = _CallRecorder(["", ""])  # empty = invalid
            report = scan_grounding(repo, cfg={"enabled": True, "symbol_check": False},
                                     caller=caller)
            cats = {f["category"] for f in report["findings"]}
            self.assertIn("uninterpreted-grounding", cats)
            un = [f for f in report["findings"] if f["category"] == "uninterpreted-grounding"]
            self.assertEqual(un[0]["severity"], "info")
            self.assertIn("model interpretation unavailable", un[0]["recommendation"])

    def test_create_intended_produces_no_finding(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Gap", touch=["src/fake.py"]),
            ])
            caller = _CallRecorder([
                _valid_interp_response("t1", "src/fake.py", "create-intended"),
            ])
            report = scan_grounding(repo, cfg={"enabled": True, "symbol_check": False},
                                     caller=caller)
            cats = {f["category"] for f in report["findings"]}
            self.assertNotIn("grounding-error", cats)


# ---------------------------------------------------------------------------
# Outside-repo: model never invoked
# ---------------------------------------------------------------------------


class OutsideRepoNoModelTests(unittest.TestCase):
    def test_outside_repo_yields_high_without_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Cross-repo", touch=["../other/x.py"]),
            ])
            caller = _CallRecorder([])
            report = scan_grounding(repo, cfg={"enabled": True, "symbol_check": False},
                                     caller=caller)
            self.assertEqual(len(caller.calls), 0)
            outside = [f for f in report["findings"] if f["category"] == "outside-repo-path"]
            self.assertEqual(len(outside), 1)
            self.assertEqual(outside[0]["severity"], "high")
            self.assertIn("../other/x.py", outside[0]["evidence"])


# ---------------------------------------------------------------------------
# Integration: scan_grounding high-level
# ---------------------------------------------------------------------------


class ScanGroundingIntegrationTests(unittest.TestCase):
    def test_disabled_returns_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _write_graph(Path(td) / ".workgraph", [])
            report = scan_grounding(Path(td), cfg={"enabled": False})
            self.assertFalse(report["enabled"])
            self.assertEqual(report["summary"]["findings_total"], 0)

    def test_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "dup.py").write_text("x = 1\n")
            _write_graph(repo / ".workgraph", [
                _task("t1", title="Mixed",
                      touch=["src/fake.py"],
                      creates=["src/dup.py"]),
            ])
            caller = _CallRecorder([
                json.dumps([
                    {"task_id": "t1", "item": "src/fake.py", "judgment": "grounding-error",
                     "rationale": "", "suggested_fix": ""},
                    {"task_id": "t1", "item": "src/dup.py", "judgment": "collision-risk",
                     "rationale": "", "suggested_fix": ""},
                ]),
            ])
            report = scan_grounding(repo, cfg={"enabled": True, "symbol_check": False},
                                     caller=caller)
            self.assertGreaterEqual(report["summary"]["findings_total"], 2)


class RunExistdriftCheckTests(unittest.TestCase):
    def test_disabled_returns_zeroed_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = run_existdrift_check(
                repo_name="demo", repo_path=Path(td),
                policy_cfg={"enabled": False},
            )
            self.assertFalse(report["enabled"])
            self.assertEqual(report["summary"]["findings_total"], 0)


# ---------------------------------------------------------------------------
# Evidence bundle — kept as-is (already doctrine-clean)
# ---------------------------------------------------------------------------


class EvidenceBundleTests(unittest.TestCase):
    def test_tree_excludes_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("x = 1\n")
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "dep.js").write_text("// junk\n")
            bundle = build_evidence_bundle(repo)
            self.assertIn("app.py", bundle)
            self.assertNotIn("node_modules", bundle)

    def test_detects_pytest_ini(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "pytest.ini").write_text("[pytest]\n")
            bundle = build_evidence_bundle(repo)
            self.assertIn("pytest", bundle.lower())

    def test_finds_hint_noun_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "heartbeat.py").write_text("x = 1\n")
            (repo / "src" / "router.py").write_text("x = 2\n")
            bundle = build_evidence_bundle(repo, hint_text="Add heartbeat endpoint")
            match_section = bundle.split("### Paths Matching Your Goal")[-1]
            self.assertIn("heartbeat.py", match_section)
            self.assertNotIn("router.py", match_section)

    def test_detects_doc_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "README.md").write_text("# Test\n")
            (repo / "CLAUDE.md").write_text("# Test\n")
            bundle = build_evidence_bundle(repo)
            self.assertIn("README.md", bundle)
            self.assertIn("CLAUDE.md", bundle)


# ---------------------------------------------------------------------------
# Lane + policy tests
# ---------------------------------------------------------------------------


class LaneRegistryTests(unittest.TestCase):
    def test_existdrift_resolves_in_internal_lanes(self) -> None:
        from driftdriver.cli.check import INTERNAL_LANES
        self.assertIn("existdrift", INTERNAL_LANES)
        self.assertEqual(INTERNAL_LANES["existdrift"], "driftdriver.existdrift")


class PolicyConfigTests(unittest.TestCase):
    def test_existdrift_field_exists_on_dataclass(self) -> None:
        from driftdriver.policy import _default_existdrift_cfg
        cfg = _default_existdrift_cfg()
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["severity_grounding_error"], "warning")
        self.assertEqual(cfg["severity_collision"], "high")
        self.assertEqual(cfg["severity_outside_repo"], "high")

    def test_existdrift_in_toml_template(self) -> None:
        from driftdriver.policy import _default_policy_text
        text = _default_policy_text()
        self.assertIn("[existdrift]", text)


# ---------------------------------------------------------------------------
# Helper: read tasks from graph.jsonl for tests
# ---------------------------------------------------------------------------


def _read_tasks(repo: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Minimal task reader for test setup."""
    from driftdriver.existdrift import _read_workgraph_tasks
    return _read_workgraph_tasks(repo)


if __name__ == "__main__":
    unittest.main()
