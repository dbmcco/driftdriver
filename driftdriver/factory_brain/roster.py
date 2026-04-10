# ABOUTME: Factory roster — tracks enrolled repos, supports discovery, handles enrollment lifecycle.
# ABOUTME: Persists roster to JSON, scans workspaces for .workgraph dirs, preserves unenrollment history.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.paia_topology import is_noncanonical_paia_repo


@dataclass
class Roster:
    """Tracks all repos known to the factory brain."""

    repos: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_roster(roster_file: Path) -> Roster:
    """Load roster from JSON. Return empty Roster if missing or corrupt."""
    try:
        data = json.loads(roster_file.read_text())
        return Roster(repos=data.get("repos", {}))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return Roster()


def save_roster(roster: Roster, roster_file: Path) -> None:
    """Persist roster to JSON."""
    roster_file.parent.mkdir(parents=True, exist_ok=True)
    roster_file.write_text(json.dumps({"repos": roster.repos}, indent=2) + "\n")


def enroll_repo(roster: Roster, *, path: str, target: str) -> str:
    """Add a repo to the roster. Returns the repo name (directory basename)."""
    name = Path(path).name
    roster.repos[name] = {
        "path": path,
        "target": target,
        "status": "active",
        "enrolled_at": datetime.now(timezone.utc).isoformat(),
    }
    return name


def unenroll_repo(roster: Roster, *, name: str) -> None:
    """Mark a repo as inactive. Preserves history — does NOT delete."""
    entry = roster.repos[name]
    entry["status"] = "inactive"
    entry["unenrolled_at"] = datetime.now(timezone.utc).isoformat()


def active_repos(roster: Roster) -> dict[str, dict[str, Any]]:
    """Return only repos with status=active."""
    return {k: v for k, v in roster.repos.items() if v.get("status") == "active"}


def discover_repos(
    workspace_root: Path,
    *,
    max_depth: int = 3,
    exclude: set[str] | None = None,
) -> list[Path]:
    """Scan for directories containing .workgraph/. Skip hidden dirs, don't recurse into found repos."""
    exclude = exclude or set()
    found: list[Path] = []

    def _scan(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name in exclude:
                continue
            if (entry / ".workgraph").is_dir():
                if is_noncanonical_paia_repo(entry, workspace_root=workspace_root):
                    continue
                found.append(entry)
                # Don't recurse into repos once found
                continue
            _scan(entry, depth + 1)

    _scan(workspace_root, 0)
    return found
