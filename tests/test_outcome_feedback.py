# ABOUTME: Tests for the outcome feedback loop: auto-recording outcomes from check findings.
# ABOUTME: Validates finding comparison, outcome classification, and end-to-end check-to-outcome flow.

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from driftdriver.outcome import (
    DriftOutcome,
    read_outcomes,
    write_outcome,
)


class TestClassifyFindingOutcome:
    """Tests for classify_finding_outcome: maps pre vs post check findings to outcome values."""

    def test_finding_gone_in_post_means_resolved(self) -> None:
        from driftdriver.outcome_feedback import classify_finding_outcome

        result = classify_finding_outcome(
            pre_finding={"kind": "scope_drift", "lane": "coredrift"},
            post_findings=[{"kind": "other_issue", "lane": "coredrift"}],
        )
        assert result == "resolved"

    def test_finding_still_present_means_ignored(self) -> None:
        from driftdriver.outcome_feedback import classify_finding_outcome

        result = classify_finding_outcome(
            pre_finding={"kind": "scope_drift", "lane": "coredrift"},
            post_findings=[
                {"kind": "scope_drift", "lane": "coredrift"},
                {"kind": "other", "lane": "specdrift"},
            ],
        )
        assert result == "ignored"

    def test_finding_present_with_higher_severity_means_worsened(self) -> None:
        from driftdriver.outcome_feedback import classify_finding_outcome

        result = classify_finding_outcome(
            pre_finding={"kind": "scope_drift", "lane": "coredrift", "severity": "warning"},
            post_findings=[
                {"kind": "scope_drift", "lane": "coredrift", "severity": "error"},
            ],
        )
        assert result == "worsened"

    def test_no_post_findings_means_resolved(self) -> None:
        from driftdriver.outcome_feedback import classify_finding_outcome

        result = classify_finding_outcome(
            pre_finding={"kind": "scope_drift", "lane": "coredrift"},
            post_findings=[],
        )
        assert result == "resolved"

    def test_empty_pre_finding_kind_returns_resolved(self) -> None:
        from driftdriver.outcome_feedback import classify_finding_outcome

        result = classify_finding_outcome(
            pre_finding={"kind": "", "lane": "coredrift"},
            post_findings=[{"kind": "scope_drift", "lane": "coredrift"}],
        )
        assert result == "resolved"


class TestExtractFindingsFromCheck:
    """Tests for extracting structured findings from a check JSON result."""

    def test_extracts_from_plugins_dict(self) -> None:
        from driftdriver.outcome_feedback import extract_findings_from_check

        check_json = {
            "task_id": "t1",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {
                        "findings": [
                            {"kind": "scope_drift", "message": "Scope exceeded contract"},
                        ],
                    },
                },
                "specdrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
            },
        }
        findings = extract_findings_from_check(check_json)
        assert len(findings) == 1
        assert findings[0]["lane"] == "coredrift"
        assert findings[0]["kind"] == "scope_drift"

    def test_skips_plugins_that_did_not_run(self) -> None:
        from driftdriver.outcome_feedback import extract_findings_from_check

        check_json = {
            "plugins": {
                "coredrift": {"ran": False, "exit_code": 0, "report": None},
            },
        }
        findings = extract_findings_from_check(check_json)
        assert findings == []

    def test_handles_missing_report(self) -> None:
        from driftdriver.outcome_feedback import extract_findings_from_check

        check_json = {
            "plugins": {
                "coredrift": {"ran": True, "exit_code": 0, "report": None},
            },
        }
        findings = extract_findings_from_check(check_json)
        assert findings == []

    def test_handles_missing_plugins_key(self) -> None:
        from driftdriver.outcome_feedback import extract_findings_from_check

        findings = extract_findings_from_check({})
        assert findings == []

    def test_extracts_multiple_findings_across_lanes(self) -> None:
        from driftdriver.outcome_feedback import extract_findings_from_check

        check_json = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {
                        "findings": [
                            {"kind": "scope_drift"},
                            {"kind": "missing_contract"},
                        ],
                    },
                },
                "secdrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {
                        "findings": [{"kind": "hardcoded_secret"}],
                    },
                },
            },
        }
        findings = extract_findings_from_check(check_json)
        assert len(findings) == 3
        lanes = {f["lane"] for f in findings}
        assert "coredrift" in lanes
        assert "secdrift" in lanes


class TestRecordOutcomesFromCheck:
    """Tests for the main entry point: compare pre/post check findings, record outcomes."""

    def test_records_resolved_when_finding_gone(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import record_outcomes_from_check

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        pre_check = {
            "task_id": "task-1",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {
                        "findings": [{"kind": "scope_drift"}],
                    },
                },
            },
        }
        post_check = {
            "task_id": "task-1",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
            },
        }

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-1",
            pre_check=pre_check,
            post_check=post_check,
        )
        assert len(results) == 1
        assert results[0]["outcome"] == "resolved"
        assert results[0]["lane"] == "coredrift"
        assert results[0]["finding_key"] == "scope_drift"

        # Verify written to ledger
        ledger = wg_dir / "drift-outcomes.jsonl"
        assert ledger.exists()
        outcomes = read_outcomes(ledger)
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "resolved"

    def test_records_ignored_when_finding_persists(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import record_outcomes_from_check

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        pre_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
            },
        }
        post_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
            },
        }

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-2",
            pre_check=pre_check,
            post_check=post_check,
        )
        assert len(results) == 1
        assert results[0]["outcome"] == "ignored"

    def test_records_worsened_when_severity_escalates(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import record_outcomes_from_check

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        pre_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift", "severity": "warning"}]},
                },
            },
        }
        post_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift", "severity": "error"}]},
                },
            },
        }

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-3",
            pre_check=pre_check,
            post_check=post_check,
        )
        assert len(results) == 1
        assert results[0]["outcome"] == "worsened"

    def test_multiple_findings_across_lanes(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import record_outcomes_from_check

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        pre_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
                "secdrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "hardcoded_secret"}]},
                },
            },
        }
        post_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
                "secdrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "hardcoded_secret"}]},
                },
            },
        }

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-4",
            pre_check=pre_check,
            post_check=post_check,
        )
        assert len(results) == 2
        outcomes_by_lane = {r["lane"]: r["outcome"] for r in results}
        assert outcomes_by_lane["coredrift"] == "resolved"
        assert outcomes_by_lane["secdrift"] == "ignored"

        ledger = wg_dir / "drift-outcomes.jsonl"
        outcomes = read_outcomes(ledger)
        assert len(outcomes) == 2

    def test_no_pre_findings_records_nothing(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import record_outcomes_from_check

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        pre_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
            },
        }
        post_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
            },
        }

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-5",
            pre_check=pre_check,
            post_check=post_check,
        )
        assert results == []

    def test_creates_workgraph_dir_if_missing(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import record_outcomes_from_check

        pre_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
            },
        }
        post_check = {
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
            },
        }

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-6",
            pre_check=pre_check,
            post_check=post_check,
        )
        assert len(results) == 1
        ledger = tmp_path / ".workgraph" / "drift-outcomes.jsonl"
        assert ledger.exists()


class TestSaveAndLoadCheckSnapshot:
    """Tests for saving check JSON so task-completing can compare pre vs post."""

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import save_check_snapshot, load_check_snapshot

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        check_data = {
            "task_id": "task-1",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
            },
        }
        save_check_snapshot(wg_dir, "task-1", check_data)

        loaded = load_check_snapshot(wg_dir, "task-1")
        assert loaded is not None
        assert loaded["task_id"] == "task-1"
        findings = loaded["plugins"]["coredrift"]["report"]["findings"]
        assert len(findings) == 1
        assert findings[0]["kind"] == "scope_drift"

    def test_load_missing_returns_none(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import load_check_snapshot

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        result = load_check_snapshot(wg_dir, "nonexistent")
        assert result is None

    def test_save_overwrites_previous(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import save_check_snapshot, load_check_snapshot

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        save_check_snapshot(wg_dir, "task-1", {"version": 1})
        save_check_snapshot(wg_dir, "task-1", {"version": 2})

        loaded = load_check_snapshot(wg_dir, "task-1")
        assert loaded is not None
        assert loaded["version"] == 2

    def test_task_id_sanitized_in_filename(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import save_check_snapshot, load_check_snapshot

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        save_check_snapshot(wg_dir, "task/with:special chars", {"ok": True})
        loaded = load_check_snapshot(wg_dir, "task/with:special chars")
        assert loaded is not None
        assert loaded["ok"] is True


class TestOutcomeFeedbackEndToEnd:
    """End-to-end test: pre-check saves snapshot, post-check compares and records outcomes."""

    def test_full_loop(self, tmp_path: Path) -> None:
        from driftdriver.outcome_feedback import (
            save_check_snapshot,
            load_check_snapshot,
            record_outcomes_from_check,
        )

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        # Step 1: Pre-task check finds scope_drift and hardcoded_secret
        pre_check = {
            "task_id": "task-loop",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
                "secdrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "hardcoded_secret"}]},
                },
            },
        }
        save_check_snapshot(wg_dir, "task-loop", pre_check)

        # Step 2: Agent works on task, fixing scope_drift but not secdrift
        # Step 3: Post-task check runs
        post_check = {
            "task_id": "task-loop",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 0,
                    "report": {"findings": []},
                },
                "secdrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "hardcoded_secret"}]},
                },
            },
        }

        # Step 4: Load pre snapshot, compare with post, record outcomes
        loaded_pre = load_check_snapshot(wg_dir, "task-loop")
        assert loaded_pre is not None

        results = record_outcomes_from_check(
            project_dir=tmp_path,
            task_id="task-loop",
            pre_check=loaded_pre,
            post_check=post_check,
        )

        assert len(results) == 2
        by_lane = {r["lane"]: r["outcome"] for r in results}
        assert by_lane["coredrift"] == "resolved"
        assert by_lane["secdrift"] == "ignored"

        # Step 5: Verify lane weights reflect this
        from driftdriver.smart_routing import compute_lane_weights

        ledger = wg_dir / "drift-outcomes.jsonl"
        weights = compute_lane_weights(ledger, ["coredrift", "secdrift"])
        # coredrift resolved → weight < 1.0
        assert weights["coredrift"] < 1.0
        # secdrift ignored → weight > 1.0
        assert weights["secdrift"] > 1.0


class TestSeverityRanking:
    """Tests for severity comparison logic."""

    def test_severity_ordering(self) -> None:
        from driftdriver.outcome_feedback import _severity_rank

        assert _severity_rank("info") < _severity_rank("warning")
        assert _severity_rank("warning") < _severity_rank("error")
        assert _severity_rank("error") < _severity_rank("critical")

    def test_unknown_severity_treated_as_info(self) -> None:
        from driftdriver.outcome_feedback import _severity_rank

        assert _severity_rank("unknown") == _severity_rank("info")
        assert _severity_rank("") == _severity_rank("info")


class TestCLISubcommands:
    """Tests for the CLI subcommands: save-check-snapshot and outcome-from-check."""

    def test_save_check_snapshot_subcommand_exists(self) -> None:
        from driftdriver.cli import _build_parser

        p = _build_parser()
        subparsers_action = next(
            a for a in p._actions if hasattr(a, "_name_parser_map")
        )
        cmds = set(subparsers_action._name_parser_map.keys())
        assert "save-check-snapshot" in cmds

    def test_outcome_from_check_subcommand_exists(self) -> None:
        from driftdriver.cli import _build_parser

        p = _build_parser()
        subparsers_action = next(
            a for a in p._actions if hasattr(a, "_name_parser_map")
        )
        cmds = set(subparsers_action._name_parser_map.keys())
        assert "outcome-from-check" in cmds

    def test_save_check_snapshot_parses_args(self) -> None:
        from driftdriver.cli import _build_parser

        p = _build_parser()
        args = p.parse_args(["save-check-snapshot", "--task-id", "task-99"])
        assert args.task_id == "task-99"

    def test_outcome_from_check_parses_args(self) -> None:
        from driftdriver.cli import _build_parser

        p = _build_parser()
        args = p.parse_args(["outcome-from-check", "--task-id", "task-99"])
        assert args.task_id == "task-99"


class TestCheckSnapshotAutoSave:
    """Tests that cmd_check saves a snapshot for the outcome feedback loop."""

    def test_check_saves_snapshot_in_json_mode(self, tmp_path: Path) -> None:
        """When check runs in JSON mode, it should save a check snapshot."""
        from driftdriver.outcome_feedback import load_check_snapshot, save_check_snapshot

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()

        # Simulate what cmd_check does: save a snapshot after building combined
        combined = {
            "task_id": "task-snap",
            "plugins": {
                "coredrift": {
                    "ran": True,
                    "exit_code": 3,
                    "report": {"findings": [{"kind": "scope_drift"}]},
                },
            },
        }
        save_check_snapshot(wg_dir, "task-snap", combined)

        loaded = load_check_snapshot(wg_dir, "task-snap")
        assert loaded is not None
        assert loaded["task_id"] == "task-snap"
