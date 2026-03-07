# ABOUTME: Standard plugin contract for all drift lanes (internal and external)
# ABOUTME: Defines the interface every lane must implement for consistent routing

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class LaneFinding:
    """A single finding from a drift lane check."""
    message: str
    severity: str = "info"  # info, warning, error, critical
    file: str = ""
    line: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class LaneResult:
    """Structured output from a drift lane execution."""
    lane: str
    findings: list[LaneFinding]
    exit_code: int
    summary: str


def validate_lane_output(raw: str) -> LaneResult | None:
    """Parse raw JSON output from a lane into a LaneResult.

    Returns None if the output is malformed or missing required fields.
    All drift lanes (internal and external) should produce JSON matching
    this contract.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if "lane" not in data:
        return None

    findings = []
    for f in data.get("findings", []):
        findings.append(LaneFinding(
            message=f.get("message", ""),
            severity=f.get("severity", "info"),
            file=f.get("file", ""),
            line=f.get("line", 0),
            tags=f.get("tags", []),
        ))

    return LaneResult(
        lane=data["lane"],
        findings=findings,
        exit_code=data.get("exit_code", 0),
        summary=data.get("summary", ""),
    )
