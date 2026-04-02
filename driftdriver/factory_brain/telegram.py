# ABOUTME: Telegram notification module for factory brain kill/alert messages.
# ABOUTME: Loads bot config from TOML, POSTs to Telegram Bot API, never raises.

from __future__ import annotations

import json
import logging
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "workgraph" / "notify.toml"


def load_telegram_config(
    config_path: Path | None = None,
    section: str = "telegram_factory",
) -> dict[str, str] | None:
    """Load Telegram bot_token and chat_id from a TOML config file.

    Reads the given section (default: telegram_factory for dFactory bot).
    Returns {"bot_token": ..., "chat_id": ...} or None if missing/invalid.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        logger.debug("Telegram config not found at %s", path)
        return None
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        telegram = data.get(section, {})
        bot_token = telegram.get("bot_token")
        chat_id = telegram.get("chat_id")
        if not bot_token or not chat_id:
            logger.warning("Telegram config at %s missing bot_token or chat_id", path)
            return None
        return {"bot_token": str(bot_token), "chat_id": str(chat_id)}
    except Exception as exc:
        logger.warning("Failed to load Telegram config from %s: %s", path, exc)
        return None


def send_telegram(*, bot_token: str, chat_id: str, message: str) -> bool:
    """Send a message via Telegram Bot API. Returns True on success, False on failure.

    Never raises -- all exceptions are caught and logged.
    """
    prefixed = "\U0001f3ed *Factory Brain*\n\n" + message
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": prefixed,
        "parse_mode": "Markdown",
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if body.get("ok"):
                logger.info("Telegram message sent successfully")
                return True
            logger.warning("Telegram API returned ok=false: %s", body)
            return False
    except Exception as exc:
        logger.warning("Failed to send Telegram message: %s", exc)
        return False
