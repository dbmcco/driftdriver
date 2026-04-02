# ABOUTME: Directive schema, validator, parser, and executor for the factory brain.
# ABOUTME: Maps LLM-generated action intents to concrete subprocess/file operations.

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DIRECTIVE_SCHEMA: dict[str, list[str]] = {
    "kill_process": ["pid"],
    "kill_daemon": ["repo"],
    "clear_locks": ["repo"],
    "start_dispatch_loop": ["repo"],
    "stop_dispatch_loop": ["repo"],
    "spawn_agent": ["repo", "task_id"],
    "set_mode": ["repo", "mode"],
    "adjust_concurrency": ["repo", "max_agents"],
    "enroll": ["repo"],
    "unenroll": ["repo"],
    "set_attractor_target": ["repo", "target"],
    "send_telegram": ["message"],
    "escalate": ["reason"],
    "noop": ["reason"],
    "create_decision": ["repo", "question", "category"],
    "enforce_compliance": ["repo"],
    "apply_skill_fix":      ["agent", "skill_file", "diff"],
    "propose_agent_fix":    ["agent", "component", "finding_summary", "proposed_diff"],
    "restart_paia_service": ["service"],
}


@dataclass
class Directive:
    action: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class BrainResponse:
    reasoning: str
    directives: list[Directive]
    telegram: str | None = None
    escalate: bool = False


def parse_brain_response(raw: dict[str, Any]) -> BrainResponse:
    """Parse JSON from model output into a BrainResponse."""
    directives = []
    for d in raw.get("directives", []):
        directives.append(Directive(
            action=d["action"],
            params=d.get("params", {}),
        ))
    return BrainResponse(
        reasoning=raw.get("reasoning", ""),
        directives=directives,
        telegram=raw.get("telegram"),
        escalate=raw.get("escalate", False),
    )


def validate_directive(d: Directive) -> bool:
    """Check that action is known and all required params are present."""
    if d.action not in DIRECTIVE_SCHEMA:
        return False
    required = DIRECTIVE_SCHEMA[d.action]
    for param in required:
        if param not in d.params:
            return False
    return True


def _run_cmd(cmd: list[str], *, timeout: int = 30) -> tuple[int, str]:
    """Subprocess helper. Returns (exit_code, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, f"timeout after {timeout}s"
    except Exception as exc:
        return 1, str(exc)


def _resolve_repo_dir(repo: str, repo_paths: dict[str, str]) -> Path | None:
    """Resolve a repo name to its filesystem path."""
    path_str = repo_paths.get(repo)
    if path_str is None:
        return None
    return Path(path_str)


def _handle_noop(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    reason = d.params.get("reason", "no reason given")
    logger.info("noop: %s", reason)
    return {"status": "ok", "reason": reason}


def _handle_kill_process(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    pid = d.params["pid"]
    if dry_run:
        return {"status": "dry_run", "action": "kill_process", "pid": pid}
    code, output = _run_cmd(["kill", "-9", str(pid)])
    return {"status": "ok" if code == 0 else "error", "exit_code": code, "output": output}


def _handle_kill_daemon(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_dir = _resolve_repo_dir(d.params["repo"], repo_paths)
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {d.params['repo']}"}
    if dry_run:
        return {"status": "dry_run", "action": "kill_daemon", "repo": d.params["repo"]}
    code, output = _run_cmd(["wg", "service", "stop"], timeout=15)
    return {"status": "ok" if code == 0 else "error", "exit_code": code, "output": output}


def _handle_clear_locks(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_dir = _resolve_repo_dir(d.params["repo"], repo_paths)
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {d.params['repo']}"}
    if dry_run:
        return {"status": "dry_run", "action": "clear_locks", "repo": d.params["repo"]}
    service_dir = repo_dir / ".workgraph" / "service"
    removed: list[str] = []
    for name in ("daemon.sock", "daemon.lock", ".registry.lock"):
        p = service_dir / name
        if p.exists():
            p.unlink()
            removed.append(name)
    return {"status": "ok", "removed": removed}


def _handle_start_dispatch_loop(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_dir = _resolve_repo_dir(d.params["repo"], repo_paths)
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {d.params['repo']}"}
    if dry_run:
        return {"status": "dry_run", "action": "start_dispatch_loop", "repo": d.params["repo"]}
    script = repo_dir / ".workgraph" / "dispatch-loop.sh"
    if not script.exists():
        return {"status": "error", "error": f"dispatch-loop.sh not found in {repo_dir}"}
    proc = subprocess.Popen(
        [str(script)],
        cwd=str(repo_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"status": "ok", "pid": proc.pid}


def _handle_stop_dispatch_loop(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo = d.params["repo"]
    if dry_run:
        return {"status": "dry_run", "action": "stop_dispatch_loop", "repo": repo}
    code, output = _run_cmd(["pkill", "-f", "dispatch-loop"])
    return {"status": "ok" if code == 0 else "error", "exit_code": code, "output": output}


def _handle_spawn_agent(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_dir = _resolve_repo_dir(d.params["repo"], repo_paths)
    task_id = d.params["task_id"]
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {d.params['repo']}"}
    if dry_run:
        return {"status": "dry_run", "action": "spawn_agent", "repo": d.params["repo"], "task_id": task_id}
    code, output = _run_cmd(["wg", "spawn", "--executor", "claude", task_id], timeout=60)
    return {"status": "ok" if code == 0 else "error", "exit_code": code, "output": output}


def _handle_set_mode(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_dir = _resolve_repo_dir(d.params["repo"], repo_paths)
    mode = d.params["mode"]
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {d.params['repo']}"}
    if dry_run:
        return {"status": "dry_run", "action": "set_mode", "repo": d.params["repo"], "mode": mode}
    code, output = _run_cmd([
        "driftdriver", "speedriftd", "status",
        "--set-mode", mode,
    ], timeout=15)
    return {"status": "ok" if code == 0 else "error", "exit_code": code, "output": output}


def _handle_adjust_concurrency(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_dir = _resolve_repo_dir(d.params["repo"], repo_paths)
    max_agents = d.params["max_agents"]
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {d.params['repo']}"}
    if dry_run:
        return {"status": "dry_run", "action": "adjust_concurrency", "repo": d.params["repo"], "max_agents": max_agents}
    config_path = repo_dir / "factory-config.json"
    config: dict[str, Any] = {}
    if config_path.exists():
        config = json.loads(config_path.read_text())
    config["max_agents"] = max_agents
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    return {"status": "ok", "config_path": str(config_path), "max_agents": max_agents}


def _handle_enroll(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo_path_str = d.params.get("repo", "")
    repo_path = Path(repo_path_str)
    if dry_run:
        return {"status": "dry_run", "action": "enroll", "repo": repo_path_str}
    if not repo_path.exists():
        return {"action": "enroll", "status": "error", "error": f"path not found: {repo_path_str}"}
    if not (repo_path / ".workgraph").exists():
        return {"action": "enroll", "status": "error", "error": f"no .workgraph in: {repo_path_str}"}
    dispatch = repo_path / ".workgraph" / "executors" / "dispatch-loop.sh"
    if not dispatch.exists():
        template = Path(__file__).parent.parent / "templates" / "dispatch-loop.sh"
        if template.exists():
            import shutil
            dispatch.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(template), str(dispatch))
            dispatch.chmod(0o755)
            logger.info("Installed dispatch-loop.sh in %s", repo_path.name)
    return {"action": "enroll", "status": "ok", "repo": repo_path_str, "dispatch_installed": dispatch.exists()}


def _handle_unenroll(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo = d.params.get("repo", "")
    if dry_run:
        return {"status": "dry_run", "action": "unenroll", "repo": repo}
    _run_cmd(["pkill", "-f", f"dispatch-loop.sh.*{repo}"])
    logger.info("Unenrolled: %s", repo)
    return {"action": "unenroll", "status": "ok", "repo": repo}


def _handle_set_attractor_target(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    return {"status": "deferred", "action": "set_attractor_target", "repo": d.params["repo"], "target": d.params["target"]}


def _handle_send_telegram(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram

    msg = d.params.get("message", "")
    if dry_run:
        return {"action": "send_telegram", "status": "dry_run", "message": msg}
    config = load_telegram_config()
    if not config:
        logger.warning("No Telegram config found, skipping")
        return {"action": "send_telegram", "status": "no_config", "message": msg}
    ok = send_telegram(
        bot_token=config["bot_token"],
        chat_id=config["chat_id"],
        message=msg,
    )
    return {"action": "send_telegram", "status": "ok" if ok else "error", "message": msg}


def _handle_escalate(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    reason = d.params.get("reason", "no reason given")
    logger.warning("escalation: %s", reason)
    return {"status": "ok", "action": "escalate", "reason": reason}


def _handle_create_decision(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo = d.params.get("repo", "")
    question = d.params.get("question", "")
    category = d.params.get("category", "")
    repo_dir = _resolve_repo_dir(repo, repo_paths)
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {repo}"}
    if dry_run:
        return {"status": "dry_run", "action": "create_decision", "repo": repo}
    import time as _time
    decision_id = f"dec-{int(_time.time() * 1000)}"
    decisions_dir = repo_dir / ".workgraph" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_file = decisions_dir / f"{decision_id}.json"
    decision_file.write_text(json.dumps({
        "id": decision_id,
        "question": question,
        "category": category,
        "status": "open",
    }, indent=2) + "\n")
    return {"status": "ok", "action": "create_decision", "decision_id": decision_id, "repo": repo}


def _handle_enforce_compliance(d: Directive, *, dry_run: bool, repo_paths: dict[str, str]) -> dict[str, Any]:
    repo = d.params.get("repo", "")
    repo_dir = _resolve_repo_dir(repo, repo_paths)
    if repo_dir is None:
        return {"status": "error", "error": f"unknown repo: {repo}"}
    if dry_run:
        return {"status": "dry_run", "action": "enforce_compliance", "repo": repo}
    violations: list[str] = []
    if not (repo_dir / ".workgraph").is_dir():
        violations.append("missing .workgraph directory")
    if not (repo_dir / ".workgraph" / "drifts").is_dir():
        violations.append("missing .workgraph/drifts directory")
    return {
        "status": "ok",
        "action": "enforce_compliance",
        "repo": repo,
        "compliant": len(violations) == 0,
        "violations": violations,
    }


def _execute_apply_skill_fix(params: dict, *, dry_run: bool = False) -> str:
    """Auto-apply a skill fix directly (used for small/auto fixes from the healer)."""
    from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
    from driftdriver.paia_agent_health.fixes import apply_fix
    agent = params.get("agent", "")
    skill_file = params.get("skill_file", "")
    diff = params.get("diff", "")
    if not agent or not skill_file or not diff:
        return "error: apply_skill_fix requires agent, skill_file, diff"
    if dry_run:
        return f"dry_run: would apply fix to {agent}/{skill_file}"
    finding = Finding(agent=agent, pattern_type="directive", evidence=[],
                      evidence_count=0, affected_component=skill_file,
                      severity="low", confidence=1.0)
    proposal = FixProposal(finding=finding, change_summary="directive-triggered fix",
                           diff=diff, auto_apply=True, risk="low")
    apply_fix(proposal)
    return f"applied fix to {agent}/{skill_file}"


def _execute_propose_agent_fix(params: dict, *, dry_run: bool = False) -> str:
    """Send a large fix proposal via Telegram for user approval."""
    from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
    from driftdriver.paia_agent_health.fixes import send_proposal
    agent = params.get("agent", "")
    component = params.get("component", "")
    finding_summary = params.get("finding_summary", "")
    proposed_diff = params.get("proposed_diff", "")
    if dry_run:
        return f"dry_run: would propose fix for {agent}/{component}"
    finding = Finding(agent=agent, pattern_type="directive", evidence=[finding_summary],
                      evidence_count=1, affected_component=component,
                      severity="medium", confidence=1.0)
    proposal = FixProposal(finding=finding, change_summary=finding_summary,
                           diff=proposed_diff, auto_apply=False, risk="medium")
    dec_id = send_proposal(proposal)
    return f"proposed fix {dec_id} for {agent}/{component}"


def _execute_restart_paia_service(params: dict, *, dry_run: bool = False) -> str:
    """Restart a PAIA launchd service via launchctl kickstart."""
    import os
    import subprocess
    service = params.get("service", "")
    if not service:
        return "error: restart_paia_service requires service"
    label = f"com.paia.{service}"
    uid = os.getuid()
    domain = f"gui/{uid}/{label}"
    if dry_run:
        return f"dry_run: would run launchctl kickstart -k {domain}"
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", domain],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0:
        return f"restarted {label}"
    return f"restart failed ({result.returncode}): {result.stderr.strip()[:200]}"


_HANDLERS: dict[str, Any] = {
    "noop": _handle_noop,
    "kill_process": _handle_kill_process,
    "kill_daemon": _handle_kill_daemon,
    "clear_locks": _handle_clear_locks,
    "start_dispatch_loop": _handle_start_dispatch_loop,
    "stop_dispatch_loop": _handle_stop_dispatch_loop,
    "spawn_agent": _handle_spawn_agent,
    "set_mode": _handle_set_mode,
    "adjust_concurrency": _handle_adjust_concurrency,
    "enroll": _handle_enroll,
    "unenroll": _handle_unenroll,
    "set_attractor_target": _handle_set_attractor_target,
    "send_telegram": _handle_send_telegram,
    "escalate": _handle_escalate,
    "create_decision": _handle_create_decision,
    "enforce_compliance": _handle_enforce_compliance,
    "apply_skill_fix":      lambda d, **kw: _execute_apply_skill_fix(d.params, dry_run=kw.get("dry_run", False)),
    "propose_agent_fix":    lambda d, **kw: _execute_propose_agent_fix(d.params, dry_run=kw.get("dry_run", False)),
    "restart_paia_service": lambda d, **kw: _execute_restart_paia_service(d.params, dry_run=kw.get("dry_run", False)),
}


def execute_directive(
    d: Directive,
    *,
    dry_run: bool = False,
    repo_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute a single directive, returning a result dict."""
    if repo_paths is None:
        repo_paths = {}
    handler = _HANDLERS.get(d.action)
    if handler is None:
        return {"status": "error", "error": f"unknown action: {d.action}"}
    try:
        return handler(d, dry_run=dry_run, repo_paths=repo_paths)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def execute_directives(
    directives: list[Directive],
    *,
    dry_run: bool = False,
    repo_paths: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Execute a batch of directives, returning results for each."""
    results: list[dict[str, Any]] = []
    for d in directives:
        results.append(execute_directive(d, dry_run=dry_run, repo_paths=repo_paths))
    return results
