# ABOUTME: Tests for the loop detection module.
# ABOUTME: Covers fingerprint recording, loop detection, suggestion generation, and state management.
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.loop_detection import (
    LoopDetection,
    clear_state,
    detect_loop,
    fingerprint_action,
    record_action,
    suggest_alternative,
)


class FingerprintActionTests(unittest.TestCase):
    def test_returns_string(self) -> None:
        fp = fingerprint_action("Bash", "abc123")
        self.assertIsInstance(fp, str)

    def test_same_inputs_same_fingerprint(self) -> None:
        fp1 = fingerprint_action("Bash", "abc123")
        fp2 = fingerprint_action("Bash", "abc123")
        self.assertEqual(fp1, fp2)

    def test_different_inputs_different_fingerprint(self) -> None:
        fp1 = fingerprint_action("Bash", "abc123")
        fp2 = fingerprint_action("Read", "abc123")
        self.assertNotEqual(fp1, fp2)


class RecordActionTests(unittest.TestCase):
    def test_record_action_creates_file(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            fp = fingerprint_action("Bash", "somehash")
            record_action(state_dir, fp)
            state_file = state_dir / ".loop-state"
            self.assertTrue(state_file.exists())

    def test_record_action_appends_fingerprint(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            fp = fingerprint_action("Bash", "somehash")
            record_action(state_dir, fp)
            record_action(state_dir, fp)
            state_file = state_dir / ".loop-state"
            lines = state_file.read_text().splitlines()
            self.assertEqual(len(lines), 2)


class DetectLoopTests(unittest.TestCase):
    def test_no_loop_under_threshold(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            fp = fingerprint_action("Bash", "cmd1")
            record_action(state_dir, fp)
            record_action(state_dir, fp)
            result = detect_loop(state_dir, threshold=3)
            self.assertFalse(result.detected)

    def test_loop_detected_at_threshold(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            fp = fingerprint_action("Bash", "cmd1")
            for _ in range(3):
                record_action(state_dir, fp)
            result = detect_loop(state_dir, threshold=3)
            self.assertTrue(result.detected)
            self.assertEqual(result.occurrences, 3)

    def test_different_fingerprints_no_loop(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            for i in range(3):
                fp = fingerprint_action("Bash", f"cmd{i}")
                record_action(state_dir, fp)
            result = detect_loop(state_dir, threshold=3)
            self.assertFalse(result.detected)

    def test_returns_most_common_pattern(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            fp_a = fingerprint_action("Bash", "cmd_a")
            fp_b = fingerprint_action("Read", "cmd_b")
            for _ in range(3):
                record_action(state_dir, fp_a)
            record_action(state_dir, fp_b)
            result = detect_loop(state_dir, threshold=3)
            self.assertTrue(result.detected)
            self.assertEqual(result.pattern, fp_a)

    def test_no_loop_empty_state(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            result = detect_loop(state_dir)
            self.assertFalse(result.detected)


class SuggestAlternativeTests(unittest.TestCase):
    def test_suggestion_mentions_pattern(self) -> None:
        pattern = fingerprint_action("Bash", "some_cmd")
        suggestion = suggest_alternative(pattern, recent_actions=["Bash: some_cmd"])
        self.assertIn(pattern, suggestion)

    def test_suggestion_is_nonempty(self) -> None:
        pattern = fingerprint_action("Read", "somefile")
        suggestion = suggest_alternative(pattern, recent_actions=[])
        self.assertIsInstance(suggestion, str)
        self.assertTrue(len(suggestion) > 0)


class ClearStateTests(unittest.TestCase):
    def test_clear_state_removes_file(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            fp = fingerprint_action("Bash", "cmd1")
            record_action(state_dir, fp)
            state_file = state_dir / ".loop-state"
            self.assertTrue(state_file.exists())
            clear_state(state_dir)
            self.assertFalse(state_file.exists())

    def test_clear_state_noop_when_no_file(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            # Should not raise
            clear_state(state_dir)


if __name__ == "__main__":
    unittest.main()
