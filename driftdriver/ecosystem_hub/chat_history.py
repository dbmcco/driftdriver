# ABOUTME: Persistent JSONL-backed chat history for the ecosystem hub agent.
# ABOUTME: Supports multiple named sessions; active session is chat_active.jsonl.

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path


class ChatHistory:
    """Single chat session backed by a JSONL file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def append(self, user: str, assistant: str) -> None:
        turn = {
            "timestamp": datetime.now(UTC).isoformat(),
            "user": user,
            "assistant": assistant,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            f.write(json.dumps(turn) + "\n")

    def load(self, limit: int = 50) -> list[dict]:
        if not self._path.exists():
            return []
        turns = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return turns[-limit:]

    def clear(self) -> None:
        if self._path.exists():
            self._path.write_text("")

    def to_anthropic_messages(self, limit: int = 20) -> list[dict]:
        turns = self.load(limit=limit)
        msgs = []
        for t in turns:
            msgs.append({"role": "user", "content": t["user"]})
            msgs.append({"role": "assistant", "content": t["assistant"]})
        return msgs

    def first_user_message(self) -> str:
        turns = self.load(limit=1)
        if turns:
            return turns[0].get("user", "")
        return ""


class ChatSessionManager:
    """Manages multiple named chat sessions in a directory.

    Active session: chat_active.jsonl
    Archived sessions: chat_{timestamp}.jsonl
    """

    _ACTIVE_NAME = "chat_active.jsonl"
    _ARCHIVE_PATTERN = re.compile(r"^chat_(\d{8}T\d{6})\.jsonl$")

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def active(self) -> ChatHistory:
        return ChatHistory(self._dir / self._ACTIVE_NAME)

    def new_session(self) -> ChatHistory:
        """Archive the current active session and return a fresh one."""
        active_path = self._dir / self._ACTIVE_NAME
        if active_path.exists() and active_path.stat().st_size > 0:
            ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
            active_path.rename(self._dir / f"chat_{ts}.jsonl")
        # Fresh active session
        return ChatHistory(active_path)

    def list_sessions(self) -> list[dict]:
        """Return metadata for all sessions, newest first.

        Each entry: {id, label, path, turn_count, started_at, last_at}
        """
        sessions = []

        # Active session
        active_path = self._dir / self._ACTIVE_NAME
        if active_path.exists() and active_path.stat().st_size > 0:
            h = ChatHistory(active_path)
            turns = h.load(limit=100)
            if turns:
                sessions.append({
                    "id": "active",
                    "label": turns[0].get("user", "")[:60] or "Active chat",
                    "turn_count": len(turns),
                    "started_at": turns[0].get("timestamp", ""),
                    "last_at": turns[-1].get("timestamp", ""),
                    "active": True,
                })

        # Archived sessions
        archived = []
        for f in self._dir.iterdir():
            m = self._ARCHIVE_PATTERN.match(f.name)
            if m:
                archived.append((m.group(1), f))
        archived.sort(key=lambda x: x[0], reverse=True)

        for ts, path in archived[:20]:
            h = ChatHistory(path)
            turns = h.load(limit=100)
            if not turns:
                continue
            sessions.append({
                "id": ts,
                "label": turns[0].get("user", "")[:60] or f"Chat {ts}",
                "turn_count": len(turns),
                "started_at": turns[0].get("timestamp", ""),
                "last_at": turns[-1].get("timestamp", ""),
                "active": False,
            })

        return sessions

    def get_session(self, session_id: str) -> ChatHistory | None:
        if session_id == "active":
            return self.active()
        path = self._dir / f"chat_{session_id}.jsonl"
        if path.exists():
            return ChatHistory(path)
        return None

    def recent_context_summary(self, max_sessions: int = 3) -> str:
        """Return a brief text summary of recent sessions for agent context."""
        sessions = self.list_sessions()
        if not sessions:
            return "No previous conversations."
        lines = ["Recent conversations (for context):"]
        for s in sessions[:max_sessions]:
            label = s["label"]
            turns = s["turn_count"]
            when = s.get("last_at", "")[:10]
            lines.append(f"  - {when}: \"{label}\" ({turns} turns)")
        return "\n".join(lines)
