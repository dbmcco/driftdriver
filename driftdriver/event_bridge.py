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


def filter_federated_knowledge(
    entries: list[dict],
    *,
    min_confidence: float = 0.5,
    max_age_days: int = 90,
    max_per_peer: int = 50,
) -> list[dict]:
    """Filter federated knowledge entries for quality.

    Removes low-confidence, stale, and duplicate entries.
    Caps entries per source to prevent one peer from dominating.
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
