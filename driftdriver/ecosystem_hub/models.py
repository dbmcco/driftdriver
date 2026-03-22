# ABOUTME: Dataclass definitions for ecosystem hub domain objects.
# ABOUTME: NextWorkItem, RepoSnapshot, UpstreamCandidate, DraftPRRequest.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NextWorkItem:
    repo: str
    task_id: str
    title: str
    status: str
    priority: int


@dataclass
class RepoSnapshot:
    name: str
    path: str
    exists: bool
    source: str = ""
    tags: list[str] = field(default_factory=list)
    lifecycle: str = "active"
    daemon_posture: str = "always-on"
    errors: list[str] = field(default_factory=list)
    git_branch: str = ""
    git_dirty: bool = False
    dirty_file_count: int = 0
    untracked_file_count: int = 0
    ahead: int = 0
    behind: int = 0
    workgraph_exists: bool = False
    wg_available: bool = False
    reporting: bool = False
    heartbeat_age_seconds: int | None = None
    service_running: bool = False
    task_counts: dict[str, int] = field(default_factory=dict)
    in_progress: list[dict[str, str]] = field(default_factory=list)
    ready: list[dict[str, Any]] = field(default_factory=list)
    blocked_open: int = 0
    missing_dependencies: int = 0
    stale_open: list[dict[str, Any]] = field(default_factory=list)
    stale_in_progress: list[dict[str, Any]] = field(default_factory=list)
    dependency_issues: list[dict[str, Any]] = field(default_factory=list)
    cross_repo_dependencies: list[dict[str, Any]] = field(default_factory=list)
    task_graph_nodes: list[dict[str, Any]] = field(default_factory=list)
    task_graph_edges: list[dict[str, Any]] = field(default_factory=list)
    activity_state: str = "unknown"
    stalled: bool = False
    stall_reasons: list[str] = field(default_factory=list)
    narrative: str = ""
    security: dict[str, Any] = field(default_factory=dict)
    security_findings: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    quality_findings: list[dict[str, Any]] = field(default_factory=list)
    repo_north_star: dict[str, Any] = field(default_factory=dict)
    northstar: dict[str, Any] = field(default_factory=dict)
    runtime: dict[str, Any] = field(default_factory=dict)
    service_status: dict[str, Any] = field(default_factory=dict)
    presence_actors: list[dict[str, Any]] = field(default_factory=list)
    continuation_intent: dict[str, Any] = field(default_factory=dict)
    attractor_target: str = ""
    attractor_status: str = ""
    attractor_last_run: dict[str, Any] = field(default_factory=dict)

    def top_next_work(self, limit: int = 3) -> list[NextWorkItem]:
        out: list[NextWorkItem] = []
        for task in self.in_progress[:limit]:
            out.append(
                NextWorkItem(
                    repo=self.name,
                    task_id=str(task.get("id") or ""),
                    title=str(task.get("title") or ""),
                    status="in-progress",
                    priority=100,
                )
            )
        remaining = max(0, limit - len(out))
        for task in self.ready[:remaining]:
            out.append(
                NextWorkItem(
                    repo=self.name,
                    task_id=str(task.get("id") or ""),
                    title=str(task.get("title") or ""),
                    status="ready",
                    priority=60,
                )
            )
        return out


@dataclass
class UpstreamCandidate:
    repo: str
    path: str
    branch: str
    base_ref: str
    ahead: int
    behind: int
    working_tree_dirty: bool
    changed_files: list[str]
    category: str
    summary: str


@dataclass
class DraftPRRequest:
    repo: str
    repo_path: str
    branch: str
    base: str
    title: str
    body: str
    command: list[str]
