# ABOUTME: Tests for ChatHistory JSONL persistence module.
# ABOUTME: Covers append, load, clear, limit, and Anthropic message conversion.

import json

import pytest

from driftdriver.ecosystem_hub.chat_history import ChatHistory


def test_append_and_load(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("hello", "hi there")
    turns = h.load()
    assert len(turns) == 1
    assert turns[0]["user"] == "hello"
    assert turns[0]["assistant"] == "hi there"
    assert "timestamp" in turns[0]


def test_load_empty(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    assert h.load() == []


def test_clear(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("a", "b")
    h.clear()
    assert h.load() == []


def test_limit(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    for i in range(10):
        h.append(f"u{i}", f"a{i}")
    turns = h.load(limit=3)
    assert len(turns) == 3
    assert turns[-1]["user"] == "u9"


def test_multiple_turns(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("first", "first response")
    h.append("second", "second response")
    turns = h.load()
    assert len(turns) == 2
    assert turns[0]["user"] == "first"
    assert turns[1]["user"] == "second"


def test_to_anthropic_messages(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    h.append("what is the status?", "here is the status...")
    msgs = h.to_anthropic_messages(limit=10)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "what is the status?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "here is the status..."


def test_to_anthropic_messages_empty(tmp_path):
    h = ChatHistory(tmp_path / "chat.jsonl")
    assert h.to_anthropic_messages() == []


def test_survives_corrupt_line(tmp_path):
    path = tmp_path / "chat.jsonl"
    path.write_text('{"user":"a","assistant":"b","timestamp":"t"}\nNOT JSON\n{"user":"c","assistant":"d","timestamp":"t"}\n')
    h = ChatHistory(path)
    turns = h.load()
    assert len(turns) == 2
    assert turns[0]["user"] == "a"
    assert turns[1]["user"] == "c"


def test_creates_parent_dirs(tmp_path):
    path = tmp_path / "deep" / "nested" / "chat.jsonl"
    h = ChatHistory(path)
    h.append("x", "y")
    assert path.exists()
