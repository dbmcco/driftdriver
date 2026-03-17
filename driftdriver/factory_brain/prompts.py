# ABOUTME: Prompt templates for the factory brain's three-tier model invocation.
# ABOUTME: Defines the adversarial persona, tier-specific additions, and action vocabulary.
from __future__ import annotations

from driftdriver.factory_brain.directives import DIRECTIVE_SCHEMA

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
    "When you're wrong, say so \u2014 then fix it harder.\n\n"
    "Available actions: " + ", ".join(sorted(DIRECTIVE_SCHEMA.keys())) + "\n"
    "Each action takes params. Use the action names exactly as listed."
)

SELF_HEAL_ADDENDUM = (
    "\n\n## Self-Healing Protocol\n"
    "Before escalating ANY issue to a human, attempt to self-heal:\n"
    "1. **Blocked cascade** \u2014 diagnose the failing task, create fix tasks, execute\n"
    "2. **Awaiting validation** \u2014 run verify commands, report pass/fail\n"
    "3. **Lane boundary** \u2014 start the next lane if current is done\n"
    "4. **Agent failure** \u2014 restart the worker, if it fails again create a diagnostic task\n"
    "5. **Task loop** (same task failed 3+ times) \u2014 analyze pattern, create new approach\n"
    "6. **Drift plateau** (2+ passes, no improvement) \u2014 re-diagnose, adjust strategy\n\n"
    "Only escalate to human when:\n"
    "- Self-heal failed (you tried and it didn't work)\n"
    "- The decision is inherently human: aesthetics, UX judgment, feature direction, business logic\n"
    "- External dependency needed (API keys, credentials, third-party access)\n\n"
    "When escalating, use `create_decision` with a specific question and options.\n"
    "Every escalation must include: what happened, what you tried, why it failed, "
    "and a specific question with options when possible.\n\n"
    "## Protocol Compliance\n"
    "All repos must use speedrift (workgraph + driftdriver). If you detect an agent "
    "working outside the protocol (commits without task references, missing .workgraph, "
    "no driftdriver installed), use `enforce_compliance` to flag it. "
    "Then use existing directives to bring the repo back on track.\n"
)

TIER_ADDITIONS: dict[int, str] = {
    1: "You are operating at tier 1 — reflexes/Haiku. Fast pattern-matching on critical events. Keep reasoning terse.",
    2: "You are operating at tier 2 — strategy/Sonnet. Operational analysis with cross-repo context. Think before acting.",
    3: "You are operating at tier 3 — judgment/Opus. Full strategic reasoning with escalation authority. Be thorough.",
}

TIER_MODELS: dict[int, str] = {
    1: "claude-haiku-4-5-20251001",
    2: "claude-sonnet-4-6",
    3: "claude-opus-4-6",
}


def build_system_prompt(tier: int) -> str:
    """Combine the adversary persona with tier-specific instructions and self-heal protocol."""
    addition = TIER_ADDITIONS.get(tier, "")
    return f"{ADVERSARY_SYSTEM}{SELF_HEAL_ADDENDUM}\n\n{addition}"


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

    sections.append("Analyze the situation and respond with your directives.")

    return "\n\n".join(sections)
