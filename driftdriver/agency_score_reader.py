# ABOUTME: Reads agency evaluation JSONs from .workgraph/agency/evaluations/
# ABOUTME: Computes rolling average score (0-100) for the self_improvement axis.
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def read_agency_eval_score(
    repo_path: Path,
    *,
    window_days: float = 7.0,
) -> float | None:
    """Read agency evaluation scores from the last `window_days` days.

    Returns average score in 0-100 range, or None if no evaluations exist.
    Malformed files are skipped silently.
    """
    evals_dir = repo_path / ".workgraph" / "agency" / "evaluations"
    if not evals_dir.is_dir():
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    scores: list[float] = []

    for path in evals_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        raw_score = data.get("score")
        if not isinstance(raw_score, (int, float)):
            continue

        raw_ts = data.get("timestamp", "")
        try:
            text = raw_ts.strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            ts = datetime.fromisoformat(text)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            continue

        if ts < cutoff:
            continue

        scores.append(float(raw_score))

    if not scores:
        return None

    return round(sum(scores) / len(scores) * 100.0, 1)
