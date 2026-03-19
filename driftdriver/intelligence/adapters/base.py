# ABOUTME: Abstract adapter contract for ecosystem intelligence signal sources
# ABOUTME: Keeps adapters aligned on normalized Signal output and health status reporting

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from driftdriver.intelligence.models import Signal


class SourceAdapter(ABC):
    source_type: str

    @abstractmethod
    def sync(self, config: dict[str, Any], last_synced_at: datetime | None) -> list[Signal]:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError
