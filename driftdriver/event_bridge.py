# ABOUTME: Maps claude-session-driver JSONL events to Lessons MCP record_event calls
# ABOUTME: Bridges worker lifecycle events to knowledge capture with quality-gated federation

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_EVENT_TYPE_MAP = {
    "pre_tool_use": "tool_use",
    "session_start": "observation",
    "session_end": "observation",
    "stop": "observation",
    "user_prompt_submit": "observation",
}


@dataclass
class MappedEvent:
    session_id: str
    event_type: str  # decision, error, observation, tool_use
    project: str
    payload: dict
    peer_id: str = ""
    cross_repo_ref: str = ""


def map_event(raw_event: dict, session_id: str, project: str) -> MappedEvent | None:
    """Map a session-driver event dict to a MappedEvent, or None if unknown."""
    event_name = raw_event.get("event")
    event_type = _EVENT_TYPE_MAP.get(event_name)
    if event_type is None:
        return None

    if event_name == "pre_tool_use":
        payload = {
            "event": event_name,
            "tool": raw_event.get("tool"),
            "tool_input": raw_event.get("tool_input", {}),
        }
    else:
        payload = {k: v for k, v in raw_event.items() if k != "ts"}

    return MappedEvent(
        session_id=session_id,
        event_type=event_type,
        project=project,
        payload=payload,
    )


def parse_events_file(events_file: Path) -> list[dict]:
    """Read a JSONL file and return a list of parsed event dicts, skipping malformed lines."""
    events = []
    with events_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def bridge_events(events_file: Path, session_id: str, project: str) -> list[MappedEvent]:
    """Parse events file and map all events, filtering out unmapped ones."""
    raw_events = parse_events_file(events_file)
    mapped = [map_event(e, session_id=session_id, project=project) for e in raw_events]
    return [m for m in mapped if m is not None]


def format_mcp_call(event: MappedEvent) -> dict:
    """Format a MappedEvent into the JSON shape Lessons MCP record_event expects."""
    return {
        "session_id": event.session_id,
        "event_type": event.event_type,
        "project": event.project,
        "payload": event.payload,
    }


_CONFIDENCE_STR_TO_FLOAT = {"high": 0.9, "medium": 0.7, "low": 0.3}

# Minimum word length for scope matching to avoid false positives on short words
_SCOPE_MIN_WORD_LEN = 3


def _parse_entry_date(entry: dict) -> datetime | None:
    """Extract the best available date from a knowledge entry.

    Checks last_confirmed, created_at, and the provenance field
    (which embeds created_at as 'lessons-db:<iso-date>').
    """
    for field in ("last_confirmed", "created_at"):
        raw = entry.get(field)
        if raw:
            try:
                return datetime.fromisoformat(str(raw))
            except (ValueError, TypeError):
                pass
    # Fall back to provenance embedded date (e.g. "lessons-db:2026-01-15T10:30:00")
    prov = entry.get("provenance", "")
    if prov.startswith("lessons-db:"):
        date_str = prov[len("lessons-db:"):]
        if date_str:
            try:
                return datetime.fromisoformat(date_str)
            except (ValueError, TypeError):
                pass
    return None


def _extract_entry_words(entry: dict) -> set[str]:
    """Extract lowercase words from an entry's content, category, fact_type, and tags.

    Only includes words of length >= _SCOPE_MIN_WORD_LEN to reduce false positives.
    """
    parts: list[str] = []
    for field in ("content", "category", "fact_type"):
        val = entry.get(field, "")
        if isinstance(val, str):
            parts.append(val)
    tags = entry.get("tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    text = " ".join(parts).lower()
    return {w for w in text.split() if len(w) >= _SCOPE_MIN_WORD_LEN}


def match_scope_relevance(entry: dict, scope_tags: list[str]) -> bool:
    """Check if a knowledge entry is relevant to the receiving repo's scope.

    Returns True if any scope tag appears as a whole word in the entry's
    content, category, fact_type, or tags fields.  Empty scope_tags means
    no filtering — accept everything.
    """
    if not scope_tags:
        return True
    entry_words = _extract_entry_words(entry)
    # Also check category/fact_type/tags as exact tag matches (not just words)
    exact_fields: set[str] = set()
    for field in ("category", "fact_type"):
        val = entry.get(field, "")
        if isinstance(val, str) and val.strip():
            exact_fields.add(val.strip().lower())
    tags = entry.get("tags")
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, str) and t.strip():
                exact_fields.add(t.strip().lower())

    for tag in scope_tags:
        tag_lower = tag.lower()
        if tag_lower in exact_fields:
            return True
        if tag_lower in entry_words:
            return True
    return False


def filter_federated_knowledge(
    entries: list[dict],
    *,
    min_confidence: float = 0.6,
    max_age_days: int = 90,
    max_per_peer: int = 50,
    scope_tags: list[str] | None = None,
) -> list[dict]:
    """Filter federated knowledge entries for quality.

    Applies five gates before accepting a federated entry:
    1. Confidence — rejects entries below min_confidence (default 0.6)
    2. Scope relevance — if scope_tags provided, rejects entries that don't
       match the receiving repo's tech stack / domain keywords
    3. Staleness — rejects entries older than max_age_days
    4. Deduplication — rejects duplicate content (MD5 of first 100 chars)
    5. Per-peer cap — limits entries from any single source
    """
    cutoff = datetime.now() - timedelta(days=max_age_days)
    seen_hashes: set[str] = set()
    filtered: list[dict] = []
    peer_counts: dict[str, int] = {}

    for entry in entries:
        # Confidence gate — handle both float and string values
        conf = entry.get("confidence", 0.0)
        if isinstance(conf, str):
            conf = _CONFIDENCE_STR_TO_FLOAT.get(conf, 0.0)
        if conf < min_confidence:
            continue

        # Scope relevance gate
        if scope_tags is not None and not match_scope_relevance(entry, scope_tags):
            continue

        # Staleness gate
        entry_date = _parse_entry_date(entry)
        if entry_date is not None and entry_date < cutoff:
            continue

        # Dedup gate — hash first 100 chars of content
        content_prefix = entry.get("content", "")[:100]
        content_hash = hashlib.md5(content_prefix.encode()).hexdigest()
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)

        # Per-peer cap
        source = entry.get("_peer", entry.get("source", entry.get("project", "unknown")))
        peer_counts[source] = peer_counts.get(source, 0) + 1
        if peer_counts[source] > max_per_peer:
            continue

        filtered.append(entry)

    return filtered


def federate_learnings(project_dir: Path, peer_registry: object) -> list[dict]:
    """Read knowledge.jsonl from each reachable peer and return combined entries.

    Args:
        project_dir: local project directory (unused but kept for API symmetry)
        peer_registry: PeerRegistry instance with .peers() method
    """
    all_entries: list[dict] = []
    for peer in peer_registry.peers():
        kb_path = Path(peer.path) / ".workgraph" / "knowledge.jsonl"
        if not kb_path.exists():
            continue
        try:
            with kb_path.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entry["_peer"] = peer.name
                        all_entries.append(entry)
                    except json.JSONDecodeError:
                        pass
        except OSError:
            continue
    return filter_federated_knowledge(all_entries)
