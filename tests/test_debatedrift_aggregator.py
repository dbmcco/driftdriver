# ABOUTME: Tests for debatedrift log aggregator — sentinel detection, round counting, merge.
# ABOUTME: Uses real temp files; no mocks.
from __future__ import annotations

import tempfile
from pathlib import Path

from driftdriver.debatedrift.aggregator import (
    AggregatorState,
    count_round_ends,
    detect_sentinel,
    merge_logs,
)


class TestCountRoundEnds:
    def test_zero_when_empty(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("")
            path = Path(f.name)
        assert count_round_ends(path) == 0

    def test_counts_round_end_markers(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("some output\n[ROUND:END]\nmore output\n[ROUND:END]\n")
            path = Path(f.name)
        assert count_round_ends(path) == 2

    def test_partial_marker_not_counted(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("ROUND:END without brackets\n")
            path = Path(f.name)
        assert count_round_ends(path) == 0


class TestDetectSentinel:
    def test_detects_concluded(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("Some proxy output\nDEBATE:CONCLUDED\n")
            path = Path(f.name)
        assert detect_sentinel(path, "DEBATE:CONCLUDED") is True

    def test_detects_deadlock(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("DEBATE:DEADLOCK\n")
            path = Path(f.name)
        assert detect_sentinel(path, "DEBATE:DEADLOCK") is True

    def test_no_false_positive(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as f:
            f.write("The debate is ongoing\n")
            path = Path(f.name)
        assert detect_sentinel(path, "DEBATE:CONCLUDED") is False

    def test_returns_false_when_file_missing(self) -> None:
        assert detect_sentinel(Path("/tmp/nonexistent_xyz_log.txt"), "DEBATE:CONCLUDED") is False


class TestMergeLogs:
    def test_merged_contains_both_panes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text(
                "2026-03-17T10:00:00 Debater A says hello\n", encoding="utf-8"
            )
            (debate_dir / "pane-b.log").write_text(
                "2026-03-17T10:00:05 Debater B responds\n", encoding="utf-8"
            )
            out = debate_dir / "debate.log"
            merge_logs(debate_dir=debate_dir, output_path=out)
            content = out.read_text(encoding="utf-8")
            assert "Debater A says hello" in content
            assert "Debater B responds" in content

    def test_merge_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("line1\n", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("line2\n", encoding="utf-8")
            out = debate_dir / "debate.log"
            merge_logs(debate_dir=debate_dir, output_path=out)
            first = out.read_text(encoding="utf-8")
            merge_logs(debate_dir=debate_dir, output_path=out)
            second = out.read_text(encoding="utf-8")
            assert first == second


class TestAggregatorState:
    def test_initial_state(self) -> None:
        state = AggregatorState()
        assert state.round_count == 0
        assert state.terminated is False
        assert state.termination_kind is None

    def test_update_increments_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("[ROUND:END]\n[ROUND:END]\n", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("[ROUND:END]\n", encoding="utf-8")
            (debate_dir / "pane-c.log").write_text("proxy listening\n", encoding="utf-8")
            state = AggregatorState()
            state.update(debate_dir=debate_dir)
            assert state.round_count == 3

    def test_detects_concluded_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-c.log").write_text("DEBATE:CONCLUDED\n", encoding="utf-8")
            state = AggregatorState()
            state.update(debate_dir=debate_dir)
            assert state.terminated is True
            assert state.termination_kind == "concluded"

    def test_detects_deadlock_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            debate_dir = Path(td)
            (debate_dir / "pane-a.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-b.log").write_text("", encoding="utf-8")
            (debate_dir / "pane-c.log").write_text("DEBATE:DEADLOCK\n", encoding="utf-8")
            state = AggregatorState()
            state.update(debate_dir=debate_dir)
            assert state.terminated is True
            assert state.termination_kind == "deadlock"
