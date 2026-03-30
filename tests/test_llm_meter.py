# ABOUTME: Tests for the LLM spend meter — recording, cost estimation, and querying.
# ABOUTME: Validates record_spend, extract_usage_from_claude_json, extract_usage_from_api_response, query_spend.

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from driftdriver.llm_meter import (
    SpendRecord,
    estimate_cost,
    extract_usage_from_api_response,
    extract_usage_from_claude_json,
    query_spend,
    record_spend,
)


# --- estimate_cost ---


def test_estimate_cost_haiku():
    cost = estimate_cost("haiku", input_tokens=1000, output_tokens=500)
    assert cost > 0
    # Haiku is cheap — should be under 1 cent for this volume
    assert cost < 0.01


def test_estimate_cost_sonnet():
    cost = estimate_cost("sonnet", input_tokens=1000, output_tokens=500)
    assert cost > 0
    # Sonnet more expensive than haiku
    assert cost > estimate_cost("haiku", input_tokens=1000, output_tokens=500)


def test_estimate_cost_opus():
    cost = estimate_cost("opus", input_tokens=1000, output_tokens=500)
    assert cost > 0
    assert cost > estimate_cost("sonnet", input_tokens=1000, output_tokens=500)


def test_estimate_cost_unknown_model_uses_sonnet_rate():
    cost = estimate_cost("unknown-model-xyz", input_tokens=1000, output_tokens=500)
    sonnet_cost = estimate_cost("sonnet", input_tokens=1000, output_tokens=500)
    assert cost == sonnet_cost


def test_estimate_cost_zero_tokens():
    assert estimate_cost("haiku", input_tokens=0, output_tokens=0) == 0.0


# --- extract_usage_from_claude_json ---


def test_extract_usage_from_claude_json_with_usage():
    cli_output = {
        "result": "some text",
        "usage": {"input_tokens": 150, "output_tokens": 42},
    }
    tokens = extract_usage_from_claude_json(cli_output)
    assert tokens == (150, 42)


def test_extract_usage_from_claude_json_missing_usage():
    cli_output = {"result": "some text"}
    tokens = extract_usage_from_claude_json(cli_output)
    assert tokens is None


def test_extract_usage_from_claude_json_not_dict():
    assert extract_usage_from_claude_json("plain string") is None


# --- extract_usage_from_api_response ---


def test_extract_usage_from_api_response():
    body = {
        "content": [{"type": "text", "text": "hello"}],
        "usage": {"input_tokens": 200, "output_tokens": 80},
    }
    tokens = extract_usage_from_api_response(body)
    assert tokens == (200, 80)


def test_extract_usage_from_api_response_missing():
    body = {"content": [{"type": "text", "text": "hello"}]}
    assert extract_usage_from_api_response(body) is None


# --- record_spend ---


def test_record_spend_creates_file(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    record_spend(
        agent="factory-brain",
        model="haiku",
        input_tokens=100,
        output_tokens=50,
        log_path=log_path,
    )
    assert log_path.exists()
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["agent"] == "factory-brain"
    assert rec["model"] == "haiku"
    assert rec["input_tokens"] == 100
    assert rec["output_tokens"] == 50
    assert "ts" in rec
    assert "estimated_cost_usd" in rec
    assert rec["estimated_cost_usd"] > 0


def test_record_spend_appends(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    record_spend(agent="a1", model="haiku", input_tokens=10, output_tokens=5, log_path=log_path)
    record_spend(agent="a2", model="sonnet", input_tokens=20, output_tokens=10, log_path=log_path)
    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 2


def test_record_spend_returns_record(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    rec = record_spend(agent="test", model="haiku", input_tokens=10, output_tokens=5, log_path=log_path)
    assert isinstance(rec, SpendRecord)
    assert rec.agent == "test"
    assert rec.model == "haiku"
    assert rec.input_tokens == 10
    assert rec.output_tokens == 5


# --- query_spend ---


def _write_spend_log(log_path: Path, records: list[dict]):
    with log_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_query_spend_tail_filter(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    now = time.time()
    records = [
        {"ts": now - 7200, "agent": "old", "model": "haiku", "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.0001},
        {"ts": now - 1800, "agent": "recent", "model": "haiku", "input_tokens": 20, "output_tokens": 10, "estimated_cost_usd": 0.0002},
    ]
    _write_spend_log(log_path, records)

    result = query_spend(log_path=log_path, tail_hours=1)
    assert len(result["records"]) == 1
    assert result["records"][0]["agent"] == "recent"


def test_query_spend_by_agent(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    now = time.time()
    records = [
        {"ts": now - 100, "agent": "brain", "model": "haiku", "input_tokens": 10, "output_tokens": 5, "estimated_cost_usd": 0.001},
        {"ts": now - 50, "agent": "northstar", "model": "sonnet", "input_tokens": 20, "output_tokens": 10, "estimated_cost_usd": 0.002},
        {"ts": now - 30, "agent": "brain", "model": "haiku", "input_tokens": 30, "output_tokens": 15, "estimated_cost_usd": 0.003},
    ]
    _write_spend_log(log_path, records)

    result = query_spend(log_path=log_path, tail_hours=24, by_agent=True)
    assert "by_agent" in result
    agents = result["by_agent"]
    assert "brain" in agents
    assert "northstar" in agents
    assert agents["brain"]["total_cost_usd"] == pytest.approx(0.004)
    assert agents["brain"]["call_count"] == 2
    assert agents["northstar"]["call_count"] == 1


def test_query_spend_empty_file(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    log_path.touch()
    result = query_spend(log_path=log_path, tail_hours=24)
    assert result["records"] == []
    assert result["total_cost_usd"] == 0.0


def test_query_spend_missing_file(tmp_path: Path):
    log_path = tmp_path / "llm-spend.jsonl"
    result = query_spend(log_path=log_path, tail_hours=24)
    assert result["records"] == []
