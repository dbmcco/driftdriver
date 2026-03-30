# ABOUTME: Tests for signal-gate integration in driftdriver's own LLM call sites.
# ABOUTME: Validates that quality_planner, decompose, and evaluator respect should_fire() gating.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from driftdriver.signal_gate import record_fire


# ---------------------------------------------------------------------------
# quality_planner — signal gate integration
# ---------------------------------------------------------------------------


class TestQualityPlannerGate:
    """quality_planner.plan_from_spec should skip LLM when gate says no."""

    def _write_policy(self, tmp_path: Path, enabled: bool = True) -> Path:
        policy = tmp_path / ".workgraph" / "drift-policy.toml"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(
            f"[signal_gates]\nquality_planner = {str(enabled).lower()}\n",
            encoding="utf-8",
        )
        return policy

    def _write_spec(self, tmp_path: Path, content: str = "# Spec\nBuild a widget.") -> Path:
        spec = tmp_path / "spec.md"
        spec.write_text(content, encoding="utf-8")
        return spec

    @patch("driftdriver.quality_planner._call_llm")
    def test_skips_llm_when_gate_suppresses(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        """When gate is enabled and content unchanged, _call_llm should NOT be called."""
        from driftdriver.quality_planner import plan_from_spec

        repo_path = tmp_path
        self._write_policy(tmp_path, enabled=True)
        spec = self._write_spec(tmp_path)
        gate_dir = tmp_path / ".workgraph" / ".signal-gates"

        # First call — gate fires (no prior state), LLM returns empty plan
        mock_llm.return_value = '{"tasks": []}'
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        assert mock_llm.call_count == 1

        # Second call with same spec — gate should suppress
        mock_llm.reset_mock()
        result = plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        assert mock_llm.call_count == 0
        assert result.tasks == []

    @patch("driftdriver.quality_planner._call_llm")
    def test_fires_llm_when_content_changes(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        """When spec content changes, gate should allow LLM call."""
        from driftdriver.quality_planner import plan_from_spec

        repo_path = tmp_path
        self._write_policy(tmp_path, enabled=True)
        spec = self._write_spec(tmp_path, "# Spec v1\nBuild widget.")
        gate_dir = tmp_path / ".workgraph" / ".signal-gates"

        # First call
        mock_llm.return_value = '{"tasks": []}'
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        assert mock_llm.call_count == 1

        # Change spec content
        spec.write_text("# Spec v2\nBuild a different widget.", encoding="utf-8")
        mock_llm.reset_mock()
        mock_llm.return_value = '{"tasks": []}'
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        assert mock_llm.call_count == 1

    @patch("driftdriver.quality_planner._call_llm")
    def test_no_gate_when_disabled(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        """When gate is disabled, LLM is always called."""
        from driftdriver.quality_planner import plan_from_spec

        repo_path = tmp_path
        self._write_policy(tmp_path, enabled=False)
        spec = self._write_spec(tmp_path)

        mock_llm.return_value = '{"tasks": []}'
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        assert mock_llm.call_count == 2

    @patch("driftdriver.quality_planner._call_llm")
    def test_no_gate_when_no_policy(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        """When no drift-policy.toml exists, LLM is always called (gate disabled by default)."""
        from driftdriver.quality_planner import plan_from_spec

        repo_path = tmp_path
        spec = self._write_spec(tmp_path)

        mock_llm.return_value = '{"tasks": []}'
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
        assert mock_llm.call_count == 2

    @patch("driftdriver.quality_planner._call_llm")
    def test_dry_run_skips_gate(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        """Dry run should not trigger gate or LLM."""
        from driftdriver.quality_planner import plan_from_spec

        repo_path = tmp_path
        self._write_policy(tmp_path, enabled=True)
        spec = self._write_spec(tmp_path)

        result = plan_from_spec(spec_path=spec, repo_path=repo_path, dry_run=True, model="sonnet")
        assert mock_llm.call_count == 0
        assert result.tasks == []


# ---------------------------------------------------------------------------
# decompose — signal gate integration
# ---------------------------------------------------------------------------


class TestDecomposeGate:
    """decompose.decompose_goal should skip LLM when gate says no."""

    def _setup_wg_dir(self, tmp_path: Path, gate_enabled: bool = True) -> Path:
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir(parents=True, exist_ok=True)
        policy = wg_dir / "drift-policy.toml"
        policy.write_text(
            f"[signal_gates]\ndecompose = {str(gate_enabled).lower()}\n",
            encoding="utf-8",
        )
        # Create directive log dir
        (wg_dir / "service" / "directives").mkdir(parents=True, exist_ok=True)
        return wg_dir

    @patch("driftdriver.decompose._call_llm")
    def test_skips_llm_when_gate_suppresses(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        from driftdriver.decompose import decompose_goal
        from driftdriver.directives import DirectiveLog

        wg_dir = self._setup_wg_dir(tmp_path, gate_enabled=True)
        log = DirectiveLog(wg_dir / "service" / "directives")

        # First call — fires
        mock_llm.return_value = [{"id": "t1", "title": "Task 1", "description": "Do thing", "after": []}]
        with patch("driftdriver.decompose.ExecutorShim") as mock_shim_cls:
            mock_shim_cls.return_value.execute.return_value = "completed"
            decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="some context")
        assert mock_llm.call_count == 1

        # Second call with same goal+context — gate should suppress
        mock_llm.reset_mock()
        with patch("driftdriver.decompose.ExecutorShim") as mock_shim_cls:
            result = decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="some context")
        assert mock_llm.call_count == 0
        assert result["task_count"] == 0

    @patch("driftdriver.decompose._call_llm")
    def test_fires_llm_when_goal_changes(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        from driftdriver.decompose import decompose_goal
        from driftdriver.directives import DirectiveLog

        wg_dir = self._setup_wg_dir(tmp_path, gate_enabled=True)
        log = DirectiveLog(wg_dir / "service" / "directives")

        mock_llm.return_value = [{"id": "t1", "title": "Task 1", "description": "Do thing", "after": []}]
        with patch("driftdriver.decompose.ExecutorShim") as mock_shim_cls:
            mock_shim_cls.return_value.execute.return_value = "completed"
            decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="ctx")

        # Change goal
        mock_llm.reset_mock()
        mock_llm.return_value = [{"id": "t2", "title": "Task 2", "description": "Do other", "after": []}]
        with patch("driftdriver.decompose.ExecutorShim") as mock_shim_cls:
            mock_shim_cls.return_value.execute.return_value = "completed"
            decompose_goal(goal="Build Y", wg_dir=wg_dir, directive_log=log, context="ctx")
        assert mock_llm.call_count == 1

    @patch("driftdriver.decompose._call_llm")
    def test_no_gate_when_disabled(self, mock_llm: MagicMock, tmp_path: Path) -> None:
        from driftdriver.decompose import decompose_goal
        from driftdriver.directives import DirectiveLog

        wg_dir = self._setup_wg_dir(tmp_path, gate_enabled=False)
        log = DirectiveLog(wg_dir / "service" / "directives")

        mock_llm.return_value = []
        with patch("driftdriver.decompose.ExecutorShim"):
            decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="ctx")
            decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="ctx")
        assert mock_llm.call_count == 2


# ---------------------------------------------------------------------------
# evaluator — signal gate integration
# ---------------------------------------------------------------------------


class TestEvaluatorGate:
    """evaluator should skip LLM batches when gate says content unchanged."""

    def test_gate_suppresses_identical_batch(self, tmp_path: Path) -> None:
        """When the same batch of signals is evaluated twice, second call is gated."""
        from driftdriver.intelligence.evaluator import (
            _system_prompt,
            _build_user_prompt,
        )
        from driftdriver.signal_gate import should_fire, record_fire

        gate_dir = tmp_path / ".signal-gates"
        agent = "evaluator_repo_update"

        # Simulate a user prompt for a batch
        user_prompt = "Evaluate batch of signals: [signal-1, signal-2]"

        # First check — should fire
        assert should_fire(agent, user_prompt, gate_dir=gate_dir) is True
        record_fire(agent, user_prompt, gate_dir=gate_dir)

        # Same prompt — should suppress
        assert should_fire(agent, user_prompt, gate_dir=gate_dir) is False

    def test_gate_fires_on_new_batch(self, tmp_path: Path) -> None:
        """Different batch content should fire."""
        from driftdriver.signal_gate import should_fire, record_fire

        gate_dir = tmp_path / ".signal-gates"
        agent = "evaluator_repo_update"

        prompt1 = "Evaluate: [signal-1]"
        assert should_fire(agent, prompt1, gate_dir=gate_dir) is True
        record_fire(agent, prompt1, gate_dir=gate_dir)

        prompt2 = "Evaluate: [signal-1, signal-3]"
        assert should_fire(agent, prompt2, gate_dir=gate_dir) is True


# ---------------------------------------------------------------------------
# Finding ledger tracking for gate suppressions
# ---------------------------------------------------------------------------


class TestGateSuppressionTracking:
    """Gate suppressions should be recorded in the finding ledger."""

    def test_quality_planner_records_suppression(self, tmp_path: Path) -> None:
        """When quality_planner gate suppresses, a finding is recorded."""
        from driftdriver.quality_planner import plan_from_spec

        repo_path = tmp_path
        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir(parents=True, exist_ok=True)
        policy = wg_dir / "drift-policy.toml"
        policy.write_text(
            "[signal_gates]\nquality_planner = true\n",
            encoding="utf-8",
        )
        spec = tmp_path / "spec.md"
        spec.write_text("# Spec\nBuild widget.", encoding="utf-8")

        with patch("driftdriver.quality_planner._call_llm") as mock_llm:
            mock_llm.return_value = '{"tasks": []}'
            # First call fires
            plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")
            # Second call suppressed
            plan_from_spec(spec_path=spec, repo_path=repo_path, model="sonnet")

        ledger = wg_dir / "finding-ledger.jsonl"
        assert ledger.exists()
        entries = [json.loads(line) for line in ledger.read_text().strip().splitlines()]
        suppression_entries = [e for e in entries if e.get("finding_type") == "signal_gate_suppressed"]
        assert len(suppression_entries) >= 1
        assert suppression_entries[0]["lane"] == "quality_planner"

    def test_decompose_records_suppression(self, tmp_path: Path) -> None:
        """When decompose gate suppresses, a finding is recorded."""
        from driftdriver.decompose import decompose_goal
        from driftdriver.directives import DirectiveLog

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir(parents=True, exist_ok=True)
        (wg_dir / "service" / "directives").mkdir(parents=True, exist_ok=True)
        policy = wg_dir / "drift-policy.toml"
        policy.write_text(
            "[signal_gates]\ndecompose = true\n",
            encoding="utf-8",
        )
        log = DirectiveLog(wg_dir / "service" / "directives")

        with patch("driftdriver.decompose._call_llm") as mock_llm:
            mock_llm.return_value = [{"id": "t1", "title": "T1", "description": "D", "after": []}]
            with patch("driftdriver.decompose.ExecutorShim") as mock_shim_cls:
                mock_shim_cls.return_value.execute.return_value = "completed"
                decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="ctx")
                # Second call — suppressed
                decompose_goal(goal="Build X", wg_dir=wg_dir, directive_log=log, context="ctx")

        ledger = wg_dir / "finding-ledger.jsonl"
        assert ledger.exists()
        entries = [json.loads(line) for line in ledger.read_text().strip().splitlines()]
        suppression_entries = [e for e in entries if e.get("finding_type") == "signal_gate_suppressed"]
        assert len(suppression_entries) >= 1
        assert suppression_entries[0]["lane"] == "decompose"
