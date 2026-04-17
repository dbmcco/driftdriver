# ABOUTME: Install subcommand for driftdriver CLI.
# ABOUTME: Resolves tool binaries, writes wrappers, configures adapters and gitignore.

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from driftdriver.install import (
    InstallResult,
    ensure_amplifier_autostart_hook,
    ensure_amplifier_executor,
    install_amplifier_adapter,
    install_claude_adapter,
    install_claude_code_hooks,
    install_codex_adapter,
    install_handler_scripts,
    install_hook_scripts,
    install_lessons_mcp_config,
    install_opencode_hooks,
    refresh_existing_managed_surfaces,
    install_session_driver_executor,
    ensure_archdrift_gitignore,
    ensure_executor_guidance,
    ensure_datadrift_gitignore,
    ensure_depsdrift_gitignore,
    ensure_fixdrift_gitignore,
    ensure_debatedrift_gitignore,
    ensure_qadrift_gitignore,
    ensure_redrift_gitignore,
    ensure_specdrift_gitignore,
    ensure_coredrift_gitignore,
    ensure_therapydrift_gitignore,
    ensure_uxdrift_gitignore,
    ensure_yagnidrift_gitignore,
    resolve_bin,
    write_archdrift_wrapper,
    write_debatedrift_wrapper,
    write_qadrift_wrapper,
    write_datadrift_wrapper,
    write_depsdrift_wrapper,
    write_drifts_wrapper,
    write_driver_wrapper,
    write_fixdrift_wrapper,
    write_redrift_wrapper,
    write_specdrift_wrapper,
    write_coredrift_wrapper,
    write_therapydrift_wrapper,
    write_uxdrift_wrapper,
    write_yagnidrift_wrapper,
)
from driftdriver.policy import ensure_drift_policy
from driftdriver.workgraph import find_workgraph_dir

from .check import ExitCode, _ensure_wg_init


def cmd_install(args: argparse.Namespace) -> int:
    project_dir = Path.cwd()
    if args.dir:
        project_dir = Path(args.dir)
        if project_dir.name == ".workgraph":
            project_dir = project_dir.parent

    _ensure_wg_init(project_dir)

    wg_dir = find_workgraph_dir(project_dir)

    wrapper_mode = str(getattr(args, "wrapper_mode", "auto") or "auto").strip().lower()
    if wrapper_mode not in ("auto", "pinned", "portable"):
        print("error: --wrapper-mode must be one of: auto, pinned, portable", file=sys.stderr)
        return ExitCode.usage

    # Resolve tool bins.
    repo_root = Path(__file__).resolve().parents[2]
    driver_bin = resolve_bin(
        explicit=None,
        env_var="DRIFTDRIVER_BIN",
        which_name="driftdriver",
        candidates=[repo_root / "bin" / "driftdriver"],
    )
    if driver_bin is None:
        print("error: could not find driftdriver; set $DRIFTDRIVER_BIN", file=sys.stderr)
        return ExitCode.usage

    coredrift_bin = resolve_bin(
        explicit=Path(args.coredrift_bin) if args.coredrift_bin else None,
        env_var="COREDRIFT_BIN",
        which_name="coredrift",
        candidates=[
            repo_root.parent / "coredrift" / "bin" / "coredrift",
        ],
    )
    if coredrift_bin is None:
        print("error: could not find coredrift; pass --coredrift-bin or set $COREDRIFT_BIN", file=sys.stderr)
        return ExitCode.usage

    specdrift_bin = resolve_bin(
        explicit=Path(args.specdrift_bin) if args.specdrift_bin else None,
        env_var="SPECDRIFT_BIN",
        which_name="specdrift",
        candidates=[
            repo_root.parent / "specdrift" / "bin" / "specdrift",
        ],
    )

    include_uxdrift = bool(args.with_uxdrift or args.uxdrift_bin)
    uxdrift_bin = resolve_bin(
        explicit=Path(args.uxdrift_bin) if args.uxdrift_bin else None,
        env_var="UXDRIFT_BIN",
        which_name="uxdrift",
        candidates=[
            repo_root.parent / "uxdrift" / "bin" / "uxdrift",
        ],
    )
    if include_uxdrift and uxdrift_bin is None:
        # Best-effort: don't fail install.
        include_uxdrift = False

    include_therapydrift = bool(args.with_therapydrift or args.therapydrift_bin)
    therapydrift_bin = resolve_bin(
        explicit=Path(args.therapydrift_bin) if args.therapydrift_bin else None,
        env_var="THERAPYDRIFT_BIN",
        which_name="therapydrift",
        candidates=[
            repo_root.parent / "therapydrift" / "bin" / "therapydrift",
        ],
    )
    if include_therapydrift and therapydrift_bin is None:
        # Best-effort: don't fail install.
        include_therapydrift = False

    include_fixdrift = bool(args.with_fixdrift or args.fixdrift_bin)
    fixdrift_bin = resolve_bin(
        explicit=Path(args.fixdrift_bin) if args.fixdrift_bin else None,
        env_var="FIXDRIFT_BIN",
        which_name="fixdrift",
        candidates=[
            repo_root.parent / "fixdrift" / "bin" / "fixdrift",
        ],
    )
    if include_fixdrift and fixdrift_bin is None:
        # Best-effort: don't fail install.
        include_fixdrift = False

    include_yagnidrift = bool(args.with_yagnidrift or args.yagnidrift_bin)
    yagnidrift_bin = resolve_bin(
        explicit=Path(args.yagnidrift_bin) if args.yagnidrift_bin else None,
        env_var="YAGNIDRIFT_BIN",
        which_name="yagnidrift",
        candidates=[
            repo_root.parent / "yagnidrift" / "bin" / "yagnidrift",
        ],
    )
    if include_yagnidrift and yagnidrift_bin is None:
        # Best-effort: don't fail install.
        include_yagnidrift = False

    include_redrift = bool(args.with_redrift or args.redrift_bin)
    redrift_bin = resolve_bin(
        explicit=Path(args.redrift_bin) if args.redrift_bin else None,
        env_var="REDRIFT_BIN",
        which_name="redrift",
        candidates=[
            repo_root.parent / "redrift" / "bin" / "redrift",
        ],
    )
    if include_redrift and redrift_bin is None:
        # Best-effort: don't fail install.
        include_redrift = False

    datadrift_bin = resolve_bin(
        explicit=Path(args.datadrift_bin) if args.datadrift_bin else None,
        env_var="DATADRIFT_BIN",
        which_name="datadrift",
        candidates=[
            repo_root.parent / "datadrift" / "bin" / "datadrift",
        ],
    )

    archdrift_bin = resolve_bin(
        explicit=Path(args.archdrift_bin) if args.archdrift_bin else None,
        env_var="ARCHDRIFT_BIN",
        which_name="archdrift",
        candidates=[
            repo_root.parent / "archdrift" / "bin" / "archdrift",
        ],
    )

    depsdrift_bin = resolve_bin(
        explicit=Path(args.depsdrift_bin) if args.depsdrift_bin else None,
        env_var="DEPSDRIFT_BIN",
        which_name="depsdrift",
        candidates=[
            repo_root.parent / "depsdrift" / "bin" / "depsdrift",
        ],
    )

    if wrapper_mode == "auto":
        # Choose portable only when the core tools are installed on PATH.
        wrapper_mode = "portable" if (shutil.which("driftdriver") and shutil.which("coredrift")) else "pinned"

    if wrapper_mode == "portable":
        if not shutil.which("driftdriver"):
            print("error: --wrapper-mode portable requires driftdriver on PATH", file=sys.stderr)
            return ExitCode.usage
        if not shutil.which("coredrift"):
            print("error: --wrapper-mode portable requires coredrift on PATH", file=sys.stderr)
            return ExitCode.usage

    handler_written, handler_count = install_handler_scripts(wg_dir)
    hook_written, hook_count = install_hook_scripts(wg_dir)

    wrote_driver = write_driver_wrapper(wg_dir, driver_bin=driver_bin, wrapper_mode=wrapper_mode)
    wrote_drifts = write_drifts_wrapper(wg_dir)
    wrote_coredrift = write_coredrift_wrapper(wg_dir, coredrift_bin=coredrift_bin, wrapper_mode=wrapper_mode)
    wrote_specdrift = False
    if specdrift_bin is not None:
        wrote_specdrift = write_specdrift_wrapper(wg_dir, specdrift_bin=specdrift_bin, wrapper_mode=wrapper_mode)
    wrote_datadrift = False
    if datadrift_bin is not None:
        wrote_datadrift = write_datadrift_wrapper(wg_dir, datadrift_bin=datadrift_bin, wrapper_mode=wrapper_mode)
    wrote_archdrift = False
    if archdrift_bin is not None:
        wrote_archdrift = write_archdrift_wrapper(wg_dir, archdrift_bin=archdrift_bin, wrapper_mode=wrapper_mode)
    wrote_depsdrift = False
    if depsdrift_bin is not None:
        wrote_depsdrift = write_depsdrift_wrapper(wg_dir, depsdrift_bin=depsdrift_bin, wrapper_mode=wrapper_mode)
    wrote_uxdrift = False
    if include_uxdrift and uxdrift_bin is not None:
        wrote_uxdrift = write_uxdrift_wrapper(wg_dir, uxdrift_bin=uxdrift_bin, wrapper_mode=wrapper_mode)
    wrote_therapydrift = False
    if include_therapydrift and therapydrift_bin is not None:
        wrote_therapydrift = write_therapydrift_wrapper(
            wg_dir,
            therapydrift_bin=therapydrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_fixdrift = False
    if include_fixdrift and fixdrift_bin is not None:
        wrote_fixdrift = write_fixdrift_wrapper(
            wg_dir,
            fixdrift_bin=fixdrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_yagnidrift = False
    if include_yagnidrift and yagnidrift_bin is not None:
        wrote_yagnidrift = write_yagnidrift_wrapper(
            wg_dir,
            yagnidrift_bin=yagnidrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_redrift = False
    if include_redrift and redrift_bin is not None:
        wrote_redrift = write_redrift_wrapper(
            wg_dir,
            redrift_bin=redrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_qadrift = write_qadrift_wrapper(wg_dir)
    wrote_debatedrift = write_debatedrift_wrapper(wg_dir)

    wrote_amplifier_executor = False
    wrote_amplifier_runner = False
    wrote_amplifier_autostart_hook = False
    wrote_amplifier_autostart_hooks_json = False
    if bool(getattr(args, "with_amplifier_executor", False)):
        wrote_amplifier_executor, wrote_amplifier_runner = ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
        wrote_amplifier_autostart_hook, wrote_amplifier_autostart_hooks_json = ensure_amplifier_autostart_hook(project_dir)

    wrote_claude_code_hooks = False
    if bool(getattr(args, "with_claude_code_hooks", False)):
        wrote_claude_code_hooks = install_claude_code_hooks(project_dir)
        install_claude_adapter(project_dir)

    wrote_session_driver_executor = False
    wrote_session_driver_runner = False
    if bool(getattr(args, "all_clis", False)):
        wrote_claude_code_hooks = install_claude_code_hooks(project_dir) or wrote_claude_code_hooks
        install_claude_adapter(project_dir)
        install_codex_adapter(project_dir)
        install_opencode_hooks(project_dir)
        install_amplifier_adapter(project_dir)
        wrote_session_driver_executor, wrote_session_driver_runner = install_session_driver_executor(wg_dir)

    refreshed_surfaces = refresh_existing_managed_surfaces(project_dir, wg_dir)
    wrote_claude_code_hooks = refreshed_surfaces["wrote_claude_code_hooks"] or wrote_claude_code_hooks
    wrote_amplifier_autostart_hook = (
        refreshed_surfaces["wrote_amplifier_autostart_hook"] or wrote_amplifier_autostart_hook
    )
    wrote_amplifier_autostart_hooks_json = (
        refreshed_surfaces["wrote_amplifier_autostart_hooks_json"] or wrote_amplifier_autostart_hooks_json
    )
    wrote_session_driver_executor = (
        refreshed_surfaces["wrote_session_driver_executor"] or wrote_session_driver_executor
    )
    wrote_session_driver_runner = (
        refreshed_surfaces["wrote_session_driver_runner"] or wrote_session_driver_runner
    )

    if bool(getattr(args, "with_lessons_mcp", False)):
        install_lessons_mcp_config(wg_dir)

    updated_gitignore = ensure_coredrift_gitignore(wg_dir)
    if specdrift_bin is not None:
        updated_gitignore = ensure_specdrift_gitignore(wg_dir) or updated_gitignore
    if datadrift_bin is not None:
        updated_gitignore = ensure_datadrift_gitignore(wg_dir) or updated_gitignore
    if archdrift_bin is not None:
        updated_gitignore = ensure_archdrift_gitignore(wg_dir) or updated_gitignore
    if depsdrift_bin is not None:
        updated_gitignore = ensure_depsdrift_gitignore(wg_dir) or updated_gitignore
    if include_uxdrift:
        updated_gitignore = ensure_uxdrift_gitignore(wg_dir) or updated_gitignore
    if include_therapydrift:
        updated_gitignore = ensure_therapydrift_gitignore(wg_dir) or updated_gitignore
    if include_fixdrift:
        updated_gitignore = ensure_fixdrift_gitignore(wg_dir) or updated_gitignore
    if include_yagnidrift:
        updated_gitignore = ensure_yagnidrift_gitignore(wg_dir) or updated_gitignore
    if include_redrift:
        updated_gitignore = ensure_redrift_gitignore(wg_dir) or updated_gitignore
    updated_gitignore = ensure_qadrift_gitignore(wg_dir) or updated_gitignore
    updated_gitignore = ensure_debatedrift_gitignore(wg_dir) or updated_gitignore

    created_executor, patched_executors = ensure_executor_guidance(
        wg_dir,
        include_archdrift=bool(archdrift_bin),
        include_uxdrift=include_uxdrift,
        include_therapydrift=include_therapydrift,
        include_fixdrift=include_fixdrift,
        include_yagnidrift=include_yagnidrift,
        include_redrift=include_redrift,
    )
    wrote_policy = ensure_drift_policy(wg_dir)

    # Auto-register sibling repos as workgraph peers (best-effort).
    auto_registered_peers: list[str] = []
    try:
        from driftdriver.peer_registry import auto_discover_sibling_peers

        auto_registered_peers = auto_discover_sibling_peers(project_dir)
    except Exception:
        pass  # Never fail install due to peer discovery.

    ensured_contracts = False
    if not args.no_ensure_contracts:
        # Delegate to coredrift, since it owns the wg-contract format and defaults.
        subprocess.check_call([str(wg_dir / "coredrift"), "--dir", str(project_dir), "ensure-contracts", "--apply"])
        ensured_contracts = True

    # Signal the factory brain that this repo exists (best-effort, never fails install).
    try:
        from driftdriver.factory_brain.events import EVENTS_REL_PATH, emit_event

        events_file = project_dir / EVENTS_REL_PATH
        emit_event(
            events_file,
            kind="repo.discovered",
            repo=project_dir.name,
            payload={"path": str(project_dir), "source": "driftdriver-install"},
        )
    except Exception:
        pass

    result = InstallResult(
        wrote_drifts=wrote_drifts,
        wrote_driver=wrote_driver,
        wrote_coredrift=wrote_coredrift,
        wrote_specdrift=wrote_specdrift,
        wrote_datadrift=wrote_datadrift,
        wrote_archdrift=wrote_archdrift,
        wrote_depsdrift=wrote_depsdrift,
        wrote_uxdrift=wrote_uxdrift,
        wrote_therapydrift=wrote_therapydrift,
        wrote_fixdrift=wrote_fixdrift,
        wrote_yagnidrift=wrote_yagnidrift,
        wrote_redrift=wrote_redrift,
        wrote_qadrift=wrote_qadrift,
        wrote_debatedrift=wrote_debatedrift,
        wrote_handlers=handler_written,
        wrote_amplifier_executor=wrote_amplifier_executor,
        wrote_amplifier_runner=wrote_amplifier_runner,
        wrote_amplifier_autostart_hook=wrote_amplifier_autostart_hook,
        wrote_amplifier_autostart_hooks_json=wrote_amplifier_autostart_hooks_json,
        wrote_session_driver_executor=wrote_session_driver_executor,
        wrote_session_driver_runner=wrote_session_driver_runner,
        wrote_claude_code_hooks=wrote_claude_code_hooks,
        wrote_policy=wrote_policy,
        updated_gitignore=updated_gitignore,
        created_executor=created_executor,
        patched_executors=patched_executors,
        ensured_contracts=ensured_contracts,
    )
    if args.json:
        import json

        print(json.dumps(asdict(result), indent=2, sort_keys=False))
    else:
        msg = f"Installed Driftdriver into {wg_dir}"
        enabled: list[str] = []
        if include_uxdrift:
            enabled.append("uxdrift")
        if include_therapydrift:
            enabled.append("therapydrift")
        if include_fixdrift:
            enabled.append("fixdrift")
        if include_yagnidrift:
            enabled.append("yagnidrift")
        if include_redrift:
            enabled.append("redrift")
        if bool(getattr(args, "with_amplifier_executor", False)):
            enabled.append("amplifier-executor")
        if bool(getattr(args, "with_claude_code_hooks", False)):
            enabled.append("claude-code-hooks")
        if bool(getattr(args, "all_clis", False)):
            enabled.append("all-clis")
        if bool(getattr(args, "with_lessons_mcp", False)):
            enabled.append("lessons-mcp")
        if enabled:
            msg += f" (with {', '.join(enabled)})"
        print(msg)
        if auto_registered_peers:
            print(f"Auto-registered peers: {', '.join(auto_registered_peers)}")

    return ExitCode.ok
