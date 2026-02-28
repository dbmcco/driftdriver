# ABOUTME: Self-reflect phase — extracts learnings from completed task events
# ABOUTME: Detects patterns, gotchas, and repeated corrections for knowledge base

from dataclasses import dataclass, field
from pathlib import Path
import json
import re


@dataclass
class Learning:
    """A candidate learning extracted from task execution."""
    learning_type: str  # pattern, gotcha, decision, anti_pattern
    content: str
    confidence: str = "medium"  # high, medium, low
    source_task: str = ""
    affected_files: list[str] = field(default_factory=list)
    evidence: str = ""


def extract_from_events(events: list[dict]) -> list[Learning]:
    """Extract candidate learnings from a list of task events."""
    learnings = []

    # Check for repeated tool failures (pattern: same tool fails 2+ times)
    tool_failures = {}
    for event in events:
        if event.get("event") == "pre_tool_use":
            tool = event.get("tool", "")
            tool_failures.setdefault(tool, {"attempts": 0})
            tool_failures[tool]["attempts"] += 1

    for tool, counts in tool_failures.items():
        if counts["attempts"] > 3:
            learnings.append(Learning(
                learning_type="pattern",
                content=f"Tool '{tool}' called {counts['attempts']} times — possible loop or retry pattern",
                confidence="medium",
            ))

    return learnings


def extract_from_diff(diff_text: str) -> list[Learning]:
    """Extract learnings from a git diff (post-task)."""
    learnings = []

    # Detect large diffs (possible scope creep)
    lines = diff_text.strip().splitlines()
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))

    if added + removed > 200:
        learnings.append(Learning(
            learning_type="gotcha",
            content=f"Large diff ({added}+ / {removed}-) — consider breaking into smaller tasks",
            confidence="medium",
        ))

    # Detect TODO/FIXME additions
    todo_pattern = re.compile(r"^\+.*(?:TODO|FIXME|HACK|XXX)", re.IGNORECASE)
    todos = [l for l in lines if todo_pattern.match(l)]
    if todos:
        learnings.append(Learning(
            learning_type="gotcha",
            content=f"Task left {len(todos)} TODO/FIXME markers — incomplete work",
            confidence="high",
        ))

    return learnings


def extract_from_test_results(test_output: str) -> list[Learning]:
    """Extract learnings from test run output."""
    learnings = []

    # Detect flaky tests (passed on retry)
    if "PASSED" in test_output and "FAILED" in test_output:
        learnings.append(Learning(
            learning_type="gotcha",
            content="Test suite had mixed results — possible flaky tests",
            confidence="low",
        ))

    # Detect slow tests
    slow_match = re.search(r"(\d+) passed in (\d+\.\d+)s", test_output)
    if slow_match:
        count = int(slow_match.group(1))
        duration = float(slow_match.group(2))
        if count > 0 and duration / count > 1.0:
            learnings.append(Learning(
                learning_type="performance",
                content=f"Tests averaging {duration/count:.1f}s each — may need optimization",
                confidence="medium",
            ))

    return learnings


def detect_repeated_patterns(events: list[dict]) -> list[Learning]:
    """Detect repeated correction patterns that should become rules."""
    # Look for the same file being edited multiple times
    file_edits = {}
    for event in events:
        if event.get("tool") in ("Edit", "Write"):
            fp = event.get("tool_input", {}).get("file_path", "")
            if fp:
                file_edits[fp] = file_edits.get(fp, 0) + 1

    learnings = []
    for fp, count in file_edits.items():
        if count >= 3:
            learnings.append(Learning(
                learning_type="anti_pattern",
                content=f"File '{fp}' edited {count} times in one task — unclear requirements or bad initial design",
                confidence="medium",
                affected_files=[fp],
            ))

    return learnings


def format_learnings_for_review(learnings: list[Learning]) -> str:
    """Format learnings for human review before adding to knowledge base."""
    if not learnings:
        return "No learnings extracted from this task."
    lines = ["## Candidate Learnings (Review Before Adding to KB)"]
    for i, l in enumerate(learnings, 1):
        lines.append(f"\n### {i}. [{l.learning_type.upper()}] (confidence: {l.confidence})")
        lines.append(l.content)
        if l.affected_files:
            lines.append(f"Affects: {', '.join(l.affected_files)}")
        if l.evidence:
            lines.append(f"Evidence: {l.evidence}")
    return "\n".join(lines)


def self_reflect(
    events: list[dict] | None = None,
    diff_text: str = "",
    test_output: str = "",
) -> list[Learning]:
    """Run the full self-reflect pipeline."""
    all_learnings = []
    if events:
        all_learnings.extend(extract_from_events(events))
        all_learnings.extend(detect_repeated_patterns(events))
    if diff_text:
        all_learnings.extend(extract_from_diff(diff_text))
    if test_output:
        all_learnings.extend(extract_from_test_results(test_output))
    return all_learnings
