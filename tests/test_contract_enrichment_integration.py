# ABOUTME: Integration tests for contract enrichment wiring
# ABOUTME: Verifies cmd_enrich works with empty and non-empty knowledge

from driftdriver.wire import cmd_enrich


def test_cmd_enrich_with_empty_knowledge():
    """cmd_enrich returns zero learnings when knowledge is empty."""
    result = cmd_enrich("task-1", "Implement feature X", "myproject", [])
    assert result["learnings_added"] == 0
    assert result["contract_updated"] is False


def test_cmd_enrich_with_relevant_knowledge():
    """cmd_enrich processes knowledge entries."""
    knowledge = [
        {
            "category": "pattern",
            "content": "Always run tests before commit in this project",
            "confidence": 0.9,
            "scope": "",
        },
    ]
    result = cmd_enrich("task-1", "Implement feature X", "myproject", knowledge)
    assert isinstance(result["learnings_added"], int)
    assert isinstance(result["contract_updated"], bool)
