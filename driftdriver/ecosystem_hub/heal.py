# ABOUTME: Automated graph healing for workgraph repos — unclaims orphaned tasks,
# ABOUTME: purges dead agents, fixes corrupted log entries. Workaround for wg issues #5/#6/#7.
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_graph_lines(graph_path: Path) -> list[dict[str, Any]]:
    if not graph_path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in graph_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def _write_graph_lines(graph_path: Path, nodes: list[dict[str, Any]]) -> None:
    tmp = graph_path.with_suffix(".jsonl.heal-tmp")
    tmp.write_text(
        "\n".join(json.dumps(n) for n in nodes) + "\n",
        encoding="utf-8",
    )
    tmp.replace(graph_path)


def _load_alive_agent_ids(wg_dir: Path) -> set[str]:
    registry_path = wg_dir / "service" / "registry.json"
    if not registry_path.exists():
        # Try agents.json as fallback
        registry_path = wg_dir / "service" / "agents.json"
    if not registry_path.exists():
        return set()
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    agents = data.get("agents", {}) if isinstance(data, dict) else {}
    return {
        aid for aid, a in agents.items()
        if isinstance(a, dict) and a.get("alive", False)
    }


def _purge_dead_agents(wg_dir: Path) -> int:
    for name in ("registry.json", "agents.json"):
        registry_path = wg_dir / "service" / name
        if not registry_path.exists():
            continue
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        agents = data.get("agents", {}) if isinstance(data, dict) else {}
        alive = {k: v for k, v in agents.items() if isinstance(v, dict) and v.get("alive", False)}
        dead_count = len(agents) - len(alive)
        if dead_count > 0:
            data["agents"] = alive
            tmp = registry_path.with_suffix(".json.heal-tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(registry_path)
        return dead_count
    return 0


def heal_repo_graph(repo_path: Path) -> dict[str, Any]:
    """Heal a repo's workgraph: unclaim orphaned tasks, fix logs, purge dead agents.

    Returns a summary dict with counts of what was fixed.
    """
    wg_dir = repo_path / ".workgraph"
    graph_path = wg_dir / "graph.jsonl"
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    result: dict[str, Any] = {
        "repo": repo_path.name,
        "unclaimed_tasks": 0,
        "fixed_log_entries": 0,
        "purged_agents": 0,
        "errors": [],
    }

    if not graph_path.exists():
        return result

    # Load graph
    try:
        nodes = _read_graph_lines(graph_path)
    except Exception as exc:
        result["errors"].append(f"read_graph: {exc}")
        return result

    alive_agents = _load_alive_agent_ids(wg_dir)
    modified = False

    for node in nodes:
        if node.get("kind") != "task":
            continue

        # Fix 1: Unclaim orphaned in-progress tasks
        if node.get("status") == "in-progress":
            assigned = node.get("assigned", "")
            # If no alive agents at all, or assigned agent is not alive, unclaim
            if not alive_agents or (assigned and assigned not in alive_agents):
                node["status"] = "open"
                node.pop("started_at", None)
                log = node.get("log", [])
                if not isinstance(log, list):
                    log = []
                log.append({
                    "timestamp": now_iso,
                    "message": "Auto-healed: unclaimed orphaned in-progress task (no alive agent)",
                })
                node["log"] = log
                result["unclaimed_tasks"] += 1
                modified = True

        # Fix 2: Repair log entries missing timestamps
        for entry in node.get("log", []):
            if isinstance(entry, dict) and "timestamp" not in entry:
                entry["timestamp"] = node.get("created_at", now_iso)
                result["fixed_log_entries"] += 1
                modified = True

    # Write graph if modified
    if modified:
        try:
            _write_graph_lines(graph_path, nodes)
        except Exception as exc:
            result["errors"].append(f"write_graph: {exc}")

    # Fix 3: Purge dead agents if > 50
    try:
        dead_count = _count_dead_agents(wg_dir)
        if dead_count > 50:
            result["purged_agents"] = _purge_dead_agents(wg_dir)
    except Exception as exc:
        result["errors"].append(f"purge_agents: {exc}")

    return result


def _count_dead_agents(wg_dir: Path) -> int:
    for name in ("registry.json", "agents.json"):
        registry_path = wg_dir / "service" / name
        if not registry_path.exists():
            continue
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        agents = data.get("agents", {}) if isinstance(data, dict) else {}
        return sum(1 for a in agents.values() if isinstance(a, dict) and not a.get("alive", False))
    return 0


def heal_ecosystem(workspace_root: Path, repo_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Run heal_repo_graph on all repos (or a subset) in the workspace."""
    results: list[dict[str, Any]] = []
    if repo_names:
        paths = [workspace_root / name for name in repo_names]
    else:
        paths = sorted(p for p in workspace_root.iterdir() if p.is_dir() and (p / ".workgraph" / "graph.jsonl").exists())

    for repo_path in paths:
        if not (repo_path / ".workgraph" / "graph.jsonl").exists():
            continue
        result = heal_repo_graph(repo_path)
        if result["unclaimed_tasks"] > 0 or result["purged_agents"] > 0 or result["fixed_log_entries"] > 0:
            results.append(result)

    return results
