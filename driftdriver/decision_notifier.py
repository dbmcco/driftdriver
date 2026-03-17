# ABOUTME: Telegram notifications for decision queue items via dedicated DarklyFactory bot.
# ABOUTME: Formats decision records into messages and sends via factory-specific bot token.
from __future__ import annotations

from pathlib import Path

from driftdriver.decision_queue import DecisionRecord
from driftdriver.factory_brain.telegram import send_telegram

_CONFIG_PATH = Path("~/.config/workgraph/notify.toml").expanduser()


def load_factory_bot_config() -> dict[str, str] | None:
    """Load the dedicated factory bot config from [telegram_factory] section."""
    if not _CONFIG_PATH.exists():
        return None
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        data = tomllib.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
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
    return send_telegram(bot_token=bot_token, chat_id=chat_id, message=message)
