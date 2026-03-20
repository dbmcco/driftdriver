# ABOUTME: Persistent JSONL-backed chat history for the ecosystem hub agent.
# ABOUTME: Append turns, load with limit, clear, convert to Anthropic message format.

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class ChatHistory:
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
