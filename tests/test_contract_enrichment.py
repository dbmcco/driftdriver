# ABOUTME: Tests for task contract enrichment with Lessons MCP learnings injection
# ABOUTME: Covers keyword matching, enrichment result, formatting, and edge cases

from driftdriver.contract_enrichment import (
    EnrichmentResult,
    enrich_contract,
    find_relevant_learnings,
    format_context_block,
    build_enriched_description,
)


def _make_knowledge_entries():
    """Return representative knowledge entries similar to Lessons MCP output."""
    return [
        {
            "category": "testing",
            "content": "Always mock external API calls in unit tests to avoid flakiness",
            "confidence": 0.9,
        },
        {
            "category": "python",
            "content": "Use dataclasses for simple data containers instead of plain dicts",
            "confidence": 0.8,
        },
        {
            "category": "performance",
            "content": "Cache database query results to improve response time",
            "confidence": 0.7,
        },
        {
            "category": "testing",
            "content": "Write integration tests for database interactions",
            "confidence": 0.85,
        },
    ]


# ---------------------------------------------------------------------------
# test_find_relevant_learnings_returns_top_matches
# ---------------------------------------------------------------------------


def test_find_relevant_learnings_returns_top_matches():
    entries = _make_knowledge_entries()
    # Task description mentions "testing" and "database" — should match 2+ entries
    description = "Write testing strategy for database integration layer"
    results = find_relevant_learnings(description, entries, max_entries=3)

    assert len(results) >= 1
    # The database/integration testing entry should rank highly
    contents = [r["content"] for r in results]
    assert any("test" in c.lower() or "database" in c.lower() for c in contents)


def test_find_relevant_learnings_returns_no_more_than_max_entries():
    entries = _make_knowledge_entries()
    description = "Write testing strategy for database integration layer"
    results = find_relevant_learnings(description, entries, max_entries=2)

    assert len(results) <= 2


# ---------------------------------------------------------------------------
# test_find_relevant_learnings_empty_when_no_match
# ---------------------------------------------------------------------------


def test_find_relevant_learnings_empty_when_no_match():
    entries = _make_knowledge_entries()
    # Description uses words that appear in no knowledge entry
    description = "xyzzy frobnicate quux zorp"
    results = find_relevant_learnings(description, entries)

    assert results == []


# ---------------------------------------------------------------------------
# test_enrich_contract_with_matching_knowledge
# ---------------------------------------------------------------------------


def test_enrich_contract_with_matching_knowledge():
    entries = _make_knowledge_entries()
    result = enrich_contract(
        task_id="task-42",
        task_description="Implement testing for the database query module",
        project="myproject",
        knowledge_entries=entries,
    )

    assert isinstance(result, EnrichmentResult)
    assert result.task_id == "task-42"
    assert result.learnings_added > 0
    assert result.contract_updated is True
    assert len(result.injected_context) > 0


# ---------------------------------------------------------------------------
# test_enrich_contract_no_knowledge_available
# ---------------------------------------------------------------------------


def test_enrich_contract_no_knowledge_available():
    result = enrich_contract(
        task_id="task-99",
        task_description="Some task description",
        project="myproject",
        knowledge_entries=[],
    )

    assert result.task_id == "task-99"
    assert result.learnings_added == 0
    assert result.contract_updated is False
    assert result.injected_context == []


def test_enrich_contract_no_matching_knowledge():
    entries = _make_knowledge_entries()
    result = enrich_contract(
        task_id="task-00",
        task_description="xyzzy frobnicate quux zorp",
        project="myproject",
        knowledge_entries=entries,
    )

    assert result.learnings_added == 0
    assert result.contract_updated is False


# ---------------------------------------------------------------------------
# test_format_context_block_includes_confidence
# ---------------------------------------------------------------------------


def test_format_context_block_includes_confidence():
    learnings = [
        {"category": "testing", "content": "Use fixtures", "confidence": 0.9},
        {"category": "python", "content": "Prefer dataclasses", "confidence": 0.75},
    ]
    block = format_context_block(learnings)

    assert "Prior Learnings" in block
    assert "testing" in block
    assert "0.9" in block
    assert "python" in block
    assert "0.75" in block


def test_format_context_block_empty_returns_empty_string():
    block = format_context_block([])
    assert block == ""


# ---------------------------------------------------------------------------
# test_build_enriched_description helpers
# ---------------------------------------------------------------------------


def test_build_enriched_description_appends_context():
    original = "Implement the feature"
    context = "## Prior Learnings\n- [testing] Use fixtures (confidence: 0.9)"
    result = build_enriched_description(original, context)

    assert original in result
    assert context in result


def test_build_enriched_description_unchanged_when_empty_context():
    original = "Implement the feature"
    result = build_enriched_description(original, "")

    assert result == original
