# ABOUTME: Project profile generation from Lessons MCP data
# ABOUTME: Analyzes lane effectiveness, failure patterns, and validation rates

from collections import Counter, defaultdict
from dataclasses import dataclass, field


@dataclass
class LaneStats:
    lane: str
    runs: int = 0
    drift_detected: int = 0
    avg_score: float = 0.0


@dataclass
class ProjectProfile:
    project: str
    total_tasks: int = 0
    completed_tasks: int = 0
    lane_stats: list[LaneStats] = field(default_factory=list)
    common_failures: list[str] = field(default_factory=list)
    validation_rate: float = 0.0  # % of tasks that ran validation


def extract_lane_stats(events: list[dict]) -> list[LaneStats]:
    """Filter drift_check events, group by lane, count runs/detections, average score."""
    runs: dict[str, int] = defaultdict(int)
    drifts: dict[str, int] = defaultdict(int)
    score_sum: dict[str, float] = defaultdict(float)

    for e in events:
        if e.get("type") != "drift_check":
            continue
        lane = e.get("lane", "")
        runs[lane] += 1
        if e.get("drift_detected"):
            drifts[lane] += 1
        score_sum[lane] += float(e.get("score", 0.0))

    return [
        LaneStats(
            lane=lane,
            runs=count,
            drift_detected=drifts[lane],
            avg_score=score_sum[lane] / count,
        )
        for lane, count in runs.items()
    ]


def extract_failure_patterns(events: list[dict], top_n: int = 5) -> list[str]:
    """Group error events by first 50 chars of message; return top N patterns."""
    counts: Counter[str] = Counter()
    prefix_to_full: dict[str, str] = {}

    for e in events:
        if e.get("type") != "error":
            continue
        msg = e.get("message", "")
        prefix = msg[:50]
        counts[prefix] += 1
        if prefix not in prefix_to_full:
            prefix_to_full[prefix] = msg

    return [prefix_to_full[prefix] for prefix, _ in counts.most_common(top_n)]


def build_profile(project: str, events: list[dict]) -> ProjectProfile:
    """Build a ProjectProfile from a project name and Lessons MCP event list."""
    decision_events = [e for e in events if e.get("type") == "decision"]
    total_tasks = len(decision_events)
    completed_tasks = sum(1 for e in decision_events if e.get("status") == "completed")

    validation_count = sum(1 for e in events if e.get("type") == "validation")
    validation_rate = (validation_count / total_tasks) if total_tasks > 0 else 0.0

    return ProjectProfile(
        project=project,
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        lane_stats=extract_lane_stats(events),
        common_failures=extract_failure_patterns(events),
        validation_rate=validation_rate,
    )


def format_profile_report(profile: ProjectProfile) -> str:
    """Human-readable summary of a ProjectProfile."""
    lines: list[str] = [
        f"Project: {profile.project}",
        f"Tasks: {profile.completed_tasks}/{profile.total_tasks} completed",
        f"Validation rate: {profile.validation_rate * 100:.1f}%",
        "",
        "Lane Effectiveness:",
    ]

    if profile.lane_stats:
        lines.append(f"  {'Lane':<16} {'Runs':>6} {'Drift':>6} {'Avg Score':>10}")
        lines.append("  " + "-" * 42)
        for ls in profile.lane_stats:
            lines.append(
                f"  {ls.lane:<16} {ls.runs:>6} {ls.drift_detected:>6} {ls.avg_score:>10.2f}"
            )
    else:
        lines.append("  (no drift check data)")

    lines += ["", "Top Failure Patterns:"]
    if profile.common_failures:
        for i, msg in enumerate(profile.common_failures, 1):
            lines.append(f"  {i}. {msg}")
    else:
        lines.append("  (none)")

    return "\n".join(lines)
