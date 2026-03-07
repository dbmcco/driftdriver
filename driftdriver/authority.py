# ABOUTME: Authority and budget enforcement for the Speedrift actor system.
# ABOUTME: Controls what each actor class can do and enforces rate/count limits.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover – Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from driftdriver.actor import DEFAULT_AUTHORITY, Actor


@dataclass
class Budget:
    max_active_tasks: int = 3
    max_creates_per_hour: int = 10
    max_dispatches_per_hour: int = 5


DEFAULT_BUDGETS: dict[str, Budget] = {
    "human": Budget(max_active_tasks=999, max_creates_per_hour=999, max_dispatches_per_hour=999),
    "interactive": Budget(max_active_tasks=20, max_creates_per_hour=30, max_dispatches_per_hour=0),
    "worker": Budget(max_active_tasks=1, max_creates_per_hour=5, max_dispatches_per_hour=0),
    "daemon": Budget(max_active_tasks=10, max_creates_per_hour=20, max_dispatches_per_hour=10),
    "lane": Budget(max_active_tasks=3, max_creates_per_hour=10, max_dispatches_per_hour=0),
}


def get_authority(actor_class: str, *, policy: dict[str, Any] | None = None) -> frozenset[str]:
    """Get effective authority set for actor class, with policy overrides applied."""
    if policy and "grants" in policy and actor_class in policy["grants"]:
        return frozenset(policy["grants"][actor_class])
    return DEFAULT_AUTHORITY.get(actor_class, frozenset())


def get_budget(actor_class: str, *, policy: dict[str, Any] | None = None) -> Budget:
    """Get effective budget for actor class, with policy overrides applied."""
    if policy and "budgets" in policy and actor_class in policy["budgets"]:
        return policy["budgets"][actor_class]
    return DEFAULT_BUDGETS.get(actor_class, Budget())


def can_do(actor: Actor, operation: str, *, policy: dict[str, Any] | None = None) -> bool:
    """Check if actor's class is authorized for operation. Policy overrides defaults."""
    authority = get_authority(actor.actor_class, policy=policy)
    return operation in authority


def check_budget(
    actor: Actor,
    operation: str,
    *,
    current_count: int = 0,
    recent_count: int = 0,
    policy: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Check if actor is within budget for operation.

    Returns (allowed, reason).
    - For "create": checks current_count < max_active_tasks AND recent_count < max_creates_per_hour
    - For "dispatch": checks recent_count < max_dispatches_per_hour
    - For other operations: always (True, "")
    """
    budget = get_budget(actor.actor_class, policy=policy)

    if operation == "create":
        if current_count >= budget.max_active_tasks:
            return (
                False,
                f"max_active_tasks exceeded: {current_count} >= {budget.max_active_tasks}",
            )
        if recent_count >= budget.max_creates_per_hour:
            return (
                False,
                f"max_creates_per_hour exceeded: {recent_count} >= {budget.max_creates_per_hour}",
            )
        return (True, "")

    if operation == "dispatch":
        if recent_count >= budget.max_dispatches_per_hour:
            return (
                False,
                f"max_dispatches_per_hour exceeded: {recent_count} >= {budget.max_dispatches_per_hour}",
            )
        return (True, "")

    return (True, "")


def load_authority_policy(toml_path: Path) -> dict[str, Any]:
    """Load authority configuration from drift-policy.toml.

    Reads [authority] section for grants, budgets, and global_ceiling.
    Falls back to [loop_safety].max_ready_drift_followups for global_ceiling
    when [authority] doesn't set it (backward compat).

    Returns merged dict. Empty dict if file missing or no relevant sections.
    """
    if not toml_path.exists():
        return {}

    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    result: dict[str, Any] = {}

    authority_section = data.get("authority")
    if isinstance(authority_section, dict):
        # Parse grants overrides
        grants_raw = authority_section.get("grants")
        if isinstance(grants_raw, dict):
            grants: dict[str, frozenset[str]] = {}
            for actor_class, ops in grants_raw.items():
                if isinstance(ops, list):
                    grants[actor_class] = frozenset(str(op) for op in ops)
            if grants:
                result["grants"] = grants

        # Parse budget overrides
        budgets_raw = authority_section.get("budgets")
        if isinstance(budgets_raw, dict):
            budgets: dict[str, Budget] = {}
            for actor_class, budget_data in budgets_raw.items():
                if isinstance(budget_data, dict):
                    default = DEFAULT_BUDGETS.get(actor_class, Budget())
                    budgets[actor_class] = Budget(
                        max_active_tasks=int(budget_data.get("max_active_tasks", default.max_active_tasks)),
                        max_creates_per_hour=int(budget_data.get("max_creates_per_hour", default.max_creates_per_hour)),
                        max_dispatches_per_hour=int(
                            budget_data.get("max_dispatches_per_hour", default.max_dispatches_per_hour)
                        ),
                    )
            if budgets:
                result["budgets"] = budgets

        # Global ceiling from [authority]
        if "global_ceiling" in authority_section:
            result["global_ceiling"] = max(1, int(authority_section["global_ceiling"]))

    # Backward compat: read global ceiling from [loop_safety] if not set in [authority]
    if "global_ceiling" not in result:
        loop_safety = data.get("loop_safety")
        if isinstance(loop_safety, dict) and "max_ready_drift_followups" in loop_safety:
            result["global_ceiling"] = max(1, int(loop_safety["max_ready_drift_followups"]))

    return result
