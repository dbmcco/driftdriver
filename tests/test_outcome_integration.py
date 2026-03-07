# ABOUTME: Integration tests for the outcome loop: cmd_outcome, CLI wiring, and lane weight computation.
# ABOUTME: Validates end-to-end outcome recording, validation, and weight-based routing escalation.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from driftdriver.outcome import OUTCOME_VALUES, read_outcomes, write_outcome, DriftOutcome
from driftdriver.smart_routing import compute_lane_weights
from driftdriver.wire import cmd_outcome


class TestCmdOutcome:
    def test_writes_to_outcomes_jsonl(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        result = cmd_outcome(
            project_dir=tmp_path,
            task_id="task-1",
            lane="coredrift",
            finding_key="scope-creep",
            recommendation="Reduce scope",
            action_taken="Split task",
            outcome="resolved",
        )
        assert result["recorded"] is True
        assert result["task_id"] == "task-1"
        assert result["lane"] == "coredrift"
        assert result["outcome"] == "resolved"

        ledger = wg_dir / "outcomes.jsonl"
        assert ledger.exists()
        outcomes = read_outcomes(ledger)
        assert len(outcomes) == 1
        assert outcomes[0].task_id == "task-1"
        assert outcomes[0].lane == "coredrift"
        assert outcomes[0].outcome == "resolved"

    def test_creates_workgraph_dir_if_missing(self, tmp_path: Path) -> None:
        result = cmd_outcome(
            project_dir=tmp_path,
            task_id="task-2",
            lane="specdrift",
            finding_key="missing-spec",
            recommendation="Add spec",
            action_taken="Created spec file",
            outcome="resolved",
        )
        assert result["recorded"] is True
        ledger = tmp_path / ".workgraph" / "outcomes.jsonl"
        assert ledger.exists()

    def test_validates_outcome_values(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="invalid outcome"):
            cmd_outcome(
                project_dir=tmp_path,
                task_id="task-3",
                lane="coredrift",
                finding_key="bad",
                recommendation="fix",
                action_taken="nothing",
                outcome="banana",
            )

    def test_appends_multiple_outcomes(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        for i, outcome_val in enumerate(OUTCOME_VALUES):
            cmd_outcome(
                project_dir=tmp_path,
                task_id=f"task-{i}",
                lane="coredrift",
                finding_key=f"finding-{i}",
                recommendation="do something",
                action_taken="did something",
                outcome=outcome_val,
            )
        outcomes = read_outcomes(wg_dir / "outcomes.jsonl")
        assert len(outcomes) == len(OUTCOME_VALUES)

    def test_evidence_defaults_to_empty(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        cmd_outcome(
            project_dir=tmp_path,
            task_id="task-ev",
            lane="coredrift",
            finding_key="k",
            recommendation="r",
            action_taken="a",
            outcome="resolved",
        )
        outcomes = read_outcomes(wg_dir / "outcomes.jsonl")
        assert outcomes[0].evidence == []


class TestComputeLaneWeights:
    """Tests for compute_lane_weights (the active outcome→routing path in smart_routing.py).

    Formula: weight = 1.0 + (ignored_rate + worsened_rate) - (0.5 * resolved_rate)
    Clamped to [0.2, 3.0].
    """

    def test_correct_weights(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        # 2 resolved, 1 ignored, 1 worsened in coredrift
        for outcome_val in ("resolved", "resolved", "ignored", "worsened"):
            write_outcome(
                ledger,
                DriftOutcome(
                    task_id="t1",
                    lane="coredrift",
                    finding_key="k",
                    recommendation="r",
                    action_taken="a",
                    outcome=outcome_val,
                ),
            )
        weights = compute_lane_weights(ledger, ["coredrift"])
        assert "coredrift" in weights
        # resolved_rate=0.5, ignored_rate=0.25, worsened_rate=0.25
        # weight = 1.0 + (0.25 + 0.25) - (0.5 * 0.5) = 1.0 + 0.5 - 0.25 = 1.25
        assert weights["coredrift"] == pytest.approx(1.25)

    def test_empty_file_returns_neutral(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        ledger.write_text("")
        weights = compute_lane_weights(ledger, ["coredrift"])
        assert weights["coredrift"] == 1.0

    def test_missing_file_returns_neutral(self, tmp_path: Path) -> None:
        ledger = tmp_path / "nonexistent.jsonl"
        weights = compute_lane_weights(ledger, ["coredrift"])
        assert weights["coredrift"] == 1.0

    def test_multiple_lanes(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        # coredrift: all resolved
        write_outcome(
            ledger,
            DriftOutcome(
                task_id="t1",
                lane="coredrift",
                finding_key="k",
                recommendation="r",
                action_taken="a",
                outcome="resolved",
            ),
        )
        # specdrift: all ignored
        write_outcome(
            ledger,
            DriftOutcome(
                task_id="t2",
                lane="specdrift",
                finding_key="k",
                recommendation="r",
                action_taken="a",
                outcome="ignored",
            ),
        )
        weights = compute_lane_weights(ledger, ["coredrift", "specdrift"])
        # coredrift: resolved_rate=1.0 -> 1.0 + 0 - 0.5 = 0.5
        assert weights["coredrift"] == pytest.approx(0.5)
        # specdrift: ignored_rate=1.0 -> 1.0 + 1.0 - 0 = 2.0
        assert weights["specdrift"] == pytest.approx(2.0)

    def test_all_worsened_produces_high_weight(self, tmp_path: Path) -> None:
        ledger = tmp_path / "outcomes.jsonl"
        for _ in range(3):
            write_outcome(
                ledger,
                DriftOutcome(
                    task_id="t1",
                    lane="coredrift",
                    finding_key="k",
                    recommendation="r",
                    action_taken="a",
                    outcome="worsened",
                ),
            )
        weights = compute_lane_weights(ledger, ["coredrift"])
        # worsened_rate=1.0 -> 1.0 + 1.0 - 0 = 2.0
        assert weights["coredrift"] == pytest.approx(2.0)


class TestOutcomeCLISubcommand:
    def test_outcome_subcommand_exists(self) -> None:
        from driftdriver.cli import _build_parser

        p = _build_parser()
        subparsers_action = next(
            a for a in p._actions if hasattr(a, "_name_parser_map")
        )
        cmds = set(subparsers_action._name_parser_map.keys())
        assert "outcome" in cmds

    def test_outcome_subcommand_parses_args(self) -> None:
        from driftdriver.cli import _build_parser

        p = _build_parser()
        args = p.parse_args([
            "outcome",
            "--task-id", "task-99",
            "--lane", "coredrift",
            "--finding-key", "scope-creep",
            "--recommendation", "reduce scope",
            "--action-taken", "split task",
            "--outcome", "resolved",
        ])
        assert args.task_id == "task-99"
        assert args.lane == "coredrift"
        assert args.finding_key == "scope-creep"
        assert args.recommendation == "reduce scope"
        assert args.action_taken == "split task"
        assert args.outcome == "resolved"
