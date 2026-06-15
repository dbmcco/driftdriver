from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from driftdriver.install import refresh_existing_managed_surfaces


RefreshFn = Callable[[Path, Path], dict[str, bool]]

STATE_RELATIVE_PATH = Path(".driftdriver") / "speedrift-auto-update.json"
IGNORED_DIRS = {
    ".git",
    ".workgraph",
    ".wg",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
}


def _disabled_by_environment() -> bool:
    raw = os.environ.get("DRIFTDRIVER_DISABLE_SPEEDRIFT_AUTO_UPDATE", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _state_path(wg_dir: Path) -> Path:
    return wg_dir / STATE_RELATIVE_PATH


def _git_signature(project_dir: Path) -> dict[str, Any] | None:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(project_dir),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=str(project_dir),
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if head.returncode != 0 or status.returncode != 0:
        return None

    return {
        "mode": "git",
        "head": head.stdout.strip(),
        "status_hash": hashlib.sha256(status.stdout.encode("utf-8")).hexdigest(),
    }


def _filesystem_signature(project_dir: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        root_path = Path(root)
        for name in sorted(files):
            path = root_path / name
            try:
                stat = path.stat()
                relative = path.relative_to(project_dir)
            except OSError:
                continue
            digest.update(str(relative).encode("utf-8", errors="replace"))
            digest.update(b"\0")
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(stat.st_mtime_ns).encode("ascii"))
            digest.update(b"\0")
    return {"mode": "filesystem", "hash": digest.hexdigest()}


def repo_change_signature(project_dir: Path) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    return _git_signature(project_dir) or _filesystem_signature(project_dir)


def _load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def auto_update_for_repo_changes(
    project_dir: Path,
    wg_dir: Path,
    *,
    refresher: RefreshFn = refresh_existing_managed_surfaces,
) -> dict[str, Any]:
    """Refresh Speedrift-managed guidance when the repository has changed.

    This is intentionally narrower than `driftdriver install`: it only refreshes
    surfaces that already contain Driftdriver-managed markers or existing hook
    files. It lets ordinary Speedrift checks keep agent guidance current without
    opting a repo into new integration surfaces.
    """

    if _disabled_by_environment():
        return {
            "enabled": False,
            "changed": False,
            "refreshed": False,
            "skipped_reason": "disabled_by_environment",
        }

    project_dir = project_dir.resolve()
    wg_dir = wg_dir.resolve()
    state_path = _state_path(wg_dir)
    signature = repo_change_signature(project_dir)
    state = _load_state(state_path)

    if state.get("signature") == signature:
        return {
            "enabled": True,
            "changed": False,
            "refreshed": False,
            "signature": signature,
            "state_path": str(state_path),
        }

    refresh_result = refresher(project_dir, wg_dir)
    refreshed = any(bool(value) for value in refresh_result.values())
    final_signature = repo_change_signature(project_dir)
    payload = {
        "signature": final_signature,
        "refresh_result": refresh_result,
    }
    _write_state(state_path, payload)

    return {
        "enabled": True,
        "changed": True,
        "refreshed": refreshed,
        "signature": final_signature,
        "previous_signature": signature,
        "state_path": str(state_path),
        "refresh_result": refresh_result,
    }
