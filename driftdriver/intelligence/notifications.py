# ABOUTME: Notification dispatcher for ecosystem intelligence signals via n8n webhook
# ABOUTME: Builds typed payloads for briefings, escalations, veto reminders, and digests

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover – Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

DEFAULT_HUB_BASE = "http://127.0.0.1:8777"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "workgraph" / "notify.toml"


def deep_link_url(
    *,
    signal_id: str | None = None,
    hub_base: str = DEFAULT_HUB_BASE,
) -> str:
    base = f"{hub_base.rstrip('/')}/#intelligence"
    if signal_id:
        return f"{base}?signal={signal_id}"
    return base


@dataclass(frozen=True)
class NotificationPayload:
    notification_type: str
    summary: str
    deep_link_url: str
    urgency: str
    signal_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_type": self.notification_type,
            "summary": self.summary,
            "deep_link_url": self.deep_link_url,
            "urgency": self.urgency,
            "signal_ids": list(self.signal_ids),
        }


def load_n8n_webhook_config(
    config_path: Path | None = None,
) -> dict[str, str] | None:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    section = data.get("intelligence_notifications")
    if not isinstance(section, dict):
        return None
    url = str(section.get("webhook_url") or "").strip()
    if not url:
        return None
    return {"webhook_url": url}


def dispatch_notification(
    payload: NotificationPayload,
    *,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sent": False,
        "notification_type": payload.notification_type,
    }

    if webhook_url is None:
        config = load_n8n_webhook_config()
        webhook_url = config["webhook_url"] if config else ""

    if not webhook_url:
        result["reason"] = "no_webhook_url"
        return result

    try:
        data = json.dumps(payload.to_dict()).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
        result["sent"] = True
    except Exception as exc:
        logger.warning("Intelligence notification dispatch failed: %s", exc)
        result["error"] = str(exc)

    return result


def build_briefing_payload(
    *,
    total_signals: int,
    auto_decisions: dict[str, int],
    escalated: int,
    source_health: dict[str, str],
    hub_base: str = DEFAULT_HUB_BASE,
) -> NotificationPayload:
    auto_total = sum(auto_decisions.values())
    health_summary = ", ".join(
        f"{src}: {status}" for src, status in sorted(source_health.items())
    )
    summary = (
        f"{total_signals} signals processed, "
        f"{auto_total} auto-decided, "
        f"{escalated} escalated to inbox. "
        f"Sources: {health_summary or 'none'}"
    )
    urgency = "medium" if escalated > 5 or "error" in source_health.values() else "low"
    return NotificationPayload(
        notification_type="daily_briefing",
        summary=summary,
        deep_link_url=deep_link_url(hub_base=hub_base),
        urgency=urgency,
        signal_ids=[],
    )


def build_escalation_payload(
    *,
    signal_id: str,
    title: str,
    decision: str,
    confidence: float,
    urgency: str = "medium",
    hub_base: str = DEFAULT_HUB_BASE,
) -> NotificationPayload:
    summary = (
        f"Inbox: {title} — "
        f"recommended {decision} (confidence {confidence:.0%})"
    )
    return NotificationPayload(
        notification_type="escalation_alert",
        summary=summary,
        deep_link_url=deep_link_url(signal_id=signal_id, hub_base=hub_base),
        urgency=urgency,
        signal_ids=[signal_id],
    )


def build_veto_reminder_payload(
    *,
    signal_id: str,
    title: str,
    decision: str,
    veto_expires_at: datetime,
    hub_base: str = DEFAULT_HUB_BASE,
) -> NotificationPayload:
    expires_str = veto_expires_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = (
        f"Veto window closing in ~24h for auto-{decision}: {title}. "
        f"Expires {expires_str}"
    )
    return NotificationPayload(
        notification_type="veto_reminder",
        summary=summary,
        deep_link_url=deep_link_url(signal_id=signal_id, hub_base=hub_base),
        urgency="high",
        signal_ids=[signal_id],
    )


def build_weekly_digest_payload(
    *,
    adopted: int,
    deferred: int,
    skipped: int,
    watched: int,
    vetoed: int,
    source_health: dict[str, str],
    hub_base: str = DEFAULT_HUB_BASE,
) -> NotificationPayload:
    total = adopted + deferred + skipped + watched
    health_summary = ", ".join(
        f"{src}: {status}" for src, status in sorted(source_health.items())
    )
    summary = (
        f"Weekly: {total} signals — "
        f"{adopted} adopted, {deferred} deferred, "
        f"{skipped} skipped, {watched} watched, "
        f"{vetoed} vetoed. "
        f"Sources: {health_summary or 'none'}"
    )
    return NotificationPayload(
        notification_type="weekly_digest",
        summary=summary,
        deep_link_url=deep_link_url(hub_base=hub_base),
        urgency="low",
        signal_ids=[],
    )
