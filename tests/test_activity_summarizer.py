# ABOUTME: Tests for the LLM activity summarizer with mocked anthropic client.
# ABOUTME: Verifies cache-key logic, fallback on error, and prompt content.
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestActivitySummarizer(unittest.TestCase):
    def _make_digest(self, hash_: str | None, summary_hash: str | None = None) -> dict:
        return {
            "name": "lodestar",
            "last_commit_hash": hash_,
            "summary": None,
            "summary_hash": summary_hash,
            "changed_files": ["src/scenario.py", "src/briefings.py"],
            "windows": {
                "7d": {"count": 3, "subjects": ["feat: add regret scoring", "fix: briefing 404", "chore: deps"]},
                "24h": {"count": 1, "subjects": ["feat: add regret scoring"]},
                "48h": {"count": 2, "subjects": ["feat: add regret scoring", "fix: briefing 404"]},
                "72h": {"count": 3, "subjects": ["feat: add regret scoring", "fix: briefing 404", "chore: deps"]},
            },
        }

    def test_summary_requested_when_hash_differs(self):
        digest = self._make_digest("abc123", summary_hash=None)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Work happened on lodestar.")]
        )
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        mock_client.messages.create.assert_called_once()
        self.assertEqual(result["summary"], "Work happened on lodestar.")
        self.assertEqual(result["summary_hash"], "abc123")

    def test_summary_skipped_when_hash_matches(self):
        digest = self._make_digest("abc123", summary_hash="abc123")
        digest["summary"] = "Existing summary."
        mock_client = MagicMock()
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        mock_client.messages.create.assert_not_called()
        self.assertEqual(result["summary"], "Existing summary.")

    def test_fallback_to_none_on_api_error(self):
        digest = self._make_digest("abc123")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API down")
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        self.assertIsNone(result["summary"])
        self.assertIsNone(result["summary_hash"])

    def test_prompt_contains_commit_subjects_and_files(self):
        digest = self._make_digest("abc123")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="Summary text.")]
        )
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        summarize_repo(digest, client=mock_client)
        call_kwargs = mock_client.messages.create.call_args
        prompt_text = call_kwargs[1]["messages"][0]["content"]
        self.assertIn("feat: add regret scoring", prompt_text)
        self.assertIn("src/scenario.py", prompt_text)
        self.assertIn("lodestar", prompt_text)

    def test_no_commits_skips_summarization(self):
        digest = self._make_digest(None)
        mock_client = MagicMock()
        from driftdriver.ecosystem_hub.activity_summarizer import summarize_repo
        result = summarize_repo(digest, client=mock_client)
        mock_client.messages.create.assert_not_called()
        self.assertIsNone(result["summary"])


if __name__ == "__main__":
    unittest.main()
