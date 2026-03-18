# ABOUTME: Tests for debatedrift agent prompt generation.
# ABOUTME: Verifies sentinel instructions, round protocol, and proxy constitution path.
from __future__ import annotations

from pathlib import Path
from driftdriver.debatedrift.prompts import (
    debater_a_prompt,
    debater_b_prompt,
    proxy_prompt,
)


class TestDebaterAPrompt:
    def test_contains_topic(self) -> None:
        p = debater_a_prompt(topic="cache invalidation strategy", task_id="t1",
                              max_rounds=5, context_files=[])
        assert "cache invalidation strategy" in p

    def test_contains_round_end_sentinel(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "[ROUND:END]" in p

    def test_contains_wg_msg_instruction(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "wg msg list" in p

    def test_contains_task_id(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="my-task-123", max_rounds=5, context_files=[])
        assert "my-task-123" in p

    def test_mentions_proxy_presence(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "proxy" in p.lower()

    def test_includes_context_files(self) -> None:
        p = debater_a_prompt(topic="foo", task_id="t1", max_rounds=5,
                              context_files=["src/main.py", "README.md"])
        assert "src/main.py" in p
        assert "README.md" in p


class TestDebaterBPrompt:
    def test_diverges_from_a(self) -> None:
        pa = debater_a_prompt(topic="x", task_id="t", max_rounds=5, context_files=[])
        pb = debater_b_prompt(topic="x", task_id="t", max_rounds=5, context_files=[])
        # B's core instruction should differ — B is the contrarian
        assert pa != pb
        assert "contrarian" in pb.lower() or "challenge" in pb.lower() or "diverge" in pb.lower()

    def test_contains_round_end_sentinel(self) -> None:
        p = debater_b_prompt(topic="foo", task_id="t1", max_rounds=5, context_files=[])
        assert "[ROUND:END]" in p


class TestProxyPrompt:
    def test_contains_topic(self) -> None:
        p = proxy_prompt(topic="routing strategy", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "routing strategy" in p

    def test_contains_concluded_sentinel(self) -> None:
        p = proxy_prompt(topic="foo", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "DEBATE:CONCLUDED" in p

    def test_contains_deadlock_sentinel(self) -> None:
        p = proxy_prompt(topic="foo", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "DEBATE:DEADLOCK" in p

    def test_proxy_does_not_mention_round_end(self) -> None:
        # Proxy doesn't participate in rounds — no [ROUND:END]
        p = proxy_prompt(topic="foo", task_id="t1",
                         context_files=[], constitution_path=Path("/tmp/const.md"))
        assert "[ROUND:END]" not in p
