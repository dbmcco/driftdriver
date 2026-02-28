# ABOUTME: Selective knowledge priming — filters learnings by file scope and work type
# ABOUTME: Inspired by metaswarm: only prime agents with relevant facts, not everything

from dataclasses import dataclass, field
from pathlib import Path
import fnmatch
import json


@dataclass
class KnowledgeFact:
    """A structured knowledge fact with metadata for selective retrieval."""
    fact_id: str
    fact_type: str  # pattern, gotcha, decision, api_behavior, security, performance
    content: str
    affected_files: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    confidence: str = "medium"  # high, medium, low
    provenance: str = ""  # which task/session produced this
    usage_count: int = 0
    helpful_count: int = 0
    outdated_reports: int = 0


def load_knowledge_base(kb_path: Path) -> list[KnowledgeFact]:
    """Load knowledge facts from a JSONL file."""
    facts = []
    if not kb_path.exists():
        return facts
    for line in kb_path.read_text().strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            facts.append(KnowledgeFact(**{
                k: v for k, v in data.items()
                if k in KnowledgeFact.__dataclass_fields__
            }))
        except (json.JSONDecodeError, TypeError):
            continue
    return facts


def filter_by_file_scope(facts: list[KnowledgeFact], changed_files: list[str]) -> list[KnowledgeFact]:
    """Filter facts to those relevant to the changed files."""
    if not changed_files:
        return facts  # no scope constraint, return all
    relevant = []
    for fact in facts:
        if not fact.affected_files:
            # Facts with no file scope are always included
            relevant.append(fact)
            continue
        for af in fact.affected_files:
            for cf in changed_files:
                if _glob_match(af, cf) or _path_prefix_match(af, cf):
                    relevant.append(fact)
                    break
            else:
                continue
            break
    return relevant


def _path_prefix_match(pattern: str, path: str) -> bool:
    """Check if path starts with pattern as a directory prefix."""
    clean = pattern.rstrip("/*")
    return path == clean or path.startswith(clean + "/")


def _glob_match(pattern: str, path: str) -> bool:
    """Glob matching using fnmatch for proper wildcard support."""
    return fnmatch.fnmatch(path, pattern)


def filter_by_type(facts: list[KnowledgeFact], fact_types: list[str]) -> list[KnowledgeFact]:
    """Filter facts by type."""
    return [f for f in facts if f.fact_type in fact_types]


def filter_by_confidence(facts: list[KnowledgeFact], min_confidence: str = "medium") -> list[KnowledgeFact]:
    """Filter facts by minimum confidence level."""
    levels = {"low": 0, "medium": 1, "high": 2}
    min_level = levels.get(min_confidence, 1)
    return [f for f in facts if levels.get(f.confidence, 0) >= min_level]


def rank_facts(facts: list[KnowledgeFact]) -> list[KnowledgeFact]:
    """Rank facts by relevance (usage count, confidence, recency)."""
    levels = {"high": 3, "medium": 2, "low": 1}
    return sorted(facts, key=lambda f: (
        levels.get(f.confidence, 0),
        f.usage_count,
        -f.outdated_reports,
    ), reverse=True)


def prime_context(
    kb_path: Path,
    changed_files: list[str] | None = None,
    fact_types: list[str] | None = None,
    max_facts: int = 10,
) -> str:
    """Build a priming context string from the knowledge base."""
    facts = load_knowledge_base(kb_path)
    if changed_files:
        facts = filter_by_file_scope(facts, changed_files)
    if fact_types:
        facts = filter_by_type(facts, fact_types)
    facts = filter_by_confidence(facts)
    facts = rank_facts(facts)
    facts = facts[:max_facts]
    if not facts:
        return ""
    lines = ["## Relevant Knowledge"]
    for f in facts:
        lines.append(f"\n### [{f.fact_type.upper()}] (confidence: {f.confidence})")
        lines.append(f.content)
        if f.affected_files:
            lines.append(f"Affects: {', '.join(f.affected_files)}")
    return "\n".join(lines)


def save_fact(kb_path: Path, fact: KnowledgeFact) -> None:
    """Append a new fact to the knowledge base."""
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    with open(kb_path, "a") as f:
        data = {
            "fact_id": fact.fact_id,
            "fact_type": fact.fact_type,
            "content": fact.content,
            "affected_files": fact.affected_files,
            "affected_modules": fact.affected_modules,
            "confidence": fact.confidence,
            "provenance": fact.provenance,
            "usage_count": fact.usage_count,
            "helpful_count": fact.helpful_count,
            "outdated_reports": fact.outdated_reports,
        }
        f.write(json.dumps(data) + "\n")
