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


@dataclass(frozen=True)
class Workgraph:
    wg_dir: Path
    project_dir: Path
    tasks: dict[str, dict[str, Any]]


def find_workgraph_dir(explicit: Path | None) -> Path:
    """
    Locate the .workgraph directory.

    `explicit` may be either a project root or the .workgraph directory.
    """

    if explicit:
        p = explicit
        if p.name != ".workgraph":
            p = p / ".workgraph"
        if (p / "graph.jsonl").exists():
            return p

        start = explicit.parent if explicit.name == ".workgraph" else explicit
        for parent in [start, *start.parents]:
            candidate = parent / ".workgraph"
            if (candidate / "graph.jsonl").exists():
                return candidate
            if (parent / ".git").exists():
                break
        raise FileNotFoundError(f"Workgraph not found from: {explicit}")

    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        candidate = p / ".workgraph" / "graph.jsonl"
        if candidate.exists():
            return candidate.parent
        if (p / ".git").exists():
            break
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
