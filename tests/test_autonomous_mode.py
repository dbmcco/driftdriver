# ABOUTME: Tests for directive vocabulary gating by speedriftd control mode.
# ABOUTME: Verifies that each mode (observe, supervise, autonomous, manual) allows correct directives.

from __future__ import annotations

import unittest

from driftdriver.speedriftd_state import directives_allowed_for_mode


class TestAutonomousModeVocabulary(unittest.TestCase):
    def test_observe_allows_no_directives(self) -> None:
        allowed = directives_allowed_for_mode("observe")
        self.assertEqual(len(allowed), 0)

    def test_supervise_allows_service_and_log_only(self) -> None:
        allowed = directives_allowed_for_mode("supervise")
        self.assertIn("start_service", allowed)
        self.assertIn("stop_service", allowed)
        self.assertIn("log_to_task", allowed)
        self.assertNotIn("create_task", allowed)
        self.assertNotIn("claim_task", allowed)

    def test_autonomous_allows_full_vocabulary(self) -> None:
        allowed = directives_allowed_for_mode("autonomous")
        self.assertIn("create_task", allowed)
        self.assertIn("claim_task", allowed)
        self.assertIn("start_service", allowed)
        self.assertIn("evolve_prompt", allowed)

    def test_manual_allows_no_directives(self) -> None:
        allowed = directives_allowed_for_mode("manual")
        self.assertEqual(len(allowed), 0)
