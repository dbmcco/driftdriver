# ABOUTME: Bundle loader, validator, and parameterizer for attractor loop.
# ABOUTME: Loads TOML bundle definitions and interpolates finding context into task templates.

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskTemplate:
    """A parameterized task template within a bundle."""

    id_template: str
    title_template: str
    description_template: str = ""
    tags: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    verify: str = ""


@dataclass
class Bundle:
    """A reusable task graph fragment mapped to finding-kinds."""

    id: str
    finding_kinds: list[str]
    description: str
    tasks: list[TaskTemplate]


@dataclass
class BundleInstance:
    """A parameterized bundle ready for directive emission."""

    bundle_id: str
    finding_id: str
    tasks: list[dict[str, Any]]  # parameterized task dicts
    confidence: str = "high"  # "high" or "low"


def _safe_format(template: str, context: dict[str, str]) -> str:
    """Format a template string, leaving unknown keys as-is."""
    result = template
    for key, value in context.items():
        result = result.replace("{" + key + "}", value)
    return result


def load_bundle(path: Path) -> Bundle:
    """Load a bundle from a TOML file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    b = data.get("bundle", {})
    if "id" not in b:
        raise ValueError(f"Bundle at {path} missing 'id'")
    if "finding_kinds" not in b:
        raise ValueError(f"Bundle at {path} missing 'finding_kinds'")

    tasks = []
    for t in data.get("tasks", []):
        tasks.append(TaskTemplate(
            id_template=t.get("id_template", ""),
            title_template=t.get("title_template", ""),
            description_template=t.get("description_template", ""),
            tags=t.get("tags", []),
            after=t.get("after", []),
            verify=t.get("verify", ""),
        ))

    return Bundle(
        id=b["id"],
        finding_kinds=b["finding_kinds"],
        description=b.get("description", ""),
        tasks=tasks,
    )


def load_bundles_from_dir(directory: Path) -> list[Bundle]:
    """Load all bundles from a directory of TOML files."""
    bundles = []
    for p in sorted(directory.glob("*.toml")):
        bundles.append(load_bundle(p))
    return bundles


def parameterize_bundle(
    bundle: Bundle,
    context: dict[str, str],
) -> BundleInstance:
    """Fill in a bundle's templates with finding-specific context.

    Context keys: finding_id, task_title, evidence, file, repo_name.
    """
    tasks = []
    for t in bundle.tasks:
        task_dict: dict[str, Any] = {
            "task_id": _safe_format(t.id_template, context),
            "title": _safe_format(t.title_template, context),
            "description": _safe_format(t.description_template, context),
            "tags": list(t.tags),
        }
        if t.after:
            task_dict["after"] = [_safe_format(a, context) for a in t.after]
        if t.verify:
            task_dict["verify"] = _safe_format(t.verify, context)
        tasks.append(task_dict)

    return BundleInstance(
        bundle_id=bundle.id,
        finding_id=context.get("finding_id", ""),
        tasks=tasks,
        confidence="high",
    )
