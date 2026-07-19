from __future__ import annotations

from pathlib import Path


FOLLOW_UP = Path(__file__).parents[1] / ".workgraph" / "follow-ups-lifecycle-parity.md"


def test_downstream_lifecycle_and_learning_followups_are_explicit() -> None:
    text = FOLLOW_UP.read_text()
    assert "paia-work" in text
    assert "project abandon" in text
    assert "task cancel" in text
    assert "404" in text and "409" in text and "422" in text
    assert "paia-agents" in text
    assert "tool-failure event" in text
    assert "acceptance criteria" in text.lower()
    assert "touch set" in text.lower()
