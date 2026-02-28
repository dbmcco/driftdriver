# ABOUTME: Auto-injects relevant Lessons MCP learnings into task contracts
# ABOUTME: Enriches wg-contract blocks when tasks are claimed

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EnrichmentResult:
    task_id: str
    learnings_added: int
    contract_updated: bool
    injected_context: list[str] = field(default_factory=list)


def find_relevant_learnings(
    task_description: str,
    knowledge_entries: list[dict],
    max_entries: int = 3,
) -> list[dict]:
    """Score knowledge entries by keyword overlap with task description.

    Splits task_description into lowercase words, counts how many appear in
    each entry's content (case-insensitive).  Returns the top max_entries
    entries sorted by descending score, excluding entries with score 0.
    """
    words = {w.lower() for w in task_description.split() if len(w) > 2}
    if not words:
        return []

    scored: list[tuple[int, dict]] = []
    for entry in knowledge_entries:
        content_words = {w.lower() for w in entry.get("content", "").split()}
        score = len(words & content_words)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [entry for _, entry in scored[:max_entries]]


def format_context_block(learnings: list[dict]) -> str:
    """Format a list of knowledge entries into a markdown prior-learnings block."""
    if not learnings:
        return ""

    lines = ["## Prior Learnings"]
    for entry in learnings:
        category = entry.get("category", "general")
        content = entry.get("content", "")
        confidence = entry.get("confidence", 0.0)
        lines.append(f"- [{category}] {content} (confidence: {confidence})")

    return "\n".join(lines)


def build_enriched_description(original_description: str, context_block: str) -> str:
    """Append context_block to original_description; return original if block is empty."""
    if not context_block:
        return original_description
    return f"{original_description}\n\n{context_block}"


def enrich_contract(
    task_id: str,
    task_description: str,
    project: str,
    knowledge_entries: list[dict],
) -> EnrichmentResult:
    """Enrich a task contract with relevant prior learnings.

    Filters knowledge_entries by relevance to task_description, formats them
    into a context block, and returns an EnrichmentResult describing what was
    injected.
    """
    relevant = find_relevant_learnings(task_description, knowledge_entries)

    if not relevant:
        return EnrichmentResult(
            task_id=task_id,
            learnings_added=0,
            contract_updated=False,
        )

    context_block = format_context_block(relevant)
    injected = [entry.get("content", "") for entry in relevant]

    return EnrichmentResult(
        task_id=task_id,
        learnings_added=len(relevant),
        contract_updated=True,
        injected_context=injected,
    )
