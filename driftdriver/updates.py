from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ECOSYSTEM_REPOS: dict[str, str] = {
    "driftdriver": "dbmcco/driftdriver",
    "coredrift": "dbmcco/coredrift",
    "specdrift": "dbmcco/specdrift",
    "datadrift": "dbmcco/datadrift",
    "archdrift": "dbmcco/archdrift",
    "depsdrift": "dbmcco/depsdrift",
    "uxdrift": "dbmcco/uxdrift",
    "therapydrift": "dbmcco/therapydrift",
    "yagnidrift": "dbmcco/yagnidrift",
    "redrift": "dbmcco/redrift",
    "speedrift-ecosystem": "dbmcco/speedrift-ecosystem",
    "amplifier-bundle-speedrift": "dbmcco/amplifier-bundle-speedrift",
}

_STATE_DIR = ".driftdriver"
_STATE_FILE = "update-state.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    # Support "Z" timestamps if they appear.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _state_path(wg_dir: Path) -> Path:
    return wg_dir / _STATE_DIR / _STATE_FILE


def _default_state() -> dict[str, Any]:
    return {
        "schema": 1,
        "last_checked_at": "",
        "repos": {},
    }


def load_update_state(wg_dir: Path) -> dict[str, Any]:
    path = _state_path(wg_dir)
    if not path.exists():
        return _default_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_state()
    if not isinstance(data, dict):
        return _default_state()
    repos = data.get("repos")
    if not isinstance(repos, dict):
        data["repos"] = {}
    return data


def save_update_state(wg_dir: Path, state: dict[str, Any]) -> None:
    path = _state_path(wg_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def fetch_github_head(repo: str, *, timeout_seconds: int = 4) -> tuple[str, str]:
    url = f"https://api.github.com/repos/{repo}/commits/main"
    token = (
        os.getenv("DRIFTDRIVER_GITHUB_TOKEN")
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
    )
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "driftdriver-update-check",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 (fixed URL pattern)
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404 and not token:
            raise RuntimeError(f"{repo}: HTTP 404 (missing or private; set GITHUB_TOKEN)") from e
        if e.code == 403:
            raise RuntimeError(f"{repo}: HTTP 403 (rate-limited or token required)") from e
        raise RuntimeError(f"{repo}: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"{repo}: network error ({e.reason})") from e
    except Exception as e:
        raise RuntimeError(f"{repo}: unexpected error ({e})") from e

    sha = str(data.get("sha") or "").strip()
    commit = data.get("commit") if isinstance(data.get("commit"), dict) else {}
    committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
    date = str(committer.get("date") or "").strip()
    if not sha:
        raise RuntimeError(f"{repo}: missing commit sha from API response")
    return (sha, date)


def check_ecosystem_updates(
    *,
    wg_dir: Path,
    interval_seconds: int,
    force: bool = False,
    repos: dict[str, str] | None = None,
    fetcher: Callable[[str], tuple[str, str]] | None = None,
) -> dict[str, Any]:
    now = _now_utc()
    now_iso = _iso(now)
    state = load_update_state(wg_dir)
    last_checked = _parse_iso(str(state.get("last_checked_at") or ""))
    interval = max(0, int(interval_seconds))

    if (not force) and last_checked is not None and interval > 0:
        elapsed = (now - last_checked).total_seconds()
        if elapsed < interval:
            return {
                "checked_at": now_iso,
                "skipped": True,
                "reason": "interval_not_elapsed",
                "interval_seconds": interval,
                "elapsed_seconds": int(elapsed),
                "has_updates": False,
                "updates": [],
                "repos": [],
            }

    repo_map = repos or dict(ECOSYSTEM_REPOS)
    pull = fetcher or (lambda repo: fetch_github_head(repo))
    state_repos = state.get("repos") if isinstance(state.get("repos"), dict) else {}
    updates: list[dict[str, Any]] = []
    checked_repos: list[dict[str, Any]] = []

    for tool, repo in repo_map.items():
        prev = state_repos.get(tool) if isinstance(state_repos.get(tool), dict) else {}
        prev_sha = str(prev.get("sha") or "").strip()
        entry: dict[str, Any] = {
            "tool": tool,
            "repo": repo,
            "previous_sha": prev_sha or None,
            "current_sha": None,
            "current_date": None,
            "changed": False,
            "error": None,
        }
        try:
            current_sha, current_date = pull(repo)
            entry["current_sha"] = current_sha
            entry["current_date"] = current_date
            changed = bool(prev_sha) and (current_sha != prev_sha)
            entry["changed"] = changed
            if changed:
                updates.append(
                    {
                        "tool": tool,
                        "repo": repo,
                        "previous_sha": prev_sha,
                        "current_sha": current_sha,
                        "current_date": current_date,
                    }
                )
            state_repos[tool] = {
                "repo": repo,
                "sha": current_sha,
                "commit_date": current_date,
                "seen_at": now_iso,
            }
        except Exception as e:
            entry["error"] = str(e)
        checked_repos.append(entry)

    state["repos"] = state_repos
    state["last_checked_at"] = now_iso
    save_update_state(wg_dir, state)

    return {
        "checked_at": now_iso,
        "skipped": False,
        "reason": "",
        "interval_seconds": interval,
        "has_updates": bool(updates),
        "updates": updates,
        "repos": checked_repos,
    }


def summarize_updates(result: dict[str, Any]) -> str:
    updates = result.get("updates") or []
    if not updates:
        return "No ecosystem updates detected."

    lines = ["Speedrift ecosystem updates detected:"]
    for item in updates:
        tool = str(item.get("tool") or "unknown")
        prev = str(item.get("previous_sha") or "")[:7] or "unknown"
        cur = str(item.get("current_sha") or "")[:7] or "unknown"
        lines.append(f"- {tool}: {prev} -> {cur}")
    lines.append("Decision needed: should the model/toolchain self-update now?")
    return "\n".join(lines)
