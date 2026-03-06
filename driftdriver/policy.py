from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ORDER = [
    "coredrift",
    "specdrift",
    "datadrift",
    "archdrift",
    "depsdrift",
    "uxdrift",
    "therapydrift",
    "fixdrift",
    "yagnidrift",
    "redrift",
]

ALLOWED_MODES = {"observe", "advise", "redirect", "heal", "breaker"}
AUTONOMY_LEVELS = {"observe", "safe-fix", "safe-pr", "trusted-merge"}


def _default_factory_cfg() -> dict[str, Any]:
    return {
        "enabled": False,
        "cycle_seconds": 90,
        "plan_only": True,
        "max_repos_per_cycle": 5,
        "max_actions_per_cycle": 12,
        "emit_followups": False,
        "max_followups_per_repo": 2,
        "write_decision_ledger": True,
        "hard_stop_on_failed_verification": True,
    }


def _default_model_cfg() -> dict[str, Any]:
    return {
        "planner_profile": "default",
        "worker_profile": "default",
        "temperature": 0.2,
        "adversarial_prompts": True,
        "max_tool_rounds": 6,
    }


def _default_sourcedrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_seconds": 21600,
        "max_deltas_per_cycle": 20,
        "auto_create_followups": True,
        "allow_auto_integrate": False,
    }


def _default_syncdrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "allow_rebase": True,
        "allow_merge": True,
        "allow_destructive_sync": False,
        "require_clean_before_pr": True,
    }


def _default_stalledrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "open_without_progress_minutes": 120,
        "max_auto_unblock_actions": 3,
        "auto_split_large_tasks": True,
    }


def _default_servicedrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "restart_budget_per_cycle": 4,
        "restart_cooldown_seconds": 180,
        "escalate_after_consecutive_failures": 3,
    }


def _default_federatedrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "open_draft_prs": True,
        "auto_update_existing_drafts": True,
        "allow_auto_merge": False,
        "required_checks": ["drifts", "tests", "lint"],
    }


def _default_secdrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_seconds": 14400,
        "max_findings_per_repo": 40,
        "scan_max_files": 320,
        "scan_max_file_bytes": 262144,
        "run_pentest": False,
        "allow_network_scans": False,
        "target_urls": [],
        "emit_review_tasks": True,
        "max_review_tasks_per_repo": 3,
        "hard_stop_on_critical": False,
    }


def _default_qadrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_seconds": 21600,
        "max_findings_per_repo": 40,
        "emit_review_tasks": True,
        "max_review_tasks_per_repo": 3,
        "include_playwright": True,
        "include_test_health": True,
        "include_workgraph_health": True,
    }


def _default_sessiondriver_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "require_session_driver": True,
        "allow_cli_fallback": False,
        "max_dispatch_per_repo": 2,
        "worker_timeout_seconds": 1800,
        "drift_failure_threshold": 3,
    }


def _default_plandrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_seconds": 14400,
        "max_findings_per_repo": 40,
        "emit_review_tasks": True,
        "max_review_tasks_per_repo": 3,
        "require_integration_tests": True,
        "require_e2e_tests": True,
        "require_failure_loopbacks": True,
        "require_continuation_edges": True,
        "continuation_runtime": "double-shot-latte",
        "orchestration_runtime": "claude-session-driver",
        "allow_tmux_fallback": True,
        "hard_stop_on_critical": False,
    }


def _default_northstardrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "emit_review_tasks": True,
        "emit_operator_prompts": True,
        "daily_rollup": True,
        "weekly_trends": True,
        "score_window": "1d",
        "comparison_window": "7d",
        "dirty_repo_blocks_auto_mutation": True,
        "max_auto_interventions_per_cycle": 3,
        "max_review_tasks_per_repo": 2,
        "require_metric_evidence": True,
        "history_points": 18,
        "latent_repo_floor_score": 68.0,
    }


def _default_autonomy_default_cfg() -> dict[str, Any]:
    return {
        "level": "observe",
        "can_push": False,
        "can_open_pr": False,
        "can_merge": False,
        "max_actions_per_cycle": 1,
    }


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
    reporting_central_repo: str
    reporting_auto_report: bool
    reporting_include_knowledge: bool
    factory: dict[str, Any]
    model: dict[str, Any]
    sourcedrift: dict[str, Any]
    syncdrift: dict[str, Any]
    stalledrift: dict[str, Any]
    servicedrift: dict[str, Any]
    federatedrift: dict[str, Any]
    secdrift: dict[str, Any]
    qadrift: dict[str, Any]
    sessiondriver: dict[str, Any]
    plandrift: dict[str, Any]
    northstardrift: dict[str, Any]
    autonomy_default: dict[str, Any]
    autonomy_repos: list[dict[str, Any]]


def _default_policy_text() -> str:
    return (
        "schema = 1\n"
        "mode = \"redirect\"\n"
        "order = [\"coredrift\", \"specdrift\", \"datadrift\", \"archdrift\", \"depsdrift\", \"uxdrift\", \"therapydrift\", \"fixdrift\", \"yagnidrift\", \"redrift\"]\n"
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
        "\n"
        "[reporting]\n"
        "central_repo = \"\"\n"
        "auto_report = true\n"
        "include_knowledge = true\n"
        "\n"
        "[factory]\n"
        "enabled = false\n"
        "cycle_seconds = 90\n"
        "plan_only = true\n"
        "max_repos_per_cycle = 5\n"
        "max_actions_per_cycle = 12\n"
        "emit_followups = false\n"
        "max_followups_per_repo = 2\n"
        "write_decision_ledger = true\n"
        "hard_stop_on_failed_verification = true\n"
        "\n"
        "[model]\n"
        "planner_profile = \"default\"\n"
        "worker_profile = \"default\"\n"
        "temperature = 0.2\n"
        "adversarial_prompts = true\n"
        "max_tool_rounds = 6\n"
        "\n"
        "[sourcedrift]\n"
        "enabled = true\n"
        "interval_seconds = 21600\n"
        "max_deltas_per_cycle = 20\n"
        "auto_create_followups = true\n"
        "allow_auto_integrate = false\n"
        "\n"
        "[syncdrift]\n"
        "enabled = true\n"
        "allow_rebase = true\n"
        "allow_merge = true\n"
        "allow_destructive_sync = false\n"
        "require_clean_before_pr = true\n"
        "\n"
        "[stalledrift]\n"
        "enabled = true\n"
        "open_without_progress_minutes = 120\n"
        "max_auto_unblock_actions = 3\n"
        "auto_split_large_tasks = true\n"
        "\n"
        "[servicedrift]\n"
        "enabled = true\n"
        "restart_budget_per_cycle = 4\n"
        "restart_cooldown_seconds = 180\n"
        "escalate_after_consecutive_failures = 3\n"
        "\n"
        "[federatedrift]\n"
        "enabled = true\n"
        "open_draft_prs = true\n"
        "auto_update_existing_drafts = true\n"
        "allow_auto_merge = false\n"
        "required_checks = [\"drifts\", \"tests\", \"lint\"]\n"
        "\n"
        "[secdrift]\n"
        "enabled = true\n"
        "interval_seconds = 14400\n"
        "max_findings_per_repo = 40\n"
        "scan_max_files = 320\n"
        "scan_max_file_bytes = 262144\n"
        "run_pentest = false\n"
        "allow_network_scans = false\n"
        "target_urls = []\n"
        "emit_review_tasks = true\n"
        "max_review_tasks_per_repo = 3\n"
        "hard_stop_on_critical = false\n"
        "\n"
        "[qadrift]\n"
        "enabled = true\n"
        "interval_seconds = 21600\n"
        "max_findings_per_repo = 40\n"
        "emit_review_tasks = true\n"
        "max_review_tasks_per_repo = 3\n"
        "include_playwright = true\n"
        "include_test_health = true\n"
        "include_workgraph_health = true\n"
        "\n"
        "[sessiondriver]\n"
        "enabled = true\n"
        "require_session_driver = true\n"
        "allow_cli_fallback = false\n"
        "max_dispatch_per_repo = 2\n"
        "worker_timeout_seconds = 1800\n"
        "drift_failure_threshold = 3\n"
        "\n"
        "[plandrift]\n"
        "enabled = true\n"
        "interval_seconds = 14400\n"
        "max_findings_per_repo = 40\n"
        "emit_review_tasks = true\n"
        "max_review_tasks_per_repo = 3\n"
        "require_integration_tests = true\n"
        "require_e2e_tests = true\n"
        "require_failure_loopbacks = true\n"
        "require_continuation_edges = true\n"
        "continuation_runtime = \"double-shot-latte\"\n"
        "orchestration_runtime = \"claude-session-driver\"\n"
        "allow_tmux_fallback = true\n"
        "hard_stop_on_critical = false\n"
        "\n"
        "[northstardrift]\n"
        "enabled = true\n"
        "emit_review_tasks = true\n"
        "emit_operator_prompts = true\n"
        "daily_rollup = true\n"
        "weekly_trends = true\n"
        "score_window = \"1d\"\n"
        "comparison_window = \"7d\"\n"
        "dirty_repo_blocks_auto_mutation = true\n"
        "max_auto_interventions_per_cycle = 3\n"
        "max_review_tasks_per_repo = 2\n"
        "require_metric_evidence = true\n"
        "history_points = 18\n"
        "latent_repo_floor_score = 68.0\n"
        "\n"
        "[autonomy.default]\n"
        "level = \"observe\"\n"
        "can_push = false\n"
        "can_open_pr = false\n"
        "can_merge = false\n"
        "max_actions_per_cycle = 1\n"
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
            reporting_central_repo="",
            reporting_auto_report=True,
            reporting_include_knowledge=True,
            factory=_default_factory_cfg(),
            model=_default_model_cfg(),
            sourcedrift=_default_sourcedrift_cfg(),
            syncdrift=_default_syncdrift_cfg(),
            stalledrift=_default_stalledrift_cfg(),
            servicedrift=_default_servicedrift_cfg(),
            federatedrift=_default_federatedrift_cfg(),
            secdrift=_default_secdrift_cfg(),
            qadrift=_default_qadrift_cfg(),
            sessiondriver=_default_sessiondriver_cfg(),
            plandrift=_default_plandrift_cfg(),
            northstardrift=_default_northstardrift_cfg(),
            autonomy_default=_default_autonomy_default_cfg(),
            autonomy_repos=[],
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
            reporting_central_repo="",
            reporting_auto_report=True,
            reporting_include_knowledge=True,
            factory=_default_factory_cfg(),
            model=_default_model_cfg(),
            sourcedrift=_default_sourcedrift_cfg(),
            syncdrift=_default_syncdrift_cfg(),
            stalledrift=_default_stalledrift_cfg(),
            servicedrift=_default_servicedrift_cfg(),
            federatedrift=_default_federatedrift_cfg(),
            secdrift=_default_secdrift_cfg(),
            qadrift=_default_qadrift_cfg(),
            sessiondriver=_default_sessiondriver_cfg(),
            plandrift=_default_plandrift_cfg(),
            northstardrift=_default_northstardrift_cfg(),
            autonomy_default=_default_autonomy_default_cfg(),
            autonomy_repos=[],
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

    reporting = data.get("reporting") if isinstance(data.get("reporting"), dict) else {}
    reporting_central_repo = str(reporting.get("central_repo", ""))
    reporting_auto_report = bool(reporting.get("auto_report", True))
    reporting_include_knowledge = bool(reporting.get("include_knowledge", True))

    factory_raw = data.get("factory") if isinstance(data.get("factory"), dict) else {}
    factory = _default_factory_cfg()
    factory["enabled"] = bool(factory_raw.get("enabled", factory["enabled"]))
    factory["cycle_seconds"] = max(5, int(factory_raw.get("cycle_seconds", factory["cycle_seconds"])))
    factory["plan_only"] = bool(factory_raw.get("plan_only", factory["plan_only"]))
    factory["max_repos_per_cycle"] = max(1, int(factory_raw.get("max_repos_per_cycle", factory["max_repos_per_cycle"])))
    factory["max_actions_per_cycle"] = max(
        1, int(factory_raw.get("max_actions_per_cycle", factory["max_actions_per_cycle"]))
    )
    factory["emit_followups"] = bool(factory_raw.get("emit_followups", factory["emit_followups"]))
    factory["max_followups_per_repo"] = max(
        1, int(factory_raw.get("max_followups_per_repo", factory["max_followups_per_repo"]))
    )
    factory["write_decision_ledger"] = bool(
        factory_raw.get("write_decision_ledger", factory["write_decision_ledger"])
    )
    factory["hard_stop_on_failed_verification"] = bool(
        factory_raw.get("hard_stop_on_failed_verification", factory["hard_stop_on_failed_verification"])
    )

    model_raw = data.get("model") if isinstance(data.get("model"), dict) else {}
    model = _default_model_cfg()
    model["planner_profile"] = str(model_raw.get("planner_profile", model["planner_profile"]) or "default")
    model["worker_profile"] = str(model_raw.get("worker_profile", model["worker_profile"]) or "default")
    try:
        model["temperature"] = float(model_raw.get("temperature", model["temperature"]))
    except Exception:
        model["temperature"] = _default_model_cfg()["temperature"]
    if model["temperature"] < 0:
        model["temperature"] = 0.0
    if model["temperature"] > 2:
        model["temperature"] = 2.0
    model["adversarial_prompts"] = bool(model_raw.get("adversarial_prompts", model["adversarial_prompts"]))
    model["max_tool_rounds"] = max(1, int(model_raw.get("max_tool_rounds", model["max_tool_rounds"])))

    sourcedrift_raw = data.get("sourcedrift") if isinstance(data.get("sourcedrift"), dict) else {}
    sourcedrift = _default_sourcedrift_cfg()
    sourcedrift["enabled"] = bool(sourcedrift_raw.get("enabled", sourcedrift["enabled"]))
    sourcedrift["interval_seconds"] = max(0, int(sourcedrift_raw.get("interval_seconds", sourcedrift["interval_seconds"])))
    sourcedrift["max_deltas_per_cycle"] = max(
        1, int(sourcedrift_raw.get("max_deltas_per_cycle", sourcedrift["max_deltas_per_cycle"]))
    )
    sourcedrift["auto_create_followups"] = bool(
        sourcedrift_raw.get("auto_create_followups", sourcedrift["auto_create_followups"])
    )
    sourcedrift["allow_auto_integrate"] = bool(
        sourcedrift_raw.get("allow_auto_integrate", sourcedrift["allow_auto_integrate"])
    )

    syncdrift_raw = data.get("syncdrift") if isinstance(data.get("syncdrift"), dict) else {}
    syncdrift = _default_syncdrift_cfg()
    syncdrift["enabled"] = bool(syncdrift_raw.get("enabled", syncdrift["enabled"]))
    syncdrift["allow_rebase"] = bool(syncdrift_raw.get("allow_rebase", syncdrift["allow_rebase"]))
    syncdrift["allow_merge"] = bool(syncdrift_raw.get("allow_merge", syncdrift["allow_merge"]))
    syncdrift["allow_destructive_sync"] = bool(
        syncdrift_raw.get("allow_destructive_sync", syncdrift["allow_destructive_sync"])
    )
    syncdrift["require_clean_before_pr"] = bool(
        syncdrift_raw.get("require_clean_before_pr", syncdrift["require_clean_before_pr"])
    )

    stalledrift_raw = data.get("stalledrift") if isinstance(data.get("stalledrift"), dict) else {}
    stalledrift = _default_stalledrift_cfg()
    stalledrift["enabled"] = bool(stalledrift_raw.get("enabled", stalledrift["enabled"]))
    stalledrift["open_without_progress_minutes"] = max(
        0, int(stalledrift_raw.get("open_without_progress_minutes", stalledrift["open_without_progress_minutes"]))
    )
    stalledrift["max_auto_unblock_actions"] = max(
        0, int(stalledrift_raw.get("max_auto_unblock_actions", stalledrift["max_auto_unblock_actions"]))
    )
    stalledrift["auto_split_large_tasks"] = bool(
        stalledrift_raw.get("auto_split_large_tasks", stalledrift["auto_split_large_tasks"])
    )

    servicedrift_raw = data.get("servicedrift") if isinstance(data.get("servicedrift"), dict) else {}
    servicedrift = _default_servicedrift_cfg()
    servicedrift["enabled"] = bool(servicedrift_raw.get("enabled", servicedrift["enabled"]))
    servicedrift["restart_budget_per_cycle"] = max(
        0, int(servicedrift_raw.get("restart_budget_per_cycle", servicedrift["restart_budget_per_cycle"]))
    )
    servicedrift["restart_cooldown_seconds"] = max(
        1, int(servicedrift_raw.get("restart_cooldown_seconds", servicedrift["restart_cooldown_seconds"]))
    )
    servicedrift["escalate_after_consecutive_failures"] = max(
        1,
        int(
            servicedrift_raw.get(
                "escalate_after_consecutive_failures",
                servicedrift["escalate_after_consecutive_failures"],
            )
        ),
    )

    federatedrift_raw = data.get("federatedrift") if isinstance(data.get("federatedrift"), dict) else {}
    federatedrift = _default_federatedrift_cfg()
    federatedrift["enabled"] = bool(federatedrift_raw.get("enabled", federatedrift["enabled"]))
    federatedrift["open_draft_prs"] = bool(federatedrift_raw.get("open_draft_prs", federatedrift["open_draft_prs"]))
    federatedrift["auto_update_existing_drafts"] = bool(
        federatedrift_raw.get("auto_update_existing_drafts", federatedrift["auto_update_existing_drafts"])
    )
    federatedrift["allow_auto_merge"] = bool(
        federatedrift_raw.get("allow_auto_merge", federatedrift["allow_auto_merge"])
    )
    checks_raw = federatedrift_raw.get("required_checks")
    if isinstance(checks_raw, list):
        checks = [str(x).strip() for x in checks_raw if str(x).strip()]
        federatedrift["required_checks"] = checks or list(_default_federatedrift_cfg()["required_checks"])

    secdrift_raw = data.get("secdrift") if isinstance(data.get("secdrift"), dict) else {}
    secdrift = _default_secdrift_cfg()
    secdrift["enabled"] = bool(secdrift_raw.get("enabled", secdrift["enabled"]))
    secdrift["interval_seconds"] = max(0, int(secdrift_raw.get("interval_seconds", secdrift["interval_seconds"])))
    secdrift["max_findings_per_repo"] = max(
        1, int(secdrift_raw.get("max_findings_per_repo", secdrift["max_findings_per_repo"]))
    )
    secdrift["scan_max_files"] = max(20, int(secdrift_raw.get("scan_max_files", secdrift["scan_max_files"])))
    secdrift["scan_max_file_bytes"] = max(
        2048, int(secdrift_raw.get("scan_max_file_bytes", secdrift["scan_max_file_bytes"]))
    )
    secdrift["run_pentest"] = bool(secdrift_raw.get("run_pentest", secdrift["run_pentest"]))
    secdrift["allow_network_scans"] = bool(
        secdrift_raw.get("allow_network_scans", secdrift["allow_network_scans"])
    )
    target_urls_raw = secdrift_raw.get("target_urls")
    if isinstance(target_urls_raw, list):
        target_urls = [str(item).strip() for item in target_urls_raw if str(item).strip()]
        secdrift["target_urls"] = target_urls[:20]
    secdrift["emit_review_tasks"] = bool(secdrift_raw.get("emit_review_tasks", secdrift["emit_review_tasks"]))
    secdrift["max_review_tasks_per_repo"] = max(
        1, int(secdrift_raw.get("max_review_tasks_per_repo", secdrift["max_review_tasks_per_repo"]))
    )
    secdrift["hard_stop_on_critical"] = bool(
        secdrift_raw.get("hard_stop_on_critical", secdrift["hard_stop_on_critical"])
    )

    qadrift_raw = data.get("qadrift") if isinstance(data.get("qadrift"), dict) else {}
    qadrift = _default_qadrift_cfg()
    qadrift["enabled"] = bool(qadrift_raw.get("enabled", qadrift["enabled"]))
    qadrift["interval_seconds"] = max(0, int(qadrift_raw.get("interval_seconds", qadrift["interval_seconds"])))
    qadrift["max_findings_per_repo"] = max(
        1, int(qadrift_raw.get("max_findings_per_repo", qadrift["max_findings_per_repo"]))
    )
    qadrift["emit_review_tasks"] = bool(qadrift_raw.get("emit_review_tasks", qadrift["emit_review_tasks"]))
    qadrift["max_review_tasks_per_repo"] = max(
        1, int(qadrift_raw.get("max_review_tasks_per_repo", qadrift["max_review_tasks_per_repo"]))
    )
    qadrift["include_playwright"] = bool(qadrift_raw.get("include_playwright", qadrift["include_playwright"]))
    qadrift["include_test_health"] = bool(qadrift_raw.get("include_test_health", qadrift["include_test_health"]))
    qadrift["include_workgraph_health"] = bool(
        qadrift_raw.get("include_workgraph_health", qadrift["include_workgraph_health"])
    )

    sessiondriver_raw = data.get("sessiondriver") if isinstance(data.get("sessiondriver"), dict) else {}
    sessiondriver = _default_sessiondriver_cfg()
    sessiondriver["enabled"] = bool(sessiondriver_raw.get("enabled", sessiondriver["enabled"]))
    sessiondriver["require_session_driver"] = bool(
        sessiondriver_raw.get("require_session_driver", sessiondriver["require_session_driver"])
    )
    sessiondriver["allow_cli_fallback"] = bool(
        sessiondriver_raw.get("allow_cli_fallback", sessiondriver["allow_cli_fallback"])
    )
    sessiondriver["max_dispatch_per_repo"] = max(
        1, int(sessiondriver_raw.get("max_dispatch_per_repo", sessiondriver["max_dispatch_per_repo"]))
    )
    sessiondriver["worker_timeout_seconds"] = max(
        60, int(sessiondriver_raw.get("worker_timeout_seconds", sessiondriver["worker_timeout_seconds"]))
    )
    sessiondriver["drift_failure_threshold"] = max(
        1, int(sessiondriver_raw.get("drift_failure_threshold", sessiondriver["drift_failure_threshold"]))
    )

    plandrift_raw = data.get("plandrift") if isinstance(data.get("plandrift"), dict) else {}
    plandrift = _default_plandrift_cfg()
    plandrift["enabled"] = bool(plandrift_raw.get("enabled", plandrift["enabled"]))
    plandrift["interval_seconds"] = max(0, int(plandrift_raw.get("interval_seconds", plandrift["interval_seconds"])))
    plandrift["max_findings_per_repo"] = max(
        1, int(plandrift_raw.get("max_findings_per_repo", plandrift["max_findings_per_repo"]))
    )
    plandrift["emit_review_tasks"] = bool(plandrift_raw.get("emit_review_tasks", plandrift["emit_review_tasks"]))
    plandrift["max_review_tasks_per_repo"] = max(
        1, int(plandrift_raw.get("max_review_tasks_per_repo", plandrift["max_review_tasks_per_repo"]))
    )
    plandrift["require_integration_tests"] = bool(
        plandrift_raw.get("require_integration_tests", plandrift["require_integration_tests"])
    )
    plandrift["require_e2e_tests"] = bool(
        plandrift_raw.get("require_e2e_tests", plandrift["require_e2e_tests"])
    )
    plandrift["require_failure_loopbacks"] = bool(
        plandrift_raw.get("require_failure_loopbacks", plandrift["require_failure_loopbacks"])
    )
    plandrift["require_continuation_edges"] = bool(
        plandrift_raw.get("require_continuation_edges", plandrift["require_continuation_edges"])
    )
    plandrift["continuation_runtime"] = str(
        plandrift_raw.get("continuation_runtime", plandrift["continuation_runtime"]) or plandrift["continuation_runtime"]
    )
    plandrift["orchestration_runtime"] = str(
        plandrift_raw.get("orchestration_runtime", plandrift["orchestration_runtime"]) or plandrift["orchestration_runtime"]
    )
    plandrift["allow_tmux_fallback"] = bool(
        plandrift_raw.get("allow_tmux_fallback", plandrift["allow_tmux_fallback"])
    )
    plandrift["hard_stop_on_critical"] = bool(
        plandrift_raw.get("hard_stop_on_critical", plandrift["hard_stop_on_critical"])
    )

    northstardrift_raw = data.get("northstardrift") if isinstance(data.get("northstardrift"), dict) else {}
    northstardrift = _default_northstardrift_cfg()
    northstardrift["enabled"] = bool(northstardrift_raw.get("enabled", northstardrift["enabled"]))
    northstardrift["emit_review_tasks"] = bool(
        northstardrift_raw.get("emit_review_tasks", northstardrift["emit_review_tasks"])
    )
    northstardrift["emit_operator_prompts"] = bool(
        northstardrift_raw.get("emit_operator_prompts", northstardrift["emit_operator_prompts"])
    )
    northstardrift["daily_rollup"] = bool(northstardrift_raw.get("daily_rollup", northstardrift["daily_rollup"]))
    northstardrift["weekly_trends"] = bool(
        northstardrift_raw.get("weekly_trends", northstardrift["weekly_trends"])
    )
    northstardrift["score_window"] = str(
        northstardrift_raw.get("score_window", northstardrift["score_window"]) or northstardrift["score_window"]
    )
    northstardrift["comparison_window"] = str(
        northstardrift_raw.get("comparison_window", northstardrift["comparison_window"])
        or northstardrift["comparison_window"]
    )
    northstardrift["dirty_repo_blocks_auto_mutation"] = bool(
        northstardrift_raw.get(
            "dirty_repo_blocks_auto_mutation",
            northstardrift["dirty_repo_blocks_auto_mutation"],
        )
    )
    northstardrift["max_auto_interventions_per_cycle"] = max(
        1,
        int(
            northstardrift_raw.get(
                "max_auto_interventions_per_cycle",
                northstardrift["max_auto_interventions_per_cycle"],
            )
        ),
    )
    northstardrift["max_review_tasks_per_repo"] = max(
        1,
        int(
            northstardrift_raw.get(
                "max_review_tasks_per_repo",
                northstardrift["max_review_tasks_per_repo"],
            )
        ),
    )
    northstardrift["require_metric_evidence"] = bool(
        northstardrift_raw.get("require_metric_evidence", northstardrift["require_metric_evidence"])
    )
    northstardrift["history_points"] = max(
        6, int(northstardrift_raw.get("history_points", northstardrift["history_points"]))
    )
    try:
        northstardrift["latent_repo_floor_score"] = float(
            northstardrift_raw.get("latent_repo_floor_score", northstardrift["latent_repo_floor_score"])
        )
    except Exception:
        northstardrift["latent_repo_floor_score"] = _default_northstardrift_cfg()["latent_repo_floor_score"]

    autonomy_raw = data.get("autonomy") if isinstance(data.get("autonomy"), dict) else {}
    autonomy_default_raw = autonomy_raw.get("default") if isinstance(autonomy_raw.get("default"), dict) else {}
    autonomy_default = _default_autonomy_default_cfg()
    level_default = str(autonomy_default_raw.get("level", autonomy_default["level"])).strip().lower()
    autonomy_default["level"] = level_default if level_default in AUTONOMY_LEVELS else _default_autonomy_default_cfg()["level"]
    autonomy_default["can_push"] = bool(autonomy_default_raw.get("can_push", autonomy_default["can_push"]))
    autonomy_default["can_open_pr"] = bool(autonomy_default_raw.get("can_open_pr", autonomy_default["can_open_pr"]))
    autonomy_default["can_merge"] = bool(autonomy_default_raw.get("can_merge", autonomy_default["can_merge"]))
    autonomy_default["max_actions_per_cycle"] = max(
        0, int(autonomy_default_raw.get("max_actions_per_cycle", autonomy_default["max_actions_per_cycle"]))
    )
    autonomy_repos: list[dict[str, Any]] = []
    repos_raw = autonomy_raw.get("repo")
    if isinstance(repos_raw, list):
        for row in repos_raw:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            level = str(row.get("level", autonomy_default["level"])).strip().lower()
            autonomy_repos.append(
                {
                    "name": name,
                    "level": level if level in AUTONOMY_LEVELS else autonomy_default["level"],
                    "can_push": bool(row.get("can_push", autonomy_default["can_push"])),
                    "can_open_pr": bool(row.get("can_open_pr", autonomy_default["can_open_pr"])),
                    "can_merge": bool(row.get("can_merge", autonomy_default["can_merge"])),
                    "max_actions_per_cycle": max(
                        0, int(row.get("max_actions_per_cycle", autonomy_default["max_actions_per_cycle"]))
                    ),
                }
            )

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
        reporting_central_repo=reporting_central_repo,
        reporting_auto_report=reporting_auto_report,
        reporting_include_knowledge=reporting_include_knowledge,
        factory=factory,
        model=model,
        sourcedrift=sourcedrift,
        syncdrift=syncdrift,
        stalledrift=stalledrift,
        servicedrift=servicedrift,
        federatedrift=federatedrift,
        secdrift=secdrift,
        qadrift=qadrift,
        sessiondriver=sessiondriver,
        plandrift=plandrift,
        northstardrift=northstardrift,
        autonomy_default=autonomy_default,
        autonomy_repos=autonomy_repos,
    )
