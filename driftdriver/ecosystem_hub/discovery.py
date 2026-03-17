# ABOUTME: Repo scanning, peer detection, workspace discovery, and upstream candidate logic.
# ABOUTME: Loads ecosystem.toml, discovers active repos, generates upstream contribution candidates.
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim
from driftdriver.policy import load_drift_policy
from driftdriver.updates import (
    check_ecosystem_updates,
    load_review_config,
    summarize_updates,
)

from .models import DraftPRRequest, UpstreamCandidate

_STALE_OPEN_DAYS = 14.0
_STALE_IN_PROGRESS_DAYS = 3.0
_DISCOVERY_ACTIVE_DAYS = 30.0
_DISCOVERY_MAX_REPOS = 0
_NORTH_STAR_MAX_BYTES = 160_000


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


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 8.0,
) -> tuple[int, str, str]:
    def _invoke(actual_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            actual_cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        proc = _invoke(cmd)
    except FileNotFoundError as exc:
        if cmd and str(cmd[0]) == "wg":
            candidates = [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]
            users_root = Path("/Users")
            if users_root.exists():
                for discovered in users_root.glob("*/.cargo/bin/wg"):
                    candidates.append(str(discovered))
            seen: set[str] = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                if not Path(candidate).exists():
                    continue
                try:
                    proc = _invoke([candidate, *cmd[1:]])
                    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
                except FileNotFoundError:
                    continue
        return 127, "", str(exc)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


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


def _path_age_seconds(path: Path) -> int | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return max(0, int(time.time() - mtime))


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _service_port_alive(host: str, port: int) -> bool:
    import socket

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


def _read_small_text(path: Path) -> str:
    try:
        if path.stat().st_size > _NORTH_STAR_MAX_BYTES:
            return ""
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def resolve_central_repo_path(project_dir: Path, explicit_path: str = "") -> Path | None:
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()
    env_path = str(os.environ.get("ECOSYSTEM_HUB_CENTRAL_REPO", "") or "").strip()
    if env_path:
        return Path(env_path).expanduser().resolve()
    wg_dir = project_dir / ".workgraph"
    try:
        policy = load_drift_policy(wg_dir)
    except Exception:
        policy = None
    raw = str(getattr(policy, "reporting_central_repo", "") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    workspace_root = project_dir.parent
    if project_dir.name == "speedrift-ecosystem":
        return (project_dir / ".workgraph" / "service" / "ecosystem-central").resolve()
    sibling = workspace_root / "speedrift-ecosystem"
    if sibling.exists():
        return (sibling / ".workgraph" / "service" / "ecosystem-central").resolve()
    return None


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
    limit = max(0, max_extra)
    selected = discovered if limit == 0 else discovered[:limit]
    for _age_days, name, path in selected:
        out[name] = path
    return out


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
    for name, value in repos.items():
        key = str(name).strip()
        if not key:
            continue
        if isinstance(value, dict):
            path_raw = str(value.get("path") or "").strip()
            if path_raw:
                candidate = Path(path_raw).expanduser()
                if not candidate.is_absolute():
                    candidate = (ecosystem_toml.parent / candidate).resolve()
                out[key] = candidate
                continue
        out[key] = workspace_root / key
    return out


def _north_star_candidate_paths(repo_path: Path) -> list[Path]:
    candidates: list[Path] = []
    direct = [
        repo_path / "NORTH_STAR.md",
        repo_path / "north-star.md",
        repo_path / "README.md",
        repo_path / "STATUS.md",
        repo_path / "PROJECT_STATUS.md",
        repo_path / "ROADMAP.md",
        repo_path / "ROADMAP_CHECKLIST.md",
        repo_path / "docs" / "NORTH_STAR.md",
        repo_path / "docs" / "north-star.md",
        repo_path / "docs" / "ROADMAP.md",
        repo_path / "docs" / "ROADMAP_CHECKLIST.md",
    ]
    candidates.extend(path for path in direct if path.exists() and path.is_file())

    patterns = (
        "*north*star*.md",
        "*roadmap*.md",
        "docs/*north*star*.md",
        "docs/*roadmap*.md",
        "docs/plans/*north*star*.md",
        "docs/plans/*roadmap*.md",
        "docs/specs/*north*star*.md",
    )
    for pattern in patterns:
        for path in sorted(repo_path.glob(pattern)):
            if path.is_file():
                candidates.append(path)

    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(path)
    return ordered[:24]


def _extract_north_star_summary(text: str, start_idx: int) -> str:
    tail = text[start_idx:].splitlines()
    buf: list[str] = []
    for line in tail:
        stripped = line.strip()
        if not stripped:
            if buf:
                break
            continue
        if stripped.startswith("#"):
            if buf:
                break
            continue
        if stripped.startswith(("-", "*")):
            stripped = stripped.lstrip("-* ").strip()
        buf.append(stripped)
        if len(" ".join(buf)) >= 240:
            break
    summary = " ".join(buf).strip()
    return summary[:280]


def _collect_repo_north_star(repo_path: Path) -> dict[str, Any]:
    result = {
        "present": False,
        "status": "missing",
        "canonical": False,
        "approved": False,
        "source_path": "",
        "title": "",
        "summary": "",
        "confidence": "low",
        "signals": [],
    }

    heading_re = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.*north star.*)$", re.IGNORECASE | re.MULTILINE)
    mention_re = re.compile(r"(?im)^\s*(?:\*\*)?north star(?:\*\*)?\s*[:\-]")
    best: dict[str, Any] | None = None

    for path in _north_star_candidate_paths(repo_path):
        text = _read_small_text(path)
        if not text:
            continue
        rel = str(path.relative_to(repo_path))
        lower_rel = rel.lower()
        heading = heading_re.search(text)
        mention = mention_re.search(text)
        if not heading and not mention:
            continue

        title = ""
        summary = ""
        score = 1
        confidence = "medium"
        if heading:
            title = str(heading.group("title") or "").strip()
            summary = _extract_north_star_summary(text, heading.end())
            score = 2
        elif mention:
            title = "North Star"
            summary = _extract_north_star_summary(text, mention.end())

        canonical = False
        if "north_star" in lower_rel or "north-star" in lower_rel:
            score += 2
            canonical = True
            confidence = "high"
        elif Path(rel).name.lower() in {"readme.md", "roadmap.md", "roadmap_checklist.md", "project_status.md", "status.md"}:
            score += 1
            canonical = True

        row = {
            "present": True,
            "status": "present" if canonical else "weak",
            "canonical": canonical,
            "approved": canonical,
            "source_path": rel,
            "title": title or "North Star",
            "summary": summary,
            "confidence": confidence,
            "signals": ["heading" if heading else "mention"],
            "_score": score,
        }
        if best is None or int(row["_score"]) > int(best["_score"]):
            best = row

    if best is None:
        result["signals"] = ["no canonical north star signal in README/docs/plans"]
        return result

    best.pop("_score", None)
    return best


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
        wg_dir = Path(req.repo_path) / ".workgraph"
        log = DirectiveLog(wg_dir / "directives")
        shim = ExecutorShim(wg_dir=wg_dir, log=log, timeout=30.0)

        directive = Directive(
            source="discovery",
            repo=req.repo,
            action=Action.CREATE_UPSTREAM_PR,
            params={
                "repo": req.repo_path,
                "title": req.title,
                "body": req.body,
                "base": req.base,
                "head": req.branch,
            },
            reason="upstream contribution detected",
        )
        status = shim.execute(directive)
        results.append(
            {
                "repo": req.repo,
                "repo_path": req.repo_path,
                "ok": status == "completed",
                "directive_id": directive.id,
                "directive_status": status,
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
