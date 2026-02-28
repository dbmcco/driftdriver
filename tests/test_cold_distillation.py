# ABOUTME: Tests for cold distillation engine - Lessons MCP data maintenance
# ABOUTME: Covers event clustering, summarization, pattern finding, pruning, and full pipeline

from driftdriver.cold_distillation import (
    DistillationResult,
    cluster_events,
    distill,
    identify_patterns,
    prune_low_confidence,
    summarize_cluster,
)


# ---------------------------------------------------------------------------
# test_distill_empty_events
# ---------------------------------------------------------------------------


def test_distill_empty_events():
    result = distill([], [])
    assert isinstance(result, DistillationResult)
    assert result.events_processed == 0
    assert result.knowledge_created == 0
    assert result.patterns_identified == 0
    assert result.entries_pruned == 0
    assert result.entries_remaining == 0


# ---------------------------------------------------------------------------
# test_cluster_events_groups_by_type
# ---------------------------------------------------------------------------


def test_cluster_events_groups_by_type():
    events = [
        {"event_type": "decision", "content": "chose option A"},
        {"event_type": "error", "content": "connection failed"},
        {"event_type": "decision", "content": "chose option B"},
        {"event_type": "observation", "content": "latency spiked"},
    ]
    clusters = cluster_events(events)
    assert set(clusters.keys()) == {"decision", "error", "observation"}
    assert len(clusters["decision"]) == 2
    assert len(clusters["error"]) == 1
    assert len(clusters["observation"]) == 1


def test_cluster_events_empty():
    clusters = cluster_events([])
    assert clusters == {}


def test_cluster_events_missing_event_type():
    events = [{"data": "something"}]  # no event_type key
    result = cluster_events(events)
    # Should not raise KeyError
    assert isinstance(result, dict)
    assert "unknown" in result


# ---------------------------------------------------------------------------
# test_summarize_cluster_requires_minimum
# ---------------------------------------------------------------------------


def test_summarize_cluster_requires_minimum_returns_none_below_threshold():
    events = [
        {"event_type": "error", "content": "timeout occurred"},
        {"event_type": "error", "content": "timeout retry"},
    ]
    result = summarize_cluster("error", events)
    assert result is None


def test_summarize_cluster_returns_entry_at_minimum():
    events = [
        {"event_type": "decision", "content": "timeout retry policy"},
        {"event_type": "decision", "content": "timeout backoff strategy"},
        {"event_type": "decision", "content": "timeout threshold config"},
    ]
    result = summarize_cluster("decision", events)
    assert result is not None
    assert "3" in result["content"]
    assert "decision" in result["content"]
    assert result["category"] == "decision"


def test_summarize_cluster_content_includes_common_terms():
    events = [
        {"event_type": "error", "content": "database connection failed"},
        {"event_type": "error", "content": "database query timeout"},
        {"event_type": "error", "content": "database pool exhausted"},
    ]
    result = summarize_cluster("error", events)
    assert result is not None
    assert "database" in result["content"]


# ---------------------------------------------------------------------------
# test_prune_low_confidence_removes_below_threshold
# ---------------------------------------------------------------------------


def test_prune_low_confidence_removes_below_threshold():
    entries = [
        {"content": "keep me", "confidence": 0.8},
        {"content": "remove me", "confidence": 0.1},
        {"content": "borderline", "confidence": 0.2},
        {"content": "also keep", "confidence": 0.5},
    ]
    surviving, pruned_count = prune_low_confidence(entries, threshold=0.2)
    # exactly 0.2 is at the threshold → removed (strictly below means < not <=)
    # Wait: spec says "below threshold" so confidence < 0.2 is removed; 0.2 stays
    # Let's verify based on spec: "Removes entries with confidence below threshold"
    # 0.1 < 0.2 → removed; 0.2 is NOT below 0.2 → kept; 0.5 and 0.8 kept
    assert pruned_count == 1
    assert len(surviving) == 3
    contents = [e["content"] for e in surviving]
    assert "remove me" not in contents
    assert "keep me" in contents
    assert "borderline" in contents
    assert "also keep" in contents


def test_prune_low_confidence_default_threshold():
    entries = [
        {"content": "high confidence", "confidence": 0.9},
        {"content": "very low", "confidence": 0.05},
    ]
    surviving, pruned_count = prune_low_confidence(entries)
    assert pruned_count == 1
    assert len(surviving) == 1
    assert surviving[0]["content"] == "high confidence"


def test_prune_low_confidence_empty():
    surviving, pruned_count = prune_low_confidence([])
    assert surviving == []
    assert pruned_count == 0


# ---------------------------------------------------------------------------
# test_identify_patterns_finds_duplicates
# ---------------------------------------------------------------------------


def test_identify_patterns_finds_duplicates():
    entries = [
        {"category": "error", "content": "database timeout on query", "confidence": 0.7},
        {"category": "error", "content": "database timeout retry logic", "confidence": 0.6},
        {"category": "decision", "content": "unique one-off decision", "confidence": 0.8},
    ]
    patterns = identify_patterns(entries)
    # The two "error" category entries form a cluster of 2+ → both returned
    pattern_contents = [p["content"] for p in patterns]
    assert any("database timeout on query" in c for c in pattern_contents)
    assert any("database timeout retry logic" in c for c in pattern_contents)
    # The lone "decision" entry is not a pattern
    assert not any("unique one-off" in c for c in pattern_contents)


def test_identify_patterns_empty():
    assert identify_patterns([]) == []


def test_identify_patterns_single_entry_per_category():
    entries = [
        {"category": "decision", "content": "sole decision", "confidence": 0.9},
        {"category": "observation", "content": "sole observation", "confidence": 0.8},
    ]
    patterns = identify_patterns(entries)
    assert patterns == []


# ---------------------------------------------------------------------------
# test_distill_full_pipeline
# ---------------------------------------------------------------------------


def test_distill_full_pipeline():
    events = [
        # 4 error events (will form a cluster → 1 knowledge entry)
        {"event_type": "error", "content": "network timeout on connection"},
        {"event_type": "error", "content": "network timeout retry"},
        {"event_type": "error", "content": "network timeout backoff"},
        {"event_type": "error", "content": "network failure detected"},
        # 2 decision events (< 3, no summary created)
        {"event_type": "decision", "content": "chose strategy A"},
        {"event_type": "decision", "content": "chose strategy B"},
    ]
    existing_knowledge = [
        {"category": "error", "content": "prior network issue", "confidence": 0.05},
    ]
    result = distill(events, existing_knowledge)

    assert result.events_processed == 6
    # error cluster has 4 events → 1 knowledge entry created
    assert result.knowledge_created == 1
    # entries_pruned: the low-confidence prior entry gets pruned
    assert result.entries_pruned == 1
