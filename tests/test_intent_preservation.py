from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.directives import Action, Authority, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


def _directive(params: dict[str, object]) -> Directive:
    return Directive(
        source="test-agent",
        repo="test-repo",
        action=Action.CREATE_TASK,
        params=params,
        reason="test invalid directive",
        authority=Authority(actor="tester", actor_class="agent", budget_remaining=3),
    )


def test_invalid_mutation_is_rejected_without_subprocess_or_field_stripping(
    tmp_path: Path,
) -> None:
    log = DirectiveLog(tmp_path / "directives")
    shim = ExecutorShim(wg_dir=tmp_path, log=log)
    directive = _directive(
        {
            "task_id": "t1",
            "title": "Create task",
            "assign": "samantha",
            "execution_mode": "immediate",
        }
    )

    with patch("driftdriver.executor_shim.subprocess.run") as run:
        assert shim.execute(directive) == "failed"

    run.assert_not_called()
    failed = log.read_failed()
    assert len(failed) == 1
    record = failed[0]
    assert record["directive_id"] == directive.id
    assert record["action"] == Action.CREATE_TASK.value
    assert record["params"] == directive.params
    assert record["repairable"] is True
    assert record["error_code"] == "invalid_directive_arguments"
    assert record["expected"]["allowed_parameters"]
    assert record["received"]["unknown_fields"] == ["execution_mode"]
    assert "assign" in record["expected"]["allowed_parameters"]
    assert record["next_step"]


def test_existing_claim_agent_field_reaches_wg_without_being_dropped(tmp_path: Path) -> None:
    log = DirectiveLog(tmp_path / "directives")
    shim = ExecutorShim(wg_dir=tmp_path, log=log)
    directive = Directive(
        source="project_autopilot",
        repo="test-repo",
        action=Action.CLAIM_TASK,
        params={"task_id": "t1", "agent": "ap-t1"},
        reason="claim for execution",
    )

    with patch("driftdriver.executor_shim.subprocess.run") as run, \
         patch("driftdriver.executor_shim.load_dispatch_authority") as auth:
        auth.return_value = {"enabled": True, "reason": "active lease permits dispatch"}
        run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert shim.execute(directive) == "completed"

    command = run.call_args.args[0]
    assert "--actor" in command
    assert "ap-t1" in command


def test_non_mapping_params_get_structured_failure_evidence(tmp_path: Path) -> None:
    log = DirectiveLog(tmp_path / "directives")
    shim = ExecutorShim(wg_dir=tmp_path, log=log)
    directive = Directive(
        source="raw-json",
        repo="test-repo",
        action=Action.CREATE_TASK,
        params=None,  # type: ignore[arg-type]
        reason="malformed input",
    )

    with patch("driftdriver.executor_shim.subprocess.run") as run:
        assert shim.execute(directive) == "failed"

    run.assert_not_called()
    record = log.read_failed()[0]
    assert record["error_code"] == "invalid_directive_arguments"
    assert record["received"]["type"] == "NoneType"
    assert record["next_step"]


def test_execution_failure_does_not_guess_retryability(tmp_path: Path) -> None:
    log = DirectiveLog(tmp_path / "directives")
    shim = ExecutorShim(wg_dir=tmp_path, log=log)
    directive = _directive({"task_id": "t1", "title": "Create task"})

    with patch("driftdriver.executor_shim.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stdout="", stderr="wg failed")
        assert shim.execute(directive) == "failed"

    record = log.read_failed()[0]
    assert record["retryable"] is None
    assert record["observed_exit_code"] == 1
    assert record["retryability_basis"] == "not_classified_by_executor"


def test_failure_evidence_survives_a_fresh_log_instance(tmp_path: Path) -> None:
    directive_dir = tmp_path / "directives"
    log = DirectiveLog(directive_dir)
    shim = ExecutorShim(wg_dir=tmp_path, log=log)
    directive = _directive(
        {"task_id": "t1", "title": "Create task", "unexpected": "value"}
    )

    assert shim.execute(directive) == "failed"

    reloaded = DirectiveLog(directive_dir).read_failed()
    assert reloaded[0]["source"] == "test-agent"
    assert reloaded[0]["repo"] == "test-repo"
    assert reloaded[0]["reason"] == "test invalid directive"
    assert reloaded[0]["authority"] == {
        "actor": "tester",
        "actor_class": "agent",
        "budget_remaining": 3,
    }
    assert reloaded[0]["failed_at"]
