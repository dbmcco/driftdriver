from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        # Walk up from explicit path
        for parent in explicit.parents:
            candidate = parent / ".workgraph"
            if (candidate / "graph.jsonl").exists():
                return candidate
        raise FileNotFoundError(f"Workgraph not found from: {explicit}")

    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        candidate = p / ".workgraph" / "graph.jsonl"
        if candidate.exists():
            return candidate.parent
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
        if obj.get("type") != "task":
            continue
        tid = obj.get("id")
        if tid is None:
            continue
        tid = str(tid)
        tasks[tid] = obj

    return Workgraph(wg_dir=wg_dir, project_dir=wg_dir.parent, tasks=tasks)

