# ABOUTME: Ecosystem-wide service manifest — all LaunchAgent plists grouped by repo.
# ABOUTME: Cross-references enrolled repos in snapshot with live launchctl status.
from __future__ import annotations

import plistlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
_PROJECT_PREFIXES = (
    "com.braydon.",
    "com.paia.",
    "com.lfw.",
    "com.synthyra.",
    "com.speedrift.",
    "com.training.",
    "com.vibez-monitor.",
    "com.dbmcco.",
    "com.dossier",
)


def _launchctl_snapshot() -> dict[str, dict[str, Any]]:
    """Return {label: {pid, exit_code, loaded}} from `launchctl list`."""
    result: dict[str, dict[str, Any]] = {}
    try:
        out = subprocess.run(  # noqa: S603
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return result
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or parts[2] == "Label":
            continue
        pid_str, exit_str, label = parts
        try:
            pid: int | None = int(pid_str)
        except ValueError:
            pid = None
        try:
            exit_code: int | None = int(exit_str)
        except ValueError:
            exit_code = None
        result[label] = {"pid": pid, "exit_code": exit_code, "loaded": True}
    return result


def _read_plist(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)  # type: ignore[return-value]
    except Exception:
        return {}


def _service_status(label: str, lctl: dict[str, dict[str, Any]]) -> str:
    if label not in lctl:
        return "not_loaded"
    entry = lctl[label]
    if entry["pid"] is not None:
        return "running"
    return "stopped"


def _plist_flags(
    label: str,
    working_dir: str,
    status: str,
    exit_code: int | None,
    enrolled_paths: set[str],
    all_working_dirs: dict[str, list[str]],
) -> list[str]:
    flags: list[str] = []
    if working_dir and working_dir not in enrolled_paths:
        flags.append("orphaned")
    if working_dir and len(all_working_dirs.get(working_dir, [])) > 1:
        flags.append("duplicate_wd")
    if status == "stopped" and exit_code not in (None, 0, -15, 143):
        flags.append("error_exit")
    if status == "not_loaded":
        flags.append("not_loaded")
    return flags


def build_services_manifest(
    snapshot: dict[str, Any],
    *,
    launch_agents_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a full ecosystem-wide service manifest."""
    agents_dir = launch_agents_dir or _LAUNCH_AGENTS
    lctl = _launchctl_snapshot()

    # Index enrolled repos by path and name
    repos: list[dict[str, Any]] = snapshot.get("repos") or []
    enrolled_by_path: dict[str, dict[str, Any]] = {}
    enrolled_by_name: dict[str, dict[str, Any]] = {}
    for r in repos:
        if not isinstance(r, dict):
            continue
        p = str(r.get("path") or "").rstrip("/")
        n = str(r.get("name") or "")
        if p:
            enrolled_by_path[p] = r
        if n:
            enrolled_by_name[n] = r
    enrolled_paths = set(enrolled_by_path.keys())

    # Scan all project plists
    all_plists: list[dict[str, Any]] = []
    for plist_file in sorted(agents_dir.glob("*.plist")):
        if not any(plist_file.name.startswith(p.rstrip(".")) or plist_file.stem.startswith(p.rstrip("."))
                   for p in _PROJECT_PREFIXES):
            # also match com.dossier exactly
            if plist_file.stem not in ("com.dossier",):
                continue
        data = _read_plist(plist_file)
        label = str(data.get("Label") or plist_file.stem)
        working_dir = str(data.get("WorkingDirectory") or "").rstrip("/")
        prog_args: list[str] = data.get("ProgramArguments") or []
        program = str(data.get("Program") or (prog_args[0] if prog_args else ""))
        keep_alive = bool(data.get("KeepAlive") or data.get("KeepAlive") == {})
        run_at_load = bool(data.get("RunAtLoad"))
        lctl_entry = lctl.get(label, {})
        status = _service_status(label, lctl)
        exit_code = lctl_entry.get("exit_code")
        pid = lctl_entry.get("pid")
        all_plists.append({
            "label": label,
            "plist_path": str(plist_file),
            "program": program,
            "working_dir": working_dir,
            "keep_alive": keep_alive,
            "run_at_load": run_at_load,
            "status": status,
            "pid": pid,
            "exit_code": exit_code,
        })

    # Build working_dir → [labels] index to detect duplicates
    wd_to_labels: dict[str, list[str]] = {}
    for svc in all_plists:
        wd = svc["working_dir"]
        if wd:
            wd_to_labels.setdefault(wd, []).append(svc["label"])

    # Enrich with flags and match to enrolled repos
    # Group: enrolled repos + unmatched plists
    repo_map: dict[str, dict[str, Any]] = {}  # path -> {repo_info, services:[]}

    # Pre-seed with all enrolled repos so even repos with no plist appear
    for path, repo in enrolled_by_path.items():
        repo_map[path] = {
            "repo": str(repo.get("name") or ""),
            "path": path,
            "enrolled": True,
            "services": [],
        }

    unmatched: list[dict[str, Any]] = []

    for svc in all_plists:
        wd = svc["working_dir"]
        flags = _plist_flags(
            svc["label"], wd, svc["status"],
            svc["exit_code"], enrolled_paths, wd_to_labels,
        )
        enriched = {**svc, "flags": flags}

        if wd and wd in enrolled_by_path:
            repo_map[wd]["services"].append(enriched)
        else:
            # Try name-based match: com.paia.shell -> paia-shell
            matched = False
            label_parts = svc["label"].split(".")
            if len(label_parts) >= 3:
                candidate = label_parts[2].replace("-", "")
                for name, repo in enrolled_by_name.items():
                    if name.replace("-", "") == candidate:
                        rpath = str(repo.get("path") or "").rstrip("/")
                        if rpath in repo_map:
                            repo_map[rpath]["services"].append(enriched)
                        else:
                            repo_map[rpath] = {
                                "repo": name, "path": rpath,
                                "enrolled": True, "services": [enriched],
                            }
                        matched = True
                        break
            if not matched:
                unmatched.append(enriched)

    # Sort repo groups: enrolled with services first, then by name
    by_repo = sorted(
        repo_map.values(),
        key=lambda r: (0 if r["services"] else 1, r["repo"]),
    )

    # Summary counts
    all_svcs = all_plists
    n_running = sum(1 for s in all_svcs if s["status"] == "running")
    n_stopped = sum(1 for s in all_svcs if s["status"] == "stopped")
    n_not_loaded = sum(1 for s in all_svcs if s["status"] == "not_loaded")
    n_issues = sum(
        1 for s in all_svcs
        if s["status"] in ("stopped", "not_loaded")
        and s.get("keep_alive") or s.get("run_at_load")
        and s["exit_code"] not in (None, 0, -15, 143)
    )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "by_repo": by_repo,
        "unmatched": unmatched,
        "summary": {
            "total": len(all_svcs),
            "running": n_running,
            "stopped": n_stopped,
            "not_loaded": n_not_loaded,
            "issues": n_issues,
        },
    }


def audit_services(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a structured audit: orphans, duplicates, stopped keep-alives, not-loaded."""
    manifest = build_services_manifest(snapshot)

    orphaned: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    stopped_keep_alive: list[dict[str, Any]] = []
    not_loaded: list[dict[str, Any]] = []
    repos_no_plist: list[str] = []

    for group in manifest["by_repo"]:
        if group["enrolled"] and not group["services"]:
            repos_no_plist.append(group["repo"])
        for svc in group["services"]:
            flags = svc.get("flags") or []
            if "orphaned" in flags:
                orphaned.append({"label": svc["label"], "working_dir": svc["working_dir"]})
            if "duplicate_wd" in flags:
                duplicates.append({
                    "label": svc["label"],
                    "working_dir": svc["working_dir"],
                    "repo": group["repo"],
                })
            if svc["status"] in ("stopped", "not_loaded") and svc.get("keep_alive"):
                if svc["status"] == "not_loaded":
                    not_loaded.append({"label": svc["label"], "repo": group["repo"]})
                else:
                    stopped_keep_alive.append({
                        "label": svc["label"],
                        "repo": group["repo"],
                        "exit_code": svc.get("exit_code"),
                    })

    for svc in manifest.get("unmatched") or []:
        flags = svc.get("flags") or []
        if "orphaned" in flags:
            orphaned.append({"label": svc["label"], "working_dir": svc["working_dir"]})

    return {
        "summary": manifest["summary"],
        "orphaned_plists": orphaned,
        "duplicate_working_dirs": duplicates,
        "stopped_keep_alive": stopped_keep_alive,
        "not_loaded_keep_alive": not_loaded,
        "enrolled_repos_without_plist": repos_no_plist,
    }
