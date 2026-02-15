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
        if not (p / "graph.jsonl").exists():
            raise FileNotFoundError(f"Workgraph not found at: {p}")
        return p

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
        obj = json.loads(line)
        if obj.get("kind") != "task":
            continue
        tid = str(obj.get("id"))
        tasks[tid] = obj

    return Workgraph(wg_dir=wg_dir, project_dir=wg_dir.parent, tasks=tasks)

