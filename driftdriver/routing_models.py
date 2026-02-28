# ABOUTME: Model-mediated routing decision module for driftdriver
# ABOUTME: Formats evidence as structured prompts and parses model responses into RoutingDecision

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from driftdriver.smart_routing import EvidencePackage

# Known lane names used for auto-fence detection
KNOWN_LANES = {
    "coredrift",
    "specdrift",
    "datadrift",
    "depsdrift",
    "uxdrift",
    "archdrift",
    "therapydrift",
    "yagnidrift",
    "fixdrift",
    "redrift",
    "contrariandrift",
    "qadrift",
    "reviewdrift",
}


@dataclass
class RoutingDecision:
    """Structured routing decision produced from model or fallback reasoning."""

    selected_lanes: list[str]        # ordered list of lanes to run
    reasoning: dict[str, str]        # lane -> why it was selected/excluded
    confidence: float                # 0-1 overall confidence
    auto_fenced: list[str]           # lanes auto-selected from task fences
    model_suggested: list[str]       # lanes the model reasoning suggests
    evidence_summary: str            # brief summary of the evidence considered


def detect_fenced_lanes(task_description: str) -> list[str]:
    """Scan task description for fenced code blocks that name a known lane.

    Blocks like  ```specdrift  or  ```coredrift  indicate mandatory lane inclusion.
    Generic fences like  ```python  or  ```bash  are ignored.
    """
    pattern = re.compile(r"^```(\w+)", re.MULTILINE)
    found: list[str] = []
    for match in pattern.finditer(task_description):
        name = match.group(1)
        if name in KNOWN_LANES and name not in found:
            found.append(name)
    return found


def parse_routing_response(
    response: str,
    evidence: EvidencePackage,
) -> RoutingDecision:
    """Parse a model response string into a RoutingDecision.

    Handles markdown code fences (```json ... ```) around the JSON.
    Falls back to pattern-based lane suggestions if parsing fails.
    Always enforces auto-fenced lanes from the task description.
    Validates that selected lanes are actually installed.
    """
    auto_fenced = detect_fenced_lanes(evidence.task_description)
    installed = set(evidence.installed_lanes)

    # Attempt to extract and parse JSON
    parsed = _extract_json(response)

    if parsed is not None:
        raw_lanes: list[str] = parsed.get("selected_lanes", [])
        model_suggested = [lane for lane in raw_lanes if lane in installed]
        reasoning: dict[str, str] = parsed.get("reasoning", {})
        confidence: float = float(parsed.get("confidence", 0.75))
        evidence_summary: str = parsed.get("evidence_summary", "")

        # Enforce auto-fenced lanes
        all_lanes = list(model_suggested)
        for lane in auto_fenced:
            if lane in installed and lane not in all_lanes:
                all_lanes.append(lane)

        return RoutingDecision(
            selected_lanes=all_lanes,
            reasoning=reasoning,
            confidence=confidence,
            auto_fenced=auto_fenced,
            model_suggested=model_suggested,
            evidence_summary=evidence_summary,
        )

    # Fallback: use pattern-based suggestions
    suggested = evidence.suggest_lanes()
    fallback_lanes = [lane for lane in suggested if lane in installed]
    for lane in auto_fenced:
        if lane in installed and lane not in fallback_lanes:
            fallback_lanes.append(lane)

    return RoutingDecision(
        selected_lanes=fallback_lanes,
        reasoning={lane: "pattern-based fallback (model response unparseable)" for lane in fallback_lanes},
        confidence=0.3,
        auto_fenced=auto_fenced,
        model_suggested=[],
        evidence_summary="Fallback: used file pattern matching (model response could not be parsed)",
    )


def _extract_json(text: str) -> dict | None:
    """Extract and parse JSON from a string, handling markdown code fences."""
    # Try stripping markdown fences: ```json ... ``` or ``` ... ```
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        # Try to find a raw JSON object
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        candidate = brace_match.group(0) if brace_match else text.strip()

    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
