# ABOUTME: `driftdriver self-update` subcommand — one verb to get a repo current.
# ABOUTME: Composes install → upgrade → freshness (upstream check), in order.

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from driftdriver.upgrade.engine import apply_pending


def _project_dir(args: argparse.Namespace) -> Path:
    p = Path(args.dir) if getattr(args, "dir", None) else Path.cwd()
    if p.name == ".workgraph":
        p = p.parent
    return p


def _default_install_args(project_dir: Path) -> argparse.Namespace:
    """Construct a Namespace matching `driftdriver install --dir <project_dir>` defaults."""
    return argparse.Namespace(
        dir=str(project_dir),
        coredrift_bin=None,
        specdrift_bin=None,
        datadrift_bin=None,
        archdrift_bin=None,
        depsdrift_bin=None,
        with_uxdrift=False,
        uxdrift_bin=None,
        with_therapydrift=False,
        therapydrift_bin=None,
        with_fixdrift=False,
        fixdrift_bin=None,
        with_yagnidrift=False,
        yagnidrift_bin=None,
        with_redrift=False,
        redrift_bin=None,
        with_amplifier_executor=False,
        with_claude_code_hooks=False,
        all_clis=False,
        with_lessons_mcp=False,
        json=False,
        wrapper_mode="auto",
        no_ensure_contracts=False,
    )


def cmd_self_update(args: argparse.Namespace) -> int:
    """The one verb for 'get this repo current'.

    Composes, in order:
    1. INSTALL — refresh all managed surfaces (wrappers, handlers, route-policy, etc.)
    2. UPGRADE — apply pending migrations (idempotent, stamps state)
    3. FRESHNESS — best-effort upstream check; silent on failure

    --check is dry-run: shows what would change without writing.
    """
    check_mode = bool(getattr(args, "check", False))
    project_dir = _project_dir(args)
    rc = 0

    # ------------------------------------------------------------------
    # 1. INSTALL
    # ------------------------------------------------------------------
    if check_mode:
        # In --check mode, install is not run. The install layer has no
        # dry-run surface — it writes files via _write_text_if_changed.
        # We document this choice: --check skips install entirely.
        print("[check] install: skipped (no dry-run surface; templates are "
              "write-if-changed so reruns are safe)")
    else:
        from .install_cmd import cmd_install

        install_args = _default_install_args(project_dir)
        # Suppress install's own stdout; we'll report our own summary.
        _saved_stdout = sys.stdout
        import io

        sys.stdout = io.StringIO()
        try:
            cmd_install(install_args)
        except SystemExit as e:
            rc = max(rc, int(e.code) if e.code else 1)
        except Exception:
            rc = max(rc, 1)
        finally:
            captured = sys.stdout.getvalue()
            sys.stdout = _saved_stdout
        if rc == 0:
            print("[install] refreshed managed surfaces")
        else:
            print("[install] errors occurred", file=sys.stderr)

    # ------------------------------------------------------------------
    # 2. UPGRADE
    # ------------------------------------------------------------------
    try:
        rep = apply_pending(project_dir, dry_run=check_mode)
    except Exception as e:
        print(f"[upgrade] error: {e}", file=sys.stderr)
        rc = max(rc, 1)
        rep = None

    if rep is not None:
        tag = "[check] " if check_mode else ""
        if rep.ran and rep.changed_files:
            verb = "would apply" if check_mode else "applied"
            print(f"{tag}[upgrade] {verb} {', '.join(rep.ran)}")
            for f in rep.changed_files:
                print(f"  {tag}changed: {f}")
        elif rep.ran:
            verb = "would run" if check_mode else "ran"
            print(f"{tag}[upgrade] {verb} {', '.join(rep.ran)} (no changes)")
        else:
            print(f"{tag}[upgrade] already current")
        if rep.skipped:
            print(f"  {tag}skipped (applied previously): {', '.join(rep.skipped)}")
        for mid in rep.reviews:
            print(f"  {tag}⚠ {mid}: needs manual review", file=sys.stderr)
        for err in rep.errors:
            print(f"  {tag}✗ {err}", file=sys.stderr)
            rc = max(rc, 1)

    # ------------------------------------------------------------------
    # 3. FRESHNESS (best-effort, network-optional)
    # ------------------------------------------------------------------
    if not check_mode:
        try:
            from driftdriver.updates import fetch_github_head

            sha, date = fetch_github_head("dbmcco/driftdriver", timeout_seconds=2)
            short = sha[:12]
            print(f"[freshness] upstream HEAD: {short} ({date})")
            print(f"  refresh tool: uv tool install --force "
                  f"~/projects/experiments/driftdriver")
        except Exception:
            # Silent degrade: no network, no GitHub, no token — print nothing.
            pass

    return rc
