from __future__ import annotations

import re
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

CODEX_ADAPTER_MARKER = "## Driftdriver Integration Protocol"
COREDRIFT_MARKER = "## Coredrift Protocol"
ARCHDRIFT_MARKER = "## archdrift Protocol"
UXDRIFT_MARKER = "## uxdrift Protocol"
THERAPYDRIFT_MARKER = "## therapydrift Protocol"
FIXDRIFT_MARKER = "## fixdrift Protocol"
YAGNIDRIFT_MARKER = "## yagnidrift Protocol"
REDRIFT_MARKER = "## redrift Protocol"
SPECDRIFT_MARKER = "## specdrift Protocol"
SUPERPOWERS_MARKER = "## Superpowers Protocol"
MODEL_MEDIATED_MARKER = "## Model-Mediated Protocol"


@dataclass(frozen=True)
class InstallResult:
    wrote_drifts: bool
    wrote_driver: bool
    wrote_coredrift: bool
    wrote_specdrift: bool
    wrote_datadrift: bool
    wrote_archdrift: bool
    wrote_depsdrift: bool
    wrote_uxdrift: bool
    wrote_therapydrift: bool
    wrote_fixdrift: bool
    wrote_yagnidrift: bool
    wrote_redrift: bool
    wrote_amplifier_executor: bool
    wrote_amplifier_runner: bool
    wrote_amplifier_autostart_hook: bool
    wrote_amplifier_autostart_hooks_json: bool
    wrote_policy: bool
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


def ensure_coredrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".coredrift/")


def ensure_specdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".specdrift/")

def ensure_datadrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".datadrift/")

def ensure_archdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".archdrift/")

def ensure_depsdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".depsdrift/")


def ensure_uxdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".uxdrift/")

def ensure_therapydrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".therapydrift/")

def ensure_fixdrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".fixdrift/")

def ensure_yagnidrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".yagnidrift/")

def ensure_redrift_gitignore(wg_dir: Path) -> bool:
    return _ensure_line_in_file(wg_dir / ".gitignore", ".redrift/")


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


def write_coredrift_wrapper(wg_dir: Path, *, coredrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="coredrift", tool_bin=coredrift_bin, wrapper_mode=wrapper_mode)


def write_specdrift_wrapper(wg_dir: Path, *, specdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="specdrift", tool_bin=specdrift_bin, wrapper_mode=wrapper_mode)

def write_datadrift_wrapper(wg_dir: Path, *, datadrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="datadrift", tool_bin=datadrift_bin, wrapper_mode=wrapper_mode)

def write_archdrift_wrapper(wg_dir: Path, *, archdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="archdrift", tool_bin=archdrift_bin, wrapper_mode=wrapper_mode)

def write_depsdrift_wrapper(wg_dir: Path, *, depsdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="depsdrift", tool_bin=depsdrift_bin, wrapper_mode=wrapper_mode)


def write_uxdrift_wrapper(wg_dir: Path, *, uxdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="uxdrift", tool_bin=uxdrift_bin, wrapper_mode=wrapper_mode)

def write_therapydrift_wrapper(wg_dir: Path, *, therapydrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="therapydrift", tool_bin=therapydrift_bin, wrapper_mode=wrapper_mode)

def write_fixdrift_wrapper(wg_dir: Path, *, fixdrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="fixdrift", tool_bin=fixdrift_bin, wrapper_mode=wrapper_mode)

def write_yagnidrift_wrapper(wg_dir: Path, *, yagnidrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="yagnidrift", tool_bin=yagnidrift_bin, wrapper_mode=wrapper_mode)

def write_redrift_wrapper(wg_dir: Path, *, redrift_bin: Path, wrapper_mode: str = "pinned") -> bool:
    return write_tool_wrapper(wg_dir, tool_name="redrift", tool_bin=redrift_bin, wrapper_mode=wrapper_mode)


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


def _default_claude_executor_text(
    *,
    project_dir: Path,
    include_archdrift: bool,
    include_uxdrift: bool,
    include_therapydrift: bool,
    include_fixdrift: bool,
    include_yagnidrift: bool,
    include_redrift: bool,
) -> str:
    archdrift = ""
    if include_archdrift:
        archdrift = (
            "\n"
            f"{ARCHDRIFT_MARKER}\n"
            "- If this task includes an `archdrift` block (in the description), run:\n"
            f"  ./.workgraph/archdrift wg check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Or run the unified check (auto/all strategy can include archdrift):\n"
            f"  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Artifacts live under `.workgraph/.archdrift/`.\n"
        )

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

    therapydrift = ""
    if include_therapydrift:
        therapydrift = (
            "\n"
            f"{THERAPYDRIFT_MARKER}\n"
            "- If this task includes a `therapydrift` block (in the description), run:\n"
            "  ./.workgraph/therapydrift wg check --task {{task_id}} --write-log --create-followups\n"
            "- Or run the unified check (runs therapydrift when a spec is present):\n"
            f"  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Artifacts live under `.workgraph/.therapydrift/`.\n"
        )

    fixdrift = ""
    if include_fixdrift:
        fixdrift = (
            "\n"
            f"{FIXDRIFT_MARKER}\n"
            "- If this task includes a `fixdrift` block (in the description), run:\n"
            "  ./.workgraph/fixdrift wg check --task {{task_id}} --write-log --create-followups\n"
            "- Or run the unified check (runs fixdrift when a spec is present):\n"
            f"  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Artifacts live under `.workgraph/.fixdrift/`.\n"
        )

    yagnidrift = ""
    if include_yagnidrift:
        yagnidrift = (
            "\n"
            f"{YAGNIDRIFT_MARKER}\n"
            "- If this task includes a `yagnidrift` block (in the description), run:\n"
            "  ./.workgraph/yagnidrift wg check --task {{task_id}} --write-log --create-followups\n"
            "- Or run the unified check (runs yagnidrift when a spec is present):\n"
            f"  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Artifacts live under `.workgraph/.yagnidrift/`.\n"
        )

    redrift = ""
    if include_redrift:
        redrift = (
            "\n"
            f"{REDRIFT_MARKER}\n"
            "- If this task includes a `redrift` block (in the description), run:\n"
            "  ./.workgraph/redrift wg check --task {{task_id}} --write-log --create-followups\n"
            "- Or run the unified check (runs redrift when a spec is present):\n"
            f"  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups\n"
            "- Artifacts live under `.workgraph/.redrift/`.\n"
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

{COREDRIFT_MARKER}
- Treat the `wg-contract` block (in the task description) as binding.
- At start and just before completion, run:
  ./.workgraph/drifts check --task {{{{task_id}}}} --write-log --create-followups
- If you need to change scope, update touch globs:
  ./.workgraph/coredrift contract set-touch --task {{{{task_id}}}} <globs...>
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
{archdrift}
{uxdrift}
{therapydrift}
{fixdrift}
{yagnidrift}
{redrift}

## Workgraph Rules
- Stay focused on this task.
- Log progress: wg log {{{{task_id}}}} \"message\"
- When complete: wg done {{{{task_id}}}}
- If blocked: wg fail {{{{task_id}}}} --reason \"description\"
\"\"\"
"""


_TEMPLATE_START_RE = re.compile(r'(?P<prefix>\btemplate\s*=\s*"""\r?\n)', re.MULTILINE)


def _inject_coredrift_into_template(body: str) -> str | None:
    changed = False
    cur = body

    old = "  ./.workgraph/coredrift check --task {{task_id}} --write-log --create-followups"
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

    if COREDRIFT_MARKER in cur:
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
        f"{COREDRIFT_MARKER}\n"
        "- Treat the `wg-contract` block (in the task description) as binding.\n"
        "- At start and just before completion, run:\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- If you need to change scope, update touch globs:\n"
        "  ./.workgraph/coredrift contract set-touch --task {{task_id}} <globs...>\n"
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


def _inject_archdrift_into_template(body: str) -> str | None:
    if ARCHDRIFT_MARKER in body:
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
        f"{ARCHDRIFT_MARKER}\n"
        "- If this task includes an `archdrift` block (in the description), run:\n"
        "  ./.workgraph/archdrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (auto/all strategy can include archdrift):\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- Artifacts live under `.workgraph/.archdrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def _inject_therapydrift_into_template(body: str) -> str | None:
    if THERAPYDRIFT_MARKER in body:
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
        f"{THERAPYDRIFT_MARKER}\n"
        "- If this task includes a `therapydrift` block (in the description), run:\n"
        "  ./.workgraph/therapydrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs therapydrift when a spec is present):\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- Artifacts live under `.workgraph/.therapydrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def _inject_fixdrift_into_template(body: str) -> str | None:
    if FIXDRIFT_MARKER in body:
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
        f"{FIXDRIFT_MARKER}\n"
        "- If this task includes a `fixdrift` block (in the description), run:\n"
        "  ./.workgraph/fixdrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs fixdrift when a spec is present):\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- Artifacts live under `.workgraph/.fixdrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def _inject_yagnidrift_into_template(body: str) -> str | None:
    if YAGNIDRIFT_MARKER in body:
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
        f"{YAGNIDRIFT_MARKER}\n"
        "- If this task includes a `yagnidrift` block (in the description), run:\n"
        "  ./.workgraph/yagnidrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs yagnidrift when a spec is present):\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- Artifacts live under `.workgraph/.yagnidrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def _inject_redrift_into_template(body: str) -> str | None:
    if REDRIFT_MARKER in body:
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
        f"{REDRIFT_MARKER}\n"
        "- If this task includes a `redrift` block (in the description), run:\n"
        "  ./.workgraph/redrift wg check --task {{task_id}} --write-log --create-followups\n"
        "- Or run the unified check (runs redrift when a spec is present):\n"
        "  ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "- Artifacts live under `.workgraph/.redrift/`.\n"
    )

    return body[:end].rstrip("\n") + "\n" + insert + "\n" + body[end:]


def ensure_executor_guidance(
    wg_dir: Path,
    *,
    include_archdrift: bool,
    include_uxdrift: bool,
    include_therapydrift: bool,
    include_fixdrift: bool,
    include_yagnidrift: bool,
    include_redrift: bool,
) -> tuple[bool, list[str]]:
    executors_dir = wg_dir / "executors"
    executors_dir.mkdir(parents=True, exist_ok=True)

    created = False
    claude_path = executors_dir / "claude.toml"
    if not claude_path.exists():
        claude_path.write_text(
            _default_claude_executor_text(
                project_dir=wg_dir.parent,
                include_archdrift=include_archdrift,
                include_uxdrift=include_uxdrift,
                include_therapydrift=include_therapydrift,
                include_fixdrift=include_fixdrift,
                include_yagnidrift=include_yagnidrift,
                include_redrift=include_redrift,
            ),
            encoding="utf-8",
        )
        created = True

    patched: list[str] = []
    for p in sorted(executors_dir.glob("*.toml")):
        text = p.read_text(encoding="utf-8")
        cur = text
        changed = False

        new_text = _inject_coredrift_into_template(cur)
        if new_text is not None:
            cur = new_text
            changed = True

        if include_archdrift:
            new_text = _inject_archdrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if include_uxdrift:
            new_text = _inject_uxdrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if include_therapydrift:
            new_text = _inject_therapydrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if include_fixdrift:
            new_text = _inject_fixdrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if include_yagnidrift:
            new_text = _inject_yagnidrift_into_template(cur)
            if new_text is not None:
                cur = new_text
                changed = True

        if include_redrift:
            new_text = _inject_redrift_into_template(cur)
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


def _write_text_if_changed(path: Path, content: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    if existing == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def ensure_amplifier_executor(wg_dir: Path, *, bundle_name: str = "speedrift") -> tuple[bool, bool]:
    """
    Ensure a workgraph executor that dispatches each task to Amplifier.

    Returns:
      (wrote_executor_toml, wrote_runner_script)
    """

    executors_dir = wg_dir / "executors"
    executors_dir.mkdir(parents=True, exist_ok=True)

    runner = executors_dir / "amplifier-run.sh"
    runner_text = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "EXTRA_ARGS=()\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  case \"$1\" in\n"
        "    --model)\n"
        "      EXTRA_ARGS+=(\"--model\" \"$2\")\n"
        "      shift 2\n"
        "      ;;\n"
        "    --model=*)\n"
        "      EXTRA_ARGS+=(\"--model\" \"${1#--model=}\")\n"
        "      shift\n"
        "      ;;\n"
        "    --provider)\n"
        "      EXTRA_ARGS+=(\"--provider\" \"$2\")\n"
        "      shift 2\n"
        "      ;;\n"
        "    --provider=*)\n"
        "      EXTRA_ARGS+=(\"--provider\" \"${1#--provider=}\")\n"
        "      shift\n"
        "      ;;\n"
        "    --bundle)\n"
        "      EXTRA_ARGS+=(\"--bundle\" \"$2\")\n"
        "      shift 2\n"
        "      ;;\n"
        "    --bundle=*)\n"
        "      EXTRA_ARGS+=(\"--bundle\" \"${1#--bundle=}\")\n"
        "      shift\n"
        "      ;;\n"
        "    *)\n"
        "      EXTRA_ARGS+=(\"$1\")\n"
        "      shift\n"
        "      ;;\n"
        "  esac\n"
        "done\n\n"
        "PROMPT=$(cat)\n"
        "if [[ -z \"$PROMPT\" ]]; then\n"
        "  echo \"error: empty prompt passed to amplifier executor\" >&2\n"
        "  exit 1\n"
        "fi\n\n"
        "BUNDLE=\"${AMPLIFIER_BUNDLE:-" + bundle_name + "}\"\n"
        "exec amplifier run --mode single --output-format json --bundle \"$BUNDLE\" "
        "\"${EXTRA_ARGS[@]+${EXTRA_ARGS[@]}}\" \"$PROMPT\"\n"
    )
    wrote_runner = _write_text_if_changed(runner, runner_text)
    runner.chmod(runner.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    executor = executors_dir / "amplifier.toml"
    executor_text = (
        "[executor]\n"
        "type = \"claude\"\n"
        "command = \".workgraph/executors/amplifier-run.sh\"\n"
        "args = []\n"
        "working_dir = \"{{working_dir}}\"\n"
        "timeout = 1200\n\n"
        "[executor.env]\n"
        "WG_TASK_ID = \"{{task_id}}\"\n\n"
        "[executor.prompt_template]\n"
        "template = \"\"\"\n"
        "{{task_identity}}\n\n"
        "# Task Assignment\n\n"
        "**Task ID**: `{{task_id}}`\n"
        "**Title**: {{task_title}}\n\n"
        "## Description\n\n"
        "{{task_description}}\n\n"
        "## Context from Completed Dependencies\n\n"
        "{{task_context}}\n\n"
        "## Speedrift Execution Rules\n\n"
        "1. Workgraph is source of truth.\n"
        "2. Run drift checks at task start and before completion:\n"
        "   ./.workgraph/drifts check --task {{task_id}} --write-log --create-followups\n"
        "3. Log progress with `wg log {{task_id}} \"...\"`.\n"
        "4. Record meaningful outputs with `wg artifact {{task_id}} <path>`.\n"
        "5. If blocked, use `wg fail {{task_id}} --reason \"...\"`.\n"
        "6. If complete, use `wg done {{task_id}}`.\n"
        "\"\"\"\n"
    )
    wrote_executor = _write_text_if_changed(executor, executor_text)
    return (wrote_executor, wrote_runner)


def ensure_amplifier_autostart_hook(project_dir: Path) -> tuple[bool, bool]:
    """
    Install prompt/session hooks that keep Workgraph + Speedrift bootstrapped.

    Returns:
      (wrote_hook_script, wrote_hooks_json)
    """

    hooks_root = project_dir / ".amplifier" / "hooks"
    hook_dir = hooks_root / "speedrift-autostart"
    hook_dir.mkdir(parents=True, exist_ok=True)

    hook_script = hook_dir / "session-start.sh"
    hook_script_text = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        "PROJECT_DIR=\"${AMPLIFIER_PROJECT_DIR:-$(pwd)}\"\n"
        "PROJECT_KEY=\"$(printf '%s' \"$PROJECT_DIR\" | cksum | awk '{print $1}')\"\n"
        "SESSION_KEY=\"${AMPLIFIER_SESSION_ID:-unknown}\"\n"
        "STAMP_DIR=\"${TMPDIR:-/tmp}/speedrift-autostart\"\n"
        "STAMP_FILE=\"$STAMP_DIR/${PROJECT_KEY}-${SESSION_KEY}\"\n"
        "mkdir -p \"$STAMP_DIR\" 2>/dev/null || true\n"
        "if [[ -f \"$STAMP_FILE\" ]]; then\n"
        "  exit 0\n"
        "fi\n"
        "touch \"$STAMP_FILE\" 2>/dev/null || true\n"
        "cd \"$PROJECT_DIR\" 2>/dev/null || exit 0\n\n"
        "if [[ ! -d \".workgraph\" ]]; then\n"
        "  if command -v wg >/dev/null 2>&1; then\n"
            "    wg init >/dev/null 2>&1 || true\n"
        "  else\n"
        "    exit 0\n"
        "  fi\n"
        "fi\n\n"
        "if [[ ! -x \".workgraph/drifts\" || ! -x \".workgraph/coredrift\" || ! -x \".workgraph/executors/amplifier-run.sh\" ]]; then\n"
        "  if command -v driftdriver >/dev/null 2>&1; then\n"
        "    driftdriver --dir \"$PROJECT_DIR\" install --wrapper-mode portable --with-fixdrift --with-amplifier-executor --no-ensure-contracts >/dev/null 2>&1 || \\\n"
        "      driftdriver --dir \"$PROJECT_DIR\" install --wrapper-mode portable --no-ensure-contracts >/dev/null 2>&1 || true\n"
        "  fi\n"
        "fi\n\n"
        "if [[ -x \".workgraph/coredrift\" ]]; then\n"
        "  ./.workgraph/coredrift --dir \"$PROJECT_DIR\" ensure-contracts --apply >/dev/null 2>&1 || true\n"
        "fi\n\n"
        "AUTOPILOT_DIR=\".workgraph/service\"\n"
        "AUTOPILOT_PID=\"$AUTOPILOT_DIR/speedrift-autopilot.pid\"\n"
        "AUTOPILOT_LOG=\"$AUTOPILOT_DIR/speedrift-autopilot.log\"\n"
        "mkdir -p \"$AUTOPILOT_DIR\" >/dev/null 2>&1 || true\n\n"
        "is_pid_running() {\n"
        "  local pid=\"$1\"\n"
        "  [[ -n \"$pid\" ]] && kill -0 \"$pid\" >/dev/null 2>&1\n"
        "}\n\n"
        "if [[ -f \"$AUTOPILOT_PID\" ]]; then\n"
        "  EXISTING_PID=\"$(cat \"$AUTOPILOT_PID\" 2>/dev/null || true)\"\n"
        "  if is_pid_running \"$EXISTING_PID\"; then\n"
        "    exit 0\n"
        "  fi\n"
        "fi\n\n"
        "export PROJECT_DIR\n"
        "nohup bash -lc '\n"
        "  set -euo pipefail\n"
        "  cd \"$PROJECT_DIR\" >/dev/null 2>&1 || exit 0\n"
        "  while true; do\n"
        "    if command -v wg >/dev/null 2>&1; then\n"
        "      if ! wg --dir \"$PROJECT_DIR/.workgraph\" service status 2>/dev/null | grep -Eq \"^Service:[[:space:]]+running\"; then\n"
        "        wg --dir \"$PROJECT_DIR/.workgraph\" service start --executor amplifier >/dev/null 2>&1 || \\\n"
        "          wg --dir \"$PROJECT_DIR/.workgraph\" service start >/dev/null 2>&1 || true\n"
        "      fi\n"
        "    fi\n"
        "    if [[ -x \"$PROJECT_DIR/.workgraph/drifts\" ]]; then\n"
        "      \"$PROJECT_DIR/.workgraph/drifts\" orchestrate --write-log --create-followups >/dev/null 2>&1 || true\n"
        "    fi\n"
        "    sleep 90\n"
        "  done\n"
        "' >>\"$AUTOPILOT_LOG\" 2>&1 &\n"
        "echo \"$!\" > \"$AUTOPILOT_PID\" 2>/dev/null || true\n\n"
        "exit 0\n"
    )
    wrote_script = _write_text_if_changed(hook_script, hook_script_text)
    hook_script.chmod(hook_script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    hook_json = hook_dir / "hooks.json"
    hook_json_text = (
        "{\n"
        "  \"hooks\": {\n"
        "    \"UserPromptSubmit\": [\n"
        "      {\n"
        "        \"matcher\": \".*\",\n"
        "        \"hooks\": [\n"
        "          {\n"
        "            \"type\": \"command\",\n"
        "            \"command\": \"bash ${AMPLIFIER_HOOKS_DIR}/speedrift-autostart/session-start.sh\",\n"
        "            \"timeout\": 120\n"
        "          }\n"
        "        ]\n"
        "      }\n"
        "    ],\n"
        "    \"SessionStart\": [\n"
        "      {\n"
        "        \"matcher\": \".*\",\n"
        "        \"hooks\": [\n"
        "          {\n"
        "            \"type\": \"command\",\n"
        "            \"command\": \"bash ${AMPLIFIER_HOOKS_DIR}/speedrift-autostart/session-start.sh\",\n"
        "            \"timeout\": 120\n"
        "          }\n"
        "        ]\n"
        "      }\n"
        "    ]\n"
        "  }\n"
        "}\n"
    )
    wrote_json = _write_text_if_changed(hook_json, hook_json_text)
    return (wrote_script, wrote_json)


@dataclass(frozen=True)
class CodexAdapterResult:
    wrote_agents_md: bool


def install_codex_adapter(project_dir: Path) -> CodexAdapterResult:
    """
    Inject the Driftdriver Integration Protocol into the project's AGENTS.md.

    Reads the AGENTS.md.partial template bundled with driftdriver and either
    creates AGENTS.md (if absent) or appends to it (if present).  The operation
    is idempotent: a second call returns wrote_agents_md=False when the marker
    is already present.
    """
    template_path = (
        Path(__file__).parent
        / "templates"
        / "adapters"
        / "codex"
        / "AGENTS.md.partial"
    )
    partial = template_path.read_text(encoding="utf-8")

    agents_md = project_dir / "AGENTS.md"

    if agents_md.exists():
        existing = agents_md.read_text(encoding="utf-8")
        if CODEX_ADAPTER_MARKER in existing:
            return CodexAdapterResult(wrote_agents_md=False)
        new_content = existing.rstrip("\n") + "\n\n" + partial
    else:
        new_content = partial

    agents_md.write_text(new_content, encoding="utf-8")
    return CodexAdapterResult(wrote_agents_md=True)
