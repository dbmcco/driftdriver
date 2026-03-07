# ABOUTME: Proactive notification support for the Gate service (ecosystem hub).
# ABOUTME: Fires macOS terminal alerts or webhook calls when UAT-worthy drift findings appear.

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover – Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from driftdriver.outcome import read_outcomes

SEVERITY_ORDER = ("info", "warning", "error", "critical")

DEFAULT_NOTIFICATION_CONFIG: dict[str, Any] = {
    "enabled": False,
    "terminal": True,
    "webhook_url": "",
    "min_severity": "error",
    "cooldown_seconds": 3600,
}


def _severity_rank(severity: str) -> int:
    """Return numeric rank for severity. Unknown values map to 0 (info)."""
    s = str(severity or "").strip().lower()
    try:
        return SEVERITY_ORDER.index(s)
    except ValueError:
        return 0


def load_notification_config(toml_path: Path) -> dict[str, Any]:
    """Load [notifications] section from a TOML file, falling back to defaults."""
    if not toml_path.exists():
        return dict(DEFAULT_NOTIFICATION_CONFIG)
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_NOTIFICATION_CONFIG)
    section = data.get("notifications")
    if not isinstance(section, dict):
        return dict(DEFAULT_NOTIFICATION_CONFIG)
    merged = dict(DEFAULT_NOTIFICATION_CONFIG)
    for key in DEFAULT_NOTIFICATION_CONFIG:
        if key in section:
            merged[key] = section[key]
    return merged


def should_notify(
    finding: dict[str, Any],
    *,
    outcome_history: list[dict[str, Any]],
    min_severity: str = "error",
) -> bool:
    """Determine if a finding warrants human attention.

    Significance threshold logic:
    1. Filter outcome_history to entries matching finding["kind"].
    2. If matching history exists and >50% resulted in real changes
       (outcome == "resolved" or "worsened"), notify regardless of severity.
    3. Otherwise, fall back to severity threshold: notify if finding severity
       >= min_severity.
    """
    finding_kind = str(finding.get("kind") or "").strip()
    finding_severity = str(finding.get("severity") or "info").strip().lower()

    # Check outcome history for significance signal
    if finding_kind and outcome_history:
        matching = [
            entry for entry in outcome_history
            if str(entry.get("finding_key") or "").strip() == finding_kind
        ]
        if matching:
            significant_count = sum(
                1 for entry in matching
                if str(entry.get("outcome") or "") in ("resolved", "worsened")
            )
            significance_rate = significant_count / len(matching)
            if significance_rate > 0.5:
                return True

    # Fall back to severity threshold
    return _severity_rank(finding_severity) >= _severity_rank(min_severity)


def notify_terminal(title: str, message: str) -> None:
    """Send a macOS notification via osascript. Fails silently."""
    # Escape double quotes for AppleScript
    safe_title = title.replace('"', '\\"')
    safe_message = message.replace('"', '\\"')
    script = f'display notification "{safe_message}" with title "{safe_title}"'
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def notify_webhook(url: str, payload: dict[str, Any]) -> None:
    """POST a JSON payload to a webhook URL. Fails silently."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)  # noqa: S310
    except (OSError, urllib.error.URLError, ValueError):
        pass


class NotificationDispatcher:
    """Manages notification dispatch with cooldown tracking."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._config = config
        self._last_notified: dict[str, float] = {}

    def check_and_notify(
        self,
        finding: dict[str, Any],
        *,
        outcome_history: list[dict[str, Any]],
        repo_name: str = "",
    ) -> dict[str, Any]:
        """Check if finding warrants notification, dispatch if so.

        Returns a result dict with keys: notified, channel, reason, finding_kind, repo.
        """
        finding_kind = str(finding.get("kind") or "").strip()
        result: dict[str, Any] = {
            "notified": False,
            "channel": "",
            "reason": "",
            "finding_kind": finding_kind,
            "repo": repo_name,
        }

        if not self._config.get("enabled"):
            result["reason"] = "disabled"
            return result

        min_severity = str(self._config.get("min_severity") or "error")
        if not should_notify(finding, outcome_history=outcome_history, min_severity=min_severity):
            result["reason"] = "below_threshold"
            return result

        # Cooldown check — 0 means no cooldown
        raw_cd = self._config.get("cooldown_seconds")
        cooldown = max(0, int(raw_cd if raw_cd is not None else 3600))
        cooldown_key = f"{repo_name}:{finding_kind}"
        now = time.monotonic()
        if cooldown > 0:
            last = self._last_notified.get(cooldown_key, 0.0)
            if last > 0 and (now - last) < cooldown:
                result["reason"] = "cooldown"
                return result

        # Dispatch
        severity = str(finding.get("severity") or "")
        message_text = str(finding.get("message") or finding_kind)
        title = f"Drift: {repo_name}" if repo_name else "Drift Alert"
        body = f"[{severity}] {message_text}"

        webhook_url = str(self._config.get("webhook_url") or "").strip()
        if self._config.get("terminal"):
            notify_terminal(title, body)
            result["channel"] = "terminal"
        elif webhook_url:
            notify_webhook(webhook_url, {
                "title": title,
                "text": body,
                "repo": repo_name,
                "finding_kind": finding_kind,
                "severity": severity,
            })
            result["channel"] = "webhook"
        else:
            result["reason"] = "no_channel"
            return result

        self._last_notified[cooldown_key] = now
        result["notified"] = True
        return result


def _extract_snapshot_findings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract notification-worthy findings from a hub snapshot.

    Builds synthetic finding dicts from repo-level risk signals (errors,
    stalled state, blocked tasks, security/quality issues).
    """
    findings: list[dict[str, Any]] = []
    repos = snapshot.get("repos")
    if not isinstance(repos, list):
        return findings

    for repo_data in repos:
        if not isinstance(repo_data, dict):
            continue
        repo_name = str(repo_data.get("name") or "").strip()
        if not repo_name:
            continue

        errors = repo_data.get("errors")
        if isinstance(errors, list) and errors:
            findings.append({
                "kind": "repo-errors",
                "severity": "error",
                "lane": "ecosystem",
                "message": f"{repo_name}: {errors[0]}",
                "repo": repo_name,
            })

        if repo_data.get("stalled"):
            stall_reasons = repo_data.get("stall_reasons") or []
            reason = stall_reasons[0] if stall_reasons else "no active execution"
            findings.append({
                "kind": "repo-stalled",
                "severity": "error",
                "lane": "ecosystem",
                "message": f"{repo_name} stalled: {reason}",
                "repo": repo_name,
            })

        blocked = int(repo_data.get("blocked_open") or 0)
        if blocked > 0:
            findings.append({
                "kind": "blocked-tasks",
                "severity": "warning",
                "lane": "ecosystem",
                "message": f"{repo_name}: {blocked} blocked open tasks",
                "repo": repo_name,
            })

        sec = repo_data.get("security") if isinstance(repo_data.get("security"), dict) else {}
        if int(sec.get("critical") or 0) > 0:
            findings.append({
                "kind": "security-critical",
                "severity": "critical",
                "lane": "secdrift",
                "message": f"{repo_name}: {sec['critical']} critical security finding(s)",
                "repo": repo_name,
            })

        qa = repo_data.get("quality") if isinstance(repo_data.get("quality"), dict) else {}
        if int(qa.get("critical") or 0) > 0:
            findings.append({
                "kind": "quality-critical",
                "severity": "critical",
                "lane": "qadrift",
                "message": f"{repo_name}: {qa['critical']} critical quality finding(s)",
                "repo": repo_name,
            })

    return findings


def process_snapshot_notifications(
    snapshot: dict[str, Any],
    config: dict[str, Any],
    *,
    outcome_ledger_path: Path | None = None,
    dispatcher: NotificationDispatcher | None = None,
) -> dict[str, Any]:
    """Process an ecosystem snapshot and fire notifications for significant findings.

    This is the main integration point called from the hub server tick loop.
    Returns a summary dict.
    """
    result: dict[str, Any] = {
        "enabled": bool(config.get("enabled")),
        "findings_checked": 0,
        "notifications_sent": 0,
        "details": [],
    }

    if not config.get("enabled"):
        return result

    if dispatcher is None:
        dispatcher = NotificationDispatcher(config)

    # Load outcome history if a ledger path is provided
    outcome_history: list[dict[str, Any]] = []
    if outcome_ledger_path and outcome_ledger_path.exists():
        try:
            outcomes = read_outcomes(outcome_ledger_path)
            outcome_history = [
                {"finding_key": o.finding_key, "outcome": o.outcome}
                for o in outcomes
            ]
        except Exception:
            pass

    findings = _extract_snapshot_findings(snapshot)
    result["findings_checked"] = len(findings)

    for finding in findings:
        repo_name = str(finding.get("repo") or "")
        notify_result = dispatcher.check_and_notify(
            finding,
            outcome_history=outcome_history,
            repo_name=repo_name,
        )
        if notify_result.get("notified"):
            result["notifications_sent"] += 1
        result["details"].append(notify_result)

    return result
