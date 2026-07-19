# ABOUTME: Speedrift protocol wrapper for Agency-composed agent output.
# ABOUTME: Injects wg-contract, executor guidance, and drift check obligations around Agency prompts.

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.speedriftd_state import _append_jsonl, load_control_state, runtime_paths
from driftdriver.workgraph import validate_pi_model_spec


def record_agency_pi_fallback_receipt(
    project_dir: Path,
    *,
    task_id: str,
    selected_model: str,
    fallback_model: str,
    reason: str,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Append an audit-only Agency-to-Pi fallback receipt.

    This function intentionally reads control state but never writes it, starts
    services, or acquires leases. Any model or filesystem error is raised to the
    caller so a fallback cannot be reported as successful without a receipt.
    """
    selected = validate_pi_model_spec(selected_model)
    fallback = validate_pi_model_spec(fallback_model)
    before = load_control_state(project_dir)
    control = {"mode": before["mode"], "lease_active": before["lease_active"]}

    receipt: dict[str, Any] = {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "repo": project_dir.resolve().name,
        "task_id": str(task_id),
        "preferred_runtime": "agency",
        "preferred_model": selected,
        "fallback_runtime": "pi",
        "fallback_model": fallback,
        "reason": str(reason),
        "control_before": control,
        "control_after": control,
    }
    receipt_path = runtime_paths(project_dir)["dir"] / "agency-pi-fallback-receipts.jsonl"
    try:
        _append_jsonl(receipt_path, receipt)
    except OSError as exc:
        raise RuntimeError("could not write Agency-to-Pi fallback receipt") from exc

    after = load_control_state(project_dir)
    control_after = {"mode": after["mode"], "lease_active": after["lease_active"]}
    if control_after != control:
        raise RuntimeError("speedriftd control changed while recording Agency fallback")
    return receipt


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
