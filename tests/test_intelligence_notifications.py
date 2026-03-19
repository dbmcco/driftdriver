# ABOUTME: Tests for ecosystem intelligence notification dispatcher
# ABOUTME: Covers payload formatting, n8n webhook dispatch, error handling, and all notification types

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from driftdriver.intelligence.notifications import (
    NotificationPayload,
    build_briefing_payload,
    build_escalation_payload,
    build_veto_reminder_payload,
    build_weekly_digest_payload,
    deep_link_url,
    dispatch_notification,
    load_n8n_webhook_config,
)


# ---------------------------------------------------------------------------
# deep_link_url
# ---------------------------------------------------------------------------


class TestDeepLinkUrl:
    def test_builds_signal_link(self) -> None:
        url = deep_link_url(signal_id="abc-123")
        assert "abc-123" in url
        assert "intelligence" in url

    def test_builds_link_with_custom_base(self) -> None:
        url = deep_link_url(signal_id="xyz", hub_base="http://example.com:9999")
        assert url.startswith("http://example.com:9999")
        assert "xyz" in url

    def test_builds_dashboard_link_without_signal(self) -> None:
        url = deep_link_url()
        assert "intelligence" in url
        assert "signal=" not in url


# ---------------------------------------------------------------------------
# NotificationPayload
# ---------------------------------------------------------------------------


class TestNotificationPayload:
    def test_to_dict_has_required_fields(self) -> None:
        payload = NotificationPayload(
            notification_type="daily_briefing",
            summary="Test summary",
            deep_link_url="http://localhost:8777/#intelligence",
            urgency="low",
            signal_ids=["id-1", "id-2"],
        )
        d = payload.to_dict()
        assert d["notification_type"] == "daily_briefing"
        assert d["summary"] == "Test summary"
        assert d["deep_link_url"] == "http://localhost:8777/#intelligence"
        assert d["urgency"] == "low"
        assert d["signal_ids"] == ["id-1", "id-2"]

    def test_to_dict_with_empty_signal_ids(self) -> None:
        payload = NotificationPayload(
            notification_type="weekly_digest",
            summary="Digest",
            deep_link_url="http://localhost:8777/#intelligence",
            urgency="low",
            signal_ids=[],
        )
        d = payload.to_dict()
        assert d["signal_ids"] == []


# ---------------------------------------------------------------------------
# load_n8n_webhook_config
# ---------------------------------------------------------------------------


class TestLoadN8nWebhookConfig:
    def test_returns_none_when_file_missing(self, tmp_path: Any) -> None:
        result = load_n8n_webhook_config(tmp_path / "nonexistent.toml")
        assert result is None

    def test_loads_webhook_url(self, tmp_path: Any) -> None:
        toml_path = tmp_path / "notify.toml"
        toml_path.write_text(
            '[intelligence_notifications]\n'
            'webhook_url = "http://n8n.local:5678/webhook/intel"\n',
            encoding="utf-8",
        )
        result = load_n8n_webhook_config(toml_path)
        assert result is not None
        assert result["webhook_url"] == "http://n8n.local:5678/webhook/intel"

    def test_returns_none_when_section_missing(self, tmp_path: Any) -> None:
        toml_path = tmp_path / "notify.toml"
        toml_path.write_text(
            '[telegram]\nbot_token = "tok"\nchat_id = "123"\n',
            encoding="utf-8",
        )
        result = load_n8n_webhook_config(toml_path)
        assert result is None

    def test_returns_none_when_url_empty(self, tmp_path: Any) -> None:
        toml_path = tmp_path / "notify.toml"
        toml_path.write_text(
            '[intelligence_notifications]\nwebhook_url = ""\n',
            encoding="utf-8",
        )
        result = load_n8n_webhook_config(toml_path)
        assert result is None


# ---------------------------------------------------------------------------
# dispatch_notification
# ---------------------------------------------------------------------------


class TestDispatchNotification:
    def _make_payload(self, **overrides: Any) -> NotificationPayload:
        defaults = {
            "notification_type": "escalation_alert",
            "summary": "New inbox item",
            "deep_link_url": "http://localhost:8777/#intelligence?signal=abc",
            "urgency": "high",
            "signal_ids": ["abc"],
        }
        defaults.update(overrides)
        return NotificationPayload(**defaults)

    @patch("driftdriver.intelligence.notifications.urllib.request.urlopen")
    def test_posts_payload_to_webhook(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        payload = self._make_payload()
        result = dispatch_notification(
            payload, webhook_url="http://n8n.local:5678/webhook/intel"
        )
        assert result["sent"] is True
        assert result["notification_type"] == "escalation_alert"

        # Verify the request
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["notification_type"] == "escalation_alert"
        assert body["summary"] == "New inbox item"
        assert body["urgency"] == "high"

    @patch(
        "driftdriver.intelligence.notifications.urllib.request.urlopen",
        side_effect=OSError("network error"),
    )
    def test_handles_network_error_gracefully(self, mock_urlopen: MagicMock) -> None:
        payload = self._make_payload()
        result = dispatch_notification(
            payload, webhook_url="http://n8n.local:5678/webhook/intel"
        )
        assert result["sent"] is False
        assert "error" in result

    def test_returns_not_sent_when_no_webhook(self) -> None:
        payload = self._make_payload()
        result = dispatch_notification(payload, webhook_url="")
        assert result["sent"] is False
        assert result["reason"] == "no_webhook_url"

    @patch("driftdriver.intelligence.notifications.urllib.request.urlopen")
    def test_loads_config_when_no_url_provided(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true}'
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        payload = self._make_payload()
        with patch(
            "driftdriver.intelligence.notifications.load_n8n_webhook_config",
            return_value={"webhook_url": "http://auto.local/webhook"},
        ):
            result = dispatch_notification(payload)
        assert result["sent"] is True


# ---------------------------------------------------------------------------
# build_briefing_payload
# ---------------------------------------------------------------------------


class TestBuildBriefingPayload:
    def test_builds_daily_briefing(self) -> None:
        payload = build_briefing_payload(
            total_signals=42,
            auto_decisions={"skip": 20, "watch": 10, "adopt": 2},
            escalated=5,
            source_health={"github": "ok", "vibez": "ok"},
        )
        assert payload.notification_type == "daily_briefing"
        assert payload.urgency == "low"
        assert "42" in payload.summary
        assert len(payload.signal_ids) == 0
        assert "intelligence" in payload.deep_link_url

    def test_briefing_with_escalations_bumps_urgency(self) -> None:
        payload = build_briefing_payload(
            total_signals=10,
            auto_decisions={},
            escalated=8,
            source_health={"github": "error"},
        )
        assert payload.urgency == "medium"


# ---------------------------------------------------------------------------
# build_escalation_payload
# ---------------------------------------------------------------------------


class TestBuildEscalationPayload:
    def test_builds_escalation_alert(self) -> None:
        payload = build_escalation_payload(
            signal_id="sig-abc",
            title="New release of workgraph v2.0",
            decision="adopt",
            confidence=0.75,
            urgency="high",
        )
        assert payload.notification_type == "escalation_alert"
        assert payload.urgency == "high"
        assert "sig-abc" in payload.signal_ids
        assert "workgraph v2.0" in payload.summary
        assert "sig-abc" in payload.deep_link_url

    def test_uses_medium_urgency_by_default(self) -> None:
        payload = build_escalation_payload(
            signal_id="sig-xyz",
            title="Something",
            decision="defer",
            confidence=0.5,
        )
        assert payload.urgency == "medium"


# ---------------------------------------------------------------------------
# build_veto_reminder_payload
# ---------------------------------------------------------------------------


class TestBuildVetoReminderPayload:
    def test_builds_veto_reminder(self) -> None:
        expires = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        payload = build_veto_reminder_payload(
            signal_id="sig-veto",
            title="Auto-adopted: claude-agent-sdk update",
            decision="adopt",
            veto_expires_at=expires,
        )
        assert payload.notification_type == "veto_reminder"
        assert payload.urgency == "high"
        assert "sig-veto" in payload.signal_ids
        assert "24h" in payload.summary.lower() or "veto" in payload.summary.lower()
        assert "sig-veto" in payload.deep_link_url

    def test_summary_mentions_decision(self) -> None:
        expires = datetime(2026, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        payload = build_veto_reminder_payload(
            signal_id="sig-v2",
            title="Test signal",
            decision="skip",
            veto_expires_at=expires,
        )
        assert "skip" in payload.summary.lower()


# ---------------------------------------------------------------------------
# build_weekly_digest_payload
# ---------------------------------------------------------------------------


class TestBuildWeeklyDigestPayload:
    def test_builds_weekly_digest(self) -> None:
        payload = build_weekly_digest_payload(
            adopted=3,
            deferred=5,
            skipped=20,
            watched=8,
            vetoed=1,
            source_health={"github": "ok", "vibez": "degraded"},
        )
        assert payload.notification_type == "weekly_digest"
        assert payload.urgency == "low"
        assert "3" in payload.summary  # adopted count
        assert len(payload.signal_ids) == 0
        assert "intelligence" in payload.deep_link_url

    def test_digest_with_vetoes_noted(self) -> None:
        payload = build_weekly_digest_payload(
            adopted=1,
            deferred=2,
            skipped=10,
            watched=3,
            vetoed=4,
            source_health={},
        )
        assert "4" in payload.summary  # vetoed count
