# ABOUTME: Tests for proactive notification support in the Gate service.
# ABOUTME: Covers should_notify logic, significance threshold, cooldown, terminal/webhook dispatch, and tick integration.

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from driftdriver.notifications import (
    DEFAULT_NOTIFICATION_CONFIG,
    NotificationDispatcher,
    load_notification_config,
    notify_terminal,
    notify_webhook,
    notify_wg,
    should_notify,
)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


class TestLoadNotificationConfig:
    def test_defaults_when_no_file(self, tmp_path: Path) -> None:
        cfg = load_notification_config(tmp_path / "nonexistent.toml")
        assert cfg == DEFAULT_NOTIFICATION_CONFIG
        assert cfg["enabled"] is False
        assert cfg["terminal"] is True
        assert cfg["webhook_url"] == ""
        assert cfg["min_severity"] == "error"
        assert cfg["cooldown_seconds"] == 3600

    def test_loads_from_toml(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "drift-policy.toml"
        toml_path.write_text(
            "[notifications]\n"
            "enabled = true\n"
            "terminal = false\n"
            'webhook_url = "https://hooks.example.com/abc"\n'
            'min_severity = "warning"\n'
            "cooldown_seconds = 600\n",
            encoding="utf-8",
        )
        cfg = load_notification_config(toml_path)
        assert cfg["enabled"] is True
        assert cfg["terminal"] is False
        assert cfg["webhook_url"] == "https://hooks.example.com/abc"
        assert cfg["min_severity"] == "warning"
        assert cfg["cooldown_seconds"] == 600

    def test_partial_override_keeps_defaults(self, tmp_path: Path) -> None:
        toml_path = tmp_path / "drift-policy.toml"
        toml_path.write_text(
            "[notifications]\n"
            "enabled = true\n",
            encoding="utf-8",
        )
        cfg = load_notification_config(toml_path)
        assert cfg["enabled"] is True
        assert cfg["terminal"] is True  # default preserved
        assert cfg["cooldown_seconds"] == 3600  # default preserved


# ---------------------------------------------------------------------------
# should_notify — significance threshold
# ---------------------------------------------------------------------------


def _make_finding(
    *,
    kind: str = "scope-creep",
    severity: str = "error",
    lane: str = "coredrift",
    message: str = "Task exceeds contract scope",
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "lane": lane,
        "message": message,
    }


class TestShouldNotify:
    def test_error_severity_with_no_history_notifies(self) -> None:
        finding = _make_finding(severity="error")
        assert should_notify(finding, outcome_history=[]) is True

    def test_warning_severity_with_no_history_skips(self) -> None:
        finding = _make_finding(severity="warning")
        assert should_notify(finding, outcome_history=[], min_severity="error") is False

    def test_warning_meets_warning_threshold(self) -> None:
        finding = _make_finding(severity="warning")
        assert should_notify(finding, outcome_history=[], min_severity="warning") is True

    def test_info_below_warning_threshold(self) -> None:
        finding = _make_finding(severity="info")
        assert should_notify(finding, outcome_history=[], min_severity="warning") is False

    def test_critical_always_notifies(self) -> None:
        finding = _make_finding(severity="critical")
        assert should_notify(finding, outcome_history=[], min_severity="error") is True

    def test_history_with_high_resolution_rate_notifies(self) -> None:
        """If >50% of past reviews for this kind resulted in real changes, notify."""
        history = [
            {"finding_key": "scope-creep", "outcome": "resolved"},
            {"finding_key": "scope-creep", "outcome": "resolved"},
            {"finding_key": "scope-creep", "outcome": "ignored"},
        ]
        # 2/3 resolved => 66% > 50% => should notify even at warning
        finding = _make_finding(severity="warning", kind="scope-creep")
        assert should_notify(finding, outcome_history=history, min_severity="error") is True

    def test_history_with_low_resolution_rate_skips(self) -> None:
        """If <=50% resolved, fall back to severity threshold."""
        history = [
            {"finding_key": "scope-creep", "outcome": "resolved"},
            {"finding_key": "scope-creep", "outcome": "ignored"},
            {"finding_key": "scope-creep", "outcome": "ignored"},
            {"finding_key": "scope-creep", "outcome": "ignored"},
        ]
        # 1/4 resolved => 25% <= 50% => fall back to severity; warning < error => skip
        finding = _make_finding(severity="warning", kind="scope-creep")
        assert should_notify(finding, outcome_history=history, min_severity="error") is False

    def test_history_with_low_resolution_but_severity_meets_threshold(self) -> None:
        """Even with low resolution rate, severity >= threshold should notify."""
        history = [
            {"finding_key": "scope-creep", "outcome": "ignored"},
            {"finding_key": "scope-creep", "outcome": "ignored"},
        ]
        finding = _make_finding(severity="error", kind="scope-creep")
        assert should_notify(finding, outcome_history=history, min_severity="error") is True

    def test_history_for_different_kind_ignored(self) -> None:
        """Only history matching the same finding_key counts."""
        history = [
            {"finding_key": "other-issue", "outcome": "resolved"},
            {"finding_key": "other-issue", "outcome": "resolved"},
        ]
        # No history for scope-creep, so fallback to severity
        finding = _make_finding(severity="warning", kind="scope-creep")
        assert should_notify(finding, outcome_history=history, min_severity="error") is False

    def test_worsened_outcome_counts_as_significant(self) -> None:
        """Worsened outcomes are real changes — they count toward significance."""
        history = [
            {"finding_key": "scope-creep", "outcome": "worsened"},
            {"finding_key": "scope-creep", "outcome": "worsened"},
            {"finding_key": "scope-creep", "outcome": "ignored"},
        ]
        # 2/3 worsened => significant (worsened + resolved > 50%)
        finding = _make_finding(severity="warning", kind="scope-creep")
        assert should_notify(finding, outcome_history=history, min_severity="error") is True


# ---------------------------------------------------------------------------
# notify_terminal
# ---------------------------------------------------------------------------


class TestNotifyTerminal:
    @patch("driftdriver.notifications.subprocess.run")
    def test_calls_osascript(self, mock_run: MagicMock) -> None:
        notify_terminal("Drift Alert", "Scope creep detected in repo-x")
        mock_run.assert_called_once()
        args = mock_run.call_args
        cmd = args[0][0]
        assert cmd[0] == "osascript"
        assert "-e" in cmd
        # The AppleScript should contain the title and message
        script = cmd[cmd.index("-e") + 1]
        assert "Drift Alert" in script
        assert "Scope creep detected in repo-x" in script

    @patch("driftdriver.notifications.subprocess.run", side_effect=OSError("no osascript"))
    def test_handles_osascript_failure(self, mock_run: MagicMock) -> None:
        # Should not raise
        notify_terminal("Test", "Message")


# ---------------------------------------------------------------------------
# notify_webhook
# ---------------------------------------------------------------------------


class TestNotifyWebhook:
    @patch("driftdriver.notifications.urllib.request.urlopen")
    def test_posts_json_payload(self, mock_urlopen: MagicMock) -> None:
        payload = {"text": "drift alert", "repo": "test-repo"}
        notify_webhook("https://hooks.example.com/abc", payload)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://hooks.example.com/abc"
        assert req.get_header("Content-type") == "application/json"
        body = json.loads(req.data.decode("utf-8"))
        assert body["text"] == "drift alert"
        assert body["repo"] == "test-repo"

    @patch("driftdriver.notifications.urllib.request.urlopen", side_effect=OSError("network error"))
    def test_handles_webhook_failure(self, mock_urlopen: MagicMock) -> None:
        # Should not raise
        notify_webhook("https://hooks.example.com/abc", {"text": "test"})


# ---------------------------------------------------------------------------
# NotificationDispatcher — cooldown and integration
# ---------------------------------------------------------------------------


class TestNotificationDispatcher:
    def test_disabled_does_not_fire(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": False}
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="critical")
        with patch("driftdriver.notifications.notify_terminal") as mock_term:
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is False
        mock_term.assert_not_called()

    def test_fires_terminal_notification(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "terminal": True}
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="error")
        with patch("driftdriver.notifications.notify_terminal") as mock_term:
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is True
        assert result["channel"] == "terminal"
        mock_term.assert_called_once()

    def test_fires_webhook_notification(self) -> None:
        config = {
            **DEFAULT_NOTIFICATION_CONFIG,
            "enabled": True,
            "terminal": False,
            "webhook_url": "https://hooks.example.com/abc",
        }
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="error")
        with patch("driftdriver.notifications.notify_webhook") as mock_hook:
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is True
        assert result["channel"] == "webhook"
        mock_hook.assert_called_once()

    def test_cooldown_prevents_duplicate(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "cooldown_seconds": 3600}
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="error", kind="scope-creep")
        with patch("driftdriver.notifications.notify_terminal"):
            r1 = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
            r2 = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert r1["notified"] is True
        assert r2["notified"] is False
        assert r2["reason"] == "cooldown"

    def test_cooldown_expires(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "cooldown_seconds": 0}
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="error", kind="scope-creep")
        with patch("driftdriver.notifications.notify_terminal"):
            r1 = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
            r2 = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert r1["notified"] is True
        assert r2["notified"] is True

    def test_different_findings_no_cooldown_collision(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "cooldown_seconds": 3600}
        dispatcher = NotificationDispatcher(config)
        f1 = _make_finding(severity="error", kind="scope-creep")
        f2 = _make_finding(severity="error", kind="missing-contract")
        with patch("driftdriver.notifications.notify_terminal"):
            r1 = dispatcher.check_and_notify(f1, outcome_history=[], repo_name="test-repo")
            r2 = dispatcher.check_and_notify(f2, outcome_history=[], repo_name="test-repo")
        assert r1["notified"] is True
        assert r2["notified"] is True

    def test_should_notify_false_does_not_fire(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "min_severity": "error"}
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="info")
        with patch("driftdriver.notifications.notify_terminal") as mock_term:
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is False
        assert result["reason"] == "below_threshold"
        mock_term.assert_not_called()

    def test_wg_notify_used_when_task_id_present(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "terminal": True}
        dispatcher = NotificationDispatcher(config)
        finding = {**_make_finding(severity="error"), "task_id": "task-42"}
        with (
            patch("driftdriver.notifications.notify_terminal") as mock_term,
            patch("driftdriver.notifications.notify_wg", return_value=True) as mock_wg,
        ):
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is True
        assert "wg" in result["channel"]
        assert "terminal" in result["channel"]
        mock_wg.assert_called_once()
        mock_term.assert_called_once()

    def test_wg_notify_not_called_without_task_id(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "terminal": True}
        dispatcher = NotificationDispatcher(config)
        finding = _make_finding(severity="error")
        with (
            patch("driftdriver.notifications.notify_terminal"),
            patch("driftdriver.notifications.notify_wg") as mock_wg,
        ):
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is True
        assert result["channel"] == "terminal"
        mock_wg.assert_not_called()

    def test_wg_only_channel_when_terminal_disabled(self) -> None:
        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "terminal": False}
        dispatcher = NotificationDispatcher(config)
        finding = {**_make_finding(severity="error"), "task_id": "task-99"}
        with patch("driftdriver.notifications.notify_wg", return_value=True) as mock_wg:
            result = dispatcher.check_and_notify(finding, outcome_history=[], repo_name="test-repo")
        assert result["notified"] is True
        assert result["channel"] == "wg"
        mock_wg.assert_called_once()


# ---------------------------------------------------------------------------
# process_snapshot_notifications — snapshot-level integration
# ---------------------------------------------------------------------------


class TestProcessSnapshotNotifications:
    def test_extracts_findings_and_notifies(self) -> None:
        from driftdriver.notifications import process_snapshot_notifications

        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": True, "min_severity": "error"}
        snapshot: dict[str, Any] = {
            "repos": [
                {
                    "name": "repo-a",
                    "path": "/tmp/repo-a",
                    "errors": ["something broke"],
                    "stalled": True,
                    "stall_reasons": ["no active execution"],
                    "workgraph_exists": True,
                    "service_running": False,
                    "blocked_open": 3,
                    "task_counts": {},
                    "stale_in_progress": [],
                    "stale_open": [],
                    "missing_dependencies": 0,
                    "activity_state": "stalled",
                    "git_dirty": False,
                    "behind": 0,
                    "ahead": 0,
                    "security": {},
                    "quality": {},
                    "repo_north_star": {"present": True},
                    "cross_repo_dependencies": [],
                },
            ],
        }
        with patch("driftdriver.notifications.notify_terminal"):
            result = process_snapshot_notifications(snapshot, config, outcome_ledger_path=None)
        assert result["enabled"] is True
        assert result["findings_checked"] > 0

    def test_disabled_returns_early(self) -> None:
        from driftdriver.notifications import process_snapshot_notifications

        config = {**DEFAULT_NOTIFICATION_CONFIG, "enabled": False}
        result = process_snapshot_notifications({}, config, outcome_ledger_path=None)
        assert result["enabled"] is False
        assert result["findings_checked"] == 0
        assert result["notifications_sent"] == 0
