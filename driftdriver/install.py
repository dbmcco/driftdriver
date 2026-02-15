from __future__ import annotations

import re
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

SPEEDRIFT_MARKER = "## Speedrift Protocol"
UXRIFT_MARKER = "## uxrift Protocol"
SPECRIFT_MARKER = "## specrift Protocol"


@dataclass(frozen=True)
class InstallResult:
    wrote_rifts: bool
    wrote_driver: bool
    wrote_speedrift: bool
    wrote_specrift: bool
    wrote_uxrift: bool
    updated_gitignore: bool
    created_executor: bool
    patched_executors: list[str]
    ensured_contracts: bool


def _ensure_line_in_file(path: Path, line: str) -> bool:
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    lines = existing.splitlines()
    if any(l.strip() == line for l in lines):
        return False
    new = existing.rstrip("\n")
    if new:
        new += "\n"
    new += line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return True


def ensure_speedrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".speedrift/")


def ensure_specrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".specrift/")


def ensure_uxrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".uxrift/")


def write_tool_wrapper(wg_dir: Path, *, tool_name: str, tool_bin: Path) -> bool:
    """
    Writes .workgraph/<tool_name> wrapper pointing at a tool checkout.
    Returns True if file changed.
    """

    wrapper = wg_dir / tool_name
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f'exec "{tool_bin}" "$@"\n'
    )

    existing = wrapper.read_text(encoding="utf-8") if wrapper.exists() else None
    changed = existing != content
    if changed:
        wrapper.write_text(content, encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return changed


def write_driver_wrapper(wg_dir: Path, *, driver_bin: Path) -> bool:
    return write_tool_wrapper(wg_dir, tool_name="driftdriver", tool_bin=driver_bin)


def write_speedrift_wrapper(wg_dir: Path, *, speedrift_bin: Path) -> bool:
    return write_tool_wrapper(wg_dir, tool_name="speedrift", tool_bin=speedrift_bin)


def write_specrift_wrapper(wg_dir: Path, *, specrift_bin: Path) -> bool:
    return write_tool_wrapper(wg_dir, tool_name="specrift", tool_bin=specrift_bin)


def write_uxrift_wrapper(wg_dir: Path, *, uxrift_bin: Path) -> bool:
    return write_tool_wrapper(wg_dir, tool_name="uxrift", tool_bin=uxrift_bin)


def write_rifts_wrapper(wg_dir: Path) -> bool:
    """
    Writes .workgraph/rifts wrapper that delegates to .workgraph/driftdriver.
    """

    wrapper = wg_dir / "rifts"
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "WG_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        "exec \"$WG_DIR/driftdriver\" \"$@\"\n"
    )

    existing = wrapper.read_text(encoding="utf-8") if wrapper.exists() else None
    changed = existing != content
    if changed:
        wrapper.write_text(content, encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return changed


def _default_claude_executor_text(*, project_dir: Path, include_uxrift: bool) -> str:
    uxrift = ""
    if include_uxrift:
        uxrift = (
            "\n"
            f"{UXRIFT_MARKER}\n"
            "- If this task includes a `uxrift` block (in the description), run:\n"
            f"  ./.workgraph/uxrift wg check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Or run the unified check (runs uxrift when a spec is present):\n"
            f"  ./.workgraph/rifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- If it fails due to missing URL, set `url = \"...\"` in the `uxrift` block or pass --url.\n"
        )

    return f"""[executor]
type = "claude"
command = "claude"
args = ["--print", "--dangerously-skip-permissions", "--no-session-persistence"]

[executor.prompt_template]
template = \"\"\"
You are working in: {project_dir}

Task: {{{{task_id}}}} - {{{{task_title}}}}

Description:
{{{{task_description}}}}

Context from dependencies:
{{{{task_context}}}}

{SPEEDRIFT_MARKER}
- Treat the `wg-contract` block (in the task description) as binding.
- At start and just before completion, run:
  ./.workgraph/rifts check --task {{{{task_id}}}} --write-log --create-followups
- If you need to change scope, update touch globs:
  ./.workgraph/speedrift contract set-touch --task {{{{task_id}}}} <globs...>
- If `hardening_in_core` is flagged, avoid adding guardrails in the core task; do/complete the `harden:` follow-up task instead.
{uxrift}

## Workgraph Rules
- Stay focused on this task.
- Log progress: wg log {{{{task_id}}}} \"message\"
- When complete: wg done {{{{task_id}}}}
- If blocked: wg fail {{{{task_id}}}} --reason \"description\"
\"\"\"
"""


_TEMPLATE_START_RE = re.compile(r'(?P<prefix>\btemplate\s*=\s*"""\r?\n)', re.MULTILINE)


def _inject_speedrift_into_template(body: str) -> str | None:
    if SPEEDRIFT_MARKER in body:
        old = "  ./.workgraph/speedrift check --task {{task_id}} --write-log --create-followups"
        new = "  ./.workgraph/rifts check --task {{task_id}} --write-log --create-followups"
        upgraded = body.replace(old, new)
        if upgraded != body:
            return upgraded
        return None

    m = _TEMPLATE_START_RE.search(body)
    if not m:
        return None

    start = m.end("prefix")
    end = body.find('\"\"\"', start)
    if end == -1:
        return None

    insert = (
        "\n"
        f"{SPEEDRIFT_MARKER}\n"
        "- Treat the `wg-contract` block (in the task description) as binding.\n"
        "- At start and just before completion, run:\n"
        "  ./.workgraph/rifts check --task {{task_id}} --write-log --create-followups\n"
        "- If you need to change scope, update touch globs:\n"
        "  ./.workgraph/speedrift contract set-touch --task {{task_id}} <globs...>\n"
        "- If `hardening_in_core` is flagged, avoid adding guardrails in the core task; do/complete the `harden:` follow-up task instead.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def _inject_uxrift_into_template(body: str) -> str | None:
    if UXRIFT_MARKER in body:
        return None

    m = _TEMPLATE_START_RE.search(body)
    if not m:
        return None

    start = m.end("prefix")
    end = body.find('\"\"\"', start)
    if end == -1:
        return None

    insert = (
        "\n"
        f"{UXRIFT_MARKER}\n"
        "- If this task includes a `uxrift` block (in the description), run:\n"
        "  ./.workgraph/uxrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs uxrift when a spec is present):\n"
        "  ./.workgraph/rifts check --task {{task_id}} --write-log --create-followups\n"
        "- If it fails due to missing URL, set `url = \"...\"` in the `uxrift` block or pass --url.\n"
        "- Artifacts live under `.workgraph/.uxrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def ensure_executor_guidance(wg_dir: Path, *, include_uxrift: bool) -> tuple[bool, list[str]]:
    executors_dir = wg_dir / "executors"
    executors_dir.mkdir(parents=True, exist_ok=True)

    created = False
    claude_path = executors_dir / "claude.toml"
    if not claude_path.exists():
        claude_path.write_text(
            _default_claude_executor_text(project_dir=wg_dir.parent, include_uxrift=include_uxrift),
            encoding="utf-8",
        )
        created = True

    patched: list[str] = []
    for p in sorted(executors_dir.glob("*.toml")):
        text = p.read_text(encoding="utf-8")
        cur = text
        changed = False

        new_text = _inject_speedrift_into_template(cur)
        if new_text is not None:
            cur = new_text
            changed = True

        if include_uxrift:
            new_text = _inject_uxrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if not changed:
            continue

        p.write_text(cur, encoding="utf-8")
        patched.append(str(p))

    return (created, patched)


def resolve_bin(
    *,
    explicit: Path | None,
    env_var: str | None,
    which_name: str | None,
    candidates: list[Path],
) -> Path | None:
    def _ok(p: Path | None) -> Path | None:
        if not p:
            return None
        if p.exists() and p.is_file() and os.access(p, os.X_OK):
            return p
        return None

    if explicit:
        return _ok(explicit)

    if env_var:
        env_val = (Path(os.environ[env_var]) if env_var in os.environ else None)
        out = _ok(env_val)
        if out:
            return out

    if which_name:
        w = shutil.which(which_name)
        if w:
            out = _ok(Path(w))
            if out:
                return out

    for c in candidates:
        out = _ok(c)
        if out:
            return out

    return None
