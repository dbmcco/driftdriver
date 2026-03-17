# ABOUTME: Tests for decision queue Telegram notification formatting and sending.
# ABOUTME: Mocks send_telegram to verify message format and notification flow.
from __future__ import annotations

import unittest
from unittest.mock import patch

from driftdriver.decision_queue import DecisionRecord
from driftdriver.decision_notifier import format_decision_message, notify_decision


class FormatDecisionMessageTests(unittest.TestCase):
    def _make_decision(self, **overrides: object) -> DecisionRecord:
        defaults = {
            "id": "dec-20260313-abc123",
            "repo": "my-project",
            "status": "pending",
            "question": "Should we enable feature X?",
            "context": {"options": ["A: Yes", "B: No"]},
            "category": "feature",
            "created_at": "2026-03-13T18:00:00+00:00",
        }
        defaults.update(overrides)
        return DecisionRecord(**defaults)

    def test_message_contains_repo(self) -> None:
        msg = format_decision_message(self._make_decision())
        self.assertIn("my-project", msg)

    def test_message_contains_question(self) -> None:
        msg = format_decision_message(self._make_decision())
        self.assertIn("Should we enable feature X?", msg)

    def test_message_contains_decision_id(self) -> None:
        msg = format_decision_message(self._make_decision())
        self.assertIn("dec-20260313-abc123", msg)

    def test_message_contains_options(self) -> None:
        msg = format_decision_message(self._make_decision())
        self.assertIn("A: Yes", msg)
        self.assertIn("B: No", msg)

    def test_message_without_options(self) -> None:
        msg = format_decision_message(self._make_decision(context={}))
        self.assertIn("my-project", msg)
        self.assertIn("Should we enable feature X?", msg)

    def test_message_contains_category(self) -> None:
        msg = format_decision_message(self._make_decision())
        self.assertIn("feature", msg)

    def test_message_contains_reply_hint(self) -> None:
        msg = format_decision_message(self._make_decision())
        self.assertIn("Reply to this message", msg)


class NotifyDecisionTests(unittest.TestCase):
    def _make_decision(self) -> DecisionRecord:
        return DecisionRecord(
            id="dec-20260313-abc123",
            repo="my-project",
            status="pending",
            question="Should we enable feature X?",
            context={"options": ["A: Yes", "B: No"]},
            category="feature",
            created_at="2026-03-13T18:00:00+00:00",
        )

    @patch("driftdriver.decision_notifier.send_telegram", return_value=True)
    def test_notify_calls_send_telegram(self, mock_send: object) -> None:
        result = notify_decision(
            self._make_decision(),
            bot_token="test-token",
            chat_id="test-chat",
        )
        self.assertTrue(result)
        mock_send.assert_called_once()  # type: ignore[attr-defined]
        call_kwargs = mock_send.call_args.kwargs  # type: ignore[attr-defined]
        self.assertEqual(call_kwargs["bot_token"], "test-token")
        self.assertEqual(call_kwargs["chat_id"], "test-chat")
        self.assertIn("my-project", call_kwargs["message"])

    @patch("driftdriver.decision_notifier.send_telegram", return_value=False)
    def test_notify_returns_false_on_send_failure(self, mock_send: object) -> None:
        result = notify_decision(
            self._make_decision(),
            bot_token="test-token",
            chat_id="test-chat",
        )
        self.assertFalse(result)

    @patch("driftdriver.decision_notifier.load_factory_bot_config", return_value={"bot_token": "cfg-token", "chat_id": "cfg-chat"})
    @patch("driftdriver.decision_notifier.send_telegram", return_value=True)
    def test_notify_loads_config_when_no_credentials(self, mock_send: object, mock_cfg: object) -> None:
        result = notify_decision(self._make_decision())
        self.assertTrue(result)
        mock_cfg.assert_called_once()  # type: ignore[attr-defined]
        call_kwargs = mock_send.call_args.kwargs  # type: ignore[attr-defined]
        self.assertEqual(call_kwargs["bot_token"], "cfg-token")
        self.assertEqual(call_kwargs["chat_id"], "cfg-chat")

    @patch("driftdriver.decision_notifier.load_factory_bot_config", return_value=None)
    def test_notify_returns_false_when_no_config(self, mock_cfg: object) -> None:
        result = notify_decision(self._make_decision())
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
