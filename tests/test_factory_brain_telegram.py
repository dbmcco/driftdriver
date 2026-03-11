# ABOUTME: Tests for Telegram notification module (config loading and message sending).
# ABOUTME: Covers config parsing, missing config, successful send, and failure handling.

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram


class TestLoadTelegramConfig(unittest.TestCase):
    def test_load_telegram_config(self) -> None:
        """Write a temp TOML file and verify it loads correctly."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "notify.toml"
            config_path.write_text(
                '[telegram]\nbot_token = "123:ABC"\nchat_id = "-100999"\n'
            )
            result = load_telegram_config(config_path)
            self.assertIsNotNone(result)
            self.assertEqual(result["bot_token"], "123:ABC")
            self.assertEqual(result["chat_id"], "-100999")

    def test_load_telegram_config_missing(self) -> None:
        """Verify None is returned when config file does not exist."""
        missing = Path("/tmp/nonexistent_driftdriver_test_notify.toml")
        result = load_telegram_config(missing)
        self.assertIsNone(result)

    def test_load_telegram_config_missing_fields(self) -> None:
        """Verify None when TOML exists but lacks required fields."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "notify.toml"
            config_path.write_text("[telegram]\nbot_token = \"123:ABC\"\n")
            result = load_telegram_config(config_path)
            self.assertIsNone(result)


class TestSendTelegram(unittest.TestCase):
    @patch("driftdriver.factory_brain.telegram.urllib.request.urlopen")
    def test_send_telegram_success(self, mock_urlopen: MagicMock) -> None:
        """Mock urlopen and verify True on success."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = send_telegram(
            bot_token="123:ABC",
            chat_id="-100999",
            message="Test alert",
        )
        self.assertTrue(result)
        mock_urlopen.assert_called_once()
        # Verify the request payload
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertIn("123:ABC", req.full_url)
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["chat_id"], "-100999")
        self.assertEqual(body["parse_mode"], "Markdown")
        self.assertIn("Factory Brain", body["text"])
        self.assertIn("Test alert", body["text"])

    @patch("driftdriver.factory_brain.telegram.urllib.request.urlopen")
    def test_send_telegram_failure(self, mock_urlopen: MagicMock) -> None:
        """Mock urlopen to raise and verify False returned (no exception)."""
        mock_urlopen.side_effect = Exception("network error")
        result = send_telegram(
            bot_token="123:ABC",
            chat_id="-100999",
            message="Test alert",
        )
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
