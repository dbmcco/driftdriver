# ABOUTME: Service detection and control helpers for the ecosystem hub.
# ABOUTME: Detects workgraph, launchd, and cron services per repo. No shell=True.
from __future__ import annotations

import logging
import plistlib
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


# ---------------------------------------------------------------------------
# Low-level subprocess helpers — these are the only places subprocesses fire.
# All callers use these functions; tests patch them directly.
# ---------------------------------------------------------------------------

def _run_wg_service_status(repo_path: str) -> str:
    """Run `wg service status` and return 'running' or 'stopped'."""
    try:
        result = subprocess.run(  # noqa: S603
            ["wg", "service", "status"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        combined = (result.stdout + result.stderr).lower()
        if result.returncode == 0 and "running" in combined:
            return "running"
        return "stopped"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "stopped"


def _run_launchctl_list() -> dict[str, int | None]:
    """Run `launchctl list` and return parsed {label: pid_or_None} dict."""
    try:
        result = subprocess.run(  # noqa: S603
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return _parse_launchctl_list(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}


def _run_crontab_l(repo_path: str) -> list[str]:
    """Run `crontab -l` and return lines matching repo_path."""
    try:
        result = subprocess.run(  # noqa: S603
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        return [
            line for line in result.stdout.splitlines()
            if repo_path in line and not line.strip().startswith("#")
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_launchctl_list(output: str) -> dict[str, int | None]:
    """Parse `launchctl list` stdout into {label: pid_or_None}."""
    result: dict[str, int | None] = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        pid_str, _exit, label = parts
        if label in ("Label",):  # skip header
            continue
        try:
            result[label] = int(pid_str)
        except ValueError:
            result[label] = None  # pid_str is "-"
    return result


# ---------------------------------------------------------------------------
# Plist scanning
# ---------------------------------------------------------------------------

def _find_launchd_plists(
    repo_name: str,
    repo_path: str,
    *,
    launch_agents_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scan LaunchAgents for plist files belonging to repo_name/repo_path."""
    agents_dir = launch_agents_dir or _DEFAULT_LAUNCH_AGENTS
    if not agents_dir.is_dir():
        return []

    # Normalize repo name for filename heuristic: paia-shell -> paia.shell
    norm_name = repo_name.replace("-", ".").lower()
    results: list[dict[str, Any]] = []

    for plist_file in agents_dir.glob("*.plist"):
        try:
            with open(plist_file, "rb") as fp:
                data = plistlib.load(fp)
        except Exception:
            log.debug("Skipping malformed plist: %s", plist_file)
            continue

        label = str(data.get("Label") or "")
        working_dir = str(data.get("WorkingDirectory") or "")

        # Strategy 1: exact WorkingDirectory match
        matched = working_dir == repo_path

        # Strategy 2: filename heuristic
        if not matched:
            stem = plist_file.stem.lower()  # e.g. "com.paia.shell"
            if norm_name and norm_name in stem:
                matched = True

        if matched:
            results.append({
                "label": label,
                "plist_path": str(plist_file),
            })

    return results


# ---------------------------------------------------------------------------
# Path validation for launchd POST endpoints
# ---------------------------------------------------------------------------

def _validate_plist_path(
    plist_path: str,
    *,
    launch_agents_dir: Path | None = None,
) -> bool:
    """Return True if plist_path is a real file inside LaunchAgents dir."""
    agents_dir = launch_agents_dir or _DEFAULT_LAUNCH_AGENTS
    try:
        resolved = Path(plist_path).resolve()
        agents_resolved = agents_dir.resolve()
        return (
            resolved.exists()
            and resolved.is_file()
            and resolved.parts[: len(agents_resolved.parts)] == agents_resolved.parts
        )
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main detection function
# ---------------------------------------------------------------------------

def detect_services(
    repo_name: str,
    repo_path: str,
    *,
    launch_agents_dir: Path | None = None,
) -> dict[str, Any]:
    """Detect all services (workgraph, launchd, cron) for a repo."""
    # --- Workgraph ---
    wg_dir = Path(repo_path) / ".workgraph"
    if wg_dir.is_dir():
        wg_status = _run_wg_service_status(repo_path)
        workgraph: dict[str, Any] = {"present": True, "status": wg_status}
    else:
        workgraph = {"present": False, "status": None}

    # --- launchd ---
    launchctl_map = _run_launchctl_list()
    raw_plists = _find_launchd_plists(
        repo_name, repo_path, launch_agents_dir=launch_agents_dir
    )
    launchd_services: list[dict[str, Any]] = []
    for plist_entry in raw_plists:
        label = plist_entry["label"]
        pid = launchctl_map.get(label)
        if label in launchctl_map:
            status = "running" if pid is not None else "stopped"
        else:
            status = "stopped"
            pid = None
        launchd_services.append({
            "label": label,
            "plist_path": plist_entry["plist_path"],
            "status": status,
            "pid": pid,
        })

    # --- Cron ---
    cron_lines = _run_crontab_l(repo_path)

    return {
        "repo": repo_name,
        "workgraph": workgraph,
        "launchd": launchd_services,
        "cron": {"jobs": cron_lines},
    }
