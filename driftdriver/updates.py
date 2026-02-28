from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
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
_REVIEW_CONFIG_FILE = "ecosystem-review.json"


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


def review_config_path(wg_dir: Path, config_path: str | Path | None = None) -> Path:
    if config_path:
        return Path(config_path)
    return wg_dir / _STATE_DIR / _REVIEW_CONFIG_FILE


def _default_state() -> dict[str, Any]:
    return {
        "schema": 1,
        "last_checked_at": "",
        "repos": {},
        "users": {},
        "reports": {},
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


def _github_headers() -> dict[str, str]:
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
    return headers


def fetch_github_head(repo: str, *, timeout_seconds: int = 4) -> tuple[str, str]:
    url = f"https://api.github.com/repos/{repo}/commits/main"
    headers = _github_headers()
    has_token = "Authorization" in headers
    req = Request(
        url,
        headers=headers,
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 (fixed URL pattern)
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404 and not has_token:
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


def fetch_github_user_repos(user: str, *, limit: int = 10, timeout_seconds: int = 6) -> list[dict[str, Any]]:
    safe_user = quote(str(user).strip())
    if not safe_user:
        raise RuntimeError("empty GitHub user")
    bounded_limit = max(1, min(int(limit), 100))
    url = (
        f"https://api.github.com/users/{safe_user}/repos"
        f"?sort=updated&direction=desc&type=public&per_page={bounded_limit}"
    )
    req = Request(url, headers=_github_headers())
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 (fixed URL pattern)
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404:
            raise RuntimeError(f"{user}: HTTP 404 (user not found)") from e
        if e.code == 403:
            raise RuntimeError(f"{user}: HTTP 403 (rate-limited or token required)") from e
        raise RuntimeError(f"{user}: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"{user}: network error ({e.reason})") from e
    except Exception as e:
        raise RuntimeError(f"{user}: unexpected error ({e})") from e

    if not isinstance(data, list):
        raise RuntimeError(f"{user}: unexpected API payload type ({type(data).__name__})")

    repos: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        full_name = str(item.get("full_name") or "").strip()
        if not full_name:
            continue
        repos.append(
            {
                "full_name": full_name,
                "html_url": str(item.get("html_url") or "").strip(),
                "description": str(item.get("description") or "").strip(),
                "pushed_at": str(item.get("pushed_at") or "").strip(),
                "updated_at": str(item.get("updated_at") or "").strip(),
            }
        )
    return repos


def fetch_report_content(url: str, *, timeout_seconds: int = 8) -> str:
    clean = str(url).strip()
    if not clean:
        raise RuntimeError("empty report URL")
    from urllib.parse import urlparse
    scheme = urlparse(clean).scheme.lower()
    if scheme != "https":
        raise RuntimeError(f"Only HTTPS URLs are accepted (got {scheme}): {clean}")
    req = Request(
        clean,
        headers={
            "User-Agent": "driftdriver-ecosystem-review",
            "Accept": "text/plain,text/markdown,text/html;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 (URL from trusted config/user intent)
            raw = resp.read()
    except HTTPError as e:
        raise RuntimeError(f"{clean}: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"{clean}: network error ({e.reason})") from e
    except Exception as e:
        raise RuntimeError(f"{clean}: unexpected error ({e})") from e
    return raw.decode("utf-8", errors="replace")


def load_review_config(wg_dir: Path, config_path: str | Path | None = None) -> dict[str, Any]:
    path = review_config_path(wg_dir, config_path)
    if not path.exists():
        return {
            "repos": None,
            "extra_repos": {},
            "github_users": [],
            "reports": [],
            "report_keywords": [],
            "user_repo_limit": 10,
            "source_path": str(path),
            "exists": False,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"could not parse review config {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"review config {path} must be a JSON object")

    repos_raw = data.get("repos")
    repos: dict[str, str] | None = None
    if isinstance(repos_raw, dict):
        normalized: dict[str, str] = {}
        for k, v in repos_raw.items():
            key = str(k).strip()
            value = str(v).strip()
            if key and value:
                normalized[key] = value
        repos = normalized

    extra_repos_raw = data.get("extra_repos")
    extra_repos: dict[str, str] = {}
    if isinstance(extra_repos_raw, dict):
        for k, v in extra_repos_raw.items():
            key = str(k).strip()
            value = str(v).strip()
            if key and value:
                extra_repos[key] = value

    github_users: list[str] = []
    users_raw = data.get("github_users")
    if isinstance(users_raw, list):
        for entry in users_raw:
            if isinstance(entry, dict):
                user = str(entry.get("user") or "").strip().lstrip("@")
            else:
                user = str(entry).strip().lstrip("@")
            if user:
                github_users.append(user)

    reports: list[dict[str, Any]] = []
    reports_raw = data.get("reports")
    if isinstance(reports_raw, list):
        for entry in reports_raw:
            if isinstance(entry, str):
                url = entry.strip()
                if url:
                    reports.append({"name": url, "url": url, "keywords": []})
                continue
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            name = str(entry.get("name") or url).strip() or url
            kws_raw = entry.get("keywords")
            keywords: list[str] = []
            if isinstance(kws_raw, list):
                for kw in kws_raw:
                    kw_s = str(kw).strip()
                    if kw_s:
                        keywords.append(kw_s)
            reports.append({"name": name, "url": url, "keywords": keywords})

    report_keywords: list[str] = []
    keywords_raw = data.get("report_keywords")
    if isinstance(keywords_raw, list):
        for kw in keywords_raw:
            kw_s = str(kw).strip()
            if kw_s:
                report_keywords.append(kw_s)

    limit_raw = data.get("user_repo_limit")
    try:
        user_repo_limit = int(limit_raw) if limit_raw is not None else 10
    except Exception:
        user_repo_limit = 10
    user_repo_limit = max(1, min(user_repo_limit, 100))

    return {
        "repos": repos,
        "extra_repos": extra_repos,
        "github_users": github_users,
        "reports": reports,
        "report_keywords": report_keywords,
        "user_repo_limit": user_repo_limit,
        "source_path": str(path),
        "exists": True,
    }


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _normalize_reports(reports: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not reports:
        return out
    for raw in reports:
        if isinstance(raw, str):
            url = raw.strip()
            if not url:
                continue
            out.append({"name": url, "url": url, "keywords": []})
            continue
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        name = str(raw.get("name") or url).strip() or url
        keywords: list[str] = []
        kws = raw.get("keywords")
        if isinstance(kws, list):
            for kw in kws:
                kw_s = str(kw).strip()
                if kw_s:
                    keywords.append(kw_s)
        out.append({"name": name, "url": url, "keywords": keywords})
    return out


def _keyword_hits(text: str, keywords: list[str], *, limit: int = 5) -> list[str]:
    if not keywords:
        return []
    lowered = [k.strip().lower() for k in keywords if str(k).strip()]
    if not lowered:
        return []
    hits: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line_l = line.lower()
        if any(kw in line_l for kw in lowered):
            hits.append(line[:240])
        if len(hits) >= limit:
            break
    return hits


def check_ecosystem_updates(
    *,
    wg_dir: Path,
    interval_seconds: int,
    force: bool = False,
    repos: dict[str, str] | None = None,
    fetcher: Callable[[str], tuple[str, str]] | None = None,
    users: list[str] | None = None,
    reports: list[Any] | None = None,
    report_keywords: list[str] | None = None,
    user_repo_limit: int = 10,
    user_fetcher: Callable[[str, int], list[dict[str, Any]]] | None = None,
    report_fetcher: Callable[[str], str] | None = None,
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
                "has_discoveries": False,
                "user_findings": [],
                "report_findings": [],
                "user_checks": [],
                "report_checks": [],
            }

    repo_map = dict(ECOSYSTEM_REPOS) if repos is None else dict(repos)
    pull = fetcher or (lambda repo: fetch_github_head(repo))
    pull_user = user_fetcher or (lambda user, limit: fetch_github_user_repos(user, limit=limit))
    pull_report = report_fetcher or (lambda url: fetch_report_content(url))
    state_repos = state.get("repos") if isinstance(state.get("repos"), dict) else {}
    state_users = state.get("users") if isinstance(state.get("users"), dict) else {}
    state_reports = state.get("reports") if isinstance(state.get("reports"), dict) else {}
    updates: list[dict[str, Any]] = []
    checked_repos: list[dict[str, Any]] = []
    user_findings: list[dict[str, Any]] = []
    report_findings: list[dict[str, Any]] = []
    checked_users: list[dict[str, Any]] = []
    checked_reports: list[dict[str, Any]] = []

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

    normalized_users: list[str] = []
    for raw_user in users or []:
        user = str(raw_user).strip().lstrip("@")
        if user and user not in normalized_users:
            normalized_users.append(user)
    bounded_limit = max(1, min(int(user_repo_limit), 100))

    for user in normalized_users:
        entry: dict[str, Any] = {
            "user": user,
            "repo_count": 0,
            "findings": 0,
            "error": None,
        }
        prev_user = state_users.get(user) if isinstance(state_users.get(user), dict) else {}
        prev_repos = prev_user.get("repos") if isinstance(prev_user.get("repos"), dict) else {}
        baseline_exists = bool(prev_repos)
        current_repos: dict[str, dict[str, Any]] = {}
        try:
            repos_data = pull_user(user, bounded_limit)
            for item in repos_data:
                if not isinstance(item, dict):
                    continue
                full_name = str(item.get("full_name") or "").strip()
                if not full_name:
                    continue
                pushed_at = str(item.get("pushed_at") or "").strip()
                updated_at = str(item.get("updated_at") or "").strip()
                html_url = str(item.get("html_url") or "").strip()
                description = str(item.get("description") or "").strip()
                current_repos[full_name] = {
                    "pushed_at": pushed_at,
                    "updated_at": updated_at,
                    "html_url": html_url,
                    "description": description,
                    "seen_at": now_iso,
                }
                prev = prev_repos.get(full_name) if isinstance(prev_repos.get(full_name), dict) else {}
                prev_pushed = str(prev.get("pushed_at") or "").strip()
                if baseline_exists and not prev:
                    user_findings.append(
                        {
                            "kind": "new_repo",
                            "user": user,
                            "repo": full_name,
                            "html_url": html_url,
                            "description": description,
                            "current_pushed_at": pushed_at or None,
                        }
                    )
                    continue
                if baseline_exists and prev_pushed and pushed_at and prev_pushed != pushed_at:
                    user_findings.append(
                        {
                            "kind": "repo_pushed",
                            "user": user,
                            "repo": full_name,
                            "html_url": html_url,
                            "description": description,
                            "previous_pushed_at": prev_pushed,
                            "current_pushed_at": pushed_at,
                        }
                    )
            state_users[user] = {
                "seen_at": now_iso,
                "repo_count": len(current_repos),
                "repos": current_repos,
            }
            entry["repo_count"] = len(current_repos)
        except Exception as e:
            entry["error"] = str(e)
        checked_users.append(entry)

    normalized_reports = _normalize_reports(reports)
    global_keywords: list[str] = []
    for kw in report_keywords or []:
        kw_s = str(kw).strip()
        if kw_s and kw_s not in global_keywords:
            global_keywords.append(kw_s)

    for report in normalized_reports:
        name = str(report.get("name") or "").strip()
        url = str(report.get("url") or "").strip()
        if not url:
            continue
        entry: dict[str, Any] = {
            "name": name or url,
            "url": url,
            "changed": False,
            "keyword_hits": 0,
            "error": None,
        }
        prev = state_reports.get(url) if isinstance(state_reports.get(url), dict) else {}
        prev_hash = str(prev.get("content_hash") or "").strip()
        report_specific_keywords: list[str] = []
        for kw in report.get("keywords") or []:
            kw_s = str(kw).strip()
            if kw_s and kw_s not in report_specific_keywords:
                report_specific_keywords.append(kw_s)
        merged_keywords = global_keywords + [k for k in report_specific_keywords if k not in global_keywords]
        try:
            content = pull_report(url)
            content_hash = _text_hash(content)
            hits = _keyword_hits(content, merged_keywords)
            changed = bool(prev_hash) and (prev_hash != content_hash)
            entry["changed"] = changed
            entry["keyword_hits"] = len(hits)
            if changed:
                report_findings.append(
                    {
                        "kind": "report_changed",
                        "name": name or url,
                        "url": url,
                        "previous_hash": prev_hash,
                        "current_hash": content_hash,
                        "keyword_hits": hits,
                    }
                )
            state_reports[url] = {
                "name": name or url,
                "url": url,
                "content_hash": content_hash,
                "seen_at": now_iso,
                "last_changed_at": now_iso if changed else str(prev.get("last_changed_at") or ""),
            }
        except Exception as e:
            entry["error"] = str(e)
        checked_reports.append(entry)

    state["repos"] = state_repos
    state["users"] = state_users
    state["reports"] = state_reports
    state["last_checked_at"] = now_iso
    save_update_state(wg_dir, state)

    has_discoveries = bool(user_findings or report_findings)
    return {
        "checked_at": now_iso,
        "skipped": False,
        "reason": "",
        "interval_seconds": interval,
        "has_updates": bool(updates),
        "updates": updates,
        "repos": checked_repos,
        "has_discoveries": has_discoveries,
        "user_findings": user_findings,
        "report_findings": report_findings,
        "user_checks": checked_users,
        "report_checks": checked_reports,
    }


def summarize_updates(result: dict[str, Any]) -> str:
    updates = result.get("updates") or []
    user_findings = result.get("user_findings") or []
    report_findings = result.get("report_findings") or []
    if not updates and not user_findings and not report_findings:
        return "No ecosystem updates detected."

    lines: list[str] = []
    if updates:
        lines.append("Speedrift ecosystem updates detected:")
        for item in updates:
            tool = str(item.get("tool") or "unknown")
            prev = str(item.get("previous_sha") or "")[:7] or "unknown"
            cur = str(item.get("current_sha") or "")[:7] or "unknown"
            lines.append(f"- {tool}: {prev} -> {cur}")

    if user_findings:
        if lines:
            lines.append("")
        lines.append("Watched GitHub source findings:")
        for item in user_findings[:12]:
            kind = str(item.get("kind") or "")
            user = str(item.get("user") or "unknown")
            repo = str(item.get("repo") or "unknown")
            if kind == "new_repo":
                lines.append(f"- @{user}: new repo discovered -> {repo}")
            else:
                lines.append(f"- @{user}: repo activity changed -> {repo}")
        if len(user_findings) > 12:
            lines.append(f"- ... and {len(user_findings) - 12} more user findings")

    if report_findings:
        if lines:
            lines.append("")
        lines.append("Watched report changes detected:")
        for item in report_findings[:10]:
            name = str(item.get("name") or item.get("url") or "report")
            hits = item.get("keyword_hits")
            hit_count = len(hits) if isinstance(hits, list) else 0
            if hit_count:
                lines.append(f"- {name}: content changed ({hit_count} keyword hits)")
            else:
                lines.append(f"- {name}: content changed")
        if len(report_findings) > 10:
            lines.append(f"- ... and {len(report_findings) - 10} more report findings")

    lines.append("Decision needed: should the model/toolchain self-update now?")
    return "\n".join(lines)


def render_review_markdown(result: dict[str, Any]) -> str:
    checked_at = str(result.get("checked_at") or "").strip() or "unknown"
    updates = result.get("updates") or []
    user_findings = result.get("user_findings") or []
    report_findings = result.get("report_findings") or []
    lines = [
        "# Ecosystem Review",
        "",
        f"- Checked at (UTC): `{checked_at}`",
        f"- Repo updates: `{len(updates)}`",
        f"- GitHub user findings: `{len(user_findings)}`",
        f"- Report findings: `{len(report_findings)}`",
        "",
    ]

    lines.append("## Summary")
    lines.append("")
    lines.append(summarize_updates(result))
    lines.append("")

    lines.append("## Repo Updates")
    lines.append("")
    if updates:
        for item in updates:
            tool = str(item.get("tool") or "unknown")
            repo = str(item.get("repo") or "unknown")
            prev = str(item.get("previous_sha") or "")[:7] or "unknown"
            cur = str(item.get("current_sha") or "")[:7] or "unknown"
            lines.append(f"- `{tool}` (`{repo}`): `{prev}` -> `{cur}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## GitHub User Findings")
    lines.append("")
    if user_findings:
        for item in user_findings:
            kind = str(item.get("kind") or "change")
            user = str(item.get("user") or "unknown")
            repo = str(item.get("repo") or "unknown")
            if kind == "new_repo":
                lines.append(f"- `@{user}` new repo: `{repo}`")
            else:
                lines.append(f"- `@{user}` repo pushed: `{repo}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Report Findings")
    lines.append("")
    if report_findings:
        for item in report_findings:
            name = str(item.get("name") or item.get("url") or "report")
            url = str(item.get("url") or "")
            lines.append(f"- `{name}`: changed (`{url}`)")
            hits = item.get("keyword_hits")
            if isinstance(hits, list) and hits:
                for hit in hits[:3]:
                    lines.append(f"  - keyword hit: {str(hit)}")
    else:
        lines.append("- none")
    lines.append("")

    errors: list[str] = []
    for section in ("repos", "user_checks", "report_checks"):
        entries = result.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            err = str(entry.get("error") or "").strip()
            if not err:
                continue
            label = str(entry.get("tool") or entry.get("user") or entry.get("name") or "source")
            errors.append(f"- {label}: {err}")

    lines.append("## Lookup Errors")
    lines.append("")
    if errors:
        lines.extend(errors)
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)
