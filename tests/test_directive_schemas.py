from __future__ import annotations

import pytest

from driftdriver.directive_schemas import (
    ALL_SCHEMAS,
    DirectiveValidationError,
    validate_params,
)
from driftdriver.directives import Action


def test_every_directive_action_has_an_action_specific_schema() -> None:
    assert set(ALL_SCHEMAS) == set(Action)
    assert len(ALL_SCHEMAS) == 14
    assert all(schema.action is action for action, schema in ALL_SCHEMAS.items())


def test_valid_examples_for_each_action_validate() -> None:
    examples = {
        Action.CREATE_TASK: {"task_id": "t1", "title": "Create task"},
        Action.CLAIM_TASK: {"task_id": "t1", "agent": "ap-t1"},
        Action.COMPLETE_TASK: {"task_id": "t1", "artifacts": ["out.md"]},
        Action.FAIL_TASK: {"task_id": "t1", "reason": "blocked"},
        Action.START_SERVICE: {"repo": "/tmp/repo"},
        Action.STOP_SERVICE: {"repo": "/tmp/repo"},
        Action.LOG_TO_TASK: {"task_id": "t1", "message": "progress"},
        Action.EVOLVE_PROMPT: {},
        Action.DISPATCH_TO_PEER: {"repo": "/tmp/repo", "task_id": "t1"},
        Action.BLOCK_TASK: {"task_id": "t1"},
        Action.CREATE_VALIDATION: {"parent_task_id": "t1"},
        Action.CREATE_UPSTREAM_PR: {"title": "Fix", "body": "Details"},
        Action.ABANDON_TASK: {"task_id": "t1"},
        Action.RESCHEDULE_TASK: {"task_id": "t1", "after_hours": 2.5},
    }
    for action, params in examples.items():
        validate_params(action, params)


def test_unknown_fields_are_rejected_with_repair_guidance() -> None:
    with pytest.raises(DirectiveValidationError) as caught:
        validate_params(
            Action.CREATE_TASK,
            {"task_id": "t1", "title": "Create task", "execution_mode": "now"},
        )

    payload = caught.value.to_payload()
    assert payload["error_code"] == "invalid_directive_arguments"
    assert payload["expected"]["allowed_parameters"] == [
        "after",
        "assign",
        "description",
        "model",
        "tags",
        "task_id",
        "title",
    ]
    assert payload["received"]["unknown_fields"] == ["execution_mode"]
    assert payload["retryable"] is True
    assert "execution_mode" in payload["message"]
    assert payload["next_step"]


def test_wrong_types_name_the_field_and_expected_type() -> None:
    with pytest.raises(DirectiveValidationError) as caught:
        validate_params(
            Action.RESCHEDULE_TASK,
            {"task_id": "t1", "after_hours": "tomorrow"},
        )

    payload = caught.value.to_payload()
    assert payload["expected"]["fields"]["after_hours"] == "number"
    assert payload["violations"] == [
        {
            "field": "after_hours",
            "issue": "invalid_type",
            "expected": "number",
            "received": "str",
        }
    ]


def test_missing_required_fields_are_not_repaired_by_dropping_intent() -> None:
    with pytest.raises(DirectiveValidationError) as caught:
        validate_params(Action.LOG_TO_TASK, {"task_id": "t1"})

    payload = caught.value.to_payload()
    assert payload["violations"] == [
        {
            "field": "message",
            "issue": "missing_required_field",
            "expected": "string",
        }
    ]
    assert payload["valid_examples"]
