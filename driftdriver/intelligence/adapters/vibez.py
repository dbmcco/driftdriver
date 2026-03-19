# ABOUTME: HTTP-backed Vibez monitor adapter for community intelligence ingestion
# ABOUTME: Pulls briefing summaries, hot alerts, and contribution opportunities into normalized signals

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import urlopen

from driftdriver.intelligence.adapters.base import SourceAdapter
from driftdriver.intelligence.models import Signal


JsonFetcher = Callable[[str], Any]

LOG = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
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


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return parsed
    return []


def _flatten_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (int, float, bool)):
        return str(raw)
    if isinstance(raw, list):
        return " ".join(part for part in (_flatten_text(item) for item in raw) if part)
    if isinstance(raw, dict):
        return " ".join(part for part in (_flatten_text(item) for item in raw.values()) if part)
    return str(raw)


def _normalize_keywords(config: dict[str, Any]) -> list[str]:
    raw_keywords = config.get("keyword_filter")
    if not isinstance(raw_keywords, list):
        return []
    keywords: list[str] = []
    for raw_keyword in raw_keywords:
        keyword = str(raw_keyword).strip().lower()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords


def _matches_keywords(*, keywords: list[str], fields: list[Any]) -> bool:
    if not keywords:
        return True
    haystack = " ".join(_flatten_text(field).lower() for field in fields)
    return any(keyword in haystack for keyword in keywords)


def _default_fetch_json(url: str) -> Any:
    with urlopen(url, timeout=15) as response:  # noqa: S310 - URL comes from local trusted config
        return json.loads(response.read().decode("utf-8"))


class VibezAdapter(SourceAdapter):
    source_type = "vibez"

    def __init__(
        self,
        *,
        api_endpoint: str = "http://localhost:3100",
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self._api_endpoint = api_endpoint.rstrip("/")
        self._fetch_json = fetch_json or _default_fetch_json

    def _endpoint(self, config: dict[str, Any]) -> str:
        return str(config.get("api_endpoint") or self._api_endpoint).rstrip("/")

    def _fetch(self, config: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any:
        base = self._endpoint(config)
        query = f"?{urlencode(params)}" if params else ""
        return self._fetch_json(f"{base}{path}{query}")

    def _safe_fetch(self, config: dict[str, Any], path: str, params: dict[str, Any] | None = None) -> Any | None:
        try:
            return self._fetch(config, path, params)
        except Exception as exc:
            LOG.warning("VibezAdapter endpoint failed at %s%s: %s", self._endpoint(config), path, exc)
            return None

    def sync(self, config: dict[str, Any], last_synced_at: datetime | None) -> list[Signal]:
        _ = last_synced_at
        keywords = _normalize_keywords(config)
        briefing = self._safe_fetch(config, "/api/briefing")
        contributions = self._safe_fetch(
            config,
            "/api/contributions",
            {
                "days": int(config.get("contributions_days") or 7),
                "limit": int(config.get("contributions_limit") or 50),
            },
        )
        messages = self._safe_fetch(
            config,
            "/api/messages",
            {
                "limit": int(config.get("messages_limit") or 50),
                "minRelevance": int(config.get("messages_min_relevance") or 6),
            },
        )
        if briefing is None and contributions is None and messages is None:
            LOG.warning("VibezAdapter unavailable at %s", self._endpoint(config))
            return []

        signals: list[Signal] = []
        signals.extend(self._briefing_signals(config, briefing, keywords))
        signals.extend(self._contribution_signals(contributions, keywords))
        signals.extend(self._hot_alert_signals(messages, keywords))
        return signals

    def _briefing_signals(self, config: dict[str, Any], payload: Any, keywords: list[str]) -> list[Signal]:
        if not isinstance(payload, dict):
            return []
        report = payload.get("report")
        if not isinstance(report, dict):
            return []
        generated_at = _parse_datetime(report.get("generated_at")) or _utc_now()
        report_date = str(report.get("report_date") or generated_at.date().isoformat()).strip()
        thread_limit = max(1, int(config.get("briefing_limit") or 8))
        threads = _as_list(report.get("briefing_json"))[:thread_limit]
        signals: list[Signal] = []

        for idx, thread in enumerate(threads):
            if not isinstance(thread, dict):
                continue
            title = str(thread.get("title") or "").strip()
            insights = str(thread.get("insights") or "").strip()
            if not title and not insights:
                continue
            if not _matches_keywords(
                keywords=keywords,
                fields=[
                    title,
                    insights,
                    thread.get("participants"),
                    thread.get("links"),
                ],
            ):
                continue
            signals.append(
                Signal(
                    source_type=self.source_type,
                    source_id=f"briefing:{report_date}:{idx}:{title or 'thread'}",
                    signal_type="trend",
                    title=title or f"Vibez briefing trend {idx + 1}",
                    raw_payload={
                        "kind": "briefing_thread",
                        "report_date": report_date,
                        "generated_at": report.get("generated_at"),
                        "thread": thread,
                    },
                    detected_at=generated_at,
                )
            )

        if signals:
            return signals

        daily_memo = str(report.get("daily_memo") or "").strip()
        if not daily_memo:
            return []
        if not _matches_keywords(keywords=keywords, fields=[daily_memo]):
            return []
        return [
            Signal(
                source_type=self.source_type,
                source_id=f"briefing:{report_date}:daily-memo",
                signal_type="trend",
                title=f"Vibez daily briefing {report_date}",
                raw_payload={
                    "kind": "daily_memo",
                    "report_date": report_date,
                    "generated_at": report.get("generated_at"),
                    "daily_memo": daily_memo,
                },
                detected_at=generated_at,
            )
        ]

    def _contribution_signals(self, payload: Any, keywords: list[str]) -> list[Signal]:
        if not isinstance(payload, dict):
            return []
        opportunities = payload.get("opportunities")
        if not isinstance(opportunities, list):
            return []

        signals: list[Signal] = []
        for item in opportunities:
            if not isinstance(item, dict):
                continue
            if not _matches_keywords(
                keywords=keywords,
                fields=[
                    item.get("room_name"),
                    item.get("sender_name"),
                    item.get("body"),
                    item.get("topics"),
                    item.get("entities"),
                    item.get("contribution_themes"),
                    item.get("contribution_hint"),
                ],
            ):
                continue
            opportunity_id = str(item.get("id") or "").strip()
            if not opportunity_id:
                continue
            detected_at = _parse_datetime(item.get("timestamp")) or _utc_now()
            title = f"Vibez contribution opportunity: {item.get('room_name') or item.get('sender_name') or opportunity_id}"
            signals.append(
                Signal(
                    source_type=self.source_type,
                    source_id=opportunity_id,
                    signal_type="community_mention",
                    title=title,
                    raw_payload={
                        "kind": "contribution_opportunity",
                        **item,
                    },
                    detected_at=detected_at,
                )
            )
        return signals

    def _hot_alert_signals(self, payload: Any, keywords: list[str]) -> list[Signal]:
        if not isinstance(payload, dict):
            return []
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return []
        signals: list[Signal] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("alert_level") or "").strip().lower() != "hot":
                continue
            if not _matches_keywords(
                keywords=keywords,
                fields=[
                    item.get("room_name"),
                    item.get("sender_name"),
                    item.get("body"),
                    item.get("topics"),
                    item.get("entities"),
                    item.get("contribution_themes"),
                    item.get("contribution_hint"),
                ],
            ):
                continue
            message_id = str(item.get("id") or "").strip()
            if not message_id:
                continue
            detected_at = _parse_datetime(item.get("timestamp")) or _utc_now()
            signals.append(
                Signal(
                    source_type=self.source_type,
                    source_id=message_id,
                    signal_type="hot_alert",
                    title=f"Vibez hot alert: {item.get('room_name') or message_id}",
                    raw_payload={
                        "kind": "hot_alert_message",
                        **item,
                    },
                    detected_at=detected_at,
                )
            )
        return signals

    def health_check(self) -> dict[str, Any]:
        try:
            payload = self._fetch_json(f"{self._api_endpoint}/api/health")
            ok = bool(isinstance(payload, dict) and payload.get("ok"))
        except Exception as exc:
            return {
                "source_type": self.source_type,
                "ok": False,
                "api_endpoint": self._api_endpoint,
                "error": str(exc),
            }
        return {
            "source_type": self.source_type,
            "ok": ok,
            "api_endpoint": self._api_endpoint,
            "emits": ["community_mention", "trend", "hot_alert"],
        }
