from __future__ import annotations

import re
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

SPEEDRIFT_MARKER = "## Speedrift Protocol"
UXDRIFT_MARKER = "## uxdrift Protocol"
SPECDRIFT_MARKER = "## specdrift Protocol"
SUPERPOWERS_MARKER = "## Superpowers Protocol"
MODEL_MEDIATED_MARKER = "## Model-Mediated Protocol"


@dataclass(frozen=True)
class InstallResult:
    wrote_drifts: bool
    wrote_driver: bool
    wrote_speedrift: bool
    wrote_specdrift: bool
    wrote_datadrift: bool
    wrote_depsdrift: bool
    wrote_uxdrift: bool
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


def ensure_specdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".specdrift/")

def ensure_datadrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".datadrift/")

def ensure_depsdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".depsdrift/")


def ensure_uxdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".uxdrift/")


def _portable_wrapper_content(tool_name: str) -> str:
    """
    Commit-safe wrapper that resolves the tool from PATH at runtime.

    We explicitly skip WG_DIR in PATH to avoid recursion if `.workgraph` is added
    to PATH.
    """

    tool = str(tool_name)
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "WG_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        f"TOOL=\"{tool}\"\n"
        "FOUND=\"\"\n"
        "IFS=':' read -r -a PARTS <<< \"${PATH:-}\"\n"
        "for p in \"${PARTS[@]}\"; do\n"
        "  [[ -z \"$p\" ]] && continue\n"
        "  if [[ \"$p\" == \"$WG_DIR\" ]]; then\n"
        "    continue\n"
        "  fi\n"
        "  if [[ -x \"$p/$TOOL\" ]]; then\n"
        "    FOUND=\"$p/$TOOL\"\n"
        "    break\n"
        "  fi\n"
        "done\n"
        "if [[ -z \"$FOUND\" ]]; then\n"
        "  echo \"error: $TOOL not found on PATH (portable wrapper)\" >&2\n"
        "  exit 2\n"
        "fi\n"
        "exec \"$FOUND\" \"$@\"\n"
    )


def write_tool_wrapper(
    wg_dir: Path,
    *,
    tool_name: str,
    tool_bin: Path,
    wrapper_mode: str = "pinned",
) -> bool:
    """
    Writes .workgraph/<tool_name> wrapper.
    Returns True if file changed.
    """

    wrapper = wg_dir / tool_name
    mode = str(wrapper_mode or "pinned").strip().lower()
    if mode == "portable":
        content = _portable_wrapper_content(str(tool_name))
    else:
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


def write_driver_wrapper(wg_dir: Path, *, driver_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="driftdriver", tool_bin=driver_bin, wrapper_mode=wrapper_mode)


def write_speedrift_wrapper(wg_dir: Path, *, speedrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="speedrift", tool_bin=speedrift_bin, wrapper_mode=wrapper_mode)


def write_specdrift_wrapper(wg_dir: Path, *, specdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="specdrift", tool_bin=specdrift_bin, wrapper_mode=wrapper_mode)

def write_datadrift_wrapper(wg_dir: Path, *, datadrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="datadrift", tool_bin=datadrift_bin, wrapper_mode=wrapper_mode)

def write_depsdrift_wrapper(wg_dir: Path, *, depsdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="depsdrift", tool_bin=depsdrift_bin, wrapper_mode=wrapper_mode)


def write_uxdrift_wrapper(wg_dir: Path, *, uxdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="uxdrift", tool_bin=uxdrift_bin, wrapper_mode=wrapper_mode)


def write_drifts_wrapper(wg_dir: Path) -> bool:
    """
    Writes .workgraph/drifts wrapper that delegates to .workgraph/driftdriver.
    """

    wrapper = wg_dir / "drifts"
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


def _default_claude_executor_text(*, project_dir: Path, include_uxdrift: bool) -> str:
    uxdrift = ""
    if include_uxdrift:
        uxdrift = (
            "\n"
            f"{UXDRIFT_MARKER}\n"
            "- If this task includes a `uxdrift` block (in the description), run:\n"
            f"  ./.workgraph/uxdrift wg check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Or run the unified check (runs uxdrift when a spec is present):\n"
            f"  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- If it fails due to missing URL, set `url = \"...\"` in the `uxdrift` block or pass --url.\n"
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
  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups
- If you need to change scope, update touch globs:
  ./.workgraph/speedrift contract set-touch --task {{{{task_id}}}} <globs...>
- If `hardening_in_core` is flagged, avoid adding guardrails in the core task; do/complete the `harden:` follow-up task instead.

{SUPERPOWERS_MARKER}
- If Superpowers-style skills are available in this environment, use:
  /brainstorming before code
  /test-driven-development for behavior changes
  /verification-before-completion before `wg done`
- If not available, follow the same phases explicitly.

{MODEL_MEDIATED_MARKER}
- Separate pipes vs decisions (facts/execution vs judgment).
- If a Model-Mediated Architecture skill is available, apply it (model decides; code executes).
- Log key decisions/deviations in `wg log`, and prefer follow-up tasks over bloating the current task.
{uxdrift}

## Workgraph Rules
- Stay focused on this task.
- Log progress: wg log {{{{task_id}}}} \"message\"
- When complete: wg done {{{{task_id}}}}
- If blocked: wg fail {{{{task_id}}}} --reason \"description\"
\"\"\"
"""


_TEMPLATE_START_RE = re.compile(r'(?P<prefix>\btemplate\s*=\s*"""\r?\n)', re.MULTILINE)


def _inject_speedrift_into_template(body: str) -> str | None:
    changed = False
    cur = body

    old = "  ./.workgraph/speedrift check --task {{task_id}} --write-log --create-followups"
    new = "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups"
    if old in cur:
        cur = cur.replace(old, new)
        changed = True

    m = _TEMPLATE_START_RE.search(cur)
    if not m:
        return cur if changed else None

    start = m.end("prefix")
    end = cur.find('\"\"\"', start)
    if end == -1:
        return cur if changed else None

    if SPEEDRIFT_MARKER in cur:
        inserts: list[str] = []
        if SUPERPOWERS_MARKER not in cur:
            inserts.append(
                "\n"
                f"{SUPERPOWERS_MARKER}\n"
                "- If Superpowers-style skills are available in this environment, use:\n"
                "  /brainstorming before code\n"
                "  /test-driven-development for behavior changes\n"
                "  /verification-before-completion before `wg done`\n"
                "- If not available, follow the same phases explicitly.\n"
            )
        if MODEL_MEDIATED_MARKER not in cur:
            inserts.append(
                "\n"
                f"{MODEL_MEDIATED_MARKER}\n"
                "- Separate pipes vs decisions (facts/execution vs judgment).\n"
                "- If a Model-Mediated Architecture skill is available, apply it (model decides; code executes).\n"
                "- Log key decisions/deviations in `wg log`, and prefer follow-up tasks over bloating the current task.\n"
            )

        if inserts:
            cur = cur[:end].rstrip("\n") + "\n" + "\n".join(i.strip("\n") for i in inserts) + "\n" + cur[end:]
            changed = True

        return cur if changed else None

    insert = (
        "\n"
        f"{SPEEDRIFT_MARKER}\n"
        "- Treat the `wg-contract` block (in the task description) as binding.\n"
        "- At start and just before completion, run:\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- If you need to change scope, update touch globs:\n"
        "  ./.workgraph/speedrift contract set-touch --task {{task_id}} <globs...>\n"
        "- If `hardening_in_core` is flagged, avoid adding guardrails in the core task; do/complete the `harden:` follow-up task instead.\n"
        "\n"
        f"{SUPERPOWERS_MARKER}\n"
        "- If Superpowers-style skills are available in this environment, use:\n"
        "  /brainstorming before code\n"
        "  /test-driven-development for behavior changes\n"
        "  /verification-before-completion before `wg done`\n"
        "- If not available, follow the same phases explicitly.\n"
        "\n"
        f"{MODEL_MEDIATED_MARKER}\n"
        "- Separate pipes vs decisions (facts/execution vs judgment).\n"
        "- If a Model-Mediated Architecture skill is available, apply it (model decides; code executes).\n"
        "- Log key decisions/deviations in `wg log`, and prefer follow-up tasks over bloating the current task.\n"
    )

    return cur[:end].rstrip("\n") + "\n" + insert + "\n" + cur[end:]


def _inject_uxdrift_into_template(body: str) -> str | None:
    if UXDRIFT_MARKER in body:
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
        f"{UXDRIFT_MARKER}\n"
        "- If this task includes a `uxdrift` block (in the description), run:\n"
        "  ./.workgraph/uxdrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs uxdrift when a spec is present):\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- If it fails due to missing URL, set `url = \"...\"` in the `uxdrift` block or pass --url.\n"
        "- Artifacts live under `.workgraph/.uxdrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def ensure_executor_guidance(wg_dir: Path, *, include_uxdrift: bool) -> tuple[bool, list[str]]:
    executors_dir = wg_dir / "executors"
    executors_dir.mkdir(parents=True, exist_ok=True)

    created = False
    claude_path = executors_dir / "claude.toml"
    if not claude_path.exists():
        claude_path.write_text(
            _default_claude_executor_text(project_dir=wg_dir.parent, include_uxdrift=include_uxdrift),
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

        if include_uxdrift:
            new_text = _inject_uxdrift_into_template(cur)
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
