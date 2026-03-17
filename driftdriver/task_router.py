# ABOUTME: Task routing layer — dispatches workgraph tasks to different executors based on tags.
# ABOUTME: Routes to HTTP agent endpoints, scheduled execution, or default wg daemon.

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


@dataclass
class ExecutorConfig:
    """Configuration for a single task executor."""

    name: str
    type: str  # "http", "schedule", "claude", "wg-daemon"
    endpoint: str  # URL for http type
    tag_match: str  # pattern like "agent:samantha" or "schedule:*"


@dataclass
class RoutingConfig:
    """Top-level routing configuration read from drift-policy.toml."""

    enabled: bool
    default_executor: str
    executors: dict[str, ExecutorConfig]


@dataclass
class DispatchResult:
    """Outcome of dispatching a single task."""

    task_id: str
    dispatched: bool
    executor: str
    error: str | None = None
    skipped_reason: str | None = None


def load_routing_config(policy_path: Path) -> RoutingConfig:
    """Read [routing] section from drift-policy.toml.

    Returns defaults (enabled=False) if the file or section is missing.
    """
    if not policy_path.exists():
        return RoutingConfig(enabled=False, default_executor="wg-daemon", executors={})

    with open(policy_path, "rb") as f:
        data = tomllib.load(f)

    routing = data.get("routing")
    if routing is None:
        return RoutingConfig(enabled=False, default_executor="wg-daemon", executors={})

    enabled = bool(routing.get("enabled", False))
    default_executor = str(routing.get("default_executor", "wg-daemon"))

    executors: dict[str, ExecutorConfig] = {}
    raw_executors = routing.get("executors", {})
    for name, cfg in raw_executors.items():
        executors[name] = ExecutorConfig(
            name=name,
            type=str(cfg.get("type", "wg-daemon")),
            endpoint=str(cfg.get("endpoint", "")),
            tag_match=str(cfg.get("tag_match", "")),
        )

    return RoutingConfig(
        enabled=enabled,
        default_executor=default_executor,
        executors=executors,
    )


def _tag_matches(tag: str, pattern: str) -> bool:
    """Check if a tag matches a pattern.

    Exact match: "agent:samantha" matches "agent:samantha"
    Wildcard:    "schedule:*" matches any tag starting with "schedule:"
    """
    if pattern.endswith(":*"):
        prefix = pattern[:-1]  # "schedule:"
        return tag.startswith(prefix)
    return tag == pattern


def match_executor(task: dict, config: RoutingConfig) -> ExecutorConfig | None:
    """Match a task to an executor based on its tags.

    Returns the first matching executor, or None if no match (use default).
    """
    tags = task.get("tags", [])
    if not tags:
        return None

    for executor in config.executors.values():
        for tag in tags:
            if _tag_matches(tag, executor.tag_match):
                return executor

    return None


def _parse_schedule_tag(tag: str) -> datetime | None:
    """Parse a schedule tag into a datetime.

    Supports:
      - schedule:HH:MM          → today at that time (UTC)
      - schedule:YYYY-MM-DDTHH:MM → exact datetime (UTC)

    Returns None for non-schedule tags or unparseable values.
    """
    if not tag.startswith("schedule:"):
        return None

    value = tag[len("schedule:"):]

    # Try ISO datetime first: YYYY-MM-DDTHH:MM
    if "T" in value:
        try:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    # Try time-only: HH:MM
    parts = value.split(":")
    if len(parts) == 2:
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            now = datetime.now(timezone.utc)
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (ValueError, TypeError):
            return None

    return None


def dispatch_task(
    task: dict, executor: ExecutorConfig, repo_path: Path
) -> DispatchResult:
    """Dispatch a task to the given executor.

    For type "http":     POST to endpoint with task payload.
    For type "claude":   Spawn claude subprocess.
    For type "schedule": Check if time has arrived, dispatch if so.
    For type "wg-daemon": Return skip (let the daemon handle it).
    """
    task_id = str(task.get("id", "unknown"))

    if executor.type == "wg-daemon":
        return DispatchResult(
            task_id=task_id,
            dispatched=False,
            executor=executor.name,
            skipped_reason="wg-daemon handles this task",
        )

    if executor.type == "http":
        return _dispatch_http(task, executor, repo_path)

    if executor.type == "claude":
        return _dispatch_claude(task, executor, repo_path)

    if executor.type == "schedule":
        return _dispatch_schedule(task, executor, repo_path)

    return DispatchResult(
        task_id=task_id,
        dispatched=False,
        executor=executor.name,
        error=f"Unknown executor type: {executor.type}",
    )


def _infer_category(task: dict) -> str:
    """Infer task category from tags and title for paia agent TaskRequest."""
    tags = task.get("tags", [])
    title = str(task.get("title", "")).lower()
    for tag in tags:
        if tag.startswith("category:"):
            return tag.split(":", 1)[1]
    if any(w in title for w in ("research", "investigate", "analyze", "explore")):
        return "research"
    if any(w in title for w in ("schedule", "meeting", "calendar", "reminder")):
        return "scheduling"
    if any(w in title for w in ("build", "implement", "fix", "create", "add")):
        return "build"
    if any(w in title for w in ("deploy", "release", "ship")):
        return "ops"
    return "request"


def _infer_urgency(task: dict) -> str:
    """Infer urgency from tags."""
    tags = task.get("tags", [])
    for tag in tags:
        if tag in ("urgent", "urgency:immediate", "priority:high"):
            return "immediate"
    return "routine"


def _dispatch_http(
    task: dict, executor: ExecutorConfig, repo_path: Path
) -> DispatchResult:
    """POST task payload to an HTTP endpoint.

    Formats the payload to match paia agent TaskRequest schema:
    {task_id, source, category, urgency, context}.
    """
    task_id = str(task.get("id", "unknown"))

    # Build payload matching paia agent TaskRequest (Pydantic model)
    payload = json.dumps({
        "task_id": task_id,
        "source": "speedrift-router",
        "category": _infer_category(task),
        "urgency": _infer_urgency(task),
        "context": {
            "title": task.get("title", ""),
            "description": task.get("description", ""),
            "tags": task.get("tags", []),
            "repo": repo_path.name,
            "repo_path": str(repo_path),
            "wg_task_id": task_id,
        },
    }).encode("utf-8")

    req = Request(
        executor.endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req) as resp:
            return DispatchResult(
                task_id=task_id,
                dispatched=True,
                executor=executor.name,
            )
    except (URLError, OSError) as exc:
        return DispatchResult(
            task_id=task_id,
            dispatched=False,
            executor=executor.name,
            error=str(exc),
        )


def _dispatch_claude(
    task: dict, executor: ExecutorConfig, repo_path: Path
) -> DispatchResult:
    """Spawn a full wg agent via `wg spawn` for proper context, lifecycle, and registration.

    Uses wg's executor pipeline (prompt assembly, agent registration, output logging)
    without the coordinator LLM that hangs. Clears stale graph locks before spawning.
    """
    task_id = str(task.get("id", "unknown"))
    wg_dir = repo_path / ".workgraph"

    # Clear stale graph lock (crashed processes leave empty lock files)
    lock_path = wg_dir / "graph.lock"
    if lock_path.exists():
        try:
            lock_path.unlink()
        except OSError:
            pass

    cmd = ["wg", "--dir", str(wg_dir), "spawn", task_id, "--executor", "claude"]

    # Use task model if specified, otherwise let wg resolve
    model = task.get("model")
    if model:
        cmd.extend(["--model", model])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return DispatchResult(
                task_id=task_id,
                dispatched=True,
                executor=executor.name,
            )
        else:
            error = result.stderr.strip()[:200] or result.stdout.strip()[:200] or "spawn failed"
            return DispatchResult(
                task_id=task_id,
                dispatched=False,
                executor=executor.name,
                error=error,
            )
    except subprocess.TimeoutExpired:
        return DispatchResult(
            task_id=task_id,
            dispatched=False,
            executor=executor.name,
            error="wg spawn timed out (30s) — possible graph lock or daemon issue",
        )
    except (OSError, FileNotFoundError) as exc:
        return DispatchResult(
            task_id=task_id,
            dispatched=False,
            executor=executor.name,
            error=str(exc),
        )


def _dispatch_schedule(
    task: dict, executor: ExecutorConfig, repo_path: Path
) -> DispatchResult:
    """Check if a schedule tag's time has arrived; dispatch if so."""
    task_id = str(task.get("id", "unknown"))
    tags = task.get("tags", [])

    # Find the first schedule: tag
    schedule_dt = None
    for tag in tags:
        if tag.startswith("schedule:"):
            schedule_dt = _parse_schedule_tag(tag)
            break

    if schedule_dt is None:
        return DispatchResult(
            task_id=task_id,
            dispatched=False,
            executor=executor.name,
            skipped_reason="No parseable schedule tag found",
        )

    now = datetime.now(timezone.utc)
    if now >= schedule_dt:
        return DispatchResult(
            task_id=task_id,
            dispatched=True,
            executor=executor.name,
        )
    else:
        return DispatchResult(
            task_id=task_id,
            dispatched=False,
            executor=executor.name,
            skipped_reason=f"Schedule not yet arrived (scheduled: {schedule_dt.isoformat()})",
        )


def _find_ready_tasks(graph_lines: list[dict]) -> list[dict]:
    """Find tasks that are open with all dependencies met.

    A task is ready when:
    - kind == "task"
    - status == "open"
    - all tasks in its "after" list have status in ("done", "abandoned")
    - it's not paused
    """
    statuses = {
        n["id"]: n.get("status", "")
        for n in graph_lines
        if n.get("kind") == "task"
    }
    terminal = {"done", "abandoned"}
    ready = []
    for node in graph_lines:
        if node.get("kind") != "task" or node.get("status") != "open":
            continue
        if node.get("paused"):
            continue
        deps = node.get("after", [])
        if all(statuses.get(d, "") in terminal for d in deps):
            ready.append(node)
    return ready


def _read_graph_lines(graph_path: Path) -> list[dict[str, Any]]:
    """Read all JSONL lines from a graph file."""
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
    """Write graph lines atomically."""
    tmp = graph_path.with_suffix(".jsonl.router-tmp")
    tmp.write_text(
        "\n".join(json.dumps(n) for n in nodes) + "\n",
        encoding="utf-8",
    )
    tmp.replace(graph_path)


def claim_task(task_id: str, repo_path: Path) -> bool:
    """Claim a task by setting its status to in-progress in graph.jsonl.

    Returns True if claimed, False if already in-progress or not found.
    """
    graph_path = repo_path / ".workgraph" / "graph.jsonl"
    nodes = _read_graph_lines(graph_path)

    found = False
    for node in nodes:
        if node.get("kind") == "task" and node.get("id") == task_id:
            if node.get("status") != "open":
                return False
            node["status"] = "in-progress"
            node["started_at"] = datetime.now(timezone.utc).isoformat()
            found = True
            break

    if not found:
        return False

    _write_graph_lines(graph_path, nodes)
    return True


def _unclaim_task(task_id: str, repo_path: Path) -> None:
    """Revert a claimed task back to open (used when dispatch fails)."""
    graph_path = repo_path / ".workgraph" / "graph.jsonl"
    nodes = _read_graph_lines(graph_path)
    for node in nodes:
        if node.get("kind") == "task" and node.get("id") == task_id:
            if node.get("status") == "in-progress":
                node["status"] = "open"
                node.pop("started_at", None)
                log = node.get("log", [])
                if isinstance(log, list):
                    log.append({
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "message": "Unclaimed by router: dispatch failed, returning to open",
                    })
            break
    _write_graph_lines(graph_path, nodes)


def route_ready_tasks(
    repo_path: Path, config: RoutingConfig
) -> list[DispatchResult]:
    """Load graph, find ready tasks, match executors, claim and dispatch.

    Returns a list of DispatchResult for each ready task processed.
    """
    graph_path = repo_path / ".workgraph" / "graph.jsonl"
    nodes = _read_graph_lines(graph_path)
    ready = _find_ready_tasks(nodes)

    results: list[DispatchResult] = []
    for task in ready:
        executor = match_executor(task, config)
        if executor is None:
            # Use default executor
            executor = ExecutorConfig(
                name=config.default_executor,
                type=config.default_executor,
                endpoint="",
                tag_match="",
            )

        # Claim before dispatch (HTTP executors only — wg spawn handles its own claiming)
        if executor.type == "http":
            claimed = claim_task(str(task["id"]), repo_path)
            if not claimed:
                results.append(DispatchResult(
                    task_id=str(task["id"]),
                    dispatched=False,
                    executor=executor.name,
                    error="Failed to claim task",
                ))
                continue

        result = dispatch_task(task, executor, repo_path)

        # Unclaim on failure so the task is retried next tick (HTTP only — wg spawn doesn't claim on failure)
        if not result.dispatched and result.error and executor.type == "http":
            _unclaim_task(str(task["id"]), repo_path)

        results.append(result)

    return results


@dataclass
class CompletionResult:
    """Outcome of checking a dispatched task's completion status."""

    task_id: str
    completed: bool
    summary: str | None = None
    error: str | None = None


def check_agent_completions(
    repo_path: Path, config: RoutingConfig
) -> list[CompletionResult]:
    """Poll HTTP agent endpoints for in-progress tasks and mark completed ones as done.

    For each in-progress task with an agent tag:
    1. Match the tag to an HTTP executor
    2. GET the task status from the agent endpoint
    3. If status is "done", mark the wg task as done with the agent's summary
    4. If status is "failed", mark the wg task as failed
    """
    graph_path = repo_path / ".workgraph" / "graph.jsonl"
    nodes = _read_graph_lines(graph_path)
    results: list[CompletionResult] = []
    modified = False

    for node in nodes:
        if node.get("kind") != "task" or node.get("status") != "in-progress":
            continue

        # Only check tasks with agent tags that match HTTP executors
        executor = match_executor(node, config)
        if executor is None or executor.type != "http":
            continue

        task_id = str(node.get("id", ""))
        check_url = executor.endpoint.rstrip("/")
        # Transform /api/agent/task → /api/agent/task/{task_id}
        status_url = f"{check_url}/{task_id}"

        try:
            req = Request(status_url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            results.append(CompletionResult(
                task_id=task_id, completed=False,
                error=f"Status check failed: {str(exc)[:100]}",
            ))
            continue

        agent_status = str(data.get("status", "")).lower()
        summary = data.get("summary", "")

        if agent_status == "done":
            node["status"] = "done"
            node["completed_at"] = datetime.now(timezone.utc).isoformat()
            log = node.get("log", [])
            if not isinstance(log, list):
                log = []
            log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Completed by agent via router. Summary: {str(summary)[:200]}",
            })
            node["log"] = log
            modified = True
            results.append(CompletionResult(
                task_id=task_id, completed=True, summary=str(summary)[:500],
            ))

        elif agent_status == "failed":
            node["status"] = "failed"
            node["failure_reason"] = str(data.get("error", "Agent reported failure"))[:500]
            log = node.get("log", [])
            if not isinstance(log, list):
                log = []
            log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": f"Failed by agent via router: {node['failure_reason']}",
            })
            node["log"] = log
            modified = True
            results.append(CompletionResult(
                task_id=task_id, completed=True, error=node["failure_reason"],
            ))

        # Status "accepted" or "running" → still in progress, skip

    if modified:
        _write_graph_lines(graph_path, nodes)

    return results


def route_ecosystem(
    workspace_root: Path,
    routing_config: RoutingConfig,
    repo_names: list[str] | None = None,
) -> dict:
    """Route ready tasks across multiple repos in the workspace.

    Returns a summary dict keyed by repo name.
    """
    if repo_names is None:
        repo_names = [
            d.name
            for d in workspace_root.iterdir()
            if d.is_dir() and (d / ".workgraph" / "graph.jsonl").exists()
        ]

    summary: dict[str, Any] = {}
    for name in repo_names:
        repo_path = workspace_root / name
        wg_dir = repo_path / ".workgraph"

        if not (wg_dir / "graph.jsonl").exists():
            summary[name] = {"skipped": True, "reason": "no workgraph"}
            continue

        # Load per-repo config if available, else use provided
        per_repo_policy = wg_dir / "drift-policy.toml"
        if per_repo_policy.exists():
            repo_config = load_routing_config(per_repo_policy)
        else:
            repo_config = routing_config

        results = route_ready_tasks(repo_path, repo_config)
        dispatched = sum(1 for r in results if r.dispatched)
        skipped = sum(1 for r in results if not r.dispatched)
        errors = [r.error for r in results if r.error]

        summary[name] = {
            "dispatched": dispatched,
            "skipped": skipped,
            "errors": errors,
            "results": results,
        }

    return summary
