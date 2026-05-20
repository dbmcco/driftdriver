# ABOUTME: Tests for tmux pane classification heuristics across commands, titles, and content.
# ABOUTME: Covers known agent types plus shell, idle, and unknown fallbacks.

from __future__ import annotations

import pytest

from driftdriver.tmux_monitor import detection
from driftdriver.tmux_monitor.detection import PaneClassification, classify_pane


@pytest.fixture(autouse=True)
def _no_real_ps(monkeypatch, request):
    if request.node.cls and request.node.cls.__name__ == "TestForegroundProcessLookup":
        return
    monkeypatch.setattr(detection, "_get_foreground_process", lambda tty: ("", 0))


class TestCommandMapClassification:
    def test_claude_command_maps_to_claude_code(self):
        cls = classify_pane("", "/dev/ttys001", current_command="claude")
        assert cls.pane_type == "claude-code"

    def test_codex_command_maps_to_codex(self):
        cls = classify_pane("", "/dev/ttys002", current_command="codex")
        assert cls.pane_type == "codex"

    def test_codex_aarch_command_maps_to_codex(self):
        cls = classify_pane("", "/dev/ttys003", current_command="codex-aarch64-a")
        assert cls.pane_type == "codex"

    def test_opencode_command(self):
        cls = classify_pane("", "/dev/ttys004", current_command="opencode")
        assert cls.pane_type == "opencode"

    def test_kilocode_command(self):
        cls = classify_pane("", "/dev/ttys005", current_command="kilocode")
        assert cls.pane_type == "kilocode"

    def test_pi_command_maps_to_pi_dev(self):
        cls = classify_pane("", "/dev/ttys006", current_command="pi")
        assert cls.pane_type == "pi-dev"

    def test_versioned_command_maps_to_claude_code(self):
        cls = classify_pane("", "/dev/ttys007", current_command="2.1.128")
        assert cls.pane_type == "claude-code"

    def test_command_path_strips_to_basename(self):
        cls = classify_pane("", "/dev/ttys008", current_command="/usr/local/bin/claude")
        assert cls.pane_type == "claude-code"

    def test_command_is_case_insensitive(self):
        cls = classify_pane("", "/dev/ttys009", current_command="CLAUDE")
        assert cls.pane_type == "claude-code"

    def test_command_preserves_original_in_process_name(self):
        cls = classify_pane("", "/dev/ttys010", current_command="claude")
        assert cls.process_name == "claude"

    def test_pane_title_passed_through_command_match(self):
        cls = classify_pane("", "/dev/ttys011", current_command="claude", pane_title="some title")
        assert cls.title == "some title"


class TestTitlePatternClassification:
    def test_title_with_claude_code(self):
        cls = classify_pane("", "/dev/ttys001", pane_title="Claude Code")
        assert cls.pane_type == "claude-code"

    def test_title_with_no_space_claude_code(self):
        cls = classify_pane("", "/dev/ttys001", pane_title="claudecode")
        assert cls.pane_type == "claude-code"

    def test_title_pattern_is_case_insensitive(self):
        cls = classify_pane("", "/dev/ttys001", pane_title="CLAUDE  CODE")
        assert cls.pane_type == "claude-code"

    def test_title_only_when_command_does_not_match(self):
        cls = classify_pane("", "/dev/ttys001", current_command="vim", pane_title="Claude Code")
        assert cls.pane_type == "claude-code"


class TestContentPatternClassification:
    def test_claude_code_box_drawing_chars(self):
        content = "doing work\ncontext\ndone"
        cls = classify_pane(content, "/dev/ttys001")
        assert cls.pane_type == "idle"

    def test_claude_code_box_drawing_chars_detected(self):
        content = "\u256d\u2500 doing work\n\u2502 context\n\u2570\u2500 done"
        cls = classify_pane(content, "/dev/ttys001")
        assert cls.pane_type == "claude-code"

    def test_codex_prompt_pattern(self):
        content = "codex > waiting for input"
        cls = classify_pane(content, "/dev/ttys001")
        assert cls.pane_type == "codex"

    def test_opencode_in_content(self):
        cls = classify_pane("welcome to opencode", "/dev/ttys001")
        assert cls.pane_type == "opencode"

    def test_kilocode_in_content(self):
        cls = classify_pane("running kilocode now", "/dev/ttys001")
        assert cls.pane_type == "kilocode"

    def test_pi_dev_in_content(self):
        cls = classify_pane("pi.dev ready", "/dev/ttys001")
        assert cls.pane_type == "pi-dev"

    def test_content_only_when_no_command_or_title(self):
        cls = classify_pane("\u256d\u2500 banner", "/dev/ttys001", current_command="", pane_title="")
        assert cls.pane_type == "claude-code"


class TestPriorityOrdering:
    def test_command_beats_title(self):
        cls = classify_pane("", "/dev/ttys001", current_command="codex", pane_title="Claude Code")
        assert cls.pane_type == "codex"

    def test_command_beats_content(self):
        cls = classify_pane("\u256d\u2500", "/dev/ttys001", current_command="codex")
        assert cls.pane_type == "codex"

    def test_title_beats_content(self):
        cls = classify_pane("codex > prompt", "/dev/ttys001", pane_title="Claude Code")
        assert cls.pane_type == "claude-code"


class TestShellAndFallbackClassification:
    def test_shell_via_foreground_process(self, monkeypatch):
        monkeypatch.setattr(detection, "_get_foreground_process", lambda tty: ("zsh", 1234))
        cls = classify_pane("$ ls", "/dev/ttys001")
        assert cls.pane_type == "shell"
        assert cls.pid == 1234

    def test_shell_via_command_only(self, monkeypatch):
        monkeypatch.setattr(detection, "_get_foreground_process", lambda tty: ("/bin/zsh", 99))
        cls = classify_pane("", "/dev/ttys001", current_command="zsh")
        assert cls.pane_type == "shell"

    def test_unknown_when_no_match(self, monkeypatch):
        monkeypatch.setattr(detection, "_get_foreground_process", lambda tty: ("vim", 42))
        cls = classify_pane("editing", "/dev/ttys001")
        assert cls.pane_type == "unknown"
        assert cls.pid == 42

    def test_idle_when_no_tty_process_and_nothing_else(self):
        cls = classify_pane("", "/dev/ttys001")
        assert cls.pane_type == "idle"
        assert cls.pid == 0


class TestPaneClassificationSerialization:
    def test_to_dict_round_trip(self):
        c = PaneClassification(
            pane_type="claude-code",
            process_name="claude",
            pid=42,
            tty="/dev/ttys000",
            title="t",
        )
        assert c.to_dict() == {
            "type": "claude-code",
            "process_name": "claude",
            "pid": 42,
            "tty": "/dev/ttys000",
            "title": "t",
        }


class TestForegroundProcessLookup:
    def test_get_foreground_process_parses_ps_output(self, monkeypatch):
        class Result:
            stdout = "12345 zsh\n67890 claude\n"

        monkeypatch.setattr(
            detection.subprocess,
            "run",
            lambda *a, **k: Result(),
        )
        proc, pid = detection._get_foreground_process("/dev/ttys001")
        assert proc == "claude"
        assert pid == 67890

    def test_get_foreground_process_handles_empty_tty(self):
        assert detection._get_foreground_process("") == ("", 0)

    def test_get_foreground_process_handles_subprocess_error(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("nope")

        monkeypatch.setattr(detection.subprocess, "run", boom)
        assert detection._get_foreground_process("/dev/ttys001") == ("", 0)

    def test_get_foreground_process_handles_empty_output(self, monkeypatch):
        class Result:
            stdout = ""

        monkeypatch.setattr(detection.subprocess, "run", lambda *a, **k: Result())
        assert detection._get_foreground_process("/dev/ttys001") == ("", 0)
