# ABOUTME: Telegram notifications for decision queue items via dedicated DarklyFactory bot.
# ABOUTME: Formats decision records into messages and sends via factory-specific bot token.
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from driftdriver.decision_queue import DecisionRecord
from driftdriver.factory_brain.telegram import send_telegram


def _workgraph_config_dir() -> Path:
    env_value = os.environ.get("WORKGRAPH_CONFIG_DIR", "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve(strict=False)
    return (Path.home() / ".config" / "workgraph").resolve(strict=False)


def _config_path() -> Path:
    return _workgraph_config_dir() / "notify.toml"


def _default_ledger_path() -> Path:
    return _workgraph_config_dir() / "factory-brain" / "notification-ledger.jsonl"


def load_factory_bot_config() -> dict[str, str] | None:
    """Load the dedicated factory bot config from [telegram_factory] section."""
    config_path = _config_path()
    if not config_path.exists():
        return None
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    section = data.get("telegram_factory")
    if not isinstance(section, dict):
        return None
    token = section.get("bot_token")
    chat_id = section.get("chat_id")
    if not token or not chat_id:
        return None
    return {"bot_token": str(token), "chat_id": str(chat_id)}


def format_decision_message(decision: DecisionRecord) -> str:
    """Format a DecisionRecord into a Telegram-friendly message string."""
    lines = [
        "\u2753 *Decision Needed*",
        "",
        f"*Repo:* {decision.repo}",
        f"*Category:* {decision.category}",
        f"*Question:* {decision.question}",
    ]

    options = decision.context.get("options", [])
    if options:
        lines.append("")
        lines.append("*Options:*")
        for opt in options:
            lines.append(f"  \u2022 {opt}")

    provenance_fields = {
        "source_queue": decision.context.get("source_queue"),
        "agent_member": decision.context.get("agent_member"),
        "component": decision.context.get("component"),
        "pattern": decision.context.get("pattern"),
    }
    if any(value for value in provenance_fields.values()):
        lines.append("")
        lines.append("*Provenance:*")
        if provenance_fields["agent_member"]:
            lines.append(f"  \u2022 member: {provenance_fields['agent_member']}")
        if provenance_fields["component"]:
            lines.append(f"  \u2022 component: {provenance_fields['component']}")
        if provenance_fields["pattern"]:
            lines.append(f"  \u2022 pattern: {provenance_fields['pattern']}")
        if provenance_fields["source_queue"]:
            lines.append(f"  \u2022 source: {provenance_fields['source_queue']}")

    lines.append("")
    lines.append(f"`{decision.id}`")
    lines.append("")
    lines.append("_Reply to this message to answer._")

    return "\n".join(lines)


def notify_decision(
    decision: DecisionRecord,
    *,
    bot_token: str | None = None,
    chat_id: str | None = None,
    ledger_path: Path | None = None,
) -> bool:
    """Send a decision notification via the DarklyFactory Telegram bot.

    Uses [telegram_factory] config by default. Falls back to provided credentials.
    Returns True on success, False on failure.
    """
    if not bot_token or not chat_id:
        config = load_factory_bot_config()
        if not config:
            return False
        bot_token = config["bot_token"]
        chat_id = config["chat_id"]

    message = format_decision_message(decision)
    sent = send_telegram(bot_token=bot_token, chat_id=chat_id, message=message)

    target_ledger = ledger_path or _default_ledger_path()
    target_ledger.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "decision_id": decision.id,
        "repo": decision.repo,
        "category": decision.category,
        "channel": "telegram_factory",
        "delivery_status": "sent" if sent else "failed",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "provenance": dict(decision.context),
    }
    with target_ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return sent
