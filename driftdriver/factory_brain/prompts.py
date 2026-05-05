# ABOUTME: Prompt templates and tool schemas for the factory brain's three-tier model invocation.
# ABOUTME: Defines the adversarial persona, tier-specific additions, and the directive tool schema.
from __future__ import annotations

from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA
from driftdriver.model_routes import model_for_route

ADVERSARY_SYSTEM = (
    "You are the Factory Adversary. Your job is to find what's broken, "
    "what's about to break, and what everyone is pretending is fine. "
    "You distrust stability \u2014 silence means something failed quietly. "
    "Healthy metrics mean something isn't being measured. "
    "When you see a snapshot, your first question is: 'What's wrong that I can't see?' "
    "When an agent reports success, you ask: 'Did it actually work, or did it just exit clean?' "
    "When a repo is idle, you ask: 'Is it done, or is it stuck and nobody noticed?' "
    "You have heuristic recommendations from a rules-based system. "
    "Treat them as a naive first guess. They follow playbooks. You think. "
    "Act decisively. Log your reasoning. "
    "When you're wrong, say so \u2014 then fix it harder."
)

SELF_HEAL_ADDENDUM = (
    "Before escalating, attempt to self-heal. Known self-heal scenarios: "
    "blocked cascade (clear locks, restart dispatch), "
    "agent failure (respawn agent on same task), "
    "task loop (stop dispatch loop, create_decision for human review), "
    "drift plateau (adjust concurrency, enforce_compliance on stale repos). "
    "Use create_decision to record questions for human review. "
    "Use enforce_compliance to verify repo health before restarting. "
    "Only escalate after first attempting self-heal directives."
)

TIER_ADDITIONS: dict[int, str] = {
    1: (
        "You are operating at tier 1 — reflexes/Haiku. Fast pattern-matching on critical events. Keep reasoning terse.\n\n"
        f"{SELF_HEAL_ADDENDUM}\n\n"
        f"Available actions: {', '.join(sorted(DIRECTIVE_SCHEMA.keys()))}"
    ),
    2: (
        "You are operating at tier 2 — strategy/Sonnet. Operational analysis with cross-repo context. Think before acting.\n\n"
        f"{SELF_HEAL_ADDENDUM}\n\n"
        f"Available actions: {', '.join(sorted(DIRECTIVE_SCHEMA.keys()))}"
    ),
    3: (
        "You are operating at tier 3 — judgment/Opus. Full strategic reasoning with escalation authority. Be thorough.\n\n"
        f"{SELF_HEAL_ADDENDUM}\n\n"
        f"Available actions: {', '.join(sorted(DIRECTIVE_SCHEMA.keys()))}"
    ),
}

TIER_MODELS: dict[int, str] = {
    1: model_for_route("driftdriver.factory_brain_tier1"),
    2: model_for_route("driftdriver.factory_brain_tier2"),
    3: model_for_route("driftdriver.factory_brain_tier3"),
}

# Build the action enum from the directive schema keys
_ACTION_ENUM = sorted(DIRECTIVE_SCHEMA.keys())

DIRECTIVE_TOOL: dict = {
    "name": "issue_directives",
    "description": (
        "Issue operational directives based on your analysis of the factory state. "
        "Each directive maps to a concrete action the factory supervisor will execute."
    ),
    "input_schema": {
        "type": "object",
        "required": ["reasoning", "directives", "escalate"],
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Your adversarial analysis of the situation. What's wrong, what's about to break, what you're doing about it.",
            },
            "directives": {
                "type": "array",
                "description": "Ordered list of actions to execute.",
                "items": {
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": _ACTION_ENUM,
                            "description": "The action to perform.",
                        },
                        "params": {
                            "type": "object",
                            "description": "Action-specific parameters.",
                        },
                    },
                },
            },
            "telegram": {
                "type": ["string", "null"],
                "description": "Optional message to send to the operator via Telegram.",
            },
            "escalate": {
                "type": "boolean",
                "description": "Whether this situation requires escalation to a higher tier.",
            },
        },
    },
}


def build_system_prompt(tier: int) -> str:
    """Combine the adversary persona with tier-specific instructions."""
    addition = TIER_ADDITIONS.get(tier, "")
    return f"{ADVERSARY_SYSTEM}\n\n{addition}"


def build_user_prompt(
    *,
    trigger_event: dict | None = None,
    recent_events: list[dict] | None = None,
    snapshot: dict | None = None,
    heuristic_recommendation: str | None = None,
    recent_directives: list[dict] | None = None,
    roster: dict | None = None,
    escalation_reason: str | None = None,
    tier1_reasoning: str | None = None,
    tier2_reasoning: str | None = None,
) -> str:
    """Build the user prompt with markdown sections for each provided context."""
    import json

    sections: list[str] = []

    if trigger_event is not None:
        sections.append(f"## Trigger Event\n```json\n{json.dumps(trigger_event, indent=2)}\n```")

    if recent_events is not None:
        sections.append(f"## Recent Events\n```json\n{json.dumps(recent_events, indent=2)}\n```")

    if snapshot is not None:
        sections.append(f"## Factory Snapshot\n```json\n{json.dumps(snapshot, indent=2)}\n```")

    if heuristic_recommendation is not None:
        sections.append(f"## Heuristic Recommendation\n{heuristic_recommendation}")

    if recent_directives is not None:
        sections.append(f"## Recent Directives\n```json\n{json.dumps(recent_directives, indent=2)}\n```")

    if roster is not None:
        sections.append(f"## Repo Roster\n```json\n{json.dumps(roster, indent=2)}\n```")

    if escalation_reason is not None:
        sections.append(f"## Escalation Context\n{escalation_reason}")

    if tier1_reasoning is not None:
        sections.append(f"## Tier 1 Reasoning\n{tier1_reasoning}")

    if tier2_reasoning is not None:
        sections.append(f"## Tier 2 Reasoning\n{tier2_reasoning}")

    sections.append("Analyze the situation. Issue directives via the issue_directives tool.")

    return "\n\n".join(sections)
