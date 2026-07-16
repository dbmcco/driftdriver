from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# This is the checked-in live Pi-resolvable set for the current plan. Keep this
# explicit: silently accepting provider/model heuristics would make dispatch
# non-deterministic when a provider catalog changes.
ALLOWED_PI_MODEL_IDS = frozenset(
    {
        "zai/glm-5.2",
        "anthropic/claude-haiku-4-5",
        "anthropic/claude-sonnet-4-5",
        "anthropic/claude-opus-4-8",
    }
)
_ALLOWED_THINKING_SUFFIXES = frozenset({"low", "medium", "high"})


def _json_object(value: str | dict[str, Any], *, label: str) -> dict[str, Any]:
    if isinstance(value, dict):
        payload = value
    else:
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid Workgraph {label} JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Workgraph {label} output must be a JSON object")
    return payload


def parse_workgraph_status(value: str | dict[str, Any]) -> dict[str, Any]:
    """Validate and return the current ``wg --json status`` envelope."""
    payload = _json_object(value, label="status")
    required = {"service", "coordinator", "agents", "tasks", "recent"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"Workgraph status missing required sections: {sorted(missing)}")
    if (
        not isinstance(payload["coordinator"], dict)
        or not isinstance(payload["agents"], dict)
        or not isinstance(payload["tasks"], dict)
        or not isinstance(payload["recent"], list)
    ):
        raise ValueError("Workgraph status sections have invalid types")
    coordinator = payload["coordinator"]
    if not {"executor", "model"} <= coordinator.keys():
        raise ValueError("Workgraph status coordinator lacks executor/model")
    return payload


def parse_workgraph_ready(value: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and return the current ``wg --json ready`` task list."""
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Workgraph ready JSON") from exc
    else:
        payload = value
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError("Workgraph ready output must be a JSON array of task objects")
    required = {"id", "title", "ready", "assigned", "priority", "estimate"}
    if any(not required <= row.keys() for row in payload):
        raise ValueError("Workgraph ready task lacks required fields")
    return payload


def validate_pi_model_spec(model_spec: str) -> str:
    """Accept only an exact allowed provider/id, optionally with Pi thinking suffix."""
    value = str(model_spec or "").strip()
    base = value
    suffix = ""
    if ":" in value:
        base, suffix = value.rsplit(":", 1)
        if suffix not in _ALLOWED_THINKING_SUFFIXES:
            raise ValueError(f"model is not an allowed Pi model: {model_spec!r}")
    if base not in ALLOWED_PI_MODEL_IDS:
        raise ValueError(f"model is not an allowed Pi model: {model_spec!r}")
    return value
GRAPH_DIR_NAMES = (".workgraph", ".wg")


class WorkgraphDirectoryConflictError(RuntimeError):
    """Raised when a repository has two initialized Workgraph directories."""


@dataclass(frozen=True)
class GraphDirectoryResolution:
    path: Path
    initialized: bool
    source: str


@dataclass(frozen=True)
class Workgraph:
    wg_dir: Path
    project_dir: Path
    tasks: dict[str, dict[str, Any]]


def _is_initialized_graph(path: Path) -> bool:
    return (path / "graph.jsonl").is_file()


def resolve_workgraph_dir(
    project_dir: Path,
    explicit: Path | None = None,
) -> GraphDirectoryResolution:
    project_dir = project_dir.resolve()
    if explicit is not None:
        candidate = explicit.resolve()
        if candidate.name not in GRAPH_DIR_NAMES:
            candidate = candidate / ".workgraph"
        return GraphDirectoryResolution(
            path=candidate,
            initialized=_is_initialized_graph(candidate),
            source="explicit",
        )

    legacy = project_dir / ".workgraph"
    current = project_dir / ".wg"
    legacy_initialized = _is_initialized_graph(legacy)
    current_initialized = _is_initialized_graph(current)
    if legacy_initialized and current_initialized:
        raise WorkgraphDirectoryConflictError(
            "Two initialized Workgraph directories found: "
            f"{legacy} and {current}. Choose one graph before continuing."
        )
    if legacy_initialized:
        return GraphDirectoryResolution(legacy, True, "legacy")
    if current_initialized:
        return GraphDirectoryResolution(current, True, "current")
    if legacy.exists() and current.exists():
        raise WorkgraphDirectoryConflictError(
            "Two uninitialized Workgraph directories found: "
            f"{legacy} and {current}. Remove or archive the unintended directory."
        )
    if legacy.exists():
        return GraphDirectoryResolution(legacy, False, "existing")
    if current.exists():
        return GraphDirectoryResolution(current, False, "existing")
    return GraphDirectoryResolution(legacy, False, "default")


def find_workgraph_dir(explicit: Path | None) -> Path:
    """Locate an initialized Workgraph directory."""

    if explicit is not None and explicit.name in GRAPH_DIR_NAMES:
        direct = resolve_workgraph_dir(explicit.parent, explicit=explicit)
        if direct.initialized:
            return direct.path
        start = explicit.parent
    else:
        start = explicit if explicit is not None else Path.cwd()

    for project_dir in [start, *start.parents]:
        resolution = resolve_workgraph_dir(project_dir)
        if resolution.initialized:
            return resolution.path
        if (project_dir / ".git").exists():
            break

    if explicit is not None:
        raise FileNotFoundError(f"Workgraph not found from: {explicit}")
    raise FileNotFoundError("Could not find .workgraph/graph.jsonl; pass --dir.")


def load_workgraph(wg_dir: Path) -> Workgraph:
    graph_path = wg_dir / "graph.jsonl"
    tasks: dict[str, dict[str, Any]] = {}

    for line in graph_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry_type = obj.get("kind") or obj.get("type")
        if entry_type != "task":
            continue
        tid = obj.get("id")
        if tid is None:
            continue
        tid = str(tid)
        tasks[tid] = obj

    return Workgraph(wg_dir=wg_dir, project_dir=wg_dir.parent, tasks=tasks)
