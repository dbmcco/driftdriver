# ABOUTME: Shared data models for ecosystem intelligence sources and normalized signals
# ABOUTME: Mirrors the Postgres schema so adapters and sync code can share one contract

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Signal:
    source_type: str
    source_id: str
    signal_type: str
    title: str
    raw_payload: dict[str, Any]
    detected_at: datetime
    id: UUID = field(default_factory=uuid4)
    evaluated_at: datetime | None = None
    decision: str | None = None
    decision_reason: str | None = None
    decision_confidence: float | None = None
    decided_by: str | None = None
    acted_on: bool = False
    action_log: list[dict[str, Any]] = field(default_factory=list)
    vetoed_at: datetime | None = None
    veto_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class SourceConfigRecord:
    id: UUID
    source_type: str
    config: dict[str, Any]
    enabled: bool
    last_synced_at: datetime | None
    sync_interval_minutes: int
