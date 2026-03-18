# ABOUTME: Atomic read/write wrapper for activity-digests.json in the hub service dir.
# ABOUTME: Mirrors the _write_json/_read_json pattern from discovery.py for consistency.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_activity_digest(path: Path) -> dict[str, Any]:
    """Read activity-digests.json. Returns empty structure if file does not exist."""
    if not path.exists():
        return {"generated_at": None, "repos": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"generated_at": None, "repos": []}


def write_activity_digest(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write activity-digests.json using tmp+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)
