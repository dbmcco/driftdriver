# ABOUTME: Project autopilot — closes the north star loop
# ABOUTME: Goal decomposition → worker dispatch → drift checks → loop until done

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


from driftdriver.planner import DECOMPOSE_PROMPT_TEMPLATE, build_decompose_prompt

SESSION_DRIVER_GLOB = (
    "~/.claude/plugins/cache/superpowers-marketplace/"
    "claude-session-driver/*/scripts"
)

WORKER_PROMPT_TEMPLATE = """\
You are working in: {project_dir}

Task: {task_id} — {task_title}

{task_description}

## Protocol
1. At start, run drift check:
   ./.workgraph/drifts check --task {task_id} --write-log --create-followups
2. Follow TDD: write failing tests first, implement, verify green.
3. Log progress: wg log {task_id} "message"
4. Before completion, run drift check again:
   ./.workgraph/drifts check --task {task_id} --write-log --create-followups
5. If clean, mark done: wg done {task_id}
6. If drift findings exist, fix them or create follow-up tasks.
7. If you are stuck and need human judgment, run:
   wg fail {task_id} --reason "description of what needs human input"
"""

REVIEW_PROMPT_TEMPLATE = """\
You are a milestone reviewer in: {project_dir}

## Goal
{goal}

## Completed Tasks
{completed_tasks}

## Failed/Escalated Tasks
{problem_tasks}

## Workgraph Evaluation Evidence
{wg_eval_evidence}

## Review Protocol

You must verify the milestone by TRACING EXECUTION PATHS, not by assumption.

### Rules
1. **Map the full execution graph**: For each completed task, identify what
   the controller did, what the worker did, and what drift checks verified.
   Read the actual code and wg logs — do not assume.
2. **Trace claims through code**: For each acceptance criterion in the goal,
   follow the concrete code path that satisfies it. Cite file:line evidence.
3. **Distinguish delegation from absence**: "The controller doesn't do X"
   is NOT the same as "X isn't done." Check whether a worker, drift check,
   or workgraph resolution handles it.
4. **Test empirically**: Run dry-run or read output artifacts to verify
   claims. Check .workgraph/output/ logs and drift findings.
5. **Incorporate wg evaluation scores**: If workgraph agency evaluation data
   is present above, use it as supplementary evidence. These scores come from
   wg's 6-step evaluation cascade (individual quality 70%% + org impact 30%%).
   They are advisory — cross-reference with code evidence.

### Output
Write your review to: .workgraph/.autopilot/milestone-review.md

Structure:
- **Verified**: claims confirmed with evidence (file:line or artifact path)
- **Unverified**: claims that lack traceable evidence
- **Gaps**: genuine missing capabilities (not delegation misunderstandings)
- **Grade**: percentage of goal acceptance criteria met with evidence

Log your findings: wg log autopilot-review "summary"
"""


@dataclass
class AutopilotConfig:
    project_dir: Path
    max_parallel: int = 4
    worker_timeout: int = 1800
    drift_failure_threshold: int = 3
    dry_run: bool = False
    goal: str = ""
    no_peer_dispatch: bool = False


@dataclass
class WorkerContext:
    task_id: str
    task_title: str
    worker_name: str
    session_id: str | None = None
    started_at: float = 0.0
    status: str = "pending"  # pending, running, completed, failed, escalated
    response: str = ""
    drift_findings: list[str] = field(default_factory=list)
    drift_fail_count: int = 0


@dataclass
class AutopilotRun:
    config: AutopilotConfig
    workers: dict[str, WorkerContext] = field(default_factory=dict)
    completed_tasks: set[str] = field(default_factory=set)
    failed_tasks: set[str] = field(default_factory=set)
    escalated_tasks: set[str] = field(default_factory=set)
    started_at: float = 0.0
    loop_count: int = 0


def discover_session_driver() -> Path | None:
    """Find session-driver scripts directory."""
    from glob import glob

    expanded = os.path.expanduser(SESSION_DRIVER_GLOB)
    matches = sorted(glob(expanded))
    if matches:
        return Path(matches[-1])
    return None


def _binary_candidates(binary: str) -> list[str]:
    candidates: list[str] = []
    discovered = shutil.which(binary)
    if discovered:
        candidates.append(discovered)
    if binary == "wg":
        candidates.extend(
            [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]
        )
        users_root = Path("/Users")
        if users_root.exists():
            for extra in users_root.glob("*/.cargo/bin/wg"):
                candidates.append(str(extra))
    elif binary == "claude":
        candidates.extend(
            [
                str(Path.home() / ".npm-global" / "bin" / "claude"),
                str(Path.home() / ".local" / "bin" / "claude"),
                "/opt/homebrew/bin/claude",
                "/usr/local/bin/claude",
            ]
        )
    return candidates


def _resolve_command(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    binary = str(cmd[0] or "")
    if not binary or "/" in binary:
        return cmd
    seen: set[str] = set()
    for candidate in _binary_candidates(binary):
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if Path(candidate).exists():
            return [candidate, *cmd[1:]]
    return cmd


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    path_entries = []
    for binary in ("wg", "claude"):
        for candidate in _binary_candidates(binary):
            parent = str(Path(candidate).parent)
            if parent and parent not in path_entries:
                path_entries.append(parent)
    current = env.get("PATH", "")
    if current:
        path_entries.extend(part for part in current.split(os.pathsep) if part)
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def _run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    resolved = _resolve_command(cmd)
    try:
        return subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=_subprocess_env(),
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(
            resolved,
            127,
            stdout="",
            stderr=str(exc),
        )


def get_task_details(project_dir: Path, task_id: str) -> dict | None:
    """Get full task details from workgraph."""
    result = _run_command(["wg", "show", task_id], cwd=project_dir)
    if result.returncode != 0:
        return None

    # Parse text output for description
    lines = result.stdout.strip().splitlines()
    title = ""
    description_lines = []
    in_description = False

    for line in lines:
        if line.startswith("Title:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"):
            in_description = True
            desc_part = line.split(":", 1)[1].strip()
            if desc_part:
                description_lines.append(desc_part)
        elif in_description:
            if line.startswith(("Status:", "Blocked", "Log:", "Dependencies:")):
                in_description = False
            else:
                description_lines.append(line)

    return {
        "id": task_id,
        "title": title,
        "description": "\n".join(description_lines).strip(),
    }


def _parse_ready_output(stdout: str) -> list[dict]:
    """Parse the text output of 'wg ready' into task dicts."""
    tasks: list[dict] = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("Ready tasks:"):
            continue
        parts = line.split(" - ", 1)
        if len(parts) == 2:
            task_id = parts[0].strip()
            title = parts[1].strip()
            tasks.append({"id": task_id, "title": title, "description": ""})
    return tasks


def get_ready_tasks(project_dir: Path) -> list[dict]:
    """Get ready tasks from workgraph with full details."""
    result = _run_command(["wg", "ready"], cwd=project_dir)
    if result.returncode != 0:
        return []

    basic_tasks = _parse_ready_output(result.stdout)
    detailed = []
    for task in basic_tasks:
        details = get_task_details(project_dir, task["id"])
        if details:
            detailed.append(details)
        else:
            detailed.append(task)
    return detailed


def build_worker_prompt(task: dict, project_dir: Path) -> str:
    """Build the prompt for a task worker."""
    return WORKER_PROMPT_TEMPLATE.format(
        project_dir=str(project_dir),
        task_id=task["id"],
        task_title=task.get("title", ""),
        task_description=task.get("description", ""),
    )


def get_wg_eval_scores(project_dir: Path, task_ids: set[str]) -> str:
    """Fetch workgraph agency evaluation scores for completed tasks.

    Checks .workgraph/output/{task_id} and wg show output for evaluation
    data from wg's 6-step evaluation cascade. Returns formatted evidence
    string or 'none' if no evaluation data exists.
    """
    eval_lines: list[str] = []

    for tid in sorted(task_ids):
        # Check output log for evaluation scores
        output_dir = project_dir / ".workgraph" / "output" / tid
        if output_dir.exists() and output_dir.is_file():
            try:
                content = output_dir.read_text(encoding="utf-8")
                # Look for evaluation score patterns in output
                for line in content.splitlines():
                    line_lower = line.strip().lower()
                    if any(
                        kw in line_lower
                        for kw in ("avg_score", "evaluation", "score:", "quality:")
                    ):
                        eval_lines.append(f"- {tid}: {line.strip()}")
                        break
            except OSError:
                pass

        # Also check wg show for evaluation log entries
        try:
            result = _run_command(["wg", "show", tid], cwd=project_dir)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    line_lower = line.strip().lower()
                    if "evaluat" in line_lower and (
                        "score" in line_lower or "grade" in line_lower
                    ):
                        eval_lines.append(f"- {tid}: {line.strip()}")
                        break
        except OSError:
            pass

    return "\n".join(eval_lines) if eval_lines else "none (no wg agency evaluations found)"


def build_review_prompt(run: "AutopilotRun") -> str:
    """Build the prompt for milestone review."""
    completed = "\n".join(f"- {tid}" for tid in sorted(run.completed_tasks)) or "none"
    problems = []
    for tid in sorted(run.failed_tasks):
        problems.append(f"- {tid} (failed)")
    for tid in sorted(run.escalated_tasks):
        ctx = run.workers.get(tid)
        findings = ctx.drift_findings[:3] if ctx else []
        problems.append(f"- {tid} (escalated): {'; '.join(findings)}")
    problem_str = "\n".join(problems) or "none"

    wg_eval = get_wg_eval_scores(run.config.project_dir, run.completed_tasks)

    return REVIEW_PROMPT_TEMPLATE.format(
        project_dir=str(run.config.project_dir),
        goal=run.config.goal,
        completed_tasks=completed,
        problem_tasks=problem_str,
        wg_eval_evidence=wg_eval,
    )


def launch_worker(
    scripts_dir: Path,
    worker_name: str,
    project_dir: Path,
) -> dict | None:
    """Launch a session-driver worker. Returns {session_id, tmux_name}."""
    launch_script = scripts_dir / "launch-worker.sh"
    if not launch_script.exists():
        return None

    result = subprocess.run(
        [str(launch_script), worker_name, str(project_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return None

    try:
        return json.loads(result.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return None


def _session_log_path(session_id: str) -> Path | None:
    meta_file = Path("/tmp/claude-workers") / f"{session_id}.meta"
    if not meta_file.exists():
        return None

    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None

    cwd_raw = str(meta.get("cwd") or "").strip()
    if not cwd_raw:
        return None

    cwd = Path(cwd_raw).expanduser()
    try:
        if cwd.exists():
            cwd = cwd.resolve()
    except OSError:
        pass

    encoded = str(cwd).replace("/", "-")
    return Path.home() / ".claude" / "projects" / encoded / f"{session_id}.jsonl"


def _assistant_text_messages(log_file: Path | None) -> list[str]:
    if log_file is None or not log_file.exists():
        return []

    messages: list[str] = []
    try:
        lines = log_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        if payload.get("type") != "assistant":
            continue
        blocks = (payload.get("message") or {}).get("content") or []
        texts = [
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
        ]
        if texts:
            messages.append("\n".join(texts))
    return messages


def _assistant_text_message_count(log_file: Path | None) -> int:
    return len(_assistant_text_messages(log_file))


def _last_assistant_text(log_file: Path | None) -> str:
    messages = _assistant_text_messages(log_file)
    return messages[-1] if messages else ""


def converse(
    scripts_dir: Path,
    worker_name: str,
    session_id: str,
    prompt: str,
    timeout: int = 1800,
) -> str:
    """Send a prompt to a worker and wait for response."""
    send_script = scripts_dir / "send-prompt.sh"
    wait_script = scripts_dir / "wait-for-event.sh"
    event_file = Path("/tmp/claude-workers") / f"{session_id}.events.jsonl"
    log_file = _session_log_path(session_id)
    before_count = _assistant_text_message_count(log_file)
    after_line = 0
    if event_file.exists():
        try:
            after_line = sum(1 for _ in event_file.open("r", encoding="utf-8"))
        except OSError:
            after_line = 0

    send_result = subprocess.run(
        [str(send_script), worker_name, prompt],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if send_result.returncode != 0:
        return (send_result.stderr or send_result.stdout).strip()

    wait_result = subprocess.run(
        [str(wait_script), session_id, "stop", str(timeout), "--after-line", str(after_line)],
        capture_output=True,
        text=True,
        timeout=timeout + 60,
    )
    if wait_result.returncode != 0:
        return (wait_result.stderr or wait_result.stdout).strip()

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _assistant_text_message_count(log_file) > before_count:
            response = _last_assistant_text(log_file)
            if response:
                return response
        time.sleep(0.1)

    return "Error: Timed out waiting for assistant response in session log"


def stop_worker(scripts_dir: Path, worker_name: str, session_id: str) -> None:
    """Stop a session-driver worker."""
    stop_script = scripts_dir / "stop-worker.sh"
    subprocess.run(
        [str(stop_script), worker_name, session_id],
        capture_output=True,
        text=True,
        timeout=30,
    )


def run_drift_check(project_dir: Path, task_id: str) -> dict:
    """Run drift check on a task and return structured results."""
    drifts_path = project_dir / ".workgraph" / "drifts"
    if not drifts_path.exists():
        return {"score": "unknown", "findings": [], "error": "drifts not installed"}

    result = subprocess.run(
        [
            str(drifts_path),
            "check",
            "--task",
            task_id,
            "--write-log",
            "--create-followups",
        ],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
        timeout=120,
    )

    output = result.stdout + result.stderr
    findings = []
    score = "green"

    for line in output.splitlines():
        line_lower = line.strip().lower()
        if "score:" in line_lower:
            if "red" in line_lower:
                score = "red"
            elif "yellow" in line_lower:
                score = "yellow"
        if "finding" in line_lower and line.strip():
            findings.append(line.strip())

    return {
        "score": score,
        "findings": findings,
        "exit_code": result.returncode,
        "raw": output,
    }


def should_escalate(worker: WorkerContext, threshold: int = 3) -> bool:
    """Check if a task should be escalated to human."""
    return worker.drift_fail_count >= threshold


def dispatch_task(
    task: dict,
    project_dir: Path,
    scripts_dir: Path | None,
    run: AutopilotRun,
) -> WorkerContext:
    """Dispatch a worker for a task. Returns WorkerContext."""
    worker_name = f"ap-{task['id']}"
    ctx = WorkerContext(
        task_id=task["id"],
        task_title=task.get("title", ""),
        worker_name=worker_name,
        started_at=time.time(),
        status="running",
    )

    # Claim the task
    _run_command(["wg", "claim", task["id"]], cwd=project_dir)

    if scripts_dir is None:
        # Fallback: direct CLI execution (no session-driver)
        prompt = build_worker_prompt(task, project_dir)
        result = _run_command(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--no-session-persistence",
                "-p",
                prompt,
            ],
            cwd=project_dir,
            timeout=run.config.worker_timeout + 60,
        )
        ctx.response = result.stdout
        ctx.status = "completed" if result.returncode == 0 else "failed"
    else:
        # Session-driver path
        worker_info = launch_worker(scripts_dir, worker_name, project_dir)
        if worker_info is None:
            ctx.response = "worker launch failed"
            ctx.status = "failed"
            run.workers[task["id"]] = ctx
            return ctx

        ctx.session_id = worker_info.get("session_id")
        prompt = build_worker_prompt(task, project_dir)
        ctx.response = converse(
            scripts_dir,
            worker_name,
            ctx.session_id,
            prompt,
            run.config.worker_timeout,
        )
        stop_worker(scripts_dir, worker_name, ctx.session_id)
        if not ctx.response:
            ctx.response = "worker conversation returned no output"
        ctx.status = (
            "completed"
            if ctx.response and not ctx.response.lstrip().startswith("Error:")
            else "failed"
        )

    run.workers[task["id"]] = ctx
    return ctx


@dataclass
class _PeerAssignment:
    peer_name: str
    task_id: str
    prompt: str = ""
    status: str = "pending"  # pending, dispatched, completed, failed


def _format_task_prompt(task: dict) -> str:
    """Format a task dict into a worker session prompt including TDD protocol."""
    task_id = task.get("id", "")
    title = task.get("title", "")
    description = task.get("description", "")
    return (
        f"Task ID: {task_id}\n"
        f"Title: {title}\n\n"
        f"{description}\n\n"
        "## Protocol\n"
        "Follow TDD strictly: write failing tests first, verify RED, implement minimal "
        "code to pass, verify GREEN, then run the full suite.\n"
        "When complete, run: wg done\n"
        "Before completing, run: drifts check\n"
    )


def _plan_peer_dispatch(
    peer_registry: object,
    ready_tasks: list[dict],
) -> list[_PeerAssignment]:
    """Scan task descriptions for @peer:<name> annotations and plan dispatch."""
    peers = {p.name for p in peer_registry.peers()}
    if not peers:
        return []

    assignments: list[_PeerAssignment] = []
    pattern = re.compile(r"@peer:(\S+)")

    for task in ready_tasks:
        desc = task.get("description", "")
        match = pattern.search(desc)
        if match:
            peer_name = match.group(1)
            if peer_name in peers:
                assignments.append(_PeerAssignment(
                    peer_name=peer_name,
                    task_id=task["id"],
                    prompt=_format_task_prompt(task),
                ))
    return assignments


def _dispatch_to_peer(
    project_dir: Path,
    peer_name: str,
    task: dict,
    peer_registry: object,
) -> str | None:
    """Dispatch a task to a peer repo via IPC AddTask.

    Returns the remote task_id on success, None on failure.
    """
    from driftdriver.wg_ipc import IpcError, add_task

    socket_path = peer_registry.socket(peer_name)
    if not socket_path:
        return None

    try:
        remote_id = add_task(
            socket_path,
            title=task.get("title", ""),
            description=task.get("description", ""),
            tags=["federation", f"origin:{project_dir.name}"],
            origin=f"peer:{project_dir.name}",
        )
        return remote_id if remote_id else None
    except IpcError:
        return None


def _init_peer_registry(project_dir: Path) -> object | None:
    """Initialize peer registry if peers are configured."""
    from driftdriver.peer_registry import PeerRegistry

    registry = PeerRegistry(project_dir)
    peers = registry.peers()
    if peers:
        return registry
    return None


def _run_peer_dispatch(
    run: AutopilotRun,
    actionable: list[dict],
    peer_registry: object,
) -> list[str]:
    """Dispatch @peer:-annotated tasks to remote repos. Returns dispatched task IDs."""
    project_dir = run.config.project_dir
    assignments = _plan_peer_dispatch(peer_registry, actionable)
    dispatched: list[str] = []

    for assignment in assignments:
        task = next((t for t in actionable if t["id"] == assignment.task_id), None)
        if not task:
            continue

        if run.config.dry_run:
            print(f"[dry-run] Would dispatch to peer {assignment.peer_name}: {task['id']}")
            dispatched.append(task["id"])
            continue

        print(f"[autopilot] Dispatching to peer {assignment.peer_name}: {task['id']}")
        remote_id = _dispatch_to_peer(project_dir, assignment.peer_name, task, peer_registry)
        if remote_id:
            print(f"[autopilot] Peer {assignment.peer_name} accepted: {task['id']} → {remote_id}")
            ctx = WorkerContext(
                task_id=task["id"],
                task_title=task.get("title", ""),
                worker_name=f"peer-{assignment.peer_name}-{task['id']}",
                status="completed",
            )
            run.workers[task["id"]] = ctx
            run.completed_tasks.add(task["id"])
            dispatched.append(task["id"])
        else:
            print(f"[autopilot] Peer dispatch failed for {task['id']}, will try locally")

    return dispatched


def _run_health_check(run: AutopilotRun) -> None:
    """Check liveness of running workers and triage dead ones."""
    from driftdriver.worker_monitor import detect_dead_workers, triage_dead_worker

    # Build map of running workers: session_id → task_id
    running = {}
    for task_id, ctx in run.workers.items():
        if ctx.status == "running" and ctx.session_id:
            running[ctx.session_id] = task_id

    if not running:
        return

    dead_sessions = detect_dead_workers(running)
    for session_id in dead_sessions:
        task_id = running[session_id]
        ctx = run.workers[task_id]
        action = triage_dead_worker(
            {
                "session_id": session_id,
                "task_id": task_id,
                "status": ctx.status,
                "drift_fail_count": ctx.drift_fail_count,
            },
            strategy="conservative",
        )
        print(f"[autopilot] Dead worker detected: {task_id} ({session_id}) → {action.action}: {action.reason}")

        if action.action == "escalate":
            ctx.status = "escalated"
            run.escalated_tasks.add(task_id)
        elif action.action == "abandon":
            ctx.status = "failed"
            run.failed_tasks.add(task_id)
        # "restart" is not handled here — would need session-driver restart logic


def run_autopilot_loop(run: AutopilotRun) -> AutopilotRun:
    """Main autopilot loop: dispatch → drift check → loop until done."""
    project_dir = run.config.project_dir
    scripts_dir = discover_session_driver()
    run.started_at = time.time()

    # Initialize peer registry if federation is enabled
    peer_registry = None
    if not run.config.no_peer_dispatch:
        peer_registry = _init_peer_registry(project_dir)
        if peer_registry:
            print("[autopilot] Peer federation enabled")

    while True:
        run.loop_count += 1

        # Health check running workers before dispatching new ones
        _run_health_check(run)

        ready = get_ready_tasks(project_dir)

        # Filter out already-processed tasks
        actionable = [
            t
            for t in ready
            if t["id"] not in run.completed_tasks
            and t["id"] not in run.failed_tasks
            and t["id"] not in run.escalated_tasks
        ]

        if not actionable:
            break

        # Phase 1: Dispatch @peer:-annotated tasks to remote repos
        peer_dispatched: set[str] = set()
        if peer_registry:
            dispatched_ids = _run_peer_dispatch(run, actionable, peer_registry)
            peer_dispatched = set(dispatched_ids)

        # Phase 2: Dispatch remaining tasks locally
        local_tasks = [t for t in actionable if t["id"] not in peer_dispatched]
        batch = local_tasks[: run.config.max_parallel]

        for task in batch:
            if run.config.dry_run:
                print(f"[dry-run] Would dispatch: {task['id']} — {task.get('title')}")
                run.completed_tasks.add(task["id"])
                continue

            print(f"[autopilot] Dispatching: {task['id']} — {task.get('title')}")
            ctx = dispatch_task(task, project_dir, scripts_dir, run)

            if ctx.status == "failed":
                run.failed_tasks.add(task["id"])
                print(f"[autopilot] Worker failed: {task['id']}")
                continue

            # Post-task drift check
            drift_result = run_drift_check(project_dir, task["id"])
            ctx.drift_findings = drift_result.get("findings", [])

            if drift_result["score"] == "red":
                ctx.drift_fail_count += 1
                if should_escalate(ctx, run.config.drift_failure_threshold):
                    ctx.status = "escalated"
                    run.escalated_tasks.add(task["id"])
                    print(
                        f"[autopilot] Escalating: {task['id']} "
                        f"(drift failures: {ctx.drift_fail_count})"
                    )
                else:
                    print(
                        f"[autopilot] Drift findings on {task['id']}, "
                        "follow-ups created"
                    )
                    run.completed_tasks.add(task["id"])
            else:
                run.completed_tasks.add(task["id"])
                print(f"[autopilot] Completed: {task['id']}")

    return run


def run_milestone_review(
    run: AutopilotRun,
    scripts_dir: Path | None = None,
) -> str:
    """Dispatch a reviewer worker to verify milestone completion."""
    project_dir = run.config.project_dir
    prompt = build_review_prompt(run)

    if run.config.dry_run:
        print("[dry-run] Would dispatch milestone review")
        return "[dry-run] Review skipped"

    print("[autopilot] Running milestone review...")

    if scripts_dir:
        worker_name = "ap-reviewer"
        worker_info = launch_worker(scripts_dir, worker_name, project_dir)
        if worker_info:
            session_id = worker_info.get("session_id")
            response = converse(
                scripts_dir, worker_name, session_id, prompt, 600,
            )
            stop_worker(scripts_dir, worker_name, session_id)
            return response

    # Fallback: direct CLI
    result = _run_command(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "-p",
            prompt,
        ],
        cwd=project_dir,
        timeout=660,
    )
    return result.stdout


def decompose_goal(
    goal: str,
    project_dir: Path,
    scripts_dir: Path | None = None,
) -> str:
    """Use a session-driver worker to decompose a goal into workgraph tasks."""
    prompt = build_decompose_prompt(goal, project_dir)

    if scripts_dir:
        worker_name = "ap-decompose"
        worker_info = launch_worker(scripts_dir, worker_name, project_dir)
        if worker_info:
            session_id = worker_info.get("session_id")
            response = converse(scripts_dir, worker_name, session_id, prompt, 600)
            stop_worker(scripts_dir, worker_name, session_id)
            return response

    # Fallback: direct CLI
    result = _run_command(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "-p",
            prompt,
        ],
        cwd=project_dir,
        timeout=660,
    )
    return result.stdout


def generate_report(run: AutopilotRun) -> str:
    """Generate a summary report of the autopilot run."""
    elapsed = time.time() - run.started_at if run.started_at else 0
    lines = [
        "# Autopilot Run Report",
        "",
        f"- **Goal**: {run.config.goal}",
        f"- **Duration**: {elapsed:.0f}s",
        f"- **Loops**: {run.loop_count}",
        f"- **Completed**: {len(run.completed_tasks)}",
        f"- **Failed**: {len(run.failed_tasks)}",
        f"- **Escalated**: {len(run.escalated_tasks)}",
        "",
    ]

    if run.completed_tasks:
        lines.append("## Completed Tasks")
        for tid in sorted(run.completed_tasks):
            lines.append(f"- {tid}")
        lines.append("")

    if run.failed_tasks:
        lines.append("## Failed Tasks")
        for tid in sorted(run.failed_tasks):
            ctx = run.workers.get(tid)
            reason = (
                ctx.response[:200] if ctx else "unknown"
            )
            lines.append(f"- {tid}: {reason}")
        lines.append("")

    if run.escalated_tasks:
        lines.append("## Escalated (Human Decision Needed)")
        for tid in sorted(run.escalated_tasks):
            ctx = run.workers.get(tid)
            findings = ctx.drift_findings if ctx else []
            lines.append(f"- {tid}")
            for f in findings[:5]:
                lines.append(f"  - {f}")
        lines.append("")

    return "\n".join(lines)
