"""Action-specific validation for Speedrift directives.

This module is deliberately stdlib-only. Models choose the action and values;
code validates the mechanical contract before an external effect is attempted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from driftdriver.directives import Action


@dataclass(frozen=True)
class FieldSpec:
    type_name: str
    required: bool = False


@dataclass(frozen=True)
class ActionSchema:
    action: Action
    fields: dict[str, FieldSpec]
    examples: tuple[dict[str, Any], ...]

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.fields.items() if spec.required)


class DirectiveValidationError(ValueError):
    """A directive cannot be executed without changing or guessing its intent."""

    def __init__(
        self,
        *,
        action: Action,
        violations: list[dict[str, Any]],
        unknown_fields: list[str],
        received: Any,
        schema: ActionSchema,
    ) -> None:
        self.action = action
        self.violations = violations
        self.unknown_fields = unknown_fields
        self.received = received
        self.schema = schema
        super().__init__(self._message())

    def _message(self) -> str:
        details = [
            f"{item['field']}: {item['issue']}"
            for item in self.violations
        ]
        if self.unknown_fields:
            details.append(f"unknown fields: {', '.join(self.unknown_fields)}")
        suffix = "; ".join(details) or "invalid directive arguments"
        return f"{self.action.value} directive cannot be executed: {suffix}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "error_code": "invalid_directive_arguments",
            "message": str(self),
            "action": self.action.value,
            "violations": self.violations,
            "expected": {
                "allowed_parameters": sorted(self.schema.fields),
                "required_parameters": list(self.schema.required_fields),
                "fields": {
                    name: spec.type_name for name, spec in self.schema.fields.items()
                },
            },
            "received": (
                {
                    "fields": sorted(self.received),
                    "unknown_fields": self.unknown_fields,
                }
                if isinstance(self.received, dict)
                else {
                    "type": type(self.received).__name__,
                    "unknown_fields": [],
                }
            ),
            "valid_examples": list(self.schema.examples),
            "retryable": True,
            "repairable": True,
            "next_step": (
                f"Retry {self.action.value} with only the allowed parameters and "
                "correct the listed field violations. Do not drop requested intent."
            ),
        }


def _schema(
    action: Action,
    *,
    required: tuple[str, ...] = (),
    optional: dict[str, str] | None = None,
    example: dict[str, Any] | None = None,
) -> ActionSchema:
    fields = {name: FieldSpec("string", required=True) for name in required}
    fields.update(
        {name: FieldSpec(type_name) for name, type_name in (optional or {}).items()}
    )
    return ActionSchema(action, fields, (example or {},))


ALL_SCHEMAS: dict[Action, ActionSchema] = {
    Action.CREATE_TASK: _schema(
        Action.CREATE_TASK,
        required=("task_id", "title"),
        optional={
            "description": "string",
            "tags": "list[string]",
            "after": "list[string]",
            "assign": "string",
            "model": "string",
        },
        example={"task_id": "task-1", "title": "Implement the task"},
    ),
    Action.CLAIM_TASK: _schema(
        Action.CLAIM_TASK,
        required=("task_id",),
        optional={"agent": "string"},
        example={"task_id": "task-1", "agent": "agent-1"},
    ),
    Action.COMPLETE_TASK: _schema(
        Action.COMPLETE_TASK,
        required=("task_id",),
        optional={"artifacts": "list[string]"},
        example={"task_id": "task-1", "artifacts": ["report.md"]},
    ),
    Action.FAIL_TASK: _schema(
        Action.FAIL_TASK,
        required=("task_id",),
        optional={"reason": "string"},
        example={"task_id": "task-1", "reason": "Blocked by missing input"},
    ),
    Action.START_SERVICE: _schema(Action.START_SERVICE, required=("repo",), example={"repo": "/path/to/repo"}),
    Action.STOP_SERVICE: _schema(Action.STOP_SERVICE, required=("repo",), example={"repo": "/path/to/repo"}),
    Action.LOG_TO_TASK: _schema(
        Action.LOG_TO_TASK,
        required=("task_id", "message"),
        example={"task_id": "task-1", "message": "Progress update"},
    ),
    Action.EVOLVE_PROMPT: _schema(Action.EVOLVE_PROMPT, example={}),
    Action.DISPATCH_TO_PEER: _schema(
        Action.DISPATCH_TO_PEER,
        required=("repo", "task_id"),
        example={"repo": "/path/to/repo", "task_id": "task-1"},
    ),
    Action.BLOCK_TASK: _schema(Action.BLOCK_TASK, required=("task_id",), example={"task_id": "task-1"}),
    Action.CREATE_VALIDATION: _schema(
        Action.CREATE_VALIDATION,
        required=("parent_task_id",),
        optional={"criteria": "string"},
        example={"parent_task_id": "task-1", "criteria": "Run the focused tests"},
    ),
    Action.CREATE_UPSTREAM_PR: _schema(
        Action.CREATE_UPSTREAM_PR,
        optional={"repo": "string", "title": "string", "body": "string", "base": "string", "head": "string"},
        example={"title": "Fix upstream behavior", "body": "Explain the change"},
    ),
    Action.ABANDON_TASK: _schema(Action.ABANDON_TASK, required=("task_id",), example={"task_id": "task-1"}),
    Action.RESCHEDULE_TASK: _schema(
        Action.RESCHEDULE_TASK,
        required=("task_id",),
        optional={"after_hours": "number"},
        example={"task_id": "task-1", "after_hours": 2},
    ),
}


def _type_matches(value: Any, type_name: str) -> bool:
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "list[string]":
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return True


def validate_params(action: Action, params: Any) -> None:
    """Validate a complete directive payload without rewriting it."""
    schema = ALL_SCHEMAS[action]
    if not isinstance(params, dict):
        raise DirectiveValidationError(
            action=action,
            violations=[
                {
                    "field": "params",
                    "issue": "invalid_type",
                    "expected": "object",
                    "received": type(params).__name__,
                }
            ],
            unknown_fields=[],
            received=params,
            schema=schema,
        )
    violations: list[dict[str, Any]] = []
    for name in schema.required_fields:
        if name not in params:
            violations.append(
                {"field": name, "issue": "missing_required_field", "expected": schema.fields[name].type_name}
            )
        elif isinstance(params[name], str) and not params[name].strip():
            violations.append(
                {"field": name, "issue": "empty_required_field", "expected": schema.fields[name].type_name}
            )
    for name, value in params.items():
        spec = schema.fields.get(name)
        if spec is None:
            continue
        if not _type_matches(value, spec.type_name):
            violations.append(
                {
                    "field": name,
                    "issue": "invalid_type",
                    "expected": spec.type_name,
                    "received": type(value).__name__,
                }
            )
    unknown_fields = sorted(set(params) - set(schema.fields))
    if violations or unknown_fields:
        raise DirectiveValidationError(
            action=action,
            violations=violations,
            unknown_fields=unknown_fields,
            received=params,
            schema=schema,
        )
