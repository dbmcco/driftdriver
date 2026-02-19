from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ORDER = [
    "coredrift",
    "specdrift",
    "datadrift",
    "archdrift",
    "depsdrift",
    "uxdrift",
    "therapydrift",
    "yagnidrift",
    "redrift",
]

ALLOWED_MODES = {"observe", "advise", "redirect", "heal", "breaker"}


@dataclass(frozen=True)
class DriftPolicy:
    schema: int
    mode: str
    order: list[str]
    cooldown_seconds: int
    max_auto_actions_per_hour: int
    require_new_evidence: bool
    max_auto_depth: int
    contracts_auto_ensure: bool
    updates_enabled: bool
    updates_check_interval_seconds: int
    updates_create_followup: bool
    loop_max_redrift_depth: int
    loop_max_ready_drift_followups: int
    loop_block_followup_creation: bool


def _default_policy_text() -> str:
    return (
        "schema = 1\n"
        "mode = \"redirect\"\n"
        "order = [\"coredrift\", \"specdrift\", \"datadrift\", \"archdrift\", \"depsdrift\", \"uxdrift\", \"therapydrift\", \"yagnidrift\", \"redrift\"]\n"
        "\n"
        "[recursion]\n"
        "cooldown_seconds = 1800\n"
        "max_auto_actions_per_hour = 2\n"
        "require_new_evidence = true\n"
        "max_auto_depth = 2\n"
        "\n"
        "[contracts]\n"
        "auto_ensure = true\n"
        "\n"
        "[updates]\n"
        "enabled = true\n"
        "check_interval_seconds = 21600\n"
        "create_followup = false\n"
        "\n"
        "[loop_safety]\n"
        "max_redrift_depth = 2\n"
        "max_ready_drift_followups = 20\n"
        "block_followup_creation = true\n"
    )


def ensure_drift_policy(wg_dir: Path) -> bool:
    """
    Ensure `.workgraph/drift-policy.toml` exists.
    Returns True if file was created.
    """

    path = wg_dir / "drift-policy.toml"
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_default_policy_text(), encoding="utf-8")
    return True


def load_drift_policy(wg_dir: Path) -> DriftPolicy:
    path = wg_dir / "drift-policy.toml"
    if not path.exists():
        return DriftPolicy(
            schema=1,
            mode="redirect",
            order=list(DEFAULT_ORDER),
            cooldown_seconds=1800,
            max_auto_actions_per_hour=2,
            require_new_evidence=True,
            max_auto_depth=2,
            contracts_auto_ensure=True,
            updates_enabled=True,
            updates_check_interval_seconds=21600,
            updates_create_followup=False,
            loop_max_redrift_depth=2,
            loop_max_ready_drift_followups=20,
            loop_block_followup_creation=True,
        )

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return DriftPolicy(
            schema=1,
            mode="redirect",
            order=list(DEFAULT_ORDER),
            cooldown_seconds=1800,
            max_auto_actions_per_hour=2,
            require_new_evidence=True,
            max_auto_depth=2,
            contracts_auto_ensure=True,
            updates_enabled=True,
            updates_check_interval_seconds=21600,
            updates_create_followup=False,
            loop_max_redrift_depth=2,
            loop_max_ready_drift_followups=20,
            loop_block_followup_creation=True,
        )

    schema = int(data.get("schema", 1))
    mode_raw = str(data.get("mode", "redirect")).strip().lower()
    mode = mode_raw if mode_raw in ALLOWED_MODES else "redirect"

    order_raw = data.get("order")
    if isinstance(order_raw, list):
        order = [str(x).strip() for x in order_raw if str(x).strip()]
        # Keep baseline first; append any missing defaults.
        if "coredrift" not in order:
            order = ["coredrift", *order]
        for p in DEFAULT_ORDER:
            if p not in order:
                order.append(p)
    else:
        order = list(DEFAULT_ORDER)

    rec = data.get("recursion") if isinstance(data.get("recursion"), dict) else {}
    cooldown_seconds = int(rec.get("cooldown_seconds", 1800))
    if cooldown_seconds < 0:
        cooldown_seconds = 0
    max_auto_actions_per_hour = int(rec.get("max_auto_actions_per_hour", 2))
    if max_auto_actions_per_hour < 0:
        max_auto_actions_per_hour = 0
    require_new_evidence = bool(rec.get("require_new_evidence", True))
    max_auto_depth = int(rec.get("max_auto_depth", 2))
    if max_auto_depth < 1:
        max_auto_depth = 1

    contracts = data.get("contracts") if isinstance(data.get("contracts"), dict) else {}
    contracts_auto_ensure = bool(contracts.get("auto_ensure", True))

    updates = data.get("updates") if isinstance(data.get("updates"), dict) else {}
    updates_enabled = bool(updates.get("enabled", True))
    updates_check_interval_seconds = int(updates.get("check_interval_seconds", 21600))
    if updates_check_interval_seconds < 0:
        updates_check_interval_seconds = 0
    updates_create_followup = bool(updates.get("create_followup", False))

    loop_safety = data.get("loop_safety") if isinstance(data.get("loop_safety"), dict) else {}
    loop_max_redrift_depth = int(loop_safety.get("max_redrift_depth", 2))
    if loop_max_redrift_depth < 0:
        loop_max_redrift_depth = 0
    loop_max_ready_drift_followups = int(loop_safety.get("max_ready_drift_followups", 20))
    if loop_max_ready_drift_followups < 0:
        loop_max_ready_drift_followups = 0
    loop_block_followup_creation = bool(loop_safety.get("block_followup_creation", True))

    return DriftPolicy(
        schema=schema,
        mode=mode,
        order=order,
        cooldown_seconds=cooldown_seconds,
        max_auto_actions_per_hour=max_auto_actions_per_hour,
        require_new_evidence=require_new_evidence,
        max_auto_depth=max_auto_depth,
        contracts_auto_ensure=contracts_auto_ensure,
        updates_enabled=updates_enabled,
        updates_check_interval_seconds=updates_check_interval_seconds,
        updates_create_followup=updates_create_followup,
        loop_max_redrift_depth=loop_max_redrift_depth,
        loop_max_ready_drift_followups=loop_max_ready_drift_followups,
        loop_block_followup_creation=loop_block_followup_creation,
    )
