# ABOUTME: Tests for debatedrift TOML fence config parser.
# ABOUTME: Verifies fence extraction, defaults, and validation.
from __future__ import annotations

import pytest
from driftdriver.debatedrift.config import DebateDriftConfig, parse_debatedrift_config


class TestParseFence:
    def test_extracts_type_and_defaults(self) -> None:
        desc = (
            "Do the thing.\n\n"
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"planning\"\n"
            "```\n"
        )
        cfg = parse_debatedrift_config(desc)
        assert cfg is not None
        assert cfg.type == "planning"
        assert cfg.max_rounds == 5
        assert cfg.watchdog_timeout == 90
        assert cfg.context_files == []

    def test_extracts_all_fields(self) -> None:
        desc = (
            "```debatedrift\n"
            "schema = 1\n"
            "type = \"troubleshoot\"\n"
            "max_rounds = 3\n"
            "watchdog_timeout = 60\n"
            "context_files = [\"src/foo.py\", \"README.md\"]\n"
            "```\n"
        )
        cfg = parse_debatedrift_config(desc)
        assert cfg is not None
        assert cfg.type == "troubleshoot"
        assert cfg.max_rounds == 3
        assert cfg.watchdog_timeout == 60
        assert cfg.context_files == ["src/foo.py", "README.md"]

    def test_returns_none_when_no_fence(self) -> None:
        assert parse_debatedrift_config("just a regular task description") is None

    def test_invalid_type_raises(self) -> None:
        desc = "```debatedrift\nschema = 1\ntype = \"invalid\"\n```\n"
        with pytest.raises(ValueError, match="type"):
            parse_debatedrift_config(desc)

    def test_usecase_type_accepted(self) -> None:
        desc = "```debatedrift\nschema = 1\ntype = \"usecase\"\n```\n"
        cfg = parse_debatedrift_config(desc)
        assert cfg is not None
        assert cfg.type == "usecase"
