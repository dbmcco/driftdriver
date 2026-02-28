# ABOUTME: Cold distillation engine for Lessons MCP data maintenance
# ABOUTME: Compresses events→knowledge, identifies patterns, prunes low-confidence

from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class DistillationResult:
    events_processed: int = 0
    knowledge_created: int = 0
    patterns_identified: int = 0
    entries_pruned: int = 0
    entries_remaining: int = 0


def cluster_events(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by their event_type field."""
    clusters: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        clusters[event["event_type"]].append(event)
    return dict(clusters)


def summarize_cluster(event_type: str, events: list[dict]) -> dict | None:
    """Create a knowledge summary from a cluster of events.

    Returns None if fewer than 3 events. Otherwise returns a knowledge entry
    with content "Observed N {event_type} events related to: {common_terms}".
    """
    if len(events) < 3:
        return None

    word_counts: Counter = Counter()
    for event in events:
        content = event.get("content", event.get("message", ""))
        words = set(content.lower().split())
        word_counts.update(words)

    common_terms = [w for w, count in word_counts.most_common() if count >= 2][:5]
    terms_str = ", ".join(common_terms) if common_terms else event_type

    return {
        "category": event_type,
        "content": f"Observed {len(events)} {event_type} events related to: {terms_str}",
        "confidence": 0.7,
    }


def identify_patterns(knowledge_entries: list[dict]) -> list[dict]:
    """Find entries that appear in category clusters of 2 or more."""
    by_category: dict[str, list[dict]] = defaultdict(list)
    for entry in knowledge_entries:
        by_category[entry.get("category", "")].append(entry)

    patterns = []
    for entries in by_category.values():
        if len(entries) >= 2:
            patterns.extend(entries)
    return patterns


def prune_low_confidence(
    knowledge_entries: list[dict], threshold: float = 0.2
) -> tuple[list[dict], int]:
    """Remove entries with confidence strictly below threshold.

    Returns (surviving entries, count pruned).
    """
    surviving = [e for e in knowledge_entries if e.get("confidence", 0.0) >= threshold]
    pruned = len(knowledge_entries) - len(surviving)
    return surviving, pruned


def distill(
    events: list[dict],
    existing_knowledge: list[dict],
    prune_threshold: float = 0.2,
) -> DistillationResult:
    """Main entry point for a distillation run.

    Groups events by category, creates knowledge summaries from clusters,
    identifies patterns, prunes low-confidence entries, and returns metrics.
    """
    events_processed = len(events)

    clusters = cluster_events(events)
    new_knowledge = []
    for event_type, cluster in clusters.items():
        entry = summarize_cluster(event_type, cluster)
        if entry is not None:
            new_knowledge.append(entry)

    knowledge_created = len(new_knowledge)

    all_knowledge = new_knowledge + list(existing_knowledge)
    patterns = identify_patterns(all_knowledge)
    patterns_identified = len(patterns)

    surviving, entries_pruned = prune_low_confidence(all_knowledge, threshold=prune_threshold)
    entries_remaining = len(surviving)

    return DistillationResult(
        events_processed=events_processed,
        knowledge_created=knowledge_created,
        patterns_identified=patterns_identified,
        entries_pruned=entries_pruned,
        entries_remaining=entries_remaining,
    )
