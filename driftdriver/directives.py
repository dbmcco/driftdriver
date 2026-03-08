# ABOUTME: Directive schema — the formal contract between Speedrift (judgment) and wg (execution).
# ABOUTME: Every execution action Speedrift wants taken flows through a Directive object.

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Action(Enum):
    CREATE_TASK = "create_task"
    CLAIM_TASK = "claim_task"
    COMPLETE_TASK = "complete_task"
    FAIL_TASK = "fail_task"
    START_SERVICE = "start_service"
    STOP_SERVICE = "stop_service"
    LOG_TO_TASK = "log_to_task"
    EVOLVE_PROMPT = "evolve_prompt"
    DISPATCH_TO_PEER = "dispatch_to_peer"
    BLOCK_TASK = "block_task"
    CREATE_VALIDATION = "create_validation"
    CREATE_UPSTREAM_PR = "create_upstream_pr"


@dataclass
class Authority:
    actor: str
    actor_class: str
    budget_remaining: int = -1


@dataclass
class Directive:
    source: str
    repo: str
    action: Action
    params: dict[str, Any]
    reason: str
    authority: Authority | None = None
    priority: str = "normal"
    id: str = field(default_factory=lambda: f"dir-{uuid.uuid4().hex[:12]}")
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_json(self) -> str:
        d = asdict(self)
        d["action"] = self.action.value
        return json.dumps(d, separators=(",", ":"))

    @classmethod
    def from_json(cls, blob: str) -> Directive:
        d = json.loads(blob)
        d["action"] = Action(d["action"])
        auth = d.pop("authority", None)
        if auth and isinstance(auth, dict):
            d["authority"] = Authority(**auth)
        return cls(**d)


@dataclass
class DirectiveLog:
    """JSONL audit trail for the directive lifecycle: pending, completed, failed."""

    base_dir: Path

    def __post_init__(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _pending(self) -> Path:
        return self.base_dir / "pending.jsonl"

    @property
    def _completed(self) -> Path:
        return self.base_dir / "completed.jsonl"

    @property
    def _failed(self) -> Path:
        return self.base_dir / "failed.jsonl"

    def append(self, directive: Directive) -> None:
        with self._pending.open("a") as f:
            f.write(directive.to_json() + "\n")

    def read_pending(self) -> list[Directive]:
        if not self._pending.exists():
            return []
        completed_ids = {r["directive_id"] for r in self.read_completed()}
        failed_ids = {r["directive_id"] for r in self.read_failed()}
        done_ids = completed_ids | failed_ids
        result: list[Directive] = []
        for line in self._pending.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            d = Directive.from_json(line)
            if d.id not in done_ids:
                result.append(d)
        return result

    def mark_completed(
        self, directive_id: str, *, exit_code: int, output: str
    ) -> None:
        record = json.dumps({
            "directive_id": directive_id,
            "exit_code": exit_code,
            "output": output,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        with self._completed.open("a") as f:
            f.write(record + "\n")

    def mark_failed(
        self, directive_id: str, *, exit_code: int, error: str
    ) -> None:
        record = json.dumps({
            "directive_id": directive_id,
            "exit_code": exit_code,
            "error": error,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
        with self._failed.open("a") as f:
            f.write(record + "\n")

    def read_completed(self) -> list[dict[str, Any]]:
        return self._read_records(self._completed)

    def read_failed(self) -> list[dict[str, Any]]:
        return self._read_records(self._failed)

    @staticmethod
    def _read_records(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        result: list[dict[str, Any]] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                result.append(json.loads(line))
        return result
