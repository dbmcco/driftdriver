from __future__ import annotations

from pathlib import Path
from typing import Any

from driftdriver.policy import DriftPolicy, load_drift_policy

MANUAL_OWNER_POLICIES = {"hold", "assist"}


def normalize_manual_owner_policy(value: Any) -> str:
    mode = str(value or "hold").strip().lower()
    return mode if mode in MANUAL_OWNER_POLICIES else "hold"


def get_manual_owner_policy(
    project_dir: Path,
    *,
    policy: DriftPolicy | None = None,
) -> str:
    resolved = policy or load_drift_policy(project_dir / ".workgraph")
    cfg = dict(getattr(resolved, "speedriftd", {}) or {})
    return normalize_manual_owner_policy(cfg.get("manual_owner_policy"))


def task_owner(task: dict[str, Any]) -> str | None:
    owner = task.get("agent")
    if not isinstance(owner, str):
        return None
    owner = owner.strip()
    return owner or None


def _tag_matches(tag: str, pattern: str) -> bool:
    if pattern.endswith(":*"):
        return tag.startswith(pattern[:-1])
    return tag == pattern


def owner_has_executor(owner: str, routing_config: Any | None) -> bool:
    if routing_config is None:
        return False

    executors = getattr(routing_config, "executors", {})
    if isinstance(executors, dict):
        values = executors.values()
    else:
        values = executors

    owner_tag = f"agent:{owner}"
    for executor in values:
        tag_match = getattr(executor, "tag_match", "")
        if isinstance(executor, dict):
            tag_match = executor.get("tag_match", tag_match)
        if _tag_matches(owner_tag, str(tag_match or "")):
            return True
    return False


def apply_manual_owner_policy(
    task: dict[str, Any],
    project_dir: Path,
    *,
    routing_config: Any | None = None,
    policy: DriftPolicy | None = None,
) -> dict[str, Any] | None:
    annotated = dict(task)
    owner = task_owner(annotated)
    if owner is None or owner_has_executor(owner, routing_config):
        return annotated

    if get_manual_owner_policy(project_dir, policy=policy) != "assist":
        return None

    annotated["manual_owner_policy"] = "assist"
    annotated["manual_owner_id"] = owner
    return annotated
