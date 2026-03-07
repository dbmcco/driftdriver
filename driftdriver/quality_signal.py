# ABOUTME: Per-actor quality signal computed from drift outcome history.
# ABOUTME: Provides the feedback loop between outcomes and authority/budget decisions.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from driftdriver.outcome import OUTCOME_VALUES, read_outcomes


@dataclass
class ActorQuality:
    actor_id: str
    actor_class: str
    total_outcomes: int
    resolved_rate: float  # 0.0-1.0
    ignored_rate: float
    worsened_rate: float
    deferred_rate: float
    quality_score: float  # 0.0-1.0 composite
    trend: str  # "improving", "stable", "declining"


def _compute_rates(outcomes: list) -> dict[str, float]:
    """Compute outcome rates from a list of DriftOutcome objects."""
    total = len(outcomes)
    if total == 0:
        return {v: 0.0 for v in OUTCOME_VALUES}
    counts = {v: 0 for v in OUTCOME_VALUES}
    for o in outcomes:
        counts[o.outcome] += 1
    return {v: counts[v] / total for v in OUTCOME_VALUES}


def _compute_quality_score(rates: dict[str, float]) -> float:
    """Compute composite quality score from outcome rates.

    quality_score = resolved_rate - (2 * worsened_rate) - (0.5 * ignored_rate)
    Clamped to [0.0, 1.0].
    """
    score = (
        rates.get("resolved", 0.0)
        - 2.0 * rates.get("worsened", 0.0)
        - 0.5 * rates.get("ignored", 0.0)
    )
    return max(0.0, min(1.0, score))


def _compute_trend(
    outcomes: list,
    now: datetime,
    recent_days: int = 7,
    window_days: int = 30,
) -> str:
    """Compare recent period vs older period to determine trend.

    Compares quality score of last `recent_days` days against the preceding
    `window_days - recent_days` days.
    """
    recent_cutoff = now - timedelta(days=recent_days)
    recent = [o for o in outcomes if o.timestamp >= recent_cutoff]
    older = [o for o in outcomes if o.timestamp < recent_cutoff]

    if len(recent) < 2 or len(older) < 2:
        return "stable"

    recent_score = _compute_quality_score(_compute_rates(recent))
    older_score = _compute_quality_score(_compute_rates(older))
    diff = recent_score - older_score

    if diff > 0.1:
        return "improving"
    elif diff < -0.1:
        return "declining"
    return "stable"


def compute_actor_quality(
    outcomes_path: Path,
    actor_id: str,
    actor_class: str = "",
    window_days: int = 30,
) -> ActorQuality:
    """Compute quality signal for a specific actor from outcome history."""
    all_outcomes = read_outcomes(outcomes_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    actor_outcomes = [
        o
        for o in all_outcomes
        if o.actor_id == actor_id and o.timestamp >= cutoff
    ]

    rates = _compute_rates(actor_outcomes)
    quality_score = _compute_quality_score(rates)
    trend = _compute_trend(actor_outcomes, now, recent_days=7, window_days=window_days)

    return ActorQuality(
        actor_id=actor_id,
        actor_class=actor_class,
        total_outcomes=len(actor_outcomes),
        resolved_rate=rates["resolved"],
        ignored_rate=rates["ignored"],
        worsened_rate=rates["worsened"],
        deferred_rate=rates["deferred"],
        quality_score=quality_score,
        trend=trend,
    )


def compute_all_actor_qualities(
    outcomes_path: Path,
    window_days: int = 30,
) -> list[ActorQuality]:
    """Compute quality signals for all actors that appear in outcome history."""
    all_outcomes = read_outcomes(outcomes_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=window_days)

    # Filter to window and group by actor_id
    windowed = [o for o in all_outcomes if o.timestamp >= cutoff]
    actors: dict[str, list] = {}
    for o in windowed:
        key = o.actor_id if o.actor_id else "unknown"
        actors.setdefault(key, []).append(o)

    results: list[ActorQuality] = []
    for aid, actor_outcomes in sorted(actors.items()):
        rates = _compute_rates(actor_outcomes)
        quality_score = _compute_quality_score(rates)
        trend = _compute_trend(actor_outcomes, now, recent_days=7, window_days=window_days)
        results.append(
            ActorQuality(
                actor_id=aid,
                actor_class="",
                total_outcomes=len(actor_outcomes),
                resolved_rate=rates["resolved"],
                ignored_rate=rates["ignored"],
                worsened_rate=rates["worsened"],
                deferred_rate=rates["deferred"],
                quality_score=quality_score,
                trend=trend,
            )
        )

    return results


def quality_budget_modifier(quality: ActorQuality) -> float:
    """Return a multiplier (0.5 - 2.0) for adjusting actor budgets based on quality.

    - quality_score >= 0.8 -> 1.5x budget (high trust)
    - quality_score >= 0.6 -> 1.0x (normal)
    - quality_score >= 0.4 -> 0.75x (reduced trust)
    - quality_score < 0.4  -> 0.5x (low trust, near-human-only)
    """
    if quality.quality_score >= 0.8:
        return 1.5
    elif quality.quality_score >= 0.6:
        return 1.0
    elif quality.quality_score >= 0.4:
        return 0.75
    else:
        return 0.5


def format_quality_briefing(quality: ActorQuality) -> str:
    """Format a quality signal for agent consumption.

    Returns a concise text block summarizing the actor's quality metrics.
    """
    lines = [
        f"Quality: {quality.quality_score:.2f} ({quality.trend}). "
        f"{quality.total_outcomes} outcomes: "
        f"{quality.resolved_rate:.0%} resolved, "
        f"{quality.ignored_rate:.0%} ignored, "
        f"{quality.worsened_rate:.0%} worsened, "
        f"{quality.deferred_rate:.0%} deferred.",
    ]

    # Add per-rate commentary
    comments: list[str] = []
    if quality.resolved_rate >= 0.7:
        comments.append("Findings are frequently resolved.")
    if quality.ignored_rate >= 0.3:
        comments.append("Many findings ignored -- consider fewer or higher-signal findings.")
    if quality.worsened_rate >= 0.2:
        comments.append("Worsened rate is elevated -- review finding accuracy.")
    if quality.deferred_rate >= 0.3:
        comments.append("High deferral rate -- findings may lack actionability.")

    if comments:
        lines.append(" ".join(comments))

    return "\n".join(lines)
