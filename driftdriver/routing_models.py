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


def format_routing_prompt(evidence: EvidencePackage) -> str:
    """Format an EvidencePackage as a structured prompt for model-based lane selection.

    Returns a prompt string that:
    - Embeds the evidence context
    - Lists installed lanes and their purposes
    - Identifies auto-fenced (mandatory) lanes
    - Requests JSON-structured output
    """
    context = evidence.to_prompt_context()
    auto_fenced = detect_fenced_lanes(evidence.task_description)

    lane_descriptions = {
        "coredrift": "Core logic / implementation quality checks",
        "specdrift": "Spec/contract alignment verification",
        "datadrift": "Data model and migration checks",
        "depsdrift": "Dependency and package version checks",
        "uxdrift": "UI/UX component and accessibility checks",
        "archdrift": "Architecture and design pattern checks",
        "therapydrift": "Technical debt and code health analysis",
        "yagnidrift": "YAGNI / over-engineering detection",
        "fixdrift": "Bug-fix correctness verification",
        "redrift": "Regression detection",
    }

    lanes_list = "\n".join(
        f"- {lane}: {lane_descriptions.get(lane, 'Drift check lane')}"
        for lane in evidence.installed_lanes
    )

    mandatory_note = ""
    if auto_fenced:
        mandatory_note = (
            f"\n\nMANDATORY LANES (from task fences, always include): "
            f"{', '.join(auto_fenced)}"
        )

    prompt = f"""You are a drift-control routing agent. Given the following evidence about a development task, select which drift-check lanes should run.

{context}{mandatory_note}

## Available Lanes
{lanes_list}

## Instructions
Select which lanes to run based on the evidence. Provide your response as JSON with this structure:

{{
  "selected_lanes": ["lane1", "lane2"],
  "reasoning": {{"lane1": "reason it was selected", "lane2": "reason selected or excluded"}},
  "confidence": 0.0-1.0,
  "evidence_summary": "brief one-line summary of what drove this decision"
}}

Only select lanes from the Available Lanes list above. Always include any mandatory lanes.
"""
    return prompt


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
