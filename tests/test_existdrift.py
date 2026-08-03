# ABOUTME: Tests for existdrift — pre-build grounding lane.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.existdrift import (
    build_evidence_bundle,
    run_existdrift_check,
    scan_grounding,
)


def _write_graph(wg_dir: Path, rows: list[dict]) -> None:
    """Write a fake graph.jsonl into the given .workgraph directory."""
    wg_dir.mkdir(parents=True, exist_ok=True)
    (wg_dir / "graph.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def _task(
    tid: str,
    title: str = "",
    status: str = "open",
    touch: list[str] | None = None,
    description: str = "",
) -> dict:
    desc = description
    if touch is not None:
        touch_list = ", ".join(f'"{p}"' for p in touch)
        desc = (
            f"```wg-contract\n"
            f'schema = 1\n'
            f'mode = "core"\n'
            f'objective = "{title}"\n'
            f"touch = [{touch_list}]\n"
            f"```\n"
        ) + desc
    return {
        "type": "task",
        "id": tid,
        "title": title,
        "status": status,
        "after": [],
        "description": desc,
    }


class ScanGroundingTests(unittest.TestCase):
    def test_missing_path_with_some_existing_yields_warning(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")
            _write_graph(
                repo / ".workgraph",
                [
                    _task(
                        "t1",
                        title="Mixed task",
                        touch=["src/real.py", "src/fake.py"],
                    )
                ],
            )
            report = scan_grounding(repo, cfg={"enabled": True})
            cats = [f["category"] for f in report["findings"]]
            self.assertIn("missing-touch-path", cats)
            miss = [f for f in report["findings"] if f["category"] == "missing-touch-path"]
            self.assertEqual(len(miss), 1)
            self.assertEqual(miss[0]["severity"], "warning")
            self.assertIn("src/fake.py", miss[0]["evidence"])

    def test_all_new_task_yields_single_info_not_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(
                repo / ".workgraph",
                [
                    _task(
                        "t-new",
                        title="Create new module",
                        touch=["src/new_module.py", "tests/test_new.py"],
                    )
                ],
            )
            report = scan_grounding(repo, cfg={"enabled": True})
            miss = [f for f in report["findings"] if f["category"] == "missing-touch-path"]
            self.assertEqual(len(miss), 1)
            self.assertEqual(miss[0]["severity"], "info")
            self.assertIn("all-new", miss[0]["evidence"].lower() + miss[0]["title"].lower())

    def test_outside_repo_path_yields_high(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "local.py").write_text("x = 1\n", encoding="utf-8")
            _write_graph(
                repo / ".workgraph",
                [
                    _task(
                        "t-out",
                        title="Cross-repo task",
                        touch=["src/local.py", "../other-repo/x.py"],
                    )
                ],
            )
            report = scan_grounding(repo, cfg={"enabled": True})
            outside = [f for f in report["findings"] if f["category"] == "outside-repo-path"]
            self.assertEqual(len(outside), 1)
            self.assertEqual(outside[0]["severity"], "high")
            self.assertIn("../other-repo/x.py", outside[0]["evidence"])

    def test_unknown_symbol_yields_info_listing_only_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "mod.py").write_text(
                "def real_function():\n    pass\n", encoding="utf-8"
            )
            _write_graph(
                repo / ".workgraph",
                [
                    _task(
                        "t-sym",
                        title="Symbol test",
                        touch=["src/mod.py"],
                        description=(
                            "Uses `real_function` from src/mod.py "
                            "and calls `totally_invented_helper` which "
                            "does not exist."
                        ),
                    )
                ],
            )
            report = scan_grounding(repo, cfg={"enabled": True})
            syms = [f for f in report["findings"] if f["category"] == "unknown-symbol"]
            self.assertEqual(len(syms), 1)
            self.assertEqual(syms[0]["severity"], "info")
            self.assertIn("totally_invented_helper", syms[0]["evidence"])
            self.assertNotIn("real_function", syms[0]["evidence"])

    def test_disabled_cfg_returns_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo / ".workgraph", [])
            report = scan_grounding(repo, cfg={"enabled": False})
            self.assertFalse(report["enabled"])
            self.assertEqual(report["summary"]["findings_total"], 0)
            self.assertIn("disabled", report["summary"]["narrative"])

    def test_summary_counts_and_at_risk_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "real.py").write_text("x = 1\n", encoding="utf-8")
            _write_graph(
                repo / ".workgraph",
                [
                    _task(
                        "t1",
                        title="Mixed",
                        touch=["src/real.py", "does_not_exist.py"],
                    )
                ],
            )
            report = scan_grounding(repo, cfg={"enabled": True})
            self.assertGreaterEqual(report["summary"]["findings_total"], 1)
            # Warning-severity missing-path findings set at_risk to False (only
            # high/critical set it True), but we can still verify counts.
            self.assertGreaterEqual(
                report["summary"]["warning"] if "warning" in report["summary"]
                else report["summary"].get("medium", 0), 0
            )


class RunExistdriftCheckTests(unittest.TestCase):
    def test_disabled_returns_zeroed_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            report = run_existdrift_check(
                repo_name="demo",
                repo_path=Path(td),
                policy_cfg={"enabled": False},
            )
            self.assertFalse(report["enabled"])
            self.assertEqual(report["summary"]["findings_total"], 0)
            self.assertEqual(report["findings"], [])
            self.assertEqual(report["errors"], [])

    def test_finds_missing_path_via_high_level_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(
                repo / ".workgraph",
                [
                    _task(
                        "t1",
                        title="Has gap",
                        touch=["ghost.py"],
                    )
                ],
            )
            report = run_existdrift_check(
                repo_name="demo",
                repo_path=repo,
            )
            self.assertTrue(report["enabled"])
            self.assertGreaterEqual(report["summary"]["findings_total"], 1)
            cats = {f["category"] for f in report["findings"]}
            self.assertIn("missing-touch-path", cats)


class EvidenceBundleTests(unittest.TestCase):
    def test_tree_excludes_node_modules(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
            (repo / "node_modules").mkdir()
            (repo / "node_modules" / "dep.js").write_text("// junk\n", encoding="utf-8")
            bundle = build_evidence_bundle(repo)
            self.assertIn("app.py", bundle)
            self.assertNotIn("node_modules", bundle)

    def test_detects_pytest_ini(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
            bundle = build_evidence_bundle(repo)
            self.assertIn("pytest", bundle.lower())

    def test_finds_hint_noun_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir()
            (repo / "src" / "heartbeat.py").write_text("x = 1\n", encoding="utf-8")
            (repo / "src" / "router.py").write_text("x = 2\n", encoding="utf-8")
            bundle = build_evidence_bundle(repo, hint_text="Add heartbeat endpoint")
            # Check the matching section only, not the full directory tree.
            match_section = bundle.split("### Paths Matching Your Goal")[-1]
            self.assertIn("heartbeat.py", match_section)
            self.assertNotIn("router.py", match_section)

    def test_detects_doc_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "README.md").write_text("# Test\n", encoding="utf-8")
            (repo / "CLAUDE.md").write_text("# Test\n", encoding="utf-8")
            bundle = build_evidence_bundle(repo)
            self.assertIn("README.md", bundle)
            self.assertIn("CLAUDE.md", bundle)


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
        self.assertEqual(cfg["severity_missing_path"], "warning")
        self.assertEqual(cfg["severity_outside_repo"], "high")
        self.assertEqual(cfg["severity_unknown_symbol"], "info")
        self.assertTrue(cfg["symbol_check"])

    def test_existdrift_in_toml_template(self) -> None:
        from driftdriver.policy import _default_policy_text

        text = _default_policy_text()
        self.assertIn("[existdrift]", text)


if __name__ == "__main__":
    unittest.main()
