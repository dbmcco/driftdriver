# ABOUTME: Git activity scanner for the ecosystem hub.
# ABOUTME: Scans git log for all registered repos and builds windowed commit digests.
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# 8 days gives full 7d window plus 1 day buffer for timezone boundary safety.
_LOOKBACK_DAYS = 8

_WINDOWS: dict[str, int] = {"24h": 1, "48h": 2, "72h": 3, "7d": 7}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        _LOG.debug("git command failed in %s: %s", cwd, exc)
        return 1, "", str(exc)


def scan_repo_activity(name: str, path: Path) -> dict[str, Any] | None:
    """Scan git log for a single repo. Returns None if repo is not git or on error."""
    if not path.exists():
        return None

    # Verify it's a git repo
    rc, _, _ = _run_git(["rev-parse", "--git-dir"], path)
    if rc != 0:
        return None

    since = f"{_LOOKBACK_DAYS} days ago"

    # Fetch commit metadata: hash|ISO timestamp|subject|author name|author email
    rc, log_out, log_err = _run_git(
        ["log", f"--since={since}", "--format=%H|%aI|%s|%an|%ae"],
        path,
    )
    # rc != 0 with "does not have any commits yet" is OK (empty repo)
    is_empty_repo = (rc != 0 and "does not have any commits yet" in log_err)
    if rc != 0 and not is_empty_repo:
        _LOG.debug("git log failed for %s: %s", name, log_err)
        return None

    # Fetch changed filenames (blank-line separated per commit, name-only)
    _, files_out, _ = _run_git(
        ["log", f"--since={since}", "--name-only", "--format="],
        path,
    )
    changed_files = sorted(set(
        line.strip() for line in files_out.splitlines() if line.strip()
    ))

    commits: list[dict[str, Any]] = []
    now = _utc_now()

    for line in log_out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 4)
        if len(parts) < 3:
            continue
        hash_, ts_raw, subject = parts[0], parts[1], parts[2]
        author = parts[3] if len(parts) > 3 else ""
        try:
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            ts = ts.astimezone(timezone.utc)
        except ValueError:
            continue
        commits.append({"hash": hash_, "timestamp": ts, "subject": subject, "author": author})

    # Sort newest-first
    commits.sort(key=lambda c: c["timestamp"], reverse=True)

    last_commit_hash = commits[0]["hash"] if commits else None
    last_commit_at = commits[0]["timestamp"].isoformat() if commits else None

    # Build windows
    windows: dict[str, dict[str, Any]] = {}
    for label, days in _WINDOWS.items():
        cutoff = now - timedelta(days=days)
        window_commits = [c for c in commits if c["timestamp"] >= cutoff]
        windows[label] = {
            "count": len(window_commits),
            "subjects": [c["subject"] for c in window_commits],
        }

    # Build timeline entries (for /api/activity timeline)
    timeline = [
        {
            "repo": name,
            "hash": c["hash"],
            "timestamp": c["timestamp"].isoformat(),
            "subject": c["subject"],
            "author": c["author"],
        }
        for c in commits
    ]

    return {
        "name": name,
        "path": str(path),
        "last_commit_at": last_commit_at,
        "last_commit_hash": last_commit_hash,
        "summary": None,          # filled by summarizer
        "summary_hash": None,     # hash that summary was built from
        "changed_files": changed_files[:50],  # cap for LLM prompt safety
        "windows": windows,
        "timeline": timeline,
    }


def scan_all_repos(repos: dict[str, Path]) -> list[dict[str, Any]]:
    """Scan all repos. Skips repos that are not git or have errors. Returns list of digests."""
    results = []
    for name, path in repos.items():
        try:
            entry = scan_repo_activity(name, path)
            if entry is not None:
                results.append(entry)
        except Exception:
            _LOG.debug("Unexpected error scanning %s", name, exc_info=True)
    return results
