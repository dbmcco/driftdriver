# ABOUTME: GitHub-backed ecosystem intelligence adapter built on the existing updates.py scanner
# ABOUTME: Converts repo updates and watched-user discoveries into normalized Signal objects

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from driftdriver.intelligence.adapters.base import SourceAdapter
from driftdriver.intelligence.models import Signal
from driftdriver.updates import ECOSYSTEM_REPOS, check_ecosystem_updates


RepoFetcher = Callable[[str], tuple[str, str]]
UserFetcher = Callable[[str, int], list[dict[str, Any]]]
ReportFetcher = Callable[[str], str]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_repo_map(config: dict[str, Any]) -> dict[str, str]:
    raw_repos = config.get("repos")
    if isinstance(raw_repos, dict):
        repos: dict[str, str] = {}
        for key, value in raw_repos.items():
            clean_key = str(key).strip()
            clean_value = str(value).strip()
            if clean_key and clean_value:
                repos[clean_key] = clean_value
        return repos

    repos = dict(ECOSYSTEM_REPOS)
    raw_extra = config.get("extra_repos")
    if isinstance(raw_extra, dict):
        for key, value in raw_extra.items():
            clean_key = str(key).strip()
            clean_value = str(value).strip()
            if clean_key and clean_value:
                repos[clean_key] = clean_value
    return repos


def _normalize_github_users(config: dict[str, Any]) -> list[str]:
    raw_users = config.get("github_users")
    if not isinstance(raw_users, list):
        return []
    users: list[str] = []
    for raw_user in raw_users:
        user = str(raw_user).strip().lstrip("@")
        if user and user not in users:
            users.append(user)
    return users


def _normalize_reports(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_reports = config.get("reports")
    if not isinstance(raw_reports, list):
        return []
    reports: list[dict[str, Any]] = []
    for entry in raw_reports:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        name = str(entry.get("name") or url).strip() or url
        raw_keywords = entry.get("keywords")
        keywords: list[str] = []
        if isinstance(raw_keywords, list):
            for raw_keyword in raw_keywords:
                keyword = str(raw_keyword).strip()
                if keyword and keyword not in keywords:
                    keywords.append(keyword)
        reports.append({"name": name, "url": url, "keywords": keywords})
    return reports


def _normalize_report_keywords(config: dict[str, Any]) -> list[str]:
    raw_keywords = config.get("report_keywords")
    if not isinstance(raw_keywords, list):
        return []
    keywords: list[str] = []
    for raw_keyword in raw_keywords:
        keyword = str(raw_keyword).strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords


def _normalize_wg_dir(config: dict[str, Any]) -> Path:
    raw_wg_dir = str(config.get("wg_dir") or "").strip()
    if not raw_wg_dir:
        raise ValueError("github source config is missing wg_dir")
    return Path(raw_wg_dir)


class GitHubAdapter(SourceAdapter):
    source_type = "github"

    def __init__(
        self,
        *,
        fetcher: RepoFetcher | None = None,
        user_fetcher: UserFetcher | None = None,
        report_fetcher: ReportFetcher | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._user_fetcher = user_fetcher
        self._report_fetcher = report_fetcher

    def sync(self, config: dict[str, Any], last_synced_at: datetime | None) -> list[Signal]:
        _ = last_synced_at
        wg_dir = _normalize_wg_dir(config)
        result = check_ecosystem_updates(
            wg_dir=wg_dir,
            interval_seconds=0,
            force=True,
            repos=_normalize_repo_map(config),
            users=_normalize_github_users(config),
            reports=_normalize_reports(config),
            report_keywords=_normalize_report_keywords(config),
            user_repo_limit=int(config.get("user_repo_limit") or 10),
            fetcher=self._fetcher,
            user_fetcher=self._user_fetcher,
            report_fetcher=self._report_fetcher,
        )
        checked_at = _coerce_datetime(result.get("checked_at")) or _utc_now()

        signals: list[Signal] = []
        for item in result.get("updates") or []:
            repo = str(item.get("repo") or "").strip()
            current_sha = str(item.get("current_sha") or "").strip()
            if not repo or not current_sha:
                continue
            detected_at = _coerce_datetime(item.get("current_date")) or checked_at
            signals.append(
                Signal(
                    source_type=self.source_type,
                    source_id=f"{repo}@{current_sha}",
                    signal_type="repo_update",
                    title=f"Repo update detected: {repo}",
                    raw_payload={"kind": "repo_update", **item},
                    detected_at=detected_at,
                )
            )

        for item in result.get("user_findings") or []:
            kind = str(item.get("kind") or "").strip()
            repo = str(item.get("repo") or "").strip()
            user = str(item.get("user") or "").strip()
            if not repo or not user:
                continue
            if kind == "new_repo":
                detected_at = _coerce_datetime(item.get("current_pushed_at")) or checked_at
                signals.append(
                    Signal(
                        source_type=self.source_type,
                        source_id=repo,
                        signal_type="new_repo",
                        title=f"New repo from @{user}: {repo}",
                        raw_payload=item,
                        detected_at=detected_at,
                    )
                )
                continue
            if kind != "repo_pushed":
                continue
            current_pushed_at = str(item.get("current_pushed_at") or "").strip()
            detected_at = _coerce_datetime(current_pushed_at) or checked_at
            unique_suffix = current_pushed_at or checked_at.isoformat()
            signals.append(
                Signal(
                    source_type=self.source_type,
                    source_id=f"{repo}@{unique_suffix}",
                    signal_type="activity",
                    title=f"Repo activity from @{user}: {repo}",
                    raw_payload=item,
                    detected_at=detected_at,
                )
            )

        return signals

    def health_check(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "ok": True,
            "emits": ["repo_update", "new_repo", "activity"],
            "implementation": "driftdriver.updates.check_ecosystem_updates",
        }
