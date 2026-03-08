# ABOUTME: Integration test for attractor loop triggered by factorydrift cycle.
# ABOUTME: Verifies factory cycle calls attractor loop for repos with declared attractors.

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.attractor_loop import AttractorRun, run_attractor_loop, CircuitBreakers
from driftdriver.attractors import Attractor, AttractorCriterion
from driftdriver.bundles import Bundle, TaskTemplate
from driftdriver.factorydrift import _maybe_run_attractor_loop
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_attractor_loop_converges_in_two_passes():
    """Simulate a loop that fixes findings on pass 1 and converges on pass 2."""
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    bundles = [Bundle(
        id="scope-drift",
        finding_kinds=["scope_drift"],
        description="Fix scope",
        tasks=[TaskTemplate(id_template="{finding_id}-fix", title_template="Fix")],
    )]

    call_count = {"diagnose": 0}

    def mock_diagnose(repo_path):
        call_count["diagnose"] += 1
        # First two calls (pass 0: before + after): findings present then resolved
        # Third call (pass 1: before): no findings
        if call_count["diagnose"] <= 1:
            return {
                "coredrift": LaneResult(
                    lane="coredrift",
                    findings=[LaneFinding(message="scope drift", severity="warning", tags=["scope_drift"])],
                    exit_code=3, summary="1 finding",
                ),
            }
        return {"coredrift": LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean")}

    def mock_execute(plan, repo_path):
        return {inst.bundle_id: "resolved" for inst in plan.bundle_instances}

    with TemporaryDirectory() as tmp:
        run = run_attractor_loop(
            repo="test-repo",
            repo_path=Path(tmp),
            attractor=attractor,
            bundles=bundles,
            breakers=CircuitBreakers(max_passes=5),
            diagnose_fn=mock_diagnose,
            execute_fn=mock_execute,
        )

    assert run.status == "converged"
    assert len(run.passes) >= 1


def test_attractor_loop_plateaus():
    """Simulate a loop that can't fix findings -- plateaus and escalates."""
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )

    def mock_diagnose(repo_path):
        return {
            "coredrift": LaneResult(
                lane="coredrift",
                findings=[LaneFinding(message="stuck", severity="error", tags=["unknown"])],
                exit_code=3, summary="stuck",
            ),
        }

    def mock_execute(plan, repo_path):
        return {}

    with TemporaryDirectory() as tmp:
        run = run_attractor_loop(
            repo="test-repo",
            repo_path=Path(tmp),
            attractor=attractor,
            bundles=[],
            breakers=CircuitBreakers(max_passes=3, plateau_threshold=2),
            diagnose_fn=mock_diagnose,
            execute_fn=mock_execute,
        )

    assert run.status in ("plateau", "max_passes")


def test_maybe_run_attractor_loop_no_policy():
    """When no attractor target is in policy, return None."""
    with TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        result = _maybe_run_attractor_loop(
            repo_name="test-repo",
            repo_path=repo_path,
            policy={},
        )
    assert result is None


def test_maybe_run_attractor_loop_with_target():
    """When attractor target is declared, run the loop and return an AttractorRun."""
    attractor = Attractor(
        id="onboarded",
        description="Onboarded repo",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )

    def fake_diagnose(repo_path):
        return {"coredrift": LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean")}

    def fake_execute(plan, repo_path):
        return {}

    with TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        # Create service dir for save
        service_dir = repo_path / ".workgraph" / "service"
        service_dir.mkdir(parents=True)

        with patch("driftdriver.factorydrift.resolve_attractor", return_value=attractor), \
             patch("driftdriver.factorydrift.load_attractors_from_dir", return_value={"onboarded": attractor}), \
             patch("driftdriver.factorydrift.load_bundles_from_dir", return_value=[]):
            result = _maybe_run_attractor_loop(
                repo_name="test-repo",
                repo_path=repo_path,
                policy={"attractor": {"target": "onboarded"}},
                diagnose_fn=fake_diagnose,
                execute_fn=fake_execute,
            )

    assert result is not None
    assert isinstance(result, AttractorRun)
    assert result.status == "converged"
    assert result.attractor == "onboarded"


def test_maybe_run_attractor_loop_saves_run():
    """Attractor run result is persisted to the service directory."""
    attractor = Attractor(
        id="onboarded",
        description="Onboarded repo",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )

    def fake_diagnose(repo_path):
        return {"coredrift": LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean")}

    def fake_execute(plan, repo_path):
        return {}

    with TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        service_dir = repo_path / ".workgraph" / "service"
        service_dir.mkdir(parents=True)

        with patch("driftdriver.factorydrift.resolve_attractor", return_value=attractor), \
             patch("driftdriver.factorydrift.load_attractors_from_dir", return_value={"onboarded": attractor}), \
             patch("driftdriver.factorydrift.load_bundles_from_dir", return_value=[]):
            _maybe_run_attractor_loop(
                repo_name="test-repo",
                repo_path=repo_path,
                policy={"attractor": {"target": "onboarded"}},
                diagnose_fn=fake_diagnose,
                execute_fn=fake_execute,
            )

        # Check that the run was saved
        current_run = service_dir / "attractor" / "current-run.json"
        assert current_run.exists()
