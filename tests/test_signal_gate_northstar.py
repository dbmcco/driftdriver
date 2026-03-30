# ABOUTME: Tests for signal-gate integration with northstardrift and upstream_tracker.
# ABOUTME: Validates LLM call gating, disk-persisted result caching, and token spend reduction.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from driftdriver.signal_gate import (
    should_fire,
    record_fire,
)


# ---------------------------------------------------------------------------
# northstardrift: _score_alignment_with_llm signal gate
# ---------------------------------------------------------------------------


class TestAlignmentSignalGate:
    """Signal gate replaces in-memory _alignment_cache in _score_alignment_with_llm."""

    def _make_tasks(self, titles: list[str]) -> list[dict[str, Any]]:
        return [{"id": f"t{i}", "title": t} for i, t in enumerate(titles)]

    def test_first_call_fires_and_calls_llm(self, tmp_path: Path) -> None:
        """First alignment call should fire (no prior state) and invoke the LLM."""
        from driftdriver.northstardrift import _score_alignment_with_llm

        tasks = self._make_tasks(["add auth", "fix bug"])
        llm_called = []

        def fake_subprocess_run(*args, **kwargs):
            llm_called.append(True)

            class FakeResult:
                returncode = 0
                stdout = json.dumps({
                    "result": json.dumps({"score": 75, "findings": ["drift detected"]})
                })
                stderr = ""
            return FakeResult()

        with patch("driftdriver.northstardrift.subprocess.run", side_effect=fake_subprocess_run):
            with patch("driftdriver.northstardrift.extract_usage_from_claude_json", return_value=None):
                score, findings = _score_alignment_with_llm(
                    "Keep code in sync", tasks, gate_dir=tmp_path,
                )

        assert len(llm_called) == 1
        assert score == 75.0
        assert findings == ["drift detected"]

    def test_second_call_same_input_skips_llm(self, tmp_path: Path) -> None:
        """Same (statement, tasks) should skip LLM on second call."""
        from driftdriver.northstardrift import _score_alignment_with_llm

        tasks = self._make_tasks(["add auth", "fix bug"])
        llm_call_count = []

        def fake_subprocess_run(*args, **kwargs):
            llm_call_count.append(True)

            class FakeResult:
                returncode = 0
                stdout = json.dumps({
                    "result": json.dumps({"score": 80, "findings": []})
                })
                stderr = ""
            return FakeResult()

        with patch("driftdriver.northstardrift.subprocess.run", side_effect=fake_subprocess_run):
            with patch("driftdriver.northstardrift.extract_usage_from_claude_json", return_value=None):
                score1, f1 = _score_alignment_with_llm(
                    "Keep code in sync", tasks, gate_dir=tmp_path,
                )
                score2, f2 = _score_alignment_with_llm(
                    "Keep code in sync", tasks, gate_dir=tmp_path,
                )

        assert len(llm_call_count) == 1, "LLM should only be called once"
        assert score1 == score2 == 80.0
        assert f1 == f2 == []

    def test_changed_tasks_fires_again(self, tmp_path: Path) -> None:
        """When task list changes, signal gate should fire and call LLM again."""
        from driftdriver.northstardrift import _score_alignment_with_llm

        call_count = []

        def fake_subprocess_run(*args, **kwargs):
            call_count.append(True)

            class FakeResult:
                returncode = 0
                stdout = json.dumps({
                    "result": json.dumps({"score": 70, "findings": ["misaligned"]})
                })
                stderr = ""
            return FakeResult()

        with patch("driftdriver.northstardrift.subprocess.run", side_effect=fake_subprocess_run):
            with patch("driftdriver.northstardrift.extract_usage_from_claude_json", return_value=None):
                _score_alignment_with_llm(
                    "Keep code in sync",
                    self._make_tasks(["task A"]),
                    gate_dir=tmp_path,
                )
                _score_alignment_with_llm(
                    "Keep code in sync",
                    self._make_tasks(["task B"]),
                    gate_dir=tmp_path,
                )

        assert len(call_count) == 2, "Different tasks should trigger separate LLM calls"

    def test_result_persists_to_disk(self, tmp_path: Path) -> None:
        """Cached result is written to disk so it survives process restart."""
        from driftdriver.northstardrift import _score_alignment_with_llm

        tasks = self._make_tasks(["deploy feature"])

        def fake_subprocess_run(*args, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = json.dumps({
                    "result": json.dumps({"score": 90, "findings": ["good alignment"]})
                })
                stderr = ""
            return FakeResult()

        with patch("driftdriver.northstardrift.subprocess.run", side_effect=fake_subprocess_run):
            with patch("driftdriver.northstardrift.extract_usage_from_claude_json", return_value=None):
                _score_alignment_with_llm(
                    "Ship fast", tasks, gate_dir=tmp_path,
                )

        # Verify result file exists on disk
        result_file = tmp_path / "northstardrift-alignment.result.json"
        assert result_file.exists(), "Result should be persisted to disk"
        data = json.loads(result_file.read_text(encoding="utf-8"))
        assert data["score"] == 90.0
        assert data["findings"] == ["good alignment"]

    def test_empty_tasks_returns_default(self, tmp_path: Path) -> None:
        """Empty task list should return 50.0 without calling LLM."""
        from driftdriver.northstardrift import _score_alignment_with_llm

        with patch("driftdriver.northstardrift.subprocess.run") as mock_run:
            score, findings = _score_alignment_with_llm(
                "statement", [], gate_dir=tmp_path,
            )

        mock_run.assert_not_called()
        assert score == 50.0
        assert findings == []


# ---------------------------------------------------------------------------
# upstream_tracker: triage_relevance signal gate
# ---------------------------------------------------------------------------


class TestUpstreamTriageSignalGate:
    """Signal gate on triage_relevance skips LLM for unchanged inputs."""

    def test_internals_only_skips_gate_and_llm(self, tmp_path: Path) -> None:
        """internals-only category always returns 0.0 without any LLM call."""
        from driftdriver.upstream_tracker import triage_relevance

        mock_caller = lambda model, prompt: {"relevance_score": 0.9}
        score = triage_relevance(
            ["README.md"], ["update docs"], "internals-only",
            llm_caller=mock_caller, gate_dir=tmp_path,
        )
        assert score == 0.0

    def test_first_call_fires_llm(self, tmp_path: Path) -> None:
        """First triage call with new input should call LLM."""
        from driftdriver.upstream_tracker import triage_relevance

        call_count = []

        def tracking_caller(model: str, prompt: str) -> dict:
            call_count.append(True)
            return {"relevance_score": 0.7}

        score = triage_relevance(
            ["src/cli.rs"], ["new command"], "api-surface",
            llm_caller=tracking_caller, gate_dir=tmp_path,
        )
        assert len(call_count) == 1
        assert score == 0.7

    def test_same_input_skips_llm(self, tmp_path: Path) -> None:
        """Same files/subjects/category should skip LLM on second call."""
        from driftdriver.upstream_tracker import triage_relevance

        call_count = []

        def tracking_caller(model: str, prompt: str) -> dict:
            call_count.append(True)
            return {"relevance_score": 0.6}

        files = ["src/cli.rs"]
        subjects = ["add flag"]
        category = "api-surface"

        s1 = triage_relevance(
            files, subjects, category,
            llm_caller=tracking_caller, gate_dir=tmp_path,
        )
        s2 = triage_relevance(
            files, subjects, category,
            llm_caller=tracking_caller, gate_dir=tmp_path,
        )
        assert len(call_count) == 1
        assert s1 == s2 == 0.6


# ---------------------------------------------------------------------------
# upstream_tracker: deep_eval_change signal gate
# ---------------------------------------------------------------------------


class TestUpstreamDeepEvalSignalGate:
    """Signal gate on deep_eval_change skips LLM for unchanged inputs."""

    def test_first_call_fires_llm(self, tmp_path: Path) -> None:
        """First deep eval should call LLM."""
        from driftdriver.upstream_tracker import deep_eval_change

        call_count = []

        def tracking_caller(model: str, prompt: str) -> dict:
            call_count.append(True)
            return {
                "impact": "high",
                "value_gained": "new API",
                "risk_introduced": "breaking change",
                "risk_score": 0.8,
                "recommended_action": "watch",
            }

        result = deep_eval_change(
            ["src/main.rs"], ["breaking change"], "api-surface",
            context="test context",
            llm_caller=tracking_caller, gate_dir=tmp_path,
        )
        assert len(call_count) == 1
        assert result["impact"] == "high"
        assert result["risk_score"] == 0.8

    def test_same_input_skips_llm(self, tmp_path: Path) -> None:
        """Same input should skip LLM on second call."""
        from driftdriver.upstream_tracker import deep_eval_change

        call_count = []

        def tracking_caller(model: str, prompt: str) -> dict:
            call_count.append(True)
            return {
                "impact": "moderate",
                "value_gained": "improved perf",
                "risk_introduced": "none",
                "risk_score": 0.2,
                "recommended_action": "adopt",
            }

        kwargs = dict(
            changed_files=["lib/core.rs"],
            commit_subjects=["optimize loop"],
            category="behavior",
            context="test",
            llm_caller=tracking_caller,
            gate_dir=tmp_path,
        )
        r1 = deep_eval_change(**kwargs)
        r2 = deep_eval_change(**kwargs)
        assert len(call_count) == 1
        assert r1 == r2


# ---------------------------------------------------------------------------
# Signal gate result persistence helpers
# ---------------------------------------------------------------------------


class TestGatedResultPersistence:
    """Verify that gated results survive across calls (disk-backed)."""

    def test_northstar_alignment_result_survives_cache_clear(self, tmp_path: Path) -> None:
        """Even if in-memory cache is cleared, disk result is used."""
        from driftdriver.northstardrift import _score_alignment_with_llm

        tasks = [{"id": "t1", "title": "do thing"}]
        call_count = []

        def fake_subprocess_run(*args, **kwargs):
            call_count.append(True)

            class FakeResult:
                returncode = 0
                stdout = json.dumps({
                    "result": json.dumps({"score": 85, "findings": ["minor drift"]})
                })
                stderr = ""
            return FakeResult()

        with patch("driftdriver.northstardrift.subprocess.run", side_effect=fake_subprocess_run):
            with patch("driftdriver.northstardrift.extract_usage_from_claude_json", return_value=None):
                score1, _ = _score_alignment_with_llm(
                    "statement", tasks, gate_dir=tmp_path,
                )
                # Second call — gate says don't fire, reads from disk
                score2, f2 = _score_alignment_with_llm(
                    "statement", tasks, gate_dir=tmp_path,
                )

        assert len(call_count) == 1
        assert score2 == 85.0
        assert f2 == ["minor drift"]

    def test_upstream_triage_result_file_written(self, tmp_path: Path) -> None:
        """triage_relevance persists result to disk."""
        from driftdriver.upstream_tracker import triage_relevance

        def caller(model: str, prompt: str) -> dict:
            return {"relevance_score": 0.55}

        triage_relevance(
            ["a.py"], ["change"], "behavior",
            llm_caller=caller, gate_dir=tmp_path,
        )
        # Should have a result file
        result_files = list(tmp_path.glob("upstream-triage*.result.json"))
        assert len(result_files) >= 1

    def test_upstream_deepeval_result_file_written(self, tmp_path: Path) -> None:
        """deep_eval_change persists result to disk."""
        from driftdriver.upstream_tracker import deep_eval_change

        def caller(model: str, prompt: str) -> dict:
            return {
                "impact": "low",
                "value_gained": "cleanup",
                "risk_introduced": "none",
                "risk_score": 0.1,
                "recommended_action": "adopt",
            }

        deep_eval_change(
            ["b.py"], ["refactor"], "behavior", context="ctx",
            llm_caller=caller, gate_dir=tmp_path,
        )
        result_files = list(tmp_path.glob("upstream-deepeval*.result.json"))
        assert len(result_files) >= 1
