from __future__ import annotations

import json
import subprocess
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


# ---------------------------------------------------------------------------
# Publication fence semantics (confirmed against the installed wg CLI)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationOutcome:
    """Explicit outcome of one Workgraph publication mutation.

    A mutation that did not apply (non-zero exit — e.g. shared-root lock
    contention, or dependency validation refusing the release) is a
    ``coordination_wait``: retryable, recorded, and never reported as a
    silent success that would spin through repeated attempts.
    """

    status: str  # "published" | "coordination_wait"
    retryable: bool
    reason: str


def build_publish_command(task_id: str, *, only: bool = True) -> list[str]:
    """Build the publication fence-release command for one task.

    This is the single place encoding the installed wg CLI's publication
    semantics, confirmed from its help output (``wg add --help`` /
    ``wg publish --help``, wg 0.1.0):

    - ``wg add`` always creates a paused visible draft (``--paused`` is only
      a compatibility spelling), so publication is fenced at add time and no
      guessed draft/place flags belong in planner code;
    - ``wg publish <task>`` validates dependencies, then resumes the entire
      subgraph; ``--only`` releases exactly the named task;
    - ``--place-near`` / ``--place-before`` are placement hints, not fences.

    Because the native binary can represent the fence itself, no local
    shadow-fence state is maintained here.
    """
    cmd = ["wg", "publish", task_id]
    if only:
        cmd.append("--only")
    return cmd


def classify_publication_result(
    result: subprocess.CompletedProcess[str],
) -> PublicationOutcome:
    """Classify a publication mutation result (``wg publish`` or ``wg done``).

    Exit 0 means the state change applied. Any other exit is a coordination
    wait: the mutation did not take effect, a later attempt may apply once
    contention clears or dependencies complete, and the caller must record
    the wait rather than reporting success and re-issuing the mutation.
    """
    if result.returncode == 0:
        return PublicationOutcome(
            status="published", retryable=False, reason=""
        )
    stderr = (result.stderr or "").strip()
    detail = f": {stderr[:200]}" if stderr else ""
    return PublicationOutcome(
        status="coordination_wait",
        retryable=True,
        reason=f"publication did not apply (exit {result.returncode}){detail}",
    )
