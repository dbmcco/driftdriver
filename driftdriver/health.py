from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


_DRIFT_ID_RE = re.compile(
    r"^(drift-|coredrift-|specdrift-|datadrift-|archdrift-|depsdrift-|uxdrift-|therapydrift-|fixdrift-|yagnidrift-|redrift-|speedrift-)"
)
_DRIFT_WORD_RE = re.compile(r"\bdrift\b", re.IGNORECASE)
_DRIFT_TAG_RE = re.compile(r"drift|therapy|fix|yagni|redrift")
_REDRIFT_PREFIX_RE = re.compile(r"^(redrift (analyze|respec|design|build|execute|exec):\s*)+", re.IGNORECASE)


def task_status(task: dict[str, Any]) -> str:
    return str(task.get("status") or "open")


def is_active(task: dict[str, Any]) -> bool:
    status = task_status(task)
    return status not in {"done", "abandoned"}


def has_contract(task: dict[str, Any]) -> bool:
    desc = str(task.get("description") or "")
    return "```wg-contract" in desc


def is_drift_task(task: dict[str, Any]) -> bool:
    task_id = str(task.get("id") or "")
    if _DRIFT_ID_RE.search(task_id):
        return True

    title = str(task.get("title") or "")
    if _DRIFT_WORD_RE.search(title):
        return True

    tags = task.get("tags")
    if isinstance(tags, list):
        tags_text = ",".join(str(t).lower() for t in tags)
        return bool(_DRIFT_TAG_RE.search(tags_text))

    return False


def redrift_depth(task_id: str) -> int:
    return str(task_id or "").count("redrift-")


def blockers_done(task: dict[str, Any], tasks_by_id: dict[str, dict[str, Any]]) -> bool:
    blockers = task.get("blocked_by")
    if not isinstance(blockers, list) or not blockers:
        return True  # No blockers → nothing blocking it → ready

    for blocker_id in blockers:
        blocker = tasks_by_id.get(str(blocker_id))
        if not blocker:
            return False
        if task_status(blocker) != "done":
            return False
    return True


def detect_cycle_from(task_id: str, tasks_by_id: dict[str, dict[str, Any]]) -> bool:
    target = str(task_id or "")
    if not target:
        return False

    visited: set[str] = set()
    stack: set[str] = set()

    def _dfs(cur: str) -> bool:
        if cur in stack:
            return True
        if cur in visited:
            return False
        visited.add(cur)
        stack.add(cur)
        node = tasks_by_id.get(cur)
        if isinstance(node, dict):
            blockers = node.get("blocked_by")
            if isinstance(blockers, list):
                for blocker in blockers:
                    if _dfs(str(blocker)):
                        return True
        stack.remove(cur)
        return False

    return _dfs(target)


def normalize_drift_key(task: dict[str, Any]) -> str:
    title = str(task.get("title") or "").strip().lower()
    task_id = str(task.get("id") or "").strip().lower()
    if title:
        title = _REDRIFT_PREFIX_RE.sub("", title)
        title = re.sub(r"\s+", " ", title).strip()
    return title or task_id


def find_duplicate_open_drift_groups(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        if not is_drift_task(task):
            continue
        if not is_active(task):
            continue
        key = normalize_drift_key(task)
        if not key:
            continue
        groups[key].append(task)

    out: list[dict[str, Any]] = []
    for key, grouped in groups.items():
        if len(grouped) <= 1:
            continue
        out.append(
            {
                "key": key,
                "count": len(grouped),
                "task_ids": [str(t.get("id") or "") for t in grouped],
            }
        )
    out.sort(key=lambda g: (-int(g["count"]), str(g["key"])))
    return out


def _task_epoch(task: dict[str, Any]) -> int:
    value = str(task.get("created_at") or "").strip()
    if not value:
        return 0
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _future_not_before(task: dict[str, Any]) -> bool:
    raw = str(task.get("not_before") or "").strip()
    if not raw:
        return False
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp()) > int(datetime.now(timezone.utc).timestamp())


def _queue_priority(task: dict[str, Any]) -> int:
    task_id = str(task.get("id") or "")
    title = str(task.get("title") or "").lower()
    if task_id.startswith("drift-breaker-"):
        return 100
    if task_id.startswith("coredrift-pit-"):
        return 90
    if "missing_contract" in title:
        return 85
    if task_id.startswith("drift-harden-"):
        return 80
    if task_id.startswith("drift-fix-"):
        return 75
    if task_id.startswith("drift-scope-"):
        return 70
    if task_id.startswith("redrift-"):
        return 60
    return 50


def rank_ready_drift_queue(tasks: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    tasks_by_id = {str(t.get("id") or ""): t for t in tasks}
    ready: list[dict[str, Any]] = []
    for task in tasks:
        if not is_drift_task(task):
            continue
        if not is_active(task):
            continue
        if _future_not_before(task):
            continue
        if not blockers_done(task, tasks_by_id):
            continue
        ready.append(task)

    ready.sort(key=lambda t: (-_queue_priority(t), _task_epoch(t), str(t.get("id") or "")))
    out: list[dict[str, Any]] = []
    for task in ready[: max(1, int(limit))]:
        out.append(
            {
                "task_id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": task_status(task),
                "priority": _queue_priority(task),
                "created_at": str(task.get("created_at") or ""),
                "blocked_by": [str(x) for x in (task.get("blocked_by") or []) if str(x)],
            }
        )
    return out


def compute_scoreboard(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    active = [t for t in tasks if is_active(t)]
    drift = [t for t in tasks if is_drift_task(t)]
    active_drift = [t for t in drift if is_active(t)]
    ready = rank_ready_drift_queue(tasks, limit=10_000)

    active_with_contract = sum(1 for t in active if has_contract(t))
    active_total = len(active)
    contract_coverage = (active_with_contract / active_total) if active_total else 1.0

    max_depth = 0
    for t in active_drift:
        task_id = str(t.get("id") or "")
        if not task_id.startswith("redrift-"):
            continue
        max_depth = max(max_depth, redrift_depth(task_id))

    duplicate_groups = find_duplicate_open_drift_groups(tasks)
    active_ratio = (len(active_drift) / active_total) if active_total else 0.0

    status = "healthy"
    if contract_coverage < 0.7 or len(ready) > 20 or max_depth > 2:
        status = "risk"
    elif contract_coverage < 0.9 or len(ready) > 8 or max_depth > 1 or duplicate_groups:
        status = "watch"

    return {
        "status": status,
        "tasks_total": len(tasks),
        "active_tasks": active_total,
        "drift_total": len(drift),
        "active_drift": len(active_drift),
        "ready_drift": len(ready),
        "active_contract_coverage": round(contract_coverage, 4),
        "active_drift_ratio": round(active_ratio, 4),
        "max_redrift_depth": max_depth,
        "duplicate_open_drift_groups": duplicate_groups,
    }
