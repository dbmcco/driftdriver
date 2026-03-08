# ABOUTME: Directive schema — the formal contract between Speedrift (judgment) and wg (execution).
# ABOUTME: Every execution action Speedrift wants taken flows through a Directive object.

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
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
