from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from driftdriver.updates import (
    check_ecosystem_updates,
    load_review_config,
    summarize_updates,
)
from driftdriver.policy import load_drift_policy
from driftdriver.workgraph import load_workgraph


_CHILD_PROCS: dict[int, subprocess.Popen[str]] = {}
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_STALE_OPEN_DAYS = 14.0
_STALE_IN_PROGRESS_DAYS = 3.0
_MAX_TASK_GRAPH_NODES = 140
_DISCOVERY_ACTIVE_DAYS = 30.0
_DISCOVERY_MAX_REPOS = 16
_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS = 180
_SUPERVISOR_DEFAULT_MAX_STARTS = 4
_SUPERVISOR_LAST_ATTEMPT: dict[str, float] = {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(raw: str) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    # Workgraph commonly emits UTC timestamps with "Z".
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_days(raw: str, *, now: datetime) -> float | None:
    dt = _parse_iso_datetime(raw)
    if dt is None:
        return None
    delta = now - dt
    if delta < timedelta(0):
        return 0.0
    return round(delta.total_seconds() / 86400.0, 2)


def _ws_accept_key(client_key: str) -> str:
    token = f"{client_key}{_WS_GUID}".encode("ascii")
    digest = hashlib.sha1(token).digest()  # noqa: S324 - websocket protocol requires SHA-1
    return base64.b64encode(digest).decode("ascii")


def _recv_exact(sock_obj: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        piece = sock_obj.recv(count - len(chunks))
        if not piece:
            raise ConnectionError("socket_closed")
        chunks.extend(piece)
    return bytes(chunks)


def _encode_ws_frame(payload: bytes, *, opcode: int = 0x1) -> bytes:
    first = 0x80 | (opcode & 0x0F)
    size = len(payload)
    if size <= 125:
        header = bytes((first, size))
    elif size <= 65535:
        header = bytes((first, 126)) + struct.pack("!H", size)
    else:
        header = bytes((first, 127)) + struct.pack("!Q", size)
    return header + payload


def _read_ws_frame(sock_obj: socket.socket) -> tuple[int, bytes]:
    header = _recv_exact(sock_obj, 2)
    first, second = header[0], header[1]
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    size = second & 0x7F
    if size == 126:
        size = struct.unpack("!H", _recv_exact(sock_obj, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(sock_obj, 8))[0]

    mask = _recv_exact(sock_obj, 4) if masked else b""
    payload = _recv_exact(sock_obj, size) if size else b""
    if masked and payload:
        payload = bytes(value ^ mask[idx % 4] for idx, value in enumerate(payload))
    return opcode, payload


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 8.0,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _service_port_alive(host: str, port: int) -> bool:
    if port <= 0:
        return False
    probe_host = host.strip()
    if probe_host in ("", "0.0.0.0", "::"):
        probe_host = "127.0.0.1"
    try:
        with socket.create_connection((probe_host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _safe_ts_for_file(iso_ts: str) -> str:
    return iso_ts.replace(":", "-").replace("+00:00", "Z")


def resolve_central_repo_path(project_dir: Path, explicit_path: str = "") -> Path | None:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    wg_dir = project_dir / ".workgraph"
    try:
        policy = load_drift_policy(wg_dir)
    except Exception:
        return None
    raw = str(getattr(policy, "reporting_central_repo", "") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def _collect_central_reports_summary(central_repo: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    reports_root = central_repo / "reports"
    if not reports_root.exists():
        return []
    latest_by_project: list[dict[str, Any]] = []
    for project_dir in sorted(p for p in reports_root.iterdir() if p.is_dir()):
        latest_ts_dir: Path | None = None
        latest_mtime = -1.0
        for ts_dir in project_dir.iterdir():
            if not ts_dir.is_dir():
                continue
            try:
                mtime = ts_dir.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_ts_dir = ts_dir
        if latest_ts_dir is None:
            continue
        report_file = latest_ts_dir / "report.md"
        has_report = report_file.exists()
        latest_by_project.append(
            {
                "project": project_dir.name,
                "timestamp_dir": latest_ts_dir.name,
                "path": str(latest_ts_dir),
                "has_report_md": has_report,
            }
        )
    latest_by_project.sort(key=lambda x: str(x.get("project") or ""))
    return latest_by_project[:limit]


def write_central_register(
    *,
    central_repo: Path,
    project_name: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    hub_root = central_repo / "ecosystem-hub"
    register_dir = hub_root / "register"
    history_dir = hub_root / "history" / project_name
    register_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    latest_path = register_dir / f"{project_name}.json"
    stamp = _safe_ts_for_file(str(snapshot.get("generated_at") or _iso_now()))
    historical = history_dir / f"{stamp}.json"

    _write_json(latest_path, snapshot)
    _write_json(historical, snapshot)

    return {
        "central_repo": str(central_repo),
        "latest_path": str(latest_path),
        "history_path": str(historical),
    }


def _git_default_ref(repo_path: Path) -> str:
    rc, out, _ = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path)
    if rc == 0 and out:
        # refs/remotes/origin/main -> origin/main
        if out.startswith("refs/remotes/"):
            return out[len("refs/remotes/"):]
        return out
    for fallback in ("origin/main", "origin/master"):
        rc2, _, _ = _run(["git", "rev-parse", "--verify", fallback], cwd=repo_path)
        if rc2 == 0:
            return fallback
    return "origin/main"


def _normalize_dependencies(raw_after: Any) -> list[str]:
    if not isinstance(raw_after, list):
        return []
    out: list[str] = []
    for row in raw_after:
        if isinstance(row, dict):
            rid = str(row.get("id") or "").strip()
            if rid:
                out.append(rid)
        else:
            rid = str(row).strip()
            if rid:
                out.append(rid)
    return out


def _compute_ready_tasks(tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    for task in tasks.values():
        status = str(task.get("status") or "").strip().lower()
        if status not in ("open", "ready"):
            continue
        deps = _normalize_dependencies(task.get("after"))
        blocked = False
        for dep_id in deps:
            dep = tasks.get(dep_id)
            if not dep:
                continue
            dep_status = str(dep.get("status") or "").strip().lower()
            if dep_status != "done":
                blocked = True
                break
        if blocked:
            continue
        ready.append(
            {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": status,
                "after": deps,
                "created_at": str(task.get("created_at") or ""),
            }
        )
    ready.sort(key=lambda t: (t.get("created_at") or "", t.get("id") or ""))
    return ready


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
    errors: list[str] = field(default_factory=list)
    git_branch: str = ""
    git_dirty: bool = False
    ahead: int = 0
    behind: int = 0
    workgraph_exists: bool = False
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
    narrative: str = ""

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


def classify_upstream_candidate(files: list[str], subjects: list[str]) -> str:
    lower_files = [f.lower() for f in files]
    lower_subjects = [s.lower() for s in subjects]
    if lower_subjects and any(("fix" in s or "bug" in s) for s in lower_subjects):
        return "bugfix"
    if lower_files and all((f.endswith(".md") or f.startswith("docs/")) for f in lower_files):
        return "docs"
    if any(
        (
            f.startswith("scripts/")
            or f.startswith(".github/")
            or f.endswith(".yml")
            or f.endswith(".yaml")
            or f.endswith(".toml")
        )
        for f in lower_files
    ):
        return "tooling"
    return "feature"


def generate_upstream_candidates(repo_name: str, repo_path: Path) -> list[UpstreamCandidate]:
    if not (repo_path / ".git").exists():
        return []

    rc, branch, err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if rc != 0:
        return []
    current_branch = branch or "HEAD"
    base_ref = _git_default_ref(repo_path)

    rc, counts, _ = _run(["git", "rev-list", "--left-right", "--count", f"{base_ref}...HEAD"], cwd=repo_path)
    ahead = 0
    behind = 0
    if rc == 0 and counts:
        parts = counts.split()
        if len(parts) >= 2:
            try:
                behind = int(parts[0])
                ahead = int(parts[1])
            except ValueError:
                ahead = 0
                behind = 0

    rc, porcelain, _ = _run(["git", "status", "--porcelain"], cwd=repo_path)
    dirty_files: set[str] = set()
    if rc == 0 and porcelain:
        for line in porcelain.splitlines():
            raw = line[3:].strip()
            if not raw:
                continue
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1].strip()
            dirty_files.add(raw)

    rc, diff_names, _ = _run(["git", "diff", "--name-only", f"{base_ref}...HEAD"], cwd=repo_path)
    commit_files = set(diff_names.splitlines()) if rc == 0 and diff_names else set()
    changed_files = sorted({f for f in dirty_files.union(commit_files) if f})

    if ahead <= 0 and not dirty_files:
        return []
    if not changed_files:
        return []

    rc, subject_out, _ = _run(["git", "log", "--format=%s", f"{base_ref}..HEAD", "-n", "20"], cwd=repo_path)
    subjects = subject_out.splitlines() if rc == 0 and subject_out else []
    category = classify_upstream_candidate(changed_files, subjects)
    summary = (
        f"{repo_name}: {category} candidate from {current_branch} vs {base_ref} "
        f"(ahead={ahead}, dirty_files={len(dirty_files)}, files={len(changed_files)})"
    )

    return [
        UpstreamCandidate(
            repo=repo_name,
            path=str(repo_path),
            branch=current_branch,
            base_ref=base_ref,
            ahead=ahead,
            behind=behind,
            working_tree_dirty=bool(dirty_files),
            changed_files=changed_files[:120],
            category=category,
            summary=summary,
        )
    ]


def render_upstream_packets(candidates: list[UpstreamCandidate]) -> str:
    lines: list[str] = ["# Upstream Contribution Candidates", ""]
    if not candidates:
        lines.append("No upstream contribution candidates detected.")
        lines.append("")
        return "\n".join(lines)
    for cand in candidates:
        lines.append(f"## {cand.repo}: {cand.category}")
        lines.append("")
        lines.append(f"- Path: `{cand.path}`")
        lines.append(f"- Branch: `{cand.branch}`")
        lines.append(f"- Base: `{cand.base_ref}`")
        lines.append(f"- Ahead/Behind: `{cand.ahead}/{cand.behind}`")
        lines.append(f"- Dirty working tree: `{'yes' if cand.working_tree_dirty else 'no'}`")
        lines.append(f"- Summary: {cand.summary}")
        lines.append("- Suggested next action: create a small focused PR scoped to the files below.")
        lines.append("- Changed files:")
        for name in cand.changed_files[:30]:
            lines.append(f"  - `{name}`")
        if len(cand.changed_files) > 30:
            lines.append(f"  - ... and {len(cand.changed_files) - 30} more")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_draft_pr_requests(
    candidates: list[UpstreamCandidate],
    *,
    title_prefix: str = "speedrift",
) -> list[DraftPRRequest]:
    requests: list[DraftPRRequest] = []
    for cand in candidates:
        title = f"[{title_prefix}] {cand.category}: contribute {cand.repo} changes upstream"
        body = (
            f"Automated draft from Speedrift ecosystem hub.\n\n"
            f"Summary: {cand.summary}\n"
            f"Category: {cand.category}\n"
            f"Ahead/Behind vs {cand.base_ref}: {cand.ahead}/{cand.behind}\n"
            f"Working tree dirty: {'yes' if cand.working_tree_dirty else 'no'}\n\n"
            "Changed files:\n"
            + "\n".join(f"- {name}" for name in cand.changed_files[:40])
        )
        command = [
            "gh",
            "pr",
            "create",
            "--draft",
            "--title",
            title,
            "--body",
            body,
            "--base",
            cand.base_ref.replace("origin/", ""),
            "--head",
            cand.branch,
        ]
        requests.append(
            DraftPRRequest(
                repo=cand.repo,
                repo_path=cand.path,
                branch=cand.branch,
                base=cand.base_ref.replace("origin/", ""),
                title=title,
                body=body,
                command=command,
            )
        )
    return requests


def run_draft_pr_requests(
    requests: list[DraftPRRequest],
    *,
    execute: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if not execute:
        for req in requests:
            results.append(
                {
                    "repo": req.repo,
                    "repo_path": req.repo_path,
                    "dry_run": True,
                    "command": req.command,
                }
            )
        return results

    gh_rc, _, gh_err = _run(["gh", "--version"])
    if gh_rc != 0:
        return [
            {
                "repo": req.repo,
                "repo_path": req.repo_path,
                "ok": False,
                "error": f"gh_cli_missing:{gh_err or 'install gh and authenticate first'}",
            }
            for req in requests
        ]

    for req in requests:
        rc, out, err = _run(req.command, cwd=Path(req.repo_path), timeout=30.0)
        results.append(
            {
                "repo": req.repo,
                "repo_path": req.repo_path,
                "ok": rc == 0,
                "exit_code": rc,
                "stdout": out,
                "stderr": err,
                "command": req.command,
            }
        )
    return results


def apply_upstream_automation(
    *,
    service_dir: Path,
    candidates: list[UpstreamCandidate],
    title_prefix: str,
    execute_draft_prs: bool,
) -> dict[str, Any]:
    requests = build_draft_pr_requests(candidates, title_prefix=title_prefix)
    results = run_draft_pr_requests(requests, execute=execute_draft_prs)
    payload = {
        "updated_at": _iso_now(),
        "execute_draft_prs": execute_draft_prs,
        "request_count": len(requests),
        "results": results,
    }
    _write_json(service_dir / "upstream-actions.json", payload)
    return payload


def _load_ecosystem_repos(ecosystem_toml: Path, workspace_root: Path) -> dict[str, Path]:
    if not ecosystem_toml.exists():
        return {}
    try:
        data = tomllib.loads(ecosystem_toml.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = data.get("repos")
    if not isinstance(repos, dict):
        return {}
    out: dict[str, Path] = {}
    for name in repos:
        key = str(name).strip()
        if not key:
            continue
        out[key] = workspace_root / key
    return out


def _policy_uses_speedrift(wg_dir: Path) -> bool:
    policy = wg_dir / "drift-policy.toml"
    if not policy.exists():
        return False
    try:
        text = policy.read_text(encoding="utf-8")
    except OSError:
        return False
    lower = text.lower()
    markers = (
        "speedrift",
        "driftdriver",
        "coredrift",
        "specdrift",
        "datadrift",
        "depsdrift",
        "uxdrift",
        "therapydrift",
        "yagnidrift",
        "redrift",
    )
    return any(marker in lower for marker in markers)


def _discover_active_workspace_repos(
    workspace_root: Path,
    *,
    existing: set[str],
    max_extra: int = _DISCOVERY_MAX_REPOS,
    active_within_days: float = _DISCOVERY_ACTIVE_DAYS,
) -> dict[str, Path]:
    now = time.time()
    discovered: list[tuple[float, str, Path]] = []
    for child in workspace_root.iterdir():
        if not child.is_dir():
            continue
        name = child.name
        if not name or name.startswith(".") or name in existing:
            continue
        if not (child / ".git").exists():
            continue
        wg_dir = child / ".workgraph"
        graph = wg_dir / "graph.jsonl"
        if not graph.exists():
            continue
        if not _policy_uses_speedrift(wg_dir):
            continue
        try:
            age_days = (now - graph.stat().st_mtime) / 86400.0
        except OSError:
            continue
        if age_days > active_within_days:
            continue
        discovered.append((age_days, name, child))

    discovered.sort(key=lambda row: (row[0], row[1]))
    out: dict[str, Path] = {}
    for _age_days, name, path in discovered[:max(0, max_extra)]:
        out[name] = path
    return out


def _repo_token_present(text: str, repo_name: str) -> bool:
    base = str(text or "").strip().lower()
    token = str(repo_name or "").strip().lower()
    if not base or not token:
        return False
    pattern = rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)"
    return re.search(pattern, base) is not None


def _collect_cross_repo_dependencies(
    *,
    repo_name: str,
    tasks: dict[str, dict[str, Any]],
    known_repo_names: set[str],
    policy_order: list[str],
) -> list[dict[str, Any]]:
    self_name = str(repo_name or "").strip().lower()
    known = {str(name).strip().lower() for name in known_repo_names if str(name).strip()}
    known.discard(self_name)
    if not known:
        return []

    signals: dict[str, dict[str, Any]] = {}

    def ensure(repo: str) -> dict[str, Any]:
        row = signals.get(repo)
        if row is None:
            row = {
                "repo": repo,
                "score": 0,
                "task_reference": 0,
                "explicit_dependency_ref": 0,
                "policy_order": 0,
            }
            signals[repo] = row
        return row

    for task in tasks.values():
        task_id = str(task.get("id") or "")
        title = str(task.get("title") or "")
        deps = _normalize_dependencies(task.get("after"))
        lines = [task_id, title, *deps]
        for other in known:
            hit = any(_repo_token_present(line, other) for line in lines)
            if hit:
                row = ensure(other)
                row["task_reference"] += 1
                row["score"] += 2

        for dep in deps:
            dep_lower = str(dep).strip().lower()
            if not dep_lower:
                continue
            for sep in (":", "/", "#", "."):
                if sep not in dep_lower:
                    continue
                prefix = dep_lower.split(sep, 1)[0].strip()
                if prefix in known:
                    row = ensure(prefix)
                    row["explicit_dependency_ref"] += 1
                    row["score"] += 4
                    break

    for lane in policy_order:
        lane_name = str(lane or "").strip().lower()
        if not lane_name or lane_name == self_name:
            continue
        if lane_name in known:
            row = ensure(lane_name)
            row["policy_order"] += 1
            row["score"] += 1

    out: list[dict[str, Any]] = []
    for row in signals.values():
        explicit_refs = int(row.get("explicit_dependency_ref") or 0)
        task_refs = int(row.get("task_reference") or 0)
        # Ignore weak/noisy matches; keep links that are explicit or repeatedly referenced.
        if explicit_refs <= 0 and task_refs < 2:
            continue
        reasons: list[str] = []
        if explicit_refs > 0:
            reasons.append(f"explicit_dependency_ref={row['explicit_dependency_ref']}")
        if task_refs > 0:
            reasons.append(f"task_reference={row['task_reference']}")
        if int(row.get("policy_order") or 0) > 0:
            reasons.append(f"policy_order={row['policy_order']}")
        out.append(
            {
                "repo": str(row.get("repo") or ""),
                "score": int(row.get("score") or 0),
                "reasons": reasons,
            }
        )

    out.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("repo") or "")))
    return out[:24]


def _task_status_rank(status: str) -> int:
    norm = str(status or "").strip().lower()
    if norm == "in-progress":
        return 0
    if norm in ("open", "ready"):
        return 1
    if norm in ("blocked", "review"):
        return 2
    if norm == "done":
        return 4
    return 3


def _build_repo_narrative(snap: RepoSnapshot) -> str:
    if not snap.exists:
        return f"{snap.name}: repo missing from workspace."
    if snap.errors:
        return f"{snap.name}: attention required ({', '.join(snap.errors[:3])})."
    parts: list[str] = []
    in_progress = len(snap.in_progress)
    ready = len(snap.ready)
    open_count = int(snap.task_counts.get("open", 0)) + int(snap.task_counts.get("ready", 0))
    if in_progress > 0:
        parts.append(f"{in_progress} in progress")
    if ready > 0:
        parts.append(f"{ready} ready to start")
    if open_count > 0 and in_progress == 0:
        parts.append(f"{open_count} open without active execution")
    if snap.blocked_open > 0:
        parts.append(f"{snap.blocked_open} open tasks blocked by dependencies")
    if snap.missing_dependencies > 0:
        parts.append(f"{snap.missing_dependencies} missing dependency references")
    if snap.stale_open:
        parts.append(f"{len(snap.stale_open)} aging open tasks")
    if snap.stale_in_progress:
        parts.append(f"{len(snap.stale_in_progress)} long-running in-progress tasks")
    if snap.workgraph_exists and not snap.service_running:
        parts.append("workgraph service not running")
    if snap.behind > 0:
        parts.append(f"behind upstream by {snap.behind}")
    if snap.git_dirty:
        parts.append("working tree has local changes")
    if not parts:
        return f"{snap.name}: healthy, no immediate blockers."
    return f"{snap.name}: " + "; ".join(parts[:6]) + "."


def _build_repo_task_graph(tasks: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not tasks:
        return [], []

    now = datetime.now(timezone.utc)
    normalized: dict[str, dict[str, Any]] = {}
    for task in tasks.values():
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        status = str(task.get("status") or "unknown").strip().lower()
        created_at = str(task.get("created_at") or "")
        deps = _normalize_dependencies(task.get("after"))
        age = _age_days(created_at, now=now)
        blocked = False
        for dep in deps:
            dep_row = tasks.get(dep)
            if dep_row and str(dep_row.get("status") or "").strip().lower() != "done":
                blocked = True
                break
        normalized[task_id] = {
            "id": task_id,
            "title": str(task.get("title") or ""),
            "status": status,
            "created_at": created_at,
            "after": deps,
            "age_days": age,
            "blocked": blocked,
        }

    ranked = sorted(
        normalized.values(),
        key=lambda row: (
            _task_status_rank(str(row.get("status") or "")),
            -(float(row.get("age_days") or 0.0)),
            str(row.get("id") or ""),
        ),
    )
    selected_ids = [str(row.get("id") or "") for row in ranked[:_MAX_TASK_GRAPH_NODES]]
    selected: set[str] = {item for item in selected_ids if item}

    # Pull in direct dependencies for selected nodes so relationships are visible.
    for row in ranked:
        row_id = str(row.get("id") or "")
        if row_id not in selected:
            continue
        for dep in row.get("after") or []:
            dep_id = str(dep).strip()
            if dep_id and dep_id in normalized and len(selected) < _MAX_TASK_GRAPH_NODES:
                selected.add(dep_id)

    nodes = []
    for task_id in sorted(selected):
        row = normalized.get(task_id)
        if not row:
            continue
        nodes.append(
            {
                "id": task_id,
                "label": str(row.get("title") or task_id),
                "status": str(row.get("status") or "unknown"),
                "age_days": row.get("age_days"),
                "blocked": bool(row.get("blocked")),
            }
        )

    edges: list[dict[str, Any]] = []
    for task_id in sorted(selected):
        row = normalized.get(task_id)
        if not row:
            continue
        for dep in row.get("after") or []:
            dep_id = str(dep).strip()
            if dep_id and dep_id in selected:
                edges.append({"source": dep_id, "target": task_id})
    return nodes, edges


def collect_repo_snapshot(
    repo_name: str,
    repo_path: Path,
    *,
    max_next: int = 5,
    known_repo_names: set[str] | None = None,
) -> RepoSnapshot:
    snap = RepoSnapshot(name=repo_name, path=str(repo_path), exists=repo_path.exists())
    if not snap.exists:
        snap.errors.append("repo_missing")
        snap.narrative = _build_repo_narrative(snap)
        return snap
    if not (repo_path / ".git").exists():
        snap.errors.append("not_a_git_repo")
        snap.narrative = _build_repo_narrative(snap)
        return snap

    rc, branch, err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if rc != 0:
        snap.errors.append(f"git_branch_error:{err or 'unknown'}")
    else:
        snap.git_branch = branch

    rc, porcelain, err = _run(["git", "status", "--porcelain"], cwd=repo_path)
    if rc != 0:
        snap.errors.append(f"git_status_error:{err or 'unknown'}")
    else:
        snap.git_dirty = bool(porcelain)

    base_ref = _git_default_ref(repo_path)
    rc, counts, _ = _run(["git", "rev-list", "--left-right", "--count", f"{base_ref}...HEAD"], cwd=repo_path)
    if rc == 0 and counts:
        parts = counts.split()
        if len(parts) >= 2:
            try:
                snap.behind = int(parts[0])
                snap.ahead = int(parts[1])
            except ValueError:
                pass

    wg_dir = repo_path / ".workgraph"
    if not (wg_dir / "graph.jsonl").exists():
        snap.narrative = _build_repo_narrative(snap)
        return snap

    snap.workgraph_exists = True

    # Service status is best-effort; missing wg is non-fatal.
    rc, status_json, _ = _run(["wg", "--dir", str(wg_dir), "service", "status", "--json"], cwd=repo_path)
    if rc == 0 and status_json:
        try:
            status = json.loads(status_json)
        except Exception:
            status = {}
        if isinstance(status, dict):
            state = str(status.get("status") or "")
            running = bool(status.get("running")) or state == "running"
            snap.service_running = running

    wg = load_workgraph(wg_dir)
    policy_order: list[str] = []
    try:
        policy_order = list(load_drift_policy(wg_dir).order)
    except Exception:
        policy_order = []
    counts: dict[str, int] = {}
    in_progress: list[dict[str, str]] = []
    stale_open: list[dict[str, Any]] = []
    stale_in_progress: list[dict[str, Any]] = []
    dependency_issues: list[dict[str, Any]] = []
    blocked_open = 0
    missing_dependencies = 0
    now = datetime.now(timezone.utc)

    for task in wg.tasks.values():
        task_id = str(task.get("id") or "")
        task_title = str(task.get("title") or "")
        status = str(task.get("status") or "unknown").lower()
        created_at = str(task.get("created_at") or "")
        age = _age_days(created_at, now=now)
        deps = _normalize_dependencies(task.get("after"))
        counts[status] = counts.get(status, 0) + 1
        if status == "in-progress":
            in_progress.append({"id": task_id, "title": task_title})
            if age is not None and age >= _STALE_IN_PROGRESS_DAYS:
                stale_in_progress.append(
                    {
                        "id": task_id,
                        "title": task_title,
                        "status": status,
                        "age_days": age,
                        "created_at": created_at,
                    }
                )
            continue

        if status in ("open", "ready"):
            blocking: list[dict[str, str]] = []
            for dep in deps:
                dep_id = str(dep).strip()
                if not dep_id:
                    continue
                dep = wg.tasks.get(dep_id)
                if dep is None:
                    missing_dependencies += 1
                    dependency_issues.append(
                        {
                            "kind": "missing_dependency",
                            "task_id": task_id,
                            "task_title": task_title,
                            "dependency": dep_id,
                        }
                    )
                    continue
                dep_status = str(dep.get("status") or "").strip().lower()
                if dep_status != "done":
                    blocking.append({"dependency": dep_id, "status": dep_status})

            if blocking:
                blocked_open += 1
                dependency_issues.append(
                    {
                        "kind": "blocked_dependency",
                        "task_id": task_id,
                        "task_title": task_title,
                        "blocking": blocking[:4],
                    }
                )

            if age is not None and age >= _STALE_OPEN_DAYS:
                stale_open.append(
                    {
                        "id": task_id,
                        "title": task_title,
                        "status": status,
                        "age_days": age,
                        "created_at": created_at,
                    }
                )

    stale_open.sort(key=lambda row: (-float(row.get("age_days") or 0.0), str(row.get("id") or "")))
    stale_in_progress.sort(key=lambda row: (-float(row.get("age_days") or 0.0), str(row.get("id") or "")))

    snap.task_counts = counts
    snap.in_progress = in_progress
    snap.ready = _compute_ready_tasks(wg.tasks)[:max_next]
    snap.blocked_open = blocked_open
    snap.missing_dependencies = missing_dependencies
    snap.stale_open = stale_open[:20]
    snap.stale_in_progress = stale_in_progress[:20]
    snap.dependency_issues = dependency_issues[:30]
    task_graph_nodes, task_graph_edges = _build_repo_task_graph(wg.tasks)
    snap.task_graph_nodes = task_graph_nodes
    snap.task_graph_edges = task_graph_edges
    snap.cross_repo_dependencies = _collect_cross_repo_dependencies(
        repo_name=repo_name,
        tasks=wg.tasks,
        known_repo_names=known_repo_names or set(),
        policy_order=policy_order,
    )
    snap.narrative = _build_repo_narrative(snap)
    return snap


def rank_next_work(repos: list[RepoSnapshot], *, limit: int = 20) -> list[dict[str, Any]]:
    items: list[NextWorkItem] = []
    for repo in repos:
        items.extend(repo.top_next_work(limit=3))
    items.sort(key=lambda i: (-i.priority, i.repo, i.task_id))
    return [asdict(x) for x in items[:limit]]


def build_repo_dependency_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    if not repos:
        return {
            "nodes": [],
            "edges": [],
            "summary": {
                "repo_count": 0,
                "edge_count": 0,
                "linked_repos": 0,
                "isolated_repos": 0,
                "top_outbound": [],
                "top_inbound": [],
            },
        }

    node_index: dict[str, dict[str, Any]] = {}
    for repo in repos:
        attention = _repo_attention_entry(repo)
        attention_score = int(attention.get("score") or 0) if isinstance(attention, dict) else 0
        node_index[repo.name] = {
            "id": repo.name,
            "source": repo.source,
            "workgraph_exists": repo.workgraph_exists,
            "service_running": repo.service_running,
            "risk_score": attention_score,
            "outbound": 0,
            "inbound": 0,
            "outbound_weight": 0,
            "inbound_weight": 0,
        }

    edge_index: dict[tuple[str, str], dict[str, Any]] = {}
    known_repos = set(node_index.keys())
    for repo in repos:
        for dep in repo.cross_repo_dependencies:
            if not isinstance(dep, dict):
                continue
            target = str(dep.get("repo") or "").strip()
            if not target or target not in known_repos or target == repo.name:
                continue
            weight = max(1, int(dep.get("score") or 0))
            reasons_raw = dep.get("reasons")
            reasons = [str(item) for item in reasons_raw if str(item).strip()] if isinstance(reasons_raw, list) else []
            key = (repo.name, target)
            edge = edge_index.get(key)
            if edge is None:
                edge = {"source": repo.name, "target": target, "weight": 0, "reasons": []}
                edge_index[key] = edge
            edge["weight"] = min(24, int(edge.get("weight") or 0) + weight)
            existing_reasons = set(str(item) for item in edge.get("reasons") or [])
            merged = [item for item in (edge.get("reasons") or []) if str(item).strip()]
            for reason in reasons:
                if reason not in existing_reasons:
                    merged.append(reason)
                    existing_reasons.add(reason)
            edge["reasons"] = merged[:6]

    for edge in edge_index.values():
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        weight = int(edge.get("weight") or 0)
        if source in node_index:
            node_index[source]["outbound"] = int(node_index[source]["outbound"]) + 1
            node_index[source]["outbound_weight"] = int(node_index[source]["outbound_weight"]) + weight
        if target in node_index:
            node_index[target]["inbound"] = int(node_index[target]["inbound"]) + 1
            node_index[target]["inbound_weight"] = int(node_index[target]["inbound_weight"]) + weight

    nodes = [node_index[name] for name in sorted(node_index.keys())]
    edges = sorted(
        edge_index.values(),
        key=lambda row: (
            -int(row.get("weight") or 0),
            str(row.get("source") or ""),
            str(row.get("target") or ""),
        ),
    )

    linked = [row for row in nodes if int(row.get("outbound") or 0) > 0 or int(row.get("inbound") or 0) > 0]
    isolated = [row for row in nodes if row not in linked]
    top_outbound = sorted(
        nodes,
        key=lambda row: (
            -int(row.get("outbound_weight") or 0),
            -int(row.get("outbound") or 0),
            str(row.get("id") or ""),
        ),
    )[:3]
    top_inbound = sorted(
        nodes,
        key=lambda row: (
            -int(row.get("inbound_weight") or 0),
            -int(row.get("inbound") or 0),
            str(row.get("id") or ""),
        ),
    )[:3]

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "repo_count": len(nodes),
            "edge_count": len(edges),
            "linked_repos": len(linked),
            "isolated_repos": len(isolated),
            "top_outbound": [
                {
                    "repo": str(row.get("id") or ""),
                    "weight": int(row.get("outbound_weight") or 0),
                    "count": int(row.get("outbound") or 0),
                }
                for row in top_outbound
            ],
            "top_inbound": [
                {
                    "repo": str(row.get("id") or ""),
                    "weight": int(row.get("inbound_weight") or 0),
                    "count": int(row.get("inbound") or 0),
                }
                for row in top_inbound
            ],
        },
    }


def _repo_attention_entry(repo: RepoSnapshot) -> dict[str, Any] | None:
    reasons: list[str] = []
    score = 0
    if repo.errors:
        score += 18
        reasons.append(f"errors: {', '.join(repo.errors[:2])}")
    if repo.workgraph_exists and not repo.service_running:
        score += 10
        reasons.append("workgraph service stopped")
    if repo.missing_dependencies > 0:
        score += min(20, repo.missing_dependencies * 4)
        reasons.append(f"missing dependencies: {repo.missing_dependencies}")
    if repo.blocked_open > 0:
        score += min(16, repo.blocked_open * 2)
        reasons.append(f"blocked open tasks: {repo.blocked_open}")
    if repo.stale_in_progress:
        score += min(18, len(repo.stale_in_progress) * 3)
        reasons.append(f"aging in-progress: {len(repo.stale_in_progress)}")
    if repo.stale_open:
        score += min(14, len(repo.stale_open) * 2)
        reasons.append(f"aging open: {len(repo.stale_open)}")
    if repo.behind > 0:
        score += min(8, repo.behind)
        reasons.append(f"behind upstream: {repo.behind}")
    if repo.git_dirty:
        score += 2
        reasons.append("dirty working tree")
    if score <= 0:
        return None
    return {
        "repo": repo.name,
        "score": score,
        "reasons": reasons[:4],
        "narrative": repo.narrative,
    }


def build_ecosystem_overview(
    repos: list[RepoSnapshot],
    *,
    upstream_candidates: int,
    updates: dict[str, Any],
    central_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    total_open = 0
    total_ready = 0
    total_in_progress = 0
    total_done = 0
    stale_open = 0
    stale_in_progress = 0
    blocked_open = 0
    missing_dependencies = 0
    repos_with_errors = 0
    repos_with_inactive_service = 0
    repos_dirty = 0
    total_ahead = 0
    total_behind = 0
    attention: list[dict[str, Any]] = []

    for repo in repos:
        total_open += int(repo.task_counts.get("open", 0))
        total_ready += int(repo.task_counts.get("ready", 0))
        total_in_progress += int(repo.task_counts.get("in-progress", 0))
        total_done += int(repo.task_counts.get("done", 0))
        stale_open += len(repo.stale_open)
        stale_in_progress += len(repo.stale_in_progress)
        blocked_open += repo.blocked_open
        missing_dependencies += repo.missing_dependencies
        if repo.errors:
            repos_with_errors += 1
        if repo.workgraph_exists and not repo.service_running:
            repos_with_inactive_service += 1
        if repo.git_dirty:
            repos_dirty += 1
        total_ahead += repo.ahead
        total_behind += repo.behind
        entry = _repo_attention_entry(repo)
        if entry:
            attention.append(entry)

    attention.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("repo") or "")))
    return {
        "repos_total": len(repos),
        "repos_with_errors": repos_with_errors,
        "repos_with_inactive_service": repos_with_inactive_service,
        "repos_dirty": repos_dirty,
        "tasks_open": total_open,
        "tasks_ready": total_ready,
        "tasks_in_progress": total_in_progress,
        "tasks_done": total_done,
        "stale_open": stale_open,
        "stale_in_progress": stale_in_progress,
        "blocked_open": blocked_open,
        "missing_dependencies": missing_dependencies,
        "upstream_candidates": upstream_candidates,
        "central_reports": len(central_reports),
        "total_ahead": total_ahead,
        "total_behind": total_behind,
        "update_has_updates": bool(updates.get("has_updates")),
        "update_has_discoveries": bool(updates.get("has_discoveries")),
        "attention_repos": attention[:12],
    }


def build_ecosystem_narrative(overview: dict[str, Any]) -> str:
    repos_total = int(overview.get("repos_total") or 0)
    if repos_total <= 0:
        return "No repositories are currently visible to the ecosystem hub."

    blockers = int(overview.get("blocked_open") or 0) + int(overview.get("missing_dependencies") or 0)
    stale = int(overview.get("stale_open") or 0) + int(overview.get("stale_in_progress") or 0)
    service_gaps = int(overview.get("repos_with_inactive_service") or 0)
    error_repos = int(overview.get("repos_with_errors") or 0)
    active = int(overview.get("tasks_in_progress") or 0)
    ready = int(overview.get("tasks_ready") or 0)

    if error_repos > 0 or service_gaps > 0 or int(overview.get("missing_dependencies") or 0) > 0:
        tone = "Alert posture"
    elif stale > 0 or blockers > 0:
        tone = "Watch posture"
    else:
        tone = "Stable posture"

    headline = (
        f"{tone}: tracking {repos_total} repos with {active} active tasks and {ready} ready tasks."
    )
    pressure = (
        f"Pressure points: {blockers} dependency blockers, {stale} aging tasks, "
        f"{service_gaps} repos without a running workgraph service."
    )
    attention = overview.get("attention_repos") or []
    if isinstance(attention, list) and attention:
        top = attention[0] if isinstance(attention[0], dict) else {}
        repo = str(top.get("repo") or "unknown")
        reasons = top.get("reasons") or []
        reason_line = ", ".join(str(x) for x in reasons[:2]) if isinstance(reasons, list) else "high risk signals"
        focus = f"Top follow-up repo: {repo} ({reason_line})."
    else:
        focus = "No concentrated risk repo detected right now."
    return " ".join((headline, pressure, focus))


def _default_update_checker(*, project_dir: Path, repo_map: dict[str, Path]) -> dict[str, Any]:
    wg_dir = project_dir / ".workgraph"
    policy_interval = 1800
    cfg = load_review_config(wg_dir)
    extra_repos = cfg.get("extra_repos") if isinstance(cfg.get("extra_repos"), dict) else {}
    repos_remote: dict[str, str] = {}
    repos_field = cfg.get("repos")
    if isinstance(repos_field, dict):
        for k, v in repos_field.items():
            ks = str(k).strip()
            vs = str(v).strip()
            if ks and vs:
                repos_remote[ks] = vs
    for k, v in extra_repos.items():
        ks = str(k).strip()
        vs = str(v).strip()
        if ks and vs:
            repos_remote[ks] = vs

    result = check_ecosystem_updates(
        wg_dir=wg_dir,
        interval_seconds=policy_interval,
        force=False,
        repos=repos_remote if repos_remote else None,
        users=list(cfg.get("github_users") or []),
        reports=list(cfg.get("reports") or []),
        report_keywords=list(cfg.get("report_keywords") or []),
        user_repo_limit=int(cfg.get("user_repo_limit") or 10),
    )
    return {
        "has_updates": bool(result.get("has_updates")),
        "has_discoveries": bool(result.get("has_discoveries")),
        "summary": summarize_updates(result),
        "raw": result,
    }


def collect_ecosystem_snapshot(
    *,
    project_dir: Path,
    workspace_root: Path,
    ecosystem_toml: Path | None = None,
    max_next: int = 5,
    include_updates: bool = True,
    central_repo: Path | None = None,
    update_checker: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ecosystem_file = ecosystem_toml or (workspace_root / "speedrift-ecosystem" / "ecosystem.toml")
    repo_map = _load_ecosystem_repos(ecosystem_file, workspace_root)
    repo_sources: dict[str, str] = {name: "ecosystem-toml" for name in repo_map}
    if project_dir.name not in repo_map:
        repo_map[project_dir.name] = project_dir
        repo_sources[project_dir.name] = "project-dir"

    discovered = _discover_active_workspace_repos(workspace_root, existing=set(repo_map.keys()))
    for name, path in discovered.items():
        repo_map[name] = path
        repo_sources[name] = "autodiscovered"

    known_repo_names = {str(name).strip() for name in repo_map.keys() if str(name).strip()}
    repos: list[RepoSnapshot] = []
    upstream: list[UpstreamCandidate] = []
    for name, path in sorted(repo_map.items()):
        repo_snap = collect_repo_snapshot(name, path, max_next=max_next, known_repo_names=known_repo_names)
        repo_snap.source = repo_sources.get(name, "ecosystem-toml")
        repos.append(repo_snap)
        upstream.extend(generate_upstream_candidates(name, path))

    updates: dict[str, Any] = {"has_updates": False, "has_discoveries": False, "summary": ""}
    if include_updates:
        checker = update_checker or _default_update_checker
        try:
            updates = checker(project_dir=project_dir, repo_map=repo_map)
        except Exception as exc:
            updates = {
                "has_updates": False,
                "has_discoveries": False,
                "summary": f"Update check failed: {exc}",
                "raw": {},
            }

    central_reports = _collect_central_reports_summary(central_repo) if central_repo else []
    overview = build_ecosystem_overview(
        repos,
        upstream_candidates=len(upstream),
        updates=updates,
        central_reports=central_reports,
    )
    repo_dependency_overview = build_repo_dependency_overview(repos)
    narrative = build_ecosystem_narrative(overview)

    snapshot = {
        "schema": 1,
        "generated_at": _iso_now(),
        "project_dir": str(project_dir),
        "workspace_root": str(workspace_root),
        "repo_count": len(repos),
        "repos": [asdict(r) for r in repos],
        "next_work": rank_next_work(repos, limit=max_next * max(1, len(repos))),
        "updates": updates,
        "upstream_candidates": [asdict(c) for c in upstream],
        "central_reports": central_reports,
        "repo_sources": repo_sources,
        "overview": overview,
        "repo_dependency_overview": repo_dependency_overview,
        "narrative": narrative,
    }
    return snapshot


def _service_dir(project_dir: Path) -> Path:
    return project_dir / ".workgraph" / "service" / "ecosystem-hub"


def service_paths(project_dir: Path) -> dict[str, Path]:
    base = _service_dir(project_dir)
    return {
        "dir": base,
        "pid": base / "pid",
        "state": base / "state.json",
        "heartbeat": base / "heartbeat.json",
        "snapshot": base / "snapshot.json",
        "log": base / "hub.log",
    }


def write_snapshot_once(
    *,
    project_dir: Path,
    workspace_root: Path,
    ecosystem_toml: Path | None,
    include_updates: bool,
    max_next: int,
    central_repo: Path | None = None,
) -> dict[str, Any]:
    paths = service_paths(project_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    snapshot = collect_ecosystem_snapshot(
        project_dir=project_dir,
        workspace_root=workspace_root,
        ecosystem_toml=ecosystem_toml,
        include_updates=include_updates,
        max_next=max_next,
        central_repo=central_repo,
    )
    _write_json(paths["snapshot"], snapshot)
    _write_json(paths["heartbeat"], {"last_tick_at": _iso_now()})
    if central_repo is not None:
        meta = write_central_register(
            central_repo=central_repo,
            project_name=project_dir.name,
            snapshot=snapshot,
        )
        _write_json(paths["dir"] / "central-register.json", meta)
    return snapshot


def supervise_repo_services(
    *,
    repos_payload: list[dict[str, Any]],
    cooldown_seconds: int,
    max_starts: int,
) -> dict[str, Any]:
    now = time.time()
    attempted = 0
    started = 0
    failed = 0
    cooldown_skipped = 0
    checked = 0
    candidates = 0
    attempt_rows: list[dict[str, Any]] = []

    for row in repos_payload:
        if attempted >= max(1, max_starts):
            break
        if not isinstance(row, dict):
            continue
        checked += 1
        repo_name = str(row.get("name") or "")
        repo_path_raw = str(row.get("path") or "")
        if not repo_name or not repo_path_raw:
            continue
        if not bool(row.get("exists")):
            continue
        if not bool(row.get("workgraph_exists")):
            continue
        if bool(row.get("service_running")):
            continue

        in_progress = row.get("in_progress") if isinstance(row.get("in_progress"), list) else []
        ready = row.get("ready") if isinstance(row.get("ready"), list) else []
        if not in_progress and not ready:
            continue
        candidates += 1

        repo_path = Path(repo_path_raw).expanduser()
        key = str(repo_path.resolve())
        last_attempt = _SUPERVISOR_LAST_ATTEMPT.get(key, 0.0)
        if now - last_attempt < max(1, cooldown_seconds):
            cooldown_skipped += 1
            continue

        _SUPERVISOR_LAST_ATTEMPT[key] = now
        attempted += 1
        rc, out, err = _run(
            ["wg", "--dir", str(repo_path / ".workgraph"), "service", "start"],
            cwd=repo_path,
            timeout=15.0,
        )
        text = f"{out}\n{err}".strip().lower()
        ok = rc == 0 or "already running" in text
        if ok:
            started += 1
        else:
            failed += 1
        attempt_rows.append(
            {
                "repo": repo_name,
                "path": str(repo_path),
                "ok": ok,
                "exit_code": rc,
                "stdout": out,
                "stderr": err,
            }
        )

    return {
        "enabled": True,
        "cooldown_seconds": max(1, cooldown_seconds),
        "max_starts_per_cycle": max(1, max_starts),
        "checked_repos": checked,
        "restart_candidates": candidates,
        "attempted": attempted,
        "started": started,
        "failed": failed,
        "cooldown_skipped": cooldown_skipped,
        "last_tick_at": _iso_now(),
        "attempts": attempt_rows[:20],
    }


def read_service_status(project_dir: Path) -> dict[str, Any]:
    paths = service_paths(project_dir)
    pid = 0
    if paths["pid"].exists():
        raw = paths["pid"].read_text(encoding="utf-8").strip()
        try:
            pid = int(raw)
        except ValueError:
            pid = 0
    running = _process_alive(pid)
    heartbeat = _read_json(paths["heartbeat"]) if paths["heartbeat"].exists() else {}
    supervisor = heartbeat.get("supervisor") if isinstance(heartbeat.get("supervisor"), dict) else {}
    state = _read_json(paths["state"]) if paths["state"].exists() else {}
    host = str(state.get("host") or "")
    port = int(state.get("port") or 0)
    if not running and _service_port_alive(host, port):
        running = True
    snapshot_exists = paths["snapshot"].exists()
    central = _read_json(paths["dir"] / "central-register.json")
    upstream_actions = _read_json(paths["dir"] / "upstream-actions.json")
    return {
        "running": running,
        "pid": pid if running else None,
        "service_dir": str(paths["dir"]),
        "last_tick_at": str(heartbeat.get("last_tick_at") or ""),
        "last_error": str(heartbeat.get("error") or ""),
        "supervisor": supervisor,
        "started_at": str(state.get("started_at") or ""),
        "host": host,
        "port": port,
        "central_repo": str(state.get("central_repo") or ""),
        "snapshot_path": str(paths["snapshot"]),
        "snapshot_exists": snapshot_exists,
        "websocket_path": "/ws/status",
        "central_register_latest": str(central.get("latest_path") or ""),
        "upstream_action_count": int(upstream_actions.get("request_count") or 0),
        "upstream_execute_mode": bool(upstream_actions.get("execute_draft_prs", False)),
        "log_path": str(paths["log"]),
    }


class LiveStreamHub:
    def __init__(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event
        self._lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._latest_payload = ""

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def set_latest(self, snapshot: dict[str, Any]) -> str:
        payload = json.dumps(snapshot, sort_keys=False)
        with self._lock:
            self._latest_payload = payload
        return payload

    def latest_payload(self) -> str:
        with self._lock:
            return self._latest_payload

    def register(self, client: socket.socket) -> None:
        with self._lock:
            self._clients.add(client)

    def unregister(self, client: socket.socket) -> None:
        with self._lock:
            self._clients.discard(client)
        try:
            client.close()
        except OSError:
            pass

    def send_payload(self, client: socket.socket, payload: str) -> bool:
        frame = _encode_ws_frame(payload.encode("utf-8"), opcode=0x1)
        try:
            client.sendall(frame)
        except OSError:
            return False
        return True

    def broadcast_snapshot(self, snapshot: dict[str, Any]) -> None:
        payload = self.set_latest(snapshot)
        frame = _encode_ws_frame(payload.encode("utf-8"), opcode=0x1)
        stale: list[socket.socket] = []
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(frame)
            except OSError:
                stale.append(client)
        for client in stale:
            self.unregister(client)


class _HubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    snapshot_path: Path
    state_path: Path
    live_hub: LiveStreamHub | None = None

    def _read_snapshot(self) -> dict[str, Any]:
        if not self.snapshot_path.exists():
            return {
                "schema": 1,
                "generated_at": "",
                "repos": [],
                "next_work": [],
                "updates": {"summary": "No snapshot yet"},
                "upstream_candidates": [],
                "central_reports": [],
                "repo_sources": {},
                "overview": {},
                "repo_dependency_overview": {"nodes": [], "edges": [], "summary": {}},
                "supervisor": {},
                "narrative": "",
            }
        data = _read_json(self.snapshot_path)
        return data if data else {"repos": []}

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        blob = json.dumps(payload, indent=2, sort_keys=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _send_html(self, body: str, status: int = HTTPStatus.OK) -> None:
        blob = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _serve_websocket(self) -> None:
        hub = self.live_hub
        if hub is None:
            self._send_json({"error": "ws_not_configured"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        upgrade = str(self.headers.get("Upgrade") or "").lower()
        conn = str(self.headers.get("Connection") or "").lower()
        client_key = str(self.headers.get("Sec-WebSocket-Key") or "").strip()
        if upgrade != "websocket" or "upgrade" not in conn or not client_key:
            self._send_json({"error": "invalid_websocket_upgrade"}, status=HTTPStatus.BAD_REQUEST)
            return

        self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", _ws_accept_key(client_key))
        self.end_headers()

        client = self.connection
        client.settimeout(1.0)
        self.close_connection = False
        hub.register(client)

        initial = hub.latest_payload()
        if not initial:
            initial = json.dumps(self._read_snapshot(), sort_keys=False)
        if not hub.send_payload(client, initial):
            hub.unregister(client)
            return

        try:
            while not hub.stop_event.is_set():
                try:
                    opcode, payload = _read_ws_frame(client)
                except socket.timeout:
                    continue
                except (ConnectionError, OSError, struct.error, ValueError):
                    break

                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping
                    try:
                        client.sendall(_encode_ws_frame(payload, opcode=0xA))
                    except OSError:
                        break
        finally:
            hub.unregister(client)

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._send_html(render_dashboard_html())
            return
        if route in ("/ws", "/ws/status"):
            self._serve_websocket()
            return
        snapshot = self._read_snapshot()
        if route == "/api/status":
            self._send_json(snapshot)
            return
        if route == "/api/repos":
            self._send_json(snapshot.get("repos") or [])
            return
        if route == "/api/next-work":
            self._send_json(snapshot.get("next_work") or [])
            return
        if route == "/api/updates":
            self._send_json(snapshot.get("updates") or {})
            return
        if route == "/api/upstream":
            self._send_json(snapshot.get("upstream_candidates") or [])
            return
        if route == "/api/overview":
            self._send_json(
                {
                    "overview": snapshot.get("overview") or {},
                    "narrative": snapshot.get("narrative") or "",
                }
            )
            return
        if route == "/api/graph":
            repos = snapshot.get("repos") or []
            payload: list[dict[str, Any]] = []
            if isinstance(repos, list):
                for row in repos:
                    if not isinstance(row, dict):
                        continue
                    payload.append(
                        {
                            "repo": str(row.get("name") or ""),
                            "nodes": row.get("task_graph_nodes") or [],
                            "edges": row.get("task_graph_edges") or [],
                        }
                    )
            self._send_json(payload)
            return
        if route == "/api/repo-dependencies":
            self._send_json(snapshot.get("repo_dependency_overview") or {"nodes": [], "edges": [], "summary": {}})
            return
        self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep daemon logs clean and structured in our own files.
        return


def _handler_factory(snapshot_path: Path, state_path: Path, live_hub: LiveStreamHub) -> type[_HubHandler]:
    class Handler(_HubHandler):
        pass

    Handler.snapshot_path = snapshot_path
    Handler.state_path = state_path
    Handler.live_hub = live_hub
    return Handler


def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Speedrift Ecosystem Hub</title>
  <style>
    :root {
      --bg: #f5f2ea;
      --panel: #fffcf5;
      --ink: #1d2421;
      --muted: #5f6f66;
      --line: #d7cfbf;
      --accent: #0f6f7c;
      --accent-soft: #d8eef2;
      --warn: #934e1c;
      --bad: #9c2525;
      --good: #2f6e39;
      --mono: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
      --sans: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
    }
    body {
      margin: 0;
      font-family: var(--sans);
      background:
        radial-gradient(circle at 80% -10%, #e8efe9 0%, transparent 46%),
        radial-gradient(circle at 15% 0%, #f0e7d5 0%, transparent 50%),
        var(--bg);
      color: var(--ink);
    }
    header {
      padding: 1rem 1.2rem;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 252, 245, 0.8);
      backdrop-filter: blur(6px);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 {
      margin: 0;
      font-size: 1.06rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .meta {
      margin-top: 0.35rem;
      color: var(--muted);
      font-size: 0.86rem;
    }
    main {
      padding: 1rem 1.2rem 2rem;
      display: grid;
      gap: 0.95rem;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 0.9rem;
      box-shadow: 0 6px 12px rgba(24, 34, 28, 0.06);
    }
    .span-all {
      grid-column: 1 / -1;
    }
    h2 {
      margin: 0 0 0.65rem;
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #30443b;
    }
    .narrative {
      line-height: 1.45;
      font-size: 0.95rem;
      margin: 0;
      color: #1f2f28;
    }
    .cards {
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.55rem 0.6rem;
      background: linear-gradient(180deg, rgba(255,255,255,0.7), rgba(246,241,232,0.7));
    }
    .card .k {
      color: var(--muted);
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .card .v {
      margin-top: 0.2rem;
      font-size: 1.05rem;
      font-weight: 650;
      font-family: var(--mono);
    }
    .attention-list, ul {
      margin: 0;
      padding-left: 1rem;
    }
    li {
      margin: 0.23rem 0;
      font-size: 0.9rem;
    }
    .repo-grid {
      display: grid;
      gap: 0.7rem;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }
    .repo-toolbar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-bottom: 0.55rem;
    }
    #repo-summary {
      margin: 0 0 0.5rem;
      color: #31433a;
      font-size: 0.86rem;
    }
    .repo-card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.65rem;
      background: rgba(255,255,255,0.6);
      cursor: pointer;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }
    .repo-card:hover {
      border-color: #9cb7bc;
      box-shadow: 0 3px 8px rgba(30, 58, 64, 0.12);
    }
    .repo-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.6rem;
      font-size: 0.92rem;
      margin-bottom: 0.35rem;
    }
    .repo-name { font-weight: 700; }
    .pill {
      font-family: var(--mono);
      font-size: 0.72rem;
      border-radius: 999px;
      padding: 0.1rem 0.48rem;
      border: 1px solid var(--line);
      background: #f8f4eb;
      color: #47544d;
    }
    .pill.bad { color: var(--bad); border-color: #d9bcbc; background: #fbeeee; }
    .pill.warn { color: var(--warn); border-color: #e0c8b5; background: #fff3e8; }
    .pill.good { color: var(--good); border-color: #bfd8c4; background: #e8f5ea; }
    .repo-meta {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.24rem 0.4rem;
      font-size: 0.8rem;
      color: #324139;
      margin-bottom: 0.4rem;
    }
    .repo-note {
      margin: 0;
      font-size: 0.84rem;
      color: #2d3a33;
      line-height: 1.35;
    }
    .repo-note.warn { color: var(--warn); }
    .repo-actions { margin-top: 0.45rem; }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .good { color: var(--good); }
    code {
      font-family: var(--mono);
      font-size: 0.82rem;
    }
    .graph-toolbar {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-bottom: 0.55rem;
    }
    .graph-toolbar button {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.2rem 0.48rem;
      background: #fff;
      cursor: pointer;
    }
    .graph-toolbar button:hover {
      background: #f5efe2;
    }
    select {
      font: inherit;
      padding: 0.25rem 0.38rem;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
    }
    .graph-wrap {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fffdfa;
      overflow: auto;
      height: 560px;
    }
    #graph {
      display: block;
      width: 100%;
      height: 100%;
      touch-action: none;
      cursor: grab;
    }
    #graph.dragging {
      cursor: grabbing;
    }
    .graph-legend {
      display: flex;
      gap: 0.6rem;
      flex-wrap: wrap;
      margin-top: 0.5rem;
      font-size: 0.76rem;
      color: var(--muted);
    }
    .dot {
      display: inline-block;
      width: 0.65rem;
      height: 0.65rem;
      border-radius: 999px;
      margin-right: 0.28rem;
      vertical-align: baseline;
    }
    .graph-path {
      margin-top: 0.55rem;
      padding: 0.45rem 0.55rem;
      border: 1px solid var(--line);
      border-radius: 9px;
      background: #fcf8ef;
      color: #2b3c34;
      font-size: 0.82rem;
      line-height: 1.35;
      min-height: 2.4rem;
      white-space: pre-wrap;
    }
    .repo-dep-wrap {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fffdfa;
      padding: 0.45rem;
      margin-bottom: 0.55rem;
    }
    .repo-dep-meta {
      color: #4a5b53;
      font-size: 0.8rem;
      margin-bottom: 0.35rem;
    }
    #repo-dep-graph {
      width: 100%;
      height: 280px;
      border: 1px solid #e2dacb;
      border-radius: 8px;
      background: #fffcf8;
      display: block;
      overflow: visible;
    }
    #repo-dep-note {
      margin-top: 0.35rem;
      line-height: 1.28;
    }
    .action-toolbar {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      flex-wrap: wrap;
      margin-bottom: 0.55rem;
    }
    #action-summary {
      margin: 0 0 0.6rem;
      color: #31433a;
      font-size: 0.86rem;
    }
    .action-grid {
      display: grid;
      gap: 0.7rem;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
    }
    .action-panel {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.6rem;
      background: rgba(255,255,255,0.7);
      min-height: 220px;
    }
    .action-head {
      margin: 0 0 0.45rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.45rem;
    }
    .action-panel h3 {
      margin: 0;
      font-size: 0.79rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #385148;
    }
    .action-count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.5rem;
      padding: 0.02rem 0.3rem;
      border-radius: 999px;
      border: 1px solid #c8beae;
      background: #f4eee2;
      font-family: var(--mono);
      font-size: 0.72rem;
      color: #495b52;
    }
    .action-list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 0.35rem;
    }
    .action-item {
      border: 1px solid #d9d0c1;
      border-left-width: 4px;
      border-radius: 8px;
      padding: 0.4rem 0.5rem;
      background: #fffdf8;
      font-size: 0.82rem;
      line-height: 1.3;
    }
    .action-item.sev-high { border-left-color: #9c2525; }
    .action-item.sev-med { border-left-color: #a26c13; }
    .action-item.sev-low { border-left-color: #2f6e39; }
    .action-title {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.4rem;
      color: #25352f;
      margin-bottom: 0.15rem;
    }
    .action-why {
      color: #52645b;
      font-size: 0.77rem;
      margin-bottom: 0.15rem;
    }
    .action-prompt {
      margin-top: 0.2rem;
      color: #3f5148;
      font-size: 0.76rem;
      line-height: 1.28;
      background: #f7f2e8;
      border: 1px solid #dfd5c4;
      border-radius: 7px;
      padding: 0.28rem 0.32rem;
      word-break: break-word;
    }
    .action-empty {
      color: #5f6f66;
      border-style: dashed;
      border-left-width: 1px;
    }
    .action-link {
      font: inherit;
      font-size: 0.76rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 0.12rem 0.35rem;
      margin-left: 0.35rem;
      cursor: pointer;
    }
    .action-link:hover {
      background: #eef7f8;
      border-color: #9fbec5;
    }
    .cmd {
      margin-top: 0.18rem;
      color: #60726a;
      font-size: 0.76rem;
      line-height: 1.25;
    }
    .graph-all-grid {
      padding: 0.5rem;
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }
    .graph-mini {
      border: 1px solid var(--line);
      border-radius: 9px;
      background: #fff;
      padding: 0.35rem;
    }
    .graph-mini h4 {
      margin: 0 0 0.25rem;
      font-size: 0.78rem;
      color: #344a42;
      display: flex;
      justify-content: space-between;
      gap: 0.4rem;
      align-items: center;
    }
    .graph-mini svg {
      width: 100%;
      height: 150px;
      border: 1px solid #e4ddcf;
      border-radius: 7px;
      background: #fffcf8;
      display: block;
    }
    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      .repo-meta { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Speedrift Ecosystem Hub</h1>
    <div class="meta" id="meta">Loading ecosystem state…</div>
  </header>
  <main>
    <section class="span-all">
      <h2>Narrated Overview</h2>
      <p class="narrative" id="narrative">Waiting for first snapshot.</p>
    </section>

    <section class="span-all">
      <h2>Operational Overview</h2>
      <div class="cards" id="overview-cards"></div>
    </section>

    <section class="span-all">
      <h2>By Repo</h2>
      <p class="narrative" id="repo-summary">Loading repos…</p>
      <div class="repo-toolbar">
        <label for="repo-sort">Sort:</label>
        <select id="repo-sort">
          <option value="priority" selected>priority</option>
          <option value="dirty">dirty first</option>
          <option value="blocked">blocked first</option>
          <option value="behind">behind first</option>
          <option value="name">name</option>
        </select>
        <label for="repo-health-filter">Health:</label>
        <select id="repo-health-filter">
          <option value="all" selected>all</option>
          <option value="risk">risk</option>
          <option value="watch">watch</option>
          <option value="healthy">healthy</option>
        </select>
        <label for="repo-dirty-filter">Dirty:</label>
        <select id="repo-dirty-filter">
          <option value="all" selected>all</option>
          <option value="dirty">dirty only</option>
          <option value="clean">clean only</option>
        </select>
        <label for="repo-service-filter">Service:</label>
        <select id="repo-service-filter">
          <option value="all" selected>all</option>
          <option value="stopped">stopped</option>
          <option value="running">running</option>
        </select>
      </div>
      <div class="repo-grid" id="repo-grid"></div>
    </section>

    <section class="span-all" id="graph-section">
      <h2>Dependency Graph</h2>
      <div class="graph-toolbar">
        <label for="graph-repo">Repo:</label>
        <select id="graph-repo"></select>
        <label for="graph-mode">Mode:</label>
        <select id="graph-mode">
          <option value="focus" selected>focus chain</option>
          <option value="active">active + blocked</option>
          <option value="full">full graph</option>
        </select>
        <button id="graph-zoom-out" type="button">-</button>
        <button id="graph-zoom-in" type="button">+</button>
        <button id="graph-zoom-reset" type="button">reset</button>
        <span class="meta" id="graph-meta"></span>
      </div>
      <div class="repo-dep-wrap">
        <div class="repo-dep-meta" id="repo-dep-meta">Loading repo dependency overview…</div>
        <svg id="repo-dep-graph" viewBox="0 0 1200 280" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="cmd" id="repo-dep-note">Edge A -> B means repo A has dependency signals pointing to repo B. Click a repo to focus its task graph.</div>
      </div>
      <div class="graph-wrap">
        <svg id="graph" viewBox="0 0 1200 340" preserveAspectRatio="xMidYMin meet"></svg>
        <div id="graph-all" class="graph-all-grid" style="display:none;"></div>
      </div>
      <div class="graph-legend">
        <span><span class="dot" style="background:#2f6e39"></span>Done</span>
        <span><span class="dot" style="background:#0f6f7c"></span>In progress</span>
        <span><span class="dot" style="background:#a26c13"></span>Open/Ready</span>
        <span><span class="dot" style="background:#9c2525"></span>Blocked</span>
        <span><span class="dot" style="background:#8c2f2f"></span>Cycle edge</span>
      </div>
      <div class="graph-path" id="graph-path">Select a node to inspect dependency chain.</div>
    </section>

    <section class="span-all">
      <h2>Action Center</h2>
      <p class="narrative" id="action-summary">Loading prioritized actions…</p>
      <div class="action-toolbar">
        <label for="action-repo-filter">Repo filter:</label>
        <select id="action-repo-filter">
          <option value="__all__">all repos</option>
        </select>
        <label for="action-sort">Sort:</label>
        <select id="action-sort">
          <option value="priority" selected>priority</option>
          <option value="dirtiness">dirty first</option>
          <option value="age">age</option>
          <option value="repo">repo</option>
        </select>
        <label for="action-priority-filter">Priority:</label>
        <select id="action-priority-filter">
          <option value="all" selected>all</option>
          <option value="high">high only</option>
          <option value="med">medium + high</option>
        </select>
        <label for="action-dirty-filter">Repo dirty:</label>
        <select id="action-dirty-filter">
          <option value="all" selected>all</option>
          <option value="dirty">dirty only</option>
          <option value="clean">clean only</option>
        </select>
      </div>
      <div class="action-grid">
        <article class="action-panel">
          <div class="action-head">
            <h3>Attention Queue</h3>
            <span class="action-count" id="attention-count">0</span>
          </div>
          <ul class="action-list" id="attention"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Aging, Gaps, Dependencies</h3>
            <span class="action-count" id="aging-count">0</span>
          </div>
          <ul class="action-list" id="aging"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Upstream Candidates</h3>
            <span class="action-count" id="upstream-count">0</span>
          </div>
          <ul class="action-list" id="upstream"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Planned Next Work</h3>
            <span class="action-count" id="next-count">0</span>
          </div>
          <ul class="action-list" id="next"></ul>
        </article>
      </div>
    </section>

    <section class="span-all">
      <h2>Updates</h2>
      <div id="updates"></div>
    </section>
  </main>
  <script>
    let ws = null;
    let pollTimer = null;
    let reconnectTimer = null;
    let selectedRepo = '';
    let graphMode = 'focus';
    let currentData = null;
    let selectedNodeId = '';
    let actionRepoFilter = '__all__';
    let actionSortMode = 'priority';
    let actionPriorityFilter = 'all';
    let actionDirtyFilter = 'all';
    let repoSortMode = 'priority';
    let repoHealthFilter = 'all';
    let repoDirtyFilter = 'all';
    let repoServiceFilter = 'all';
    let graphModel = { repo: '', nodes: [], edges: [], pos: {} };
    const graphView = {
      scale: 1,
      tx: 0,
      ty: 0,
      drag: false,
      dragStartX: 0,
      dragStartY: 0,
      dragBaseX: 0,
      dragBaseY: 0,
    };

    function el(id) { return document.getElementById(id); }
    function n(value) { return Number.isFinite(Number(value)) ? Number(value) : 0; }
    function esc(value) {
      return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
    }
    function escAttr(value) {
      return esc(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    }
    function repoByName(name) {
      return (currentData && currentData.repos || []).find((repo) => String(repo.name || '') === String(name || '')) || null;
    }
    function repoPath(name) {
      const repo = repoByName(name);
      return repo ? String(repo.path || '') : '';
    }
    function focusRepoInGraph(name, scrollToGraph = false) {
      const value = String(name || '');
      const select = el('graph-repo');
      const exists = Array.from(select.options).some((opt) => String(opt.value) === value);
      if (!exists) return;
      selectedRepo = value;
      selectedNodeId = '';
      select.value = value;
      if (currentData) drawGraph(currentData);
      if (scrollToGraph) {
        el('graph-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }

    function qualityPill(repo) {
      const score = n(repo.blocked_open) + n(repo.missing_dependencies) + n((repo.stale_open || []).length) + n((repo.stale_in_progress || []).length);
      if ((repo.errors || []).length || score >= 5) return ['risk', 'bad'];
      if (score >= 2 || (repo.workgraph_exists && !repo.service_running)) return ['watch', 'warn'];
      return ['healthy', 'good'];
    }

    function repoPriorityScore(repo) {
      const errorWeight = n((repo.errors || []).length) * 10;
      const missingWeight = n(repo.missing_dependencies) * 6;
      const blockedWeight = n(repo.blocked_open) * 4;
      const staleWeight = n((repo.stale_open || []).length) * 2 + n((repo.stale_in_progress || []).length) * 3;
      const serviceWeight = repo.workgraph_exists && !repo.service_running ? 8 : 0;
      const behindWeight = Math.min(10, n(repo.behind));
      const dirtyWeight = repo.git_dirty ? 2 : 0;
      return errorWeight + missingWeight + blockedWeight + staleWeight + serviceWeight + behindWeight + dirtyWeight;
    }

    function repoDirtyAllowed(repo) {
      if (repoDirtyFilter === 'dirty') return !!repo.git_dirty;
      if (repoDirtyFilter === 'clean') return !repo.git_dirty;
      return true;
    }

    function repoHealthAllowed(repo) {
      if (repoHealthFilter === 'all') return true;
      const [label] = qualityPill(repo);
      return String(label || '') === repoHealthFilter;
    }

    function repoServiceAllowed(repo) {
      if (repoServiceFilter === 'all') return true;
      const state = repo.service_running ? 'running' : (repo.workgraph_exists ? 'stopped' : 'none');
      if (repoServiceFilter === 'stopped') return state === 'stopped';
      if (repoServiceFilter === 'running') return state === 'running';
      return true;
    }

    function compareRepos(a, b) {
      if (repoSortMode === 'name') {
        return String(a.name || '').localeCompare(String(b.name || ''));
      }
      if (repoSortMode === 'dirty') {
        return Number(!!b.git_dirty) - Number(!!a.git_dirty) ||
          repoPriorityScore(b) - repoPriorityScore(a) ||
          String(a.name || '').localeCompare(String(b.name || ''));
      }
      if (repoSortMode === 'blocked') {
        return n(b.blocked_open) - n(a.blocked_open) ||
          n(b.missing_dependencies) - n(a.missing_dependencies) ||
          repoPriorityScore(b) - repoPriorityScore(a) ||
          String(a.name || '').localeCompare(String(b.name || ''));
      }
      if (repoSortMode === 'behind') {
        return n(b.behind) - n(a.behind) ||
          repoPriorityScore(b) - repoPriorityScore(a) ||
          String(a.name || '').localeCompare(String(b.name || ''));
      }
      return repoPriorityScore(b) - repoPriorityScore(a) || String(a.name || '').localeCompare(String(b.name || ''));
    }

    function refreshRepoSummary(total, shown) {
      const detail = `sort=${repoSortMode}, health=${repoHealthFilter}, dirty=${repoDirtyFilter}, service=${repoServiceFilter}`;
      el('repo-summary').textContent = `Showing ${shown} of ${total} repos (${detail}).`;
    }

    function refreshActionRepoFilter(data) {
      const select = el('action-repo-filter');
      if (!select) return;
      const repos = (data.repos || [])
        .map((repo) => String(repo.name || ''))
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b));
      const existing = new Set(Array.from(select.options).map((opt) => String(opt.value || '')));
      const expected = ['__all__', ...repos];
      const needsReset = expected.length !== existing.size || expected.some((name) => !existing.has(name));
      if (needsReset) {
        select.innerHTML = ['<option value="__all__">all repos</option>', ...repos.map((name) => `<option value="${escAttr(name)}">${esc(name)}</option>`)].join('');
      }
      if (actionRepoFilter !== '__all__' && !repos.includes(actionRepoFilter)) {
        actionRepoFilter = '__all__';
      }
      select.value = actionRepoFilter;
    }

    function actionRepoAllowed(repoName) {
      const value = String(repoName || '');
      return actionRepoFilter === '__all__' || value === actionRepoFilter;
    }

    function actionRepoDirtyAllowed(repoName) {
      const repo = repoByName(repoName) || {};
      if (actionDirtyFilter === 'dirty') return !!repo.git_dirty;
      if (actionDirtyFilter === 'clean') return !repo.git_dirty;
      return true;
    }

    function actionPriorityAllowed(severity) {
      const level = Number(severity || 0);
      if (actionPriorityFilter === 'high') return level >= 3;
      if (actionPriorityFilter === 'med') return level >= 2;
      return true;
    }

    function actionRowAllowed(row) {
      const repoName = String(row.repo || '');
      return actionRepoAllowed(repoName) &&
        actionRepoDirtyAllowed(repoName) &&
        actionPriorityAllowed(row.severity);
    }

    function compareActionRows(a, b) {
      if (actionSortMode === 'repo') {
        return String(a.repo || '').localeCompare(String(b.repo || '')) ||
          Number(b.priority || 0) - Number(a.priority || 0);
      }
      if (actionSortMode === 'age') {
        return Number(b.age_days || 0) - Number(a.age_days || 0) ||
          Number(b.severity || 0) - Number(a.severity || 0) ||
          String(a.repo || '').localeCompare(String(b.repo || ''));
      }
      if (actionSortMode === 'dirtiness') {
        const dirtyDelta = Number((repoByName(b.repo) || {}).git_dirty) -
          Number((repoByName(a.repo) || {}).git_dirty);
        if (dirtyDelta !== 0) return dirtyDelta;
      }
      return Number(b.severity || 0) - Number(a.severity || 0) ||
        Number(b.priority || 0) - Number(a.priority || 0) ||
        String(a.repo || '').localeCompare(String(b.repo || ''));
    }

    function buildAgentPrompt(kind, payload) {
      const repoName = String(payload.repo || '');
      if (kind === 'attention') {
        const reasons = String(payload.reasons || '').trim();
        return `In repo ${repoName}, review attention signals (${reasons || 'no reasons supplied'}). Decide the highest-priority blocker, update Workgraph task/dependency state, and return the next concrete execution step with follow-up tasks.`;
      }
      if (kind === 'aging') {
        const taskId = String(payload.task_id || '');
        const title = String(payload.title || '');
        const label = String(payload.label || '');
        const ageDays = Number(payload.age_days || 0);
        const ageText = ageDays > 0 ? ` (age ${ageDays}d)` : '';
        return `In repo ${repoName}, resolve ${label} for task ${taskId || 'unknown'} ${title}${ageText}. Decide execute vs unblock vs close, fix dependency state in Workgraph, and summarize what changed.`;
      }
      if (kind === 'next') {
        const taskId = String(payload.task_id || '');
        const title = String(payload.title || '');
        const status = String(payload.status || 'open');
        return `In repo ${repoName}, take task ${taskId} (${title}) currently ${status}. Execute one meaningful step, keep Workgraph dependencies/status accurate, and provide a concise progress update plus any follow-up tasks.`;
      }
      if (kind === 'upstream') {
        const category = String(payload.category || 'candidate');
        const ahead = Number(payload.ahead || 0);
        const files = Number(payload.file_count || 0);
        return `In repo ${repoName}, prepare an upstream contribution plan for ${category} changes (${files} files, ahead ${ahead}). Propose smallest safe PR scope, draft title/body, and call out splits or risks before opening a PR.`;
      }
      return `In repo ${repoName}, determine the highest-priority next action, update Workgraph state, and provide a concise execution plan.`;
    }

    function renderActionItemHtml(item) {
      const repoName = String(item.repo || '');
      const prompt = String(item.prompt || '');
      const why = String(item.why || '');
      const title = String(item.title || '');
      const focusButton = `<button class="action-link" data-focus-repo="${escAttr(repoName)}">focus graph</button>`;
      const copyButton = prompt ? `<button class="action-link" data-copy-prompt="${escAttr(prompt)}">copy prompt</button>` : '';
      return `
        <li class="action-item ${actionSeverityClass(item.severity)}">
          <div class="action-title">
            <span>${title}</span>
            <span>${focusButton}${copyButton}</span>
          </div>
          <div class="action-why">${why}</div>
          ${prompt ? `<div class="action-prompt"><strong>Prompt:</strong> ${esc(prompt)}</div>` : ''}
        </li>
      `;
    }

    function actionSeverityClass(level) {
      const value = Number(level || 0);
      if (value >= 3) return 'sev-high';
      if (value >= 2) return 'sev-med';
      return 'sev-low';
    }

    function setActionCount(id, count) {
      const target = el(id);
      if (target) target.textContent = String(Math.max(0, Number(count || 0)));
    }

    function renderActionSummary(counts) {
      const total = n(counts.attention) + n(counts.aging) + n(counts.upstream) + n(counts.next);
      const repoText = actionRepoFilter === '__all__' ? 'all repos' : actionRepoFilter;
      const text =
        `Showing ${total} actionable items for ${repoText} (sort=${actionSortMode}, priority=${actionPriorityFilter}, dirty=${actionDirtyFilter}). ` +
        `Attention=${n(counts.attention)}, Aging/Gaps=${n(counts.aging)}, Upstream=${n(counts.upstream)}, Next Work=${n(counts.next)}.`;
      el('action-summary').textContent = text;
    }

    function fallbackRepoDependencyOverview(data) {
      const repos = Array.isArray(data.repos) ? data.repos : [];
      const nodes = repos.map((repo) => ({
        id: String(repo.name || ''),
        source: String(repo.source || ''),
        workgraph_exists: !!repo.workgraph_exists,
        service_running: !!repo.service_running,
        risk_score: 0,
        outbound: 0,
        inbound: 0,
        outbound_weight: 0,
        inbound_weight: 0,
      }));
      const nodeMap = new Map(nodes.map((row) => [String(row.id || ''), row]));
      const edgeMap = new Map();

      repos.forEach((repo) => {
        const source = String(repo.name || '');
        const deps = Array.isArray(repo.cross_repo_dependencies) ? repo.cross_repo_dependencies : [];
        deps.forEach((dep) => {
          if (!dep || typeof dep !== 'object') return;
          const target = String(dep.repo || '');
          if (!source || !target || source === target || !nodeMap.has(target)) return;
          const weight = Math.max(1, Number(dep.score || 0));
          const key = `${source}->${target}`;
          const prev = edgeMap.get(key) || { source, target, weight: 0, reasons: [] };
          prev.weight = Math.min(24, Number(prev.weight || 0) + weight);
          const reasons = Array.isArray(dep.reasons) ? dep.reasons.map((item) => String(item || '')).filter(Boolean) : [];
          reasons.forEach((reason) => {
            if (!prev.reasons.includes(reason)) prev.reasons.push(reason);
          });
          edgeMap.set(key, prev);
        });
      });

      const edges = Array.from(edgeMap.values()).sort((a, b) => (
        Number(b.weight || 0) - Number(a.weight || 0) ||
        String(a.source || '').localeCompare(String(b.source || '')) ||
        String(a.target || '').localeCompare(String(b.target || ''))
      ));
      edges.forEach((edge) => {
        const sourceNode = nodeMap.get(String(edge.source || ''));
        const targetNode = nodeMap.get(String(edge.target || ''));
        if (sourceNode) {
          sourceNode.outbound = Number(sourceNode.outbound || 0) + 1;
          sourceNode.outbound_weight = Number(sourceNode.outbound_weight || 0) + Number(edge.weight || 0);
        }
        if (targetNode) {
          targetNode.inbound = Number(targetNode.inbound || 0) + 1;
          targetNode.inbound_weight = Number(targetNode.inbound_weight || 0) + Number(edge.weight || 0);
        }
      });

      const isolated = nodes.filter((row) => !Number(row.outbound || 0) && !Number(row.inbound || 0));
      const topOutbound = nodes
        .slice()
        .sort((a, b) => (
          Number(b.outbound_weight || 0) - Number(a.outbound_weight || 0) ||
          Number(b.outbound || 0) - Number(a.outbound || 0) ||
          String(a.id || '').localeCompare(String(b.id || ''))
        ))
        .slice(0, 3)
        .map((row) => ({ repo: row.id, weight: row.outbound_weight, count: row.outbound }));
      const topInbound = nodes
        .slice()
        .sort((a, b) => (
          Number(b.inbound_weight || 0) - Number(a.inbound_weight || 0) ||
          Number(b.inbound || 0) - Number(a.inbound || 0) ||
          String(a.id || '').localeCompare(String(b.id || ''))
        ))
        .slice(0, 3)
        .map((row) => ({ repo: row.id, weight: row.inbound_weight, count: row.inbound }));

      return {
        nodes: nodes.sort((a, b) => String(a.id || '').localeCompare(String(b.id || ''))),
        edges,
        summary: {
          repo_count: nodes.length,
          edge_count: edges.length,
          linked_repos: nodes.length - isolated.length,
          isolated_repos: isolated.length,
          top_outbound: topOutbound,
          top_inbound: topInbound,
        },
      };
    }

    function drawRepoDependencyOverview(data) {
      const svg = el('repo-dep-graph');
      const meta = el('repo-dep-meta');
      const note = el('repo-dep-note');
      if (!svg || !meta || !note) return;

      const payload = data && data.repo_dependency_overview;
      const overview = payload && Array.isArray(payload.nodes) && Array.isArray(payload.edges)
        ? payload
        : fallbackRepoDependencyOverview(data || {});
      const nodes = Array.isArray(overview.nodes) ? overview.nodes.slice() : [];
      const edges = Array.isArray(overview.edges) ? overview.edges.slice() : [];
      const summary = overview.summary && typeof overview.summary === 'object' ? overview.summary : {};

      if (!nodes.length) {
        svg.setAttribute('viewBox', '0 0 1200 280');
        svg.innerHTML = '<text x="24" y="48" fill="#5f6f66" font-size="16">No repo dependency signals available yet.</text>';
        meta.textContent = 'repo dependencies unavailable';
        note.textContent = 'As repos reference each other in task IDs/titles/dependency links, this map will populate.';
        return;
      }

      const rankedNodes = nodes
        .slice()
        .sort((a, b) => (
          Number(b.outbound_weight || 0) - Number(a.outbound_weight || 0) ||
          Number(b.inbound_weight || 0) - Number(a.inbound_weight || 0) ||
          String(a.id || '').localeCompare(String(b.id || ''))
        ));
      const width = Math.max(1000, 260 + rankedNodes.length * 54);
      const height = 320;
      const centerX = width / 2;
      const centerY = height / 2;
      const radius = rankedNodes.length <= 1 ? 0 : Math.max(78, Math.min(width, height) * 0.34);
      const pos = {};
      rankedNodes.forEach((node, idx) => {
        const id = String(node.id || '');
        if (!id) return;
        const theta = (Math.PI * 2 * idx) / Math.max(1, rankedNodes.length);
        pos[id] = {
          x: centerX + radius * Math.cos(theta),
          y: centerY + radius * Math.sin(theta),
          node,
        };
      });

      const related = new Set();
      if (selectedRepo && selectedRepo !== "__all__") {
        related.add(selectedRepo);
        edges.forEach((edge) => {
          const source = String(edge.source || '');
          const target = String(edge.target || '');
          if (source === selectedRepo) related.add(target);
          if (target === selectedRepo) related.add(source);
        });
      }

      const edgeSvg = edges
        .filter((edge) => pos[String(edge.source || '')] && pos[String(edge.target || '')])
        .map((edge) => {
          const source = String(edge.source || '');
          const target = String(edge.target || '');
          const a = pos[source];
          const b = pos[target];
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          const cx = mx + (centerX - mx) * 0.35;
          const cy = my + (centerY - my) * 0.35;
          const weight = Number(edge.weight || 1);
          const emphasis = selectedRepo && related.size ? (source === selectedRepo || target === selectedRepo) : false;
          const opacity = selectedRepo && related.size ? (emphasis ? 0.96 : 0.25) : 0.8;
          const stroke = emphasis ? '#0f6f7c' : '#b7ad9b';
          const strokeWidth = Math.max(1.1, Math.min(4.2, 1 + weight * 0.15));
          const reasons = Array.isArray(edge.reasons) ? edge.reasons.join(', ') : '';
          const label = `${source} -> ${target} | weight=${Math.round(weight)}${reasons ? ` | ${reasons}` : ''}`;
          return `<path d="M ${a.x} ${a.y} Q ${cx} ${cy}, ${b.x} ${b.y}" stroke="${stroke}" stroke-width="${strokeWidth}" opacity="${opacity}" fill="none" marker-end="url(#repo-dep-arrow)"><title>${esc(label)}</title></path>`;
        })
        .join('');

      const nodeSvg = rankedNodes
        .map((node) => {
          const id = String(node.id || '');
          const entry = pos[id];
          if (!entry) return '';
          const row = repoByName(id) || {};
          const [_pill, kind] = qualityPill(row);
          const connected = !selectedRepo || selectedRepo === "__all__" || !related.size || related.has(id);
          const isSelected = selectedRepo === id;
          const fill = kind === 'bad' ? '#f7dfdf' : (kind === 'warn' ? '#f9ead7' : '#e2f0e4');
          const radiusNode = Math.max(12, Math.min(21, 12 + Number(node.inbound_weight || 0) * 0.08 + Number(node.outbound_weight || 0) * 0.08));
          const stroke = isSelected ? '#0f6f7c' : '#385148';
          const strokeW = isSelected ? 3 : 1.4;
          const opacity = connected ? 1 : 0.34;
          const hint = `${id} | out=${n(node.outbound)} (${n(node.outbound_weight)}) | in=${n(node.inbound)} (${n(node.inbound_weight)})`;
          return `
            <g data-focus-repo="${escAttr(id)}" data-scroll-graph="1" style="cursor:pointer; opacity:${opacity}">
              <circle cx="${entry.x}" cy="${entry.y}" r="${radiusNode}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeW}" />
              <text x="${entry.x + radiusNode + 6}" y="${entry.y + 4}" fill="#2e3d35" font-size="12">${esc(id)}</text>
              <title>${esc(hint)}</title>
            </g>
          `;
        })
        .join('');

      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.innerHTML = `
        <defs>
          <marker id="repo-dep-arrow" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">
            <polygon points="0 0, 9 3.5, 0 7" fill="#b7ad9b"></polygon>
          </marker>
        </defs>
        <rect x="0" y="0" width="${width}" height="${height}" fill="#fffcf8"></rect>
        ${edgeSvg}
        ${nodeSvg}
      `;

      meta.textContent =
        `repo dependencies | repos=${n(summary.repo_count || nodes.length)} | links=${n(summary.edge_count || edges.length)} | isolated=${n(summary.isolated_repos || 0)}`;

      const topOut = Array.isArray(summary.top_outbound) && summary.top_outbound.length ? summary.top_outbound[0] : null;
      const topIn = Array.isArray(summary.top_inbound) && summary.top_inbound.length ? summary.top_inbound[0] : null;
      const outText = topOut && topOut.repo ? `${topOut.repo} (${n(topOut.weight)})` : 'n/a';
      const inText = topIn && topIn.repo ? `${topIn.repo} (${n(topIn.weight)})` : 'n/a';
      note.innerHTML =
        `Edge A -> B means repo A has dependency signals pointing to repo B. Top outbound: <code>${esc(outText)}</code>. Top inbound: <code>${esc(inText)}</code>.`;
    }

    function renderOverviewCards(data) {
      const ov = data.overview || {};
      const supervisor = data.supervisor || {};
      const cards = [
        ['Repos', ov.repos_total],
        ['In Progress', ov.tasks_in_progress],
        ['Ready', ov.tasks_ready],
        ['Blocked', ov.blocked_open],
        ['Aging Open', ov.stale_open],
        ['Aging Active', ov.stale_in_progress],
        ['Missing Deps', ov.missing_dependencies],
        ['Orch Gaps', ov.repos_with_inactive_service],
        ['Dirty Repos', ov.repos_dirty],
        ['Svc Restarts', supervisor.started],
        ['Svc Restart Fail', supervisor.failed],
        ['Upstream PRs', ov.upstream_candidates],
      ];
      el('overview-cards').innerHTML = cards
        .map(([k, v]) => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`)
        .join('');
    }

    function renderAttention(data) {
      const ov = data.overview || {};
      const rows = Array.isArray(ov.attention_repos) ? ov.attention_repos : [];
      const out = el('attention');
      out.innerHTML = '';
      if (!rows.length) {
        out.innerHTML = '<li class="action-item action-empty">No high-pressure repo at this moment.</li>';
        setActionCount('attention-count', 0);
        return 0;
      }
      const shaped = rows.map((row) => {
        const repoName = String(row.repo || '');
        const score = n(row.score);
        const reasons = Array.isArray(row.reasons) ? row.reasons.join('; ') : '';
        const severity = score >= 22 ? 3 : (score >= 10 ? 2 : 1);
        const prompt = buildAgentPrompt('attention', { repo: repoName, reasons });
        return {
          repo: repoName,
          severity,
          priority: score,
          age_days: 0,
          title: `<code>${esc(repoName)}</code> attention score=<code>${esc(score)}</code>`,
          why: esc(reasons || 'no reason attached'),
          prompt,
        };
      });
      const filtered = shaped.filter(actionRowAllowed).sort(compareActionRows).slice(0, 12);
      if (!filtered.length) {
        out.innerHTML = '<li class="action-item action-empty">No matching attention items for current repo filter.</li>';
        setActionCount('attention-count', 0);
        return 0;
      }
      out.innerHTML = filtered.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('attention-count', filtered.length);
      return filtered.length;
    }

    function renderAging(data) {
      const out = el('aging');
      out.innerHTML = '';
      const issues = [];
      (data.repos || []).forEach((repo) => {
        (repo.stale_in_progress || []).forEach((task) => {
          issues.push({
            severity: 2,
            repo: repo.name,
            label: 'aging in-progress',
            task,
            priority: 14 + n(task.age_days),
            age_days: n(task.age_days),
          });
        });
        (repo.stale_open || []).forEach((task) => {
          issues.push({
            severity: 1,
            repo: repo.name,
            label: 'aging open',
            task,
            priority: 6 + n(task.age_days),
            age_days: n(task.age_days),
          });
        });
        (repo.dependency_issues || []).forEach((item) => {
          issues.push({
            severity: 3,
            repo: repo.name,
            label: item.kind || 'dependency issue',
            task: {id: item.task_id || '', title: item.task_title || '', age_days: ''},
            priority: 24,
            age_days: 0,
          });
        });
      });
      const shaped = issues.map((item) => {
        const repoName = String(item.repo || '');
        const taskId = String(item.task.id || '');
        const taskTitle = String(item.task.title || '');
        const age = Number(item.age_days || 0);
        const prompt = buildAgentPrompt('aging', {
          repo: repoName,
          task_id: taskId,
          title: taskTitle,
          label: item.label,
          age_days: age,
        });
        return {
          repo: repoName,
          severity: n(item.severity),
          priority: n(item.priority),
          age_days: age,
          title: `<code>${esc(repoName)}</code> ${esc(item.label)}`,
          why: `<code>${esc(taskId || 'n/a')}</code> ${esc(taskTitle)}${age > 0 ? ` age=${esc(age)}d` : ''}`,
          prompt,
        };
      });
      const sorted = shaped.filter(actionRowAllowed).sort(compareActionRows);
      const shown = sorted.slice(0, 18);
      if (!issues.length) {
        out.innerHTML = '<li class="action-item action-empty">No stale tasks or dependency gaps detected.</li>';
        setActionCount('aging-count', 0);
        return 0;
      }
      if (!shown.length) {
        out.innerHTML = '<li class="action-item action-empty">No matching aging/gap items for current filters.</li>';
        setActionCount('aging-count', 0);
        return 0;
      }
      out.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('aging-count', shown.length);
      return shown.length;
    }

    function renderRepoCards(data) {
      const allRows = (data.repos || []).slice();
      const rows = allRows
        .filter((repo) => repoHealthAllowed(repo) && repoDirtyAllowed(repo) && repoServiceAllowed(repo))
        .sort(compareRepos);
      const container = el('repo-grid');
      container.innerHTML = '';
      refreshRepoSummary(allRows.length, rows.length);
      if (!rows.length) {
        container.innerHTML = '<article class="repo-card"><p class="repo-note">No repos match the active filters.</p></article>';
        return;
      }
      rows.forEach((repo) => {
        const repoName = String(repo.name || '');
        const card = document.createElement('article');
        card.className = 'repo-card';
        card.setAttribute('data-repo-name', repoName);
        const [pillLabel, pillKind] = qualityPill(repo);
        const priorityScore = repoPriorityScore(repo);
        const errs = (repo.errors || []).length ? `<div class="warn">errors=${esc((repo.errors || []).join(','))}</div>` : '';
        card.innerHTML = `
          <div class="repo-head">
            <span class="repo-name"><code>${esc(repoName)}</code></span>
            <span class="pill ${pillKind}">${esc(pillLabel)}</span>
          </div>
          <div class="repo-meta">
            <span>priority=<code>${esc(priorityScore)}</code></span>
            <span>branch=<code>${esc(repo.git_branch || 'n/a')}</code></span>
            <span>dirty=${repo.git_dirty ? 'yes' : 'no'}</span>
            <span>ahead=${esc(repo.ahead || 0)} behind=${esc(repo.behind || 0)}</span>
            <span>service=${repo.service_running ? 'running' : (repo.workgraph_exists ? 'stopped' : 'n/a')}</span>
            <span>in-progress=${esc((repo.in_progress || []).length)} ready=${esc((repo.ready || []).length)}</span>
            <span>blocked=${esc(repo.blocked_open || 0)} missing-deps=${esc(repo.missing_dependencies || 0)}</span>
            <span>source=${esc(repo.source || 'n/a')}</span>
          </div>
          ${errs}
          <p class="repo-note ${pillKind === 'bad' ? 'warn' : ''}">${esc(repo.narrative || '')}</p>
          <div class="repo-actions">
            <button class="action-link" data-focus-repo="${escAttr(repoName)}" data-scroll-graph="1">open graph</button>
          </div>
        `;
        card.addEventListener('click', (event) => {
          const target = event.target;
          if (target && target.closest && target.closest('button')) return;
          focusRepoInGraph(repoName, true);
        });
        container.appendChild(card);
      });
    }

    function refreshGraphSelector(data) {
      const select = el('graph-repo');
      const graphRepos = (data.repos || [])
        .map((repo) => ({
          name: repo.name,
          nodes: (repo.task_graph_nodes || []).length,
          edges: (repo.task_graph_edges || []).length,
        }))
        .sort((a, b) => (
          b.edges - a.edges ||
          b.nodes - a.nodes ||
          String(a.name || '').localeCompare(String(b.name || ''))
        ));
      const repos = graphRepos.map((row) => String(row.name || ''));
      const existing = new Set(Array.from(select.options).map((opt) => opt.value));
      const expected = ["__all__", ...repos];
      const needsReset = expected.length !== existing.size || expected.some((name) => !existing.has(name));
      if (needsReset) {
        const opts = ['<option value="__all__">all repos</option>'];
        graphRepos.forEach((row) => {
          const label = `${row.name} (${row.nodes}n/${row.edges}e)`;
          opts.push(`<option value="${escAttr(row.name)}">${esc(label)}</option>`);
        });
        select.innerHTML = opts.join('');
      }
      if (!selectedRepo || (!repos.includes(selectedRepo) && selectedRepo !== "__all__")) {
        const preferred = graphRepos.find((row) => row.edges > 0) || graphRepos.find((row) => row.nodes > 0);
        selectedRepo = preferred ? String(preferred.name) : "__all__";
      }
      select.value = selectedRepo;
    }

    function laneFor(node) {
      const status = String(node.status || '').toLowerCase();
      if (node.blocked) return 3;
      if (status === 'done') return 0;
      if (status === 'in-progress') return 1;
      if (status === 'open' || status === 'ready') return 2;
      return 3;
    }

    function colorFor(node) {
      const status = String(node.status || '').toLowerCase();
      if (node.blocked) return '#9c2525';
      if (status === 'done') return '#2f6e39';
      if (status === 'in-progress') return '#0f6f7c';
      if (status === 'open' || status === 'ready') return '#a26c13';
      return '#5f6f66';
    }

    function detectCycleEdges(edges) {
      const out = new Set();
      const adj = new Map();
      (edges || []).forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (!s || !t) return;
        if (!adj.has(s)) adj.set(s, []);
        adj.get(s).push(t);
      });

      const visit = new Map();
      function dfs(node, stack) {
        visit.set(node, 1);
        const children = adj.get(node) || [];
        for (const child of children) {
          if (visit.get(child) === 1) {
            const start = stack.indexOf(child);
            if (start >= 0) {
              for (let i = start; i < stack.length - 1; i += 1) {
                out.add(`${stack[i]}->${stack[i + 1]}`);
              }
              out.add(`${stack[stack.length - 1]}->${child}`);
            }
            continue;
          }
          if (visit.get(child) === 2) continue;
          dfs(child, [...stack, child]);
        }
        visit.set(node, 2);
      }

      const keys = new Set();
      (edges || []).forEach((edge) => {
        keys.add(String(edge.source || ''));
        keys.add(String(edge.target || ''));
      });
      keys.forEach((key) => {
        if (!key) return;
        if (!visit.has(key)) dfs(key, [key]);
      });
      return out;
    }

    function normalizeGraph(repo) {
      const nodes = Array.isArray(repo.task_graph_nodes) ? repo.task_graph_nodes : [];
      const edges = Array.isArray(repo.task_graph_edges) ? repo.task_graph_edges : [];
      return { nodes, edges };
    }

    function buildAdjacency(edges) {
      const forward = new Map();
      const reverse = new Map();
      (edges || []).forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (!s || !t) return;
        if (!forward.has(s)) forward.set(s, []);
        if (!reverse.has(t)) reverse.set(t, []);
        forward.get(s).push(t);
        reverse.get(t).push(s);
      });
      return { forward, reverse };
    }

    function boundedReach(seed, map, maxDepth) {
      const seen = new Set();
      const queue = [{ id: seed, depth: 0 }];
      while (queue.length) {
        const row = queue.shift();
        const next = map.get(row.id) || [];
        next.forEach((id) => {
          if (seen.has(id)) return;
          seen.add(id);
          if (row.depth + 1 < maxDepth) {
            queue.push({ id, depth: row.depth + 1 });
          }
        });
      }
      return seen;
    }

    function chooseFocusSeed(nodes) {
      const ranked = (nodes || []).slice().sort((a, b) => {
        const laneDelta = laneFor(a) - laneFor(b);
        if (laneDelta !== 0) return laneDelta;
        const ageA = Number(a.age_days || 0);
        const ageB = Number(b.age_days || 0);
        if (ageB !== ageA) return ageB - ageA;
        return String(a.id || '').localeCompare(String(b.id || ''));
      });
      return ranked.length ? String(ranked[0].id || '') : '';
    }

    function subgraphForMode(model, mode, explicitNodeId) {
      const nodes = model.nodes || [];
      const edges = model.edges || [];
      const ids = new Set(nodes.map((node) => String(node.id || '')));
      const validSelected = explicitNodeId && ids.has(explicitNodeId) ? explicitNodeId : '';
      const seed = validSelected || chooseFocusSeed(nodes);
      if (!seed || mode === 'full') {
        return { nodes, edges, seed };
      }

      const { forward, reverse } = buildAdjacency(edges);
      let selectedIds = new Set([seed]);
      if (mode === 'focus') {
        const up = boundedReach(seed, reverse, 4);
        const down = boundedReach(seed, forward, 4);
        selectedIds = new Set([seed, ...up, ...down]);
        if (selectedIds.size <= 2 && nodes.length > selectedIds.size) {
          const ranked = nodes
            .slice()
            .sort((a, b) => (
              laneFor(a) - laneFor(b) ||
              Number(b.age_days || 0) - Number(a.age_days || 0) ||
              String(a.id || '').localeCompare(String(b.id || ''))
            ))
            .slice(0, Math.min(20, nodes.length));
          ranked.forEach((node) => selectedIds.add(String(node.id || '')));
        }
      } else {
        // active mode: prioritize in-progress/blocked/open and their immediate deps.
        selectedIds = new Set();
        nodes.forEach((node) => {
          const status = String(node.status || '').toLowerCase();
          if (status === 'in-progress' || status === 'open' || status === 'ready' || node.blocked) {
            selectedIds.add(String(node.id || ''));
          }
        });
        Array.from(selectedIds).forEach((id) => {
          (forward.get(id) || []).forEach((next) => selectedIds.add(next));
          (reverse.get(id) || []).forEach((prev) => selectedIds.add(prev));
        });
        if (!selectedIds.size) selectedIds.add(seed);
      }

      const limited = Array.from(selectedIds);
      if (limited.length > 90) {
        limited.sort((a, b) => a.localeCompare(b));
        selectedIds = new Set([seed, ...limited.slice(0, 89)]);
      }
      const subNodes = nodes.filter((node) => selectedIds.has(String(node.id || '')));
      const subEdges = edges.filter((edge) => selectedIds.has(String(edge.source || '')) && selectedIds.has(String(edge.target || '')));
      return { nodes: subNodes, edges: subEdges, seed };
    }

    function layoutGraph(model) {
      const nodes = model.nodes || [];
      const edges = model.edges || [];
      const nodeIds = new Set(nodes.map((node) => String(node.id || '')));
      const { forward, reverse } = buildAdjacency(edges);
      const indegree = new Map();
      nodes.forEach((node) => indegree.set(String(node.id || ''), 0));
      edges.forEach((edge) => {
        const t = String(edge.target || '');
        const s = String(edge.source || '');
        if (!nodeIds.has(s) || !nodeIds.has(t)) return;
        indegree.set(t, (indegree.get(t) || 0) + 1);
      });

      const queue = [];
      indegree.forEach((deg, id) => {
        if (deg === 0) queue.push(id);
      });
      const depth = new Map();
      nodes.forEach((node) => depth.set(String(node.id || ''), 0));

      while (queue.length) {
        const cur = queue.shift();
        const children = forward.get(cur) || [];
        children.forEach((child) => {
          if (!nodeIds.has(child)) return;
          const nextDepth = Math.max(depth.get(child) || 0, (depth.get(cur) || 0) + 1);
          depth.set(child, nextDepth);
          const nextDeg = (indegree.get(child) || 0) - 1;
          indegree.set(child, nextDeg);
          if (nextDeg === 0) queue.push(child);
        });
      }

      // Relax again so remaining cycle-connected nodes get a readable placement.
      for (let pass = 0; pass < nodes.length; pass += 1) {
        let changed = false;
        edges.forEach((edge) => {
          const s = String(edge.source || '');
          const t = String(edge.target || '');
          if (!nodeIds.has(s) || !nodeIds.has(t)) return;
          const candidate = (depth.get(s) || 0) + 1;
          if (candidate > (depth.get(t) || 0)) {
            depth.set(t, candidate);
            changed = true;
          }
        });
        if (!changed) break;
      }

      const byDepth = new Map();
      nodes.forEach((node) => {
        const id = String(node.id || '');
        const d = Math.max(0, Number(depth.get(id) || 0));
        if (!byDepth.has(d)) byDepth.set(d, []);
        byDepth.get(d).push(node);
      });
      const depthKeys = Array.from(byDepth.keys()).sort((a, b) => a - b);
      depthKeys.forEach((key) => {
        byDepth.get(key).sort((a, b) => {
          const rankDelta = laneFor(a) - laneFor(b);
          if (rankDelta !== 0) return rankDelta;
          const ageA = Number(a.age_days || 0);
          const ageB = Number(b.age_days || 0);
          if (ageB !== ageA) return ageB - ageA;
          return String(a.id || '').localeCompare(String(b.id || ''));
        });
      });

      const maxDepth = depthKeys.length ? Math.max(...depthKeys) : 0;
      const maxRows = depthKeys.length ? Math.max(...depthKeys.map((key) => byDepth.get(key).length)) : 1;
      const width = Math.max(1200, 260 + (maxDepth + 1) * 230);
      const height = Math.max(420, 130 + maxRows * 72);

      const pos = {};
      depthKeys.forEach((key) => {
        const list = byDepth.get(key) || [];
        list.forEach((node, idx) => {
          const id = String(node.id || '');
          pos[id] = {
            x: 120 + key * 230,
            y: 70 + idx * 72,
            node,
            depth: key,
            indegree: (reverse.get(id) || []).length,
            outdegree: (forward.get(id) || []).length,
          };
        });
      });
      return { nodes, edges, pos, width, height, maxDepth };
    }

    function traverseSelection(model, startId) {
      const { forward, reverse } = buildAdjacency(model.edges || []);
      function bfs(seed, map) {
        const seen = new Set();
        const queue = [seed];
        while (queue.length) {
          const cur = queue.shift();
          const next = map.get(cur) || [];
          next.forEach((item) => {
            if (seen.has(item)) return;
            seen.add(item);
            queue.push(item);
          });
        }
        return seen;
      }
      const ancestors = bfs(startId, reverse);
      const descendants = bfs(startId, forward);
      const pathNodes = new Set([startId, ...ancestors, ...descendants]);
      const pathEdges = new Set();
      (model.edges || []).forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (!s || !t) return;
        if (pathNodes.has(s) && pathNodes.has(t)) pathEdges.add(`${s}->${t}`);
      });
      return { ancestors, descendants, pathNodes, pathEdges };
    }

    function setGraphPathText(model, activeNodeId, traversal, cycleEdges, mode, seed) {
      const out = el('graph-path');
      if ((model.edges || []).length === 0) {
        if (!activeNodeId) {
          out.textContent = `Mode: ${mode}. No dependency edges found for this repo yet (tasks may not define "after" links).`;
          return;
        }
      }
      if (!activeNodeId) {
        const loopCount = cycleEdges.size;
        out.textContent =
          `Mode: ${mode}. Focus seed: ${seed || 'none'}.\n` +
          (loopCount > 0
            ? `Detected ${loopCount} cycle edges. Select a node to inspect dependency chain.`
            : 'Select a node to inspect dependency chain.');
        return;
      }
      const node = (model.nodes || []).find((n2) => String(n2.id) === String(activeNodeId));
      const title = node ? String(node.label || activeNodeId) : activeNodeId;
      const up = Array.from(traversal.ancestors).sort();
      const down = Array.from(traversal.descendants).sort();
      const loopHits = [];
      cycleEdges.forEach((edge) => {
        const [s, t] = edge.split('->', 2);
        if (traversal.pathNodes.has(s) || traversal.pathNodes.has(t)) loopHits.push(edge);
      });
      out.textContent =
        `Node: ${activeNodeId} (${title})\n` +
        `Upstream chain (${up.length}): ${up.slice(0, 12).join(', ') || 'none'}\n` +
        `Downstream chain (${down.length}): ${down.slice(0, 12).join(', ') || 'none'}\n` +
        `Cycle edges touching path: ${loopHits.length ? loopHits.slice(0, 12).join(', ') : 'none'}`;
    }

    function zoomGraph(multiplier) {
      graphView.scale = Math.min(3.6, Math.max(0.45, graphView.scale * multiplier));
      if (currentData) drawGraph(currentData);
    }

    function resetGraphView() {
      graphView.scale = 1;
      graphView.tx = 0;
      graphView.ty = 0;
      if (currentData) drawGraph(currentData);
    }

    function renderMiniGraphSvg(repo) {
      const base = normalizeGraph(repo);
      const shaped = subgraphForMode(base, 'focus', '');
      const model = layoutGraph(shaped);
      const width = 360;
      const height = 150;
      const sx = width / Math.max(1, model.width);
      const sy = height / Math.max(1, model.height);
      const scale = Math.max(0.25, Math.min(sx, sy));
      const tx = 8;
      const ty = 14;
      const edgeSvg = (model.edges || [])
        .filter((edge) => model.pos[edge.source] && model.pos[edge.target])
        .map((edge) => {
          const a = model.pos[edge.source];
          const b = model.pos[edge.target];
          return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#c6bdaf" stroke-width="1" opacity="0.8" />`;
        })
        .join('');
      const nodeSvg = Object.values(model.pos).map((entry) =>
        `<circle cx="${entry.x}" cy="${entry.y}" r="4.2" fill="${colorFor(entry.node)}" />`
      ).join('');
      return `<svg viewBox="0 0 ${width} ${height}"><rect x="0" y="0" width="${width}" height="${height}" fill="#fffcf8" /><g transform="translate(${tx} ${ty}) scale(${scale})">${edgeSvg}${nodeSvg}</g></svg>`;
    }

    function drawAllGraphs(data) {
      const svg = el('graph');
      const all = el('graph-all');
      const graphMeta = el('graph-meta');
      svg.style.display = 'none';
      all.style.display = 'grid';
      const repos = (data.repos || [])
        .slice()
        .sort((a, b) => (
          Number((b.task_graph_edges || []).length) - Number((a.task_graph_edges || []).length) ||
          Number((b.task_graph_nodes || []).length) - Number((a.task_graph_nodes || []).length) ||
          String(a.name || '').localeCompare(String(b.name || ''))
        ));
      graphMeta.textContent = `all repos | ${repos.length} listed`;
      const blocks = repos.map((repo) => {
        const nodes = (repo.task_graph_nodes || []).length;
        const edges = (repo.task_graph_edges || []).length;
        const mini = nodes > 0 ? renderMiniGraphSvg(repo) : '<div class="cmd">No graph nodes available.</div>';
        return `
          <article class="graph-mini">
            <h4>
              <span><code>${esc(repo.name)}</code> ${esc(nodes)}n/${esc(edges)}e</span>
              <button class="action-link" data-focus-repo="${escAttr(repo.name)}" data-scroll-graph="1">open</button>
            </h4>
            ${mini}
          </article>
        `;
      });
      all.innerHTML = `<div style="grid-column:1 / -1;" class="cmd">All repo graph previews. Click <code>open</code> to focus a repo graph.</div>${blocks.join('')}`;
      el('graph-path').textContent = 'All graph previews shown. Choose a repo for interactive path tracing.';
    }

    function drawGraph(data) {
      drawRepoDependencyOverview(data);
      const svg = el('graph');
      const all = el('graph-all');
      const graphMeta = el('graph-meta');
      if (selectedRepo === "__all__") {
        drawAllGraphs(data);
        graphModel = { repo: "__all__", nodes: [], edges: [], pos: {} };
        return;
      }
      svg.style.display = 'block';
      all.style.display = 'none';
      const repo = (data.repos || []).find((r) => r.name === selectedRepo);
      if (!repo || !Array.isArray(repo.task_graph_nodes) || repo.task_graph_nodes.length === 0) {
        svg.setAttribute('viewBox', '0 0 1200 340');
        svg.innerHTML = '<text x="40" y="60" fill="#5f6f66" font-size="18">No task graph for selected repo.</text>';
        graphMeta.textContent = `${selectedRepo} | 0 nodes`;
        el('graph-path').textContent = 'No graph data for selected repo. This usually means tasks have not been written to .workgraph/graph.jsonl yet.';
        graphModel = { repo: selectedRepo, nodes: [], edges: [], pos: {} };
        return;
      }

      const baseModel = normalizeGraph(repo);
      const shaped = subgraphForMode(baseModel, graphMode, selectedNodeId);
      const model = layoutGraph(shaped);
      graphModel = { repo: repo.name, nodes: model.nodes, edges: model.edges, pos: model.pos };
      if (selectedNodeId && !model.pos[selectedNodeId]) {
        selectedNodeId = '';
      }
      const activeNodeId = selectedNodeId || shaped.seed || '';
      const traversal = activeNodeId ? traverseSelection(model, activeNodeId) : null;
      const cycleEdges = detectCycleEdges(baseModel.edges);

      const edgeSvg = model.edges
        .filter((edge) => model.pos[edge.source] && model.pos[edge.target])
        .map((edge) => {
          const a = model.pos[edge.source];
          const b = model.pos[edge.target];
          const cx1 = a.x + Math.max(24, Math.abs(b.x - a.x) * 0.35);
          const cx2 = b.x - Math.max(24, Math.abs(b.x - a.x) * 0.35);
          const edgeKey = `${edge.source}->${edge.target}`;
          const inPath = traversal ? traversal.pathEdges.has(edgeKey) : false;
          const isCycle = cycleEdges.has(edgeKey);
          const stroke = inPath ? '#0f6f7c' : (isCycle ? '#8c2f2f' : '#b8b0a3');
          const opacity = inPath ? 1.0 : (traversal ? 0.2 : 0.82);
          const dash = isCycle ? ' stroke-dasharray="6 4"' : '';
          const width = inPath ? 2.1 : 1.4;
          return `<path d="M ${a.x} ${a.y} C ${cx1} ${a.y}, ${cx2} ${b.y}, ${b.x} ${b.y}" stroke="${stroke}" stroke-width="${width}" fill="none" opacity="${opacity}"${dash} />`;
        })
        .join('');

      const nodeSvg = Object.values(model.pos).map((entry) => {
        const label = String(entry.node.label || entry.node.id || '').slice(0, 28);
        const age = Number.isFinite(Number(entry.node.age_days)) ? `${entry.node.age_days}d` : '';
        const isSelected = activeNodeId && String(entry.node.id) === String(activeNodeId);
        const inPath = traversal ? traversal.pathNodes.has(String(entry.node.id)) : false;
        const stroke = isSelected ? '#0f6f7c' : (inPath ? '#1b5f69' : '#fff');
        const strokeW = isSelected ? 3 : (inPath ? 2 : 1);
        const opacity = traversal ? (inPath ? 1 : 0.34) : 1;
        return `
          <g class="graph-node" data-node-id="${esc(entry.node.id)}" style="opacity:${opacity}; cursor:pointer;">
            <circle cx="${entry.x}" cy="${entry.y}" r="10" fill="${colorFor(entry.node)}" stroke="${stroke}" stroke-width="${strokeW}" />
            <text x="${entry.x + 16}" y="${entry.y + 5}" fill="#2b3932" font-size="12">${esc(entry.node.id)}</text>
            <text x="${entry.x + 16}" y="${entry.y + 20}" fill="#6b776f" font-size="10">${esc(label)} ${esc(age)}</text>
          </g>
        `;
      }).join('');

      const depthLabels = Array.from({ length: Math.max(1, model.maxDepth + 1) }, (_v, idx) => idx)
        .map((depth) => `<text x="${120 + depth * 230 - 16}" y="32" fill="#6b776f" font-size="12">D${depth}</text>`)
        .join('');

      svg.setAttribute('viewBox', `0 0 ${model.width} ${model.height}`);
      svg.innerHTML =
        `<rect x="0" y="0" width="${model.width}" height="${model.height}" fill="#fffdfa" pointer-events="none" />` +
        `<g id="graph-content" transform="translate(${graphView.tx} ${graphView.ty}) scale(${graphView.scale})">${depthLabels}${edgeSvg}${nodeSvg}</g>`;
      const loopCount = cycleEdges.size;
      graphMeta.textContent =
        `${repo.name} | mode=${graphMode} | ${model.nodes.length} nodes, ${model.edges.length} edges, loops=${loopCount} | zoom=${graphView.scale.toFixed(2)}x`;
      setGraphPathText(
        model,
        activeNodeId,
        traversal || { ancestors: new Set(), descendants: new Set(), pathNodes: new Set(), pathEdges: new Set() },
        cycleEdges,
        graphMode,
        shaped.seed,
      );
    }

    function renderNext(data) {
      const list = el('next');
      list.innerHTML = '';
      const rows = (data.next_work || []).map((item) => {
        const repoName = String(item.repo || '');
        const taskId = String(item.task_id || '');
        const status = String(item.status || 'unknown');
        const severity = status === 'in-progress' ? 2 : 1;
        const prompt = buildAgentPrompt('next', {
          repo: repoName,
          task_id: taskId,
          title: String(item.title || ''),
          status,
        });
        return {
          repo: repoName,
          severity,
          priority: n(item.priority) + (severity * 3),
          age_days: 0,
          title: `<code>${esc(repoName)}</code> <code>${esc(taskId)}</code> ${esc(item.title || '')}`,
          why: `status=<code>${esc(status)}</code>`,
          prompt,
        };
      });
      const shown = rows.filter(actionRowAllowed).sort(compareActionRows).slice(0, 20);
      if (!shown.length) {
        list.innerHTML = '<li class="action-item action-empty">No next-work tasks for current repo filter.</li>';
        setActionCount('next-count', 0);
        return 0;
      }
      list.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('next-count', shown.length);
      return shown.length;
    }

    function renderUpstream(data) {
      const list = el('upstream');
      list.innerHTML = '';
      const rows = (data.upstream_candidates || []).map((item) => {
        const repoName = String(item.repo || '');
        const ahead = n(item.ahead);
        const files = n((item.changed_files || []).length);
        const prompt = buildAgentPrompt('upstream', {
          repo: repoName,
          category: String(item.category || ''),
          ahead,
          file_count: files,
        });
        return {
          repo: repoName,
          severity: 1,
          priority: ahead + files,
          age_days: 0,
          title: `<code>${esc(repoName)}</code> ${esc(item.category || 'candidate')} ahead=<code>${esc(ahead)}</code> files=<code>${esc(files)}</code>`,
          why: esc(String(item.summary || '')),
          prompt,
        };
      });
      const shown = rows.filter(actionRowAllowed).sort(compareActionRows).slice(0, 20);
      if (!shown.length) {
        list.innerHTML = '<li class="action-item action-empty">No upstream candidates for current repo filter.</li>';
        setActionCount('upstream-count', 0);
        return 0;
      }
      list.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('upstream-count', shown.length);
      return shown.length;
    }

    function render(data, source) {
      currentData = data;
      window.currentData = data;
      el('meta').textContent =
        `Generated: ${data.generated_at || 'n/a'} | repos: ${data.repo_count || 0} | transport: ${source}`;
      el('narrative').textContent = data.narrative || 'No narrative generated yet.';
      el('updates').textContent = (data.updates && data.updates.summary) ? data.updates.summary : 'No update summary';
      renderOverviewCards(data);
      refreshActionRepoFilter(data);
      const attentionCount = renderAttention(data);
      const agingCount = renderAging(data);
      renderRepoCards(data);
      const nextCount = renderNext(data);
      const upstreamCount = renderUpstream(data);
      renderActionSummary({
        attention: attentionCount,
        aging: agingCount,
        upstream: upstreamCount,
        next: nextCount,
      });
      refreshGraphSelector(data);
      drawGraph(data);
    }

    async function refreshHttp() {
      const res = await fetch('/api/status');
      const data = await res.json();
      render(data, 'http-poll');
    }

    function startPolling() {
      if (pollTimer) return;
      pollTimer = setInterval(() => refreshHttp().catch(() => {}), 10000);
    }

    function stopPolling() {
      if (!pollTimer) return;
      clearInterval(pollTimer);
      pollTimer = null;
    }

    function scheduleReconnect() {
      if (reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket();
      }, 2000);
    }

    function connectWebSocket() {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${window.location.host}/ws/status`;
      try {
        ws = new WebSocket(url);
      } catch (_err) {
        startPolling();
        scheduleReconnect();
        return;
      }

      ws.onopen = () => stopPolling();
      ws.onmessage = (event) => {
        try {
          render(JSON.parse(event.data), 'websocket');
        } catch (_err) {}
      };
      ws.onerror = () => {
        try { ws.close(); } catch (_err) {}
      };
      ws.onclose = () => {
        startPolling();
        scheduleReconnect();
      };
    }

    el('graph-repo').addEventListener('change', (event) => {
      selectedRepo = String(event.target.value || '');
      selectedNodeId = '';
      if (currentData) drawGraph(currentData);
    });
    el('action-repo-filter').addEventListener('change', (event) => {
      actionRepoFilter = String(event.target.value || '__all__');
      if (currentData) render(currentData, 'filtered');
    });
    el('action-sort').addEventListener('change', (event) => {
      actionSortMode = String(event.target.value || 'priority');
      if (currentData) render(currentData, 'filtered');
    });
    el('action-priority-filter').addEventListener('change', (event) => {
      actionPriorityFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('action-dirty-filter').addEventListener('change', (event) => {
      actionDirtyFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-sort').addEventListener('change', (event) => {
      repoSortMode = String(event.target.value || 'priority');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-health-filter').addEventListener('change', (event) => {
      repoHealthFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-dirty-filter').addEventListener('change', (event) => {
      repoDirtyFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-service-filter').addEventListener('change', (event) => {
      repoServiceFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('graph-mode').addEventListener('change', (event) => {
      graphMode = String(event.target.value || 'focus');
      selectedNodeId = '';
      if (currentData) drawGraph(currentData);
    });

    document.addEventListener('click', (event) => {
      const target = event.target;
      const copy = target && target.closest ? target.closest('[data-copy-prompt]') : null;
      if (copy) {
        const promptText = String(copy.getAttribute('data-copy-prompt') || '');
        if (promptText && navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(promptText).then(() => {
            copy.textContent = 'copied';
            setTimeout(() => { copy.textContent = 'copy prompt'; }, 900);
          }).catch(() => {});
        }
        return;
      }
      const btn = target && target.closest ? target.closest('[data-focus-repo]') : null;
      if (!btn) return;
      const repo = String(btn.getAttribute('data-focus-repo') || '');
      if (!repo) return;
      const scroll = String(btn.getAttribute('data-scroll-graph') || '') === '1';
      focusRepoInGraph(repo, scroll);
    });

    el('graph-zoom-in').addEventListener('click', () => zoomGraph(1.18));
    el('graph-zoom-out').addEventListener('click', () => zoomGraph(1 / 1.18));
    el('graph-zoom-reset').addEventListener('click', () => resetGraphView());

    const svg = el('graph');
    svg.addEventListener('wheel', (event) => {
      event.preventDefault();
      const delta = event.deltaY < 0 ? 1.08 : 0.92;
      zoomGraph(delta);
    }, { passive: false });

    svg.addEventListener('pointerdown', (event) => {
      const nodeEl = event.target && event.target.closest ? event.target.closest('[data-node-id]') : null;
      if (nodeEl) {
        selectedNodeId = String(nodeEl.getAttribute('data-node-id') || '');
        if (currentData) drawGraph(currentData);
        return;
      }
      graphView.drag = true;
      graphView.dragStartX = event.clientX;
      graphView.dragStartY = event.clientY;
      graphView.dragBaseX = graphView.tx;
      graphView.dragBaseY = graphView.ty;
      svg.classList.add('dragging');
      try { svg.setPointerCapture(event.pointerId); } catch (_err) {}
    });

    svg.addEventListener('pointermove', (event) => {
      if (!graphView.drag) return;
      graphView.tx = graphView.dragBaseX + (event.clientX - graphView.dragStartX);
      graphView.ty = graphView.dragBaseY + (event.clientY - graphView.dragStartY);
      if (currentData) drawGraph(currentData);
    });

    function endGraphDrag(event) {
      if (!graphView.drag) return;
      graphView.drag = false;
      svg.classList.remove('dragging');
      try { svg.releasePointerCapture(event.pointerId); } catch (_err) {}
    }
    svg.addEventListener('pointerup', endGraphDrag);
    svg.addEventListener('pointercancel', endGraphDrag);

    refreshHttp().catch(() => {});
    startPolling();
    connectWebSocket();
  </script>
</body>
</html>
"""


def run_service_foreground(
    *,
    project_dir: Path,
    workspace_root: Path,
    host: str,
    port: int,
    interval_seconds: int,
    include_updates: bool,
    max_next: int,
    ecosystem_toml: Path | None,
    central_repo: Path | None,
    execute_draft_prs: bool,
    draft_pr_title_prefix: str,
    supervise_services: bool,
    supervise_cooldown_seconds: int,
    supervise_max_starts: int,
) -> None:
    paths = service_paths(project_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["pid"].write_text(str(os.getpid()), encoding="utf-8")
    _write_json(
        paths["state"],
        {
            "started_at": _iso_now(),
            "host": host,
            "port": port,
            "interval_seconds": interval_seconds,
            "include_updates": include_updates,
            "max_next": max_next,
            "central_repo": str(central_repo) if central_repo else "",
            "execute_draft_prs": execute_draft_prs,
            "draft_pr_title_prefix": draft_pr_title_prefix,
            "supervise_services": supervise_services,
            "supervise_cooldown_seconds": max(1, int(supervise_cooldown_seconds)),
            "supervise_max_starts": max(1, int(supervise_max_starts)),
        },
    )

    stop_event = threading.Event()
    live_hub = LiveStreamHub(stop_event)

    def _collector_loop() -> None:
        while not stop_event.is_set():
            try:
                snapshot = write_snapshot_once(
                    project_dir=project_dir,
                    workspace_root=workspace_root,
                    ecosystem_toml=ecosystem_toml,
                    include_updates=include_updates,
                    max_next=max_next,
                    central_repo=central_repo,
                )
                if supervise_services:
                    repos_payload = snapshot.get("repos")
                    supervisor = supervise_repo_services(
                        repos_payload=repos_payload if isinstance(repos_payload, list) else [],
                        cooldown_seconds=max(1, int(supervise_cooldown_seconds)),
                        max_starts=max(1, int(supervise_max_starts)),
                    )
                else:
                    supervisor = {
                        "enabled": False,
                        "cooldown_seconds": max(1, int(supervise_cooldown_seconds)),
                        "max_starts_per_cycle": max(1, int(supervise_max_starts)),
                        "checked_repos": 0,
                        "restart_candidates": 0,
                        "attempted": 0,
                        "started": 0,
                        "failed": 0,
                        "cooldown_skipped": 0,
                        "last_tick_at": _iso_now(),
                        "attempts": [],
                    }
                snapshot["supervisor"] = supervisor
                _write_json(paths["snapshot"], snapshot)
                _write_json(paths["heartbeat"], {"last_tick_at": _iso_now(), "supervisor": supervisor})
                candidates = [
                    UpstreamCandidate(**row)
                    for row in snapshot.get("upstream_candidates", [])
                    if isinstance(row, dict)
                ]
                apply_upstream_automation(
                    service_dir=paths["dir"],
                    candidates=candidates,
                    title_prefix=draft_pr_title_prefix,
                    execute_draft_prs=execute_draft_prs,
                )
                live_hub.broadcast_snapshot(snapshot)
            except Exception as exc:
                _write_json(paths["heartbeat"], {"last_tick_at": _iso_now(), "error": str(exc)})
            stop_event.wait(max(2, interval_seconds))

    collector = threading.Thread(target=_collector_loop, name="ecosystem-hub-collector", daemon=True)
    collector.start()

    handler_cls = _handler_factory(paths["snapshot"], paths["state"], live_hub)
    server = ThreadingHTTPServer((host, port), handler_cls)

    def _graceful_shutdown(_signum: int, _frame: Any) -> None:
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()
        try:
            paths["pid"].unlink(missing_ok=True)
        except Exception:
            pass


def start_service_process(
    *,
    project_dir: Path,
    workspace_root: Path,
    host: str,
    port: int,
    interval_seconds: int,
    include_updates: bool,
    max_next: int,
    ecosystem_toml: Path | None,
    central_repo: Path | None,
    execute_draft_prs: bool,
    draft_pr_title_prefix: str,
    supervise_services: bool = True,
    supervise_cooldown_seconds: int = _SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
    supervise_max_starts: int = _SUPERVISOR_DEFAULT_MAX_STARTS,
) -> dict[str, Any]:
    status = read_service_status(project_dir)
    if status.get("running"):
        return status

    paths = service_paths(project_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    log_f = open(paths["log"], "a", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "driftdriver.ecosystem_hub",
        "--project-dir",
        str(project_dir),
        "--workspace-root",
        str(workspace_root),
    ]
    if central_repo:
        cmd.extend(["--central-repo", str(central_repo)])
    cmd.extend(
        [
        "run-service",
        "--host",
        host,
        "--port",
        str(port),
        "--interval-seconds",
        str(interval_seconds),
        "--max-next",
        str(max_next),
        "--title-prefix",
        draft_pr_title_prefix,
        "--supervise-cooldown-seconds",
        str(max(1, int(supervise_cooldown_seconds))),
        "--supervise-max-starts",
        str(max(1, int(supervise_max_starts))),
        ]
    )
    if not supervise_services:
        cmd.append("--no-supervise-services")
    if not include_updates:
        cmd.append("--skip-updates")
    if ecosystem_toml:
        cmd.extend(["--ecosystem-toml", str(ecosystem_toml)])
    if execute_draft_prs:
        cmd.append("--execute-draft-prs")
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[1])
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{package_root}:{existing_pp}" if existing_pp else package_root

    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(project_dir),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    _CHILD_PROCS[proc.pid] = proc
    log_f.close()
    # Child writes pid too; this ensures there is immediate visibility.
    paths["pid"].write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.25)
    return read_service_status(project_dir)


def stop_service_process(project_dir: Path) -> dict[str, Any]:
    paths = service_paths(project_dir)
    status = read_service_status(project_dir)
    pid = status.get("pid")
    if not pid:
        return status
    pid_i = int(pid)
    try:
        os.kill(pid_i, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _process_alive(pid_i):
            break
        time.sleep(0.1)
    if _process_alive(pid_i):
        try:
            os.kill(pid_i, signal.SIGKILL)
        except OSError:
            pass
    proc = _CHILD_PROCS.pop(pid_i, None)
    if proc is not None:
        try:
            proc.wait(timeout=1.0)
        except Exception:
            pass
    try:
        paths["pid"].unlink(missing_ok=True)
    except Exception:
        pass
    return read_service_status(project_dir)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ecosystem-hub")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="Project repo root (default: cwd)")
    p.add_argument(
        "--workspace-root",
        default="",
        help="Workspace root containing speedrift repos (default: parent of project-dir)",
    )
    p.add_argument(
        "--ecosystem-toml",
        default="",
        help="Path to ecosystem.toml (default: <workspace-root>/speedrift-ecosystem/ecosystem.toml)",
    )
    p.add_argument(
        "--central-repo",
        default="",
        help="Optional central register/report repo path (default: derived from drift-policy reporting.central_repo)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    once = sub.add_parser("once", help="Collect one ecosystem snapshot and print JSON")
    once.add_argument("--skip-updates", action="store_true", help="Skip remote update checks for this run")
    once.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    once.add_argument("--write", default="", help="Optional file path for writing snapshot JSON")

    start = sub.add_parser("start", help="Start daemonized ecosystem hub service")
    start.add_argument("--host", default="127.0.0.1", help="Bind host for web server")
    start.add_argument("--port", type=int, default=8777, help="Bind port for web server")
    start.add_argument("--interval-seconds", type=int, default=60, help="Snapshot refresh interval")
    start.add_argument("--skip-updates", action="store_true", help="Skip remote update checks while running")
    start.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    start.add_argument("--execute-draft-prs", action="store_true", help="Execute draft PR creation each cycle")
    start.add_argument("--title-prefix", default="speedrift", help="Title prefix for draft PR automation")
    start.add_argument(
        "--no-supervise-services",
        action="store_true",
        help="Disable central supervision/restart of stopped repo workgraph services",
    )
    start.add_argument(
        "--supervise-cooldown-seconds",
        type=int,
        default=_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
        help="Minimum seconds between restart attempts per repo",
    )
    start.add_argument(
        "--supervise-max-starts",
        type=int,
        default=_SUPERVISOR_DEFAULT_MAX_STARTS,
        help="Maximum repo service start attempts per collector cycle",
    )

    run_service = sub.add_parser("run-service", help="Internal: run service in foreground")
    run_service.add_argument("--host", default="127.0.0.1", help="Bind host for web server")
    run_service.add_argument("--port", type=int, default=8777, help="Bind port for web server")
    run_service.add_argument("--interval-seconds", type=int, default=60, help="Snapshot refresh interval")
    run_service.add_argument("--skip-updates", action="store_true", help="Skip remote update checks while running")
    run_service.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    run_service.add_argument("--execute-draft-prs", action="store_true", help="Execute draft PR creation each cycle")
    run_service.add_argument("--title-prefix", default="speedrift", help="Title prefix for draft PR automation")
    run_service.add_argument(
        "--no-supervise-services",
        action="store_true",
        help="Disable central supervision/restart of stopped repo workgraph services",
    )
    run_service.add_argument(
        "--supervise-cooldown-seconds",
        type=int,
        default=_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
        help="Minimum seconds between restart attempts per repo",
    )
    run_service.add_argument(
        "--supervise-max-starts",
        type=int,
        default=_SUPERVISOR_DEFAULT_MAX_STARTS,
        help="Maximum repo service start attempts per collector cycle",
    )

    automate = sub.add_parser("automate", help="Ensure unattended automation is running (start if needed)")
    automate.add_argument("--host", default="127.0.0.1", help="Bind host for web server")
    automate.add_argument("--port", type=int, default=8777, help="Bind port for web server")
    automate.add_argument("--interval-seconds", type=int, default=60, help="Snapshot refresh interval")
    automate.add_argument("--skip-updates", action="store_true", help="Skip remote update checks while running")
    automate.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    automate.add_argument("--execute-draft-prs", action="store_true", help="Execute draft PR creation each cycle")
    automate.add_argument("--title-prefix", default="speedrift", help="Title prefix for draft PR automation")
    automate.add_argument(
        "--no-supervise-services",
        action="store_true",
        help="Disable central supervision/restart of stopped repo workgraph services",
    )
    automate.add_argument(
        "--supervise-cooldown-seconds",
        type=int,
        default=_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
        help="Minimum seconds between restart attempts per repo",
    )
    automate.add_argument(
        "--supervise-max-starts",
        type=int,
        default=_SUPERVISOR_DEFAULT_MAX_STARTS,
        help="Maximum repo service start attempts per collector cycle",
    )

    sub.add_parser("status", help="Show daemon status")
    sub.add_parser("stop", help="Stop daemonized ecosystem hub service")

    packets = sub.add_parser("upstream-report", help="Write markdown packet of upstream contribution candidates")
    packets.add_argument("--output", default="", help="Output markdown path (default: stdout)")

    pr_open = sub.add_parser(
        "open-draft-pr",
        help="Prepare or execute draft PRs from detected upstream candidates (dry-run by default)",
    )
    pr_open.add_argument("--repo", default="", help="Filter to a single repo name")
    pr_open.add_argument("--title-prefix", default="speedrift", help="Prefix for draft PR titles")
    pr_open.add_argument(
        "--execute",
        action="store_true",
        help="Actually run gh pr create (default is dry-run output only)",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    project_dir = Path(args.project_dir).resolve()
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else project_dir.parent
    ecosystem_toml = Path(args.ecosystem_toml).resolve() if args.ecosystem_toml else None
    central_repo = resolve_central_repo_path(project_dir, explicit_path=str(args.central_repo))

    if args.cmd == "once":
        snapshot = write_snapshot_once(
            project_dir=project_dir,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            central_repo=central_repo,
        )
        blob = json.dumps(snapshot, indent=2, sort_keys=False)
        if args.write:
            out = Path(args.write).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(blob + "\n", encoding="utf-8")
        print(blob)
        return 0

    if args.cmd == "start":
        status = start_service_process(
            project_dir=project_dir,
            workspace_root=workspace_root,
            host=str(args.host),
            port=int(args.port),
            interval_seconds=max(2, int(args.interval_seconds)),
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=bool(args.execute_draft_prs),
            draft_pr_title_prefix=str(args.title_prefix),
            supervise_services=not bool(args.no_supervise_services),
            supervise_cooldown_seconds=max(1, int(args.supervise_cooldown_seconds)),
            supervise_max_starts=max(1, int(args.supervise_max_starts)),
        )
        print(json.dumps(status, indent=2, sort_keys=False))
        return 0

    if args.cmd == "run-service":
        run_service_foreground(
            project_dir=project_dir,
            workspace_root=workspace_root,
            host=str(args.host),
            port=int(args.port),
            interval_seconds=max(2, int(args.interval_seconds)),
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=bool(args.execute_draft_prs),
            draft_pr_title_prefix=str(args.title_prefix),
            supervise_services=not bool(args.no_supervise_services),
            supervise_cooldown_seconds=max(1, int(args.supervise_cooldown_seconds)),
            supervise_max_starts=max(1, int(args.supervise_max_starts)),
        )
        return 0

    if args.cmd == "automate":
        status = start_service_process(
            project_dir=project_dir,
            workspace_root=workspace_root,
            host=str(args.host),
            port=int(args.port),
            interval_seconds=max(2, int(args.interval_seconds)),
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=bool(args.execute_draft_prs),
            draft_pr_title_prefix=str(args.title_prefix),
            supervise_services=not bool(args.no_supervise_services),
            supervise_cooldown_seconds=max(1, int(args.supervise_cooldown_seconds)),
            supervise_max_starts=max(1, int(args.supervise_max_starts)),
        )
        print(json.dumps({"automated": True, "status": status}, indent=2, sort_keys=False))
        return 0

    if args.cmd == "status":
        print(json.dumps(read_service_status(project_dir), indent=2, sort_keys=False))
        return 0

    if args.cmd == "stop":
        print(json.dumps(stop_service_process(project_dir), indent=2, sort_keys=False))
        return 0

    if args.cmd == "upstream-report":
        snapshot = collect_ecosystem_snapshot(
            project_dir=project_dir,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            include_updates=False,
            max_next=3,
            central_repo=central_repo,
        )
        candidates = [
            UpstreamCandidate(**row)
            for row in snapshot.get("upstream_candidates", [])
            if isinstance(row, dict)
        ]
        md = render_upstream_packets(candidates)
        if args.output:
            out = Path(args.output).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        else:
            print(md, end="")
        return 0

    if args.cmd == "open-draft-pr":
        snapshot = collect_ecosystem_snapshot(
            project_dir=project_dir,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            include_updates=False,
            max_next=3,
            central_repo=central_repo,
        )
        candidates = [
            UpstreamCandidate(**row)
            for row in snapshot.get("upstream_candidates", [])
            if isinstance(row, dict)
        ]
        if args.repo:
            candidates = [c for c in candidates if c.repo == str(args.repo)]
        requests = build_draft_pr_requests(candidates, title_prefix=str(args.title_prefix))
        result = {
            "execute": bool(args.execute),
            "request_count": len(requests),
            "requests": run_draft_pr_requests(requests, execute=bool(args.execute)),
        }
        print(json.dumps(result, indent=2, sort_keys=False))
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
