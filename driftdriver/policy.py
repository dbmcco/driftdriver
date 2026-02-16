from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ORDER = [
    "speedrift",
    "specdrift",
    "datadrift",
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


def _default_policy_text() -> str:
    return (
        "schema = 1\n"
        "mode = \"redirect\"\n"
        "order = [\"speedrift\", \"specdrift\", \"datadrift\", \"depsdrift\", \"uxdrift\", \"therapydrift\", \"yagnidrift\", \"redrift\"]\n"
        "\n"
        "[recursion]\n"
        "cooldown_seconds = 1800\n"
        "max_auto_actions_per_hour = 2\n"
        "require_new_evidence = true\n"
        "max_auto_depth = 2\n"
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
        )

    schema = int(data.get("schema", 1))
    mode_raw = str(data.get("mode", "redirect")).strip().lower()
    mode = mode_raw if mode_raw in ALLOWED_MODES else "redirect"

    order_raw = data.get("order")
    if isinstance(order_raw, list):
        order = [str(x).strip() for x in order_raw if str(x).strip()]
        # Keep baseline first; append any missing defaults.
        if "speedrift" not in order:
            order = ["speedrift", *order]
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

    return DriftPolicy(
        schema=schema,
        mode=mode,
        order=order,
        cooldown_seconds=cooldown_seconds,
        max_auto_actions_per_hour=max_auto_actions_per_hour,
        require_new_evidence=require_new_evidence,
        max_auto_depth=max_auto_depth,
    )
