"""Shared model-operable contract for surfacedrift and for apps.

This module realizes the interface-compliance spec from the
``model-mediated-development`` skill: a model-facing error surface must carry a
stable error code, a readable message, what was expected, valid examples, a
retryability signal, and a concrete next step.

Two ways to consume it:

1. **As a marker (stdlib-only, always import-safe).** Decorate a class with
   ``@model_operable`` (or subclass a ``ModelOperable*`` base) to declare it a
   model-operable surface. surfacedrift's static Layer 1 then validates that the
   class declares every field in :data:`REQUIRED_FIELDS`. No pydantic required —
   the marker and field list are plain Python.

2. **As a contract (pydantic, optional).** When pydantic is installed,
   :class:`ModelOperableErrorContract` is a real ``BaseModel`` apps can subclass
   for a working, validated error-response model. surfacedrift discovers
   subclasses by their ``ModelOperable`` base name, so importing the contract is
   sufficient — no decorator needed.

The two views are deliberately consistent: ``REQUIRED_FIELDS`` is the single
source of truth that both the marker-based static check and the pydantic model
express. surfacedrift Layer 2 (deferred) will add Instructor-mediated LLM
judgment of whether a surface's *guidance* is actually adequate — semantics that
structure cannot decide and code must not hardcode.
"""
from __future__ import annotations

from typing import Any

#: Canonical required fields for a model-operable error surface, per the
#: model-mediated-development skill's interface-compliance spec. This is the
#: single source of truth surfacedrift validates against.
REQUIRED_FIELDS: tuple[str, ...] = (
    "error",           # stable machine-readable error code
    "message",         # model/human-readable explanation of the failure
    "expected",        # what was expected (type/format/constraint)
    "valid_examples",  # example valid inputs the model could retry with
    "retryable",       # whether the model should retry, and how
    "next_step",       # concrete next action the model should take
)
# `received` is recommended but optional — not always meaningful (e.g. missing
# input rather than malformed input). It is intentionally NOT in REQUIRED_FIELDS.

#: Decorator name that marks a class model-operable (static discovery).
MARKER_NAME = "model_operable"
#: Base-class name prefix that marks a class model-operable (static discovery).
MARKER_BASE_PREFIX = "ModelOperable"
#: Attribute set by :func:`model_operable` at runtime (introspection helper).
MARKER_ATTR = "__model_operable__"


def model_operable(cls):
    """Mark a class (e.g. an error-response model) as model-operable.

    surfacedrift validates marked classes against :data:`REQUIRED_FIELDS`.
    Pure-stdlib marker; safe to import without pydantic.
    """
    setattr(cls, MARKER_ATTR, True)
    return cls


# --- Optional pydantic contract -----------------------------------------------
# Defined only when pydantic is installed, so this module is import-safe in
# environments (and lane scans) that do not carry pydantic. Apps that want a
# validated contract model install pydantic and subclass ModelOperableErrorContract.
try:  # pragma: no cover - exercised only when pydantic is present
    from pydantic import BaseModel, Field

    class ModelOperableErrorContract(BaseModel):
        """Reference contract for a model-operable error surface.

        Subclass this (or set ``@model_operable`` on a plain class) and surfacedrift
        will validate that all :data:`REQUIRED_FIELDS` are declared.
        """

        error: str = Field(..., description="Stable machine-readable error code.")
        message: str = Field(..., description="Model/human-readable explanation of the failure.")
        received: Any = Field(
            default=None,
            description="What was received (optional; not always meaningful).",
        )
        expected: str = Field(..., description="What was expected (type/format/constraint).")
        valid_examples: list[Any] = Field(
            default_factory=list, description="Example valid inputs."
        )
        retryable: bool = Field(..., description="Whether the model should retry, and how.")
        next_step: str = Field(..., description="Concrete next action the model should take.")

    _PYDANTIC_AVAILABLE = True
except ImportError:  # pragma: no cover
    ModelOperableErrorContract = None  # type: ignore[assignment, misc]
    _PYDANTIC_AVAILABLE = False
