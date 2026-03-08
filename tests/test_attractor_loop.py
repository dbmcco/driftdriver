# ABOUTME: Tests for the attractor loop — convergence detection, pass orchestration, circuit breakers.
# ABOUTME: Uses mocked lane runs and task creation to test loop logic without real wg calls.

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock
from typing import Any

from driftdriver.attractor_loop import (
    AttractorRun,
    PassResult,
    CircuitBreakers,
    run_attractor_pass,
    check_convergence,
    run_attractor_loop,
)
from driftdriver.attractors import Attractor, AttractorCriterion, AttractorGap
from driftdriver.attractor_planner import ConvergencePlan, EscalationRecord
from driftdriver.bundles import BundleInstance
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_circuit_breakers_defaults():
    cb = CircuitBreakers()
    assert cb.max_passes == 3
    assert cb.plateau_threshold == 2
    assert cb.max_tasks_per_cycle == 30


def test_check_convergence_converged():
    passes = [
        PassResult(pass_number=0, findings_before=3, findings_after=0, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers())
    assert status == "converged"


def test_check_convergence_plateau():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=3, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=2, findings_before=3, findings_after=3, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers(plateau_threshold=2))
    assert status == "plateau"


def test_check_convergence_max_passes():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=4, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=4, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=2, findings_before=3, findings_after=2, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers(max_passes=3))
    assert status == "max_passes"


def test_check_convergence_improving():
    passes = [
        PassResult(pass_number=0, findings_before=5, findings_after=3, duration_seconds=10.0),
        PassResult(pass_number=1, findings_before=3, findings_after=1, duration_seconds=10.0),
    ]
    status = check_convergence(passes, CircuitBreakers())
    assert status == "continue"


def test_attractor_run_fields():
    run = AttractorRun(
        repo="test-repo",
        attractor="production-ready",
        status="converged",
    )
    assert run.repo == "test-repo"
    assert run.passes == []


def test_pass_result_fields():
    pr = PassResult(
        pass_number=0,
        findings_before=5,
        findings_after=2,
        duration_seconds=15.5,
        bundles_applied=["scope-drift"],
        bundle_outcomes={"scope-drift": "resolved"},
    )
    assert pr.findings_before == 5
    assert pr.findings_after == 2
