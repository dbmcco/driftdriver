# ABOUTME: Speedrift protocol wrapper for Agency-composed agent output.
# ABOUTME: Injects wg-contract, executor guidance, and drift check obligations around Agency prompts.

from __future__ import annotations


def _executor_guidance(task_id: str) -> str:
    """Build the executor guidance section for a given task."""
    return (
        "## Executor Guidance\n"
        "\n"
        "You are operating under the speedrift protocol. Follow these rules:\n"
        "\n"
        f"- Log progress: `wg log {task_id} \"<message>\"`\n"
        f"- When complete: `wg done {task_id}`\n"
        f"- If blocked: `wg fail {task_id} --reason \"<description>\"`\n"
        "- Stay focused on this task. Do not expand scope.\n"
        "- Prefer follow-up tasks over bloating the current task.\n"
    )


def _drift_obligations(task_id: str) -> str:
    """Build the drift check obligations section."""
    return (
        "## Drift Check Obligations\n"
        "\n"
        "At task start and just before completion, run:\n"
        "\n"
        "```bash\n"
        f"./.workgraph/drifts check --task {task_id} --write-log --create-followups\n"
        "```\n"
        "\n"
        "- Drift is advisory — never block the main task.\n"
        "- If drift shows up, prefer contract edits and follow-up tasks.\n"
        "- coredrift always runs (scope/contract checking).\n"
        "- If `hardening_in_core` is flagged, complete the `harden:` follow-up instead.\n"
    )


def wrap_agency_output(
    agency_prompt: str | None,
    wg_contract: str,
    task_id: str,
) -> str:
    """Wrap Agency-composed prompt with speedrift protocol envelope.

    Agency supplies the cognitive role (who the agent is, trade-offs, style).
    This wrapper supplies the protocol (wg-contract, drift checks, workgraph rules).

    Args:
        agency_prompt: Agency-composed agent identity/configuration. May be empty/None.
        wg_contract: The wg-contract block from the task description.
        task_id: The workgraph task ID.

    Returns:
        Full agent instruction with speedrift protocol compliance.
    """
    parts: list[str] = []

    # --- Agency identity (if present) ---
    if agency_prompt and agency_prompt.strip():
        parts.append("## Agency-Composed Agent Identity\n")
        parts.append(agency_prompt.strip())
        parts.append("")

    # --- wg-contract (if present) ---
    if wg_contract and wg_contract.strip():
        parts.append("## Task Contract\n")
        parts.append(wg_contract.strip())
        parts.append("")

    # --- Executor guidance (always) ---
    parts.append(_executor_guidance(task_id))

    # --- Drift check obligations (always) ---
    parts.append(_drift_obligations(task_id))

    return "\n".join(parts)
