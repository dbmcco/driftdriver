# ABOUTME: Project autopilot — closes the north star loop
# ABOUTME: Goal decomposition → worker dispatch → drift checks → loop until done

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


SESSION_DRIVER_GLOB = (
    "~/.claude/plugins/cache/superpowers-marketplace/"
    "claude-session-driver/*/scripts"
)

DECOMPOSE_PROMPT_TEMPLATE = """\
You are a project planner. Given a high-level goal, decompose it into
concrete workgraph tasks with dependencies.

## Goal
{goal}

## Project Directory
{project_dir}

## Instructions
1. Research the goal by reading relevant files in the project.
2. Create workgraph tasks using `wg add`. Each task must have:
   - A short `--id` (kebab-case, e.g., `feat-auth-login`)
   - A clear title
   - A `-d` description covering: what to do, which files to touch, acceptance criteria
   - `--blocked-by` dependencies where appropriate
3. Keep tasks small — each should be completable in one focused session.
4. After creating all tasks, run:
   ./.workgraph/coredrift ensure-contracts --apply
5. Print a summary of the tasks you created (id + title + deps).
6. Do NOT implement anything. Planning only.
"""

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


def get_task_details(project_dir: Path, task_id: str) -> dict | None:
    """Get full task details from workgraph."""
    result = subprocess.run(
        ["wg", "show", task_id],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
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


def get_ready_tasks(project_dir: Path) -> list[dict]:
    """Get ready tasks from workgraph with full details."""
    from .pm_coordination import parse_ready_output

    result = subprocess.run(
        ["wg", "ready"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        return []

    basic_tasks = parse_ready_output(result.stdout)
    detailed = []
    for task in basic_tasks:
        details = get_task_details(project_dir, task["id"])
        if details:
            detailed.append(details)
        else:
            detailed.append(task)
    return detailed


def build_decompose_prompt(goal: str, project_dir: Path) -> str:
    """Build the prompt for goal decomposition."""
    return DECOMPOSE_PROMPT_TEMPLATE.format(
        goal=goal,
        project_dir=str(project_dir),
    )


def build_worker_prompt(task: dict, project_dir: Path) -> str:
    """Build the prompt for a task worker."""
    return WORKER_PROMPT_TEMPLATE.format(
        project_dir=str(project_dir),
        task_id=task["id"],
        task_title=task.get("title", ""),
        task_description=task.get("description", ""),
    )


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

    return REVIEW_PROMPT_TEMPLATE.format(
        project_dir=str(run.config.project_dir),
        goal=run.config.goal,
        completed_tasks=completed,
        problem_tasks=problem_str,
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


def converse(
    scripts_dir: Path,
    worker_name: str,
    session_id: str,
    prompt: str,
    timeout: int = 1800,
) -> str:
    """Send a prompt to a worker and wait for response."""
    converse_script = scripts_dir / "converse.sh"

    result = subprocess.run(
        [str(converse_script), worker_name, session_id, prompt, str(timeout)],
        capture_output=True,
        text=True,
        timeout=timeout + 60,
    )
    return result.stdout.strip()


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
    subprocess.run(
        ["wg", "claim", task["id"]],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )

    if scripts_dir is None:
        # Fallback: direct CLI execution (no session-driver)
        prompt = build_worker_prompt(task, project_dir)
        result = subprocess.run(
            [
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                "--no-session-persistence",
                "-p",
                prompt,
            ],
            capture_output=True,
            text=True,
            cwd=str(project_dir),
            timeout=run.config.worker_timeout + 60,
        )
        ctx.response = result.stdout
        ctx.status = "completed" if result.returncode == 0 else "failed"
    else:
        # Session-driver path
        worker_info = launch_worker(scripts_dir, worker_name, project_dir)
        if worker_info is None:
            ctx.status = "failed"
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
        ctx.status = "completed"

    run.workers[task["id"]] = ctx
    return ctx


def run_autopilot_loop(run: AutopilotRun) -> AutopilotRun:
    """Main autopilot loop: dispatch → drift check → loop until done."""
    project_dir = run.config.project_dir
    scripts_dir = discover_session_driver()
    run.started_at = time.time()

    while True:
        run.loop_count += 1
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

        # Dispatch up to max_parallel
        batch = actionable[: run.config.max_parallel]

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
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "-p",
            prompt,
        ],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
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
    result = subprocess.run(
        [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--no-session-persistence",
            "-p",
            prompt,
        ],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
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
