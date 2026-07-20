# ABOUTME: `driftdriver upgrade` subcommand — apply pending migrations.
# ABOUTME: Single-repo by default; `--fleet` walks all repos under --root.

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from driftdriver.upgrade.engine import RepoUpgradeReport, apply_pending
from driftdriver.upgrade.fleet import FleetReport, run_fleet


def _project_dir(args: argparse.Namespace) -> Path:
    p = Path(args.dir) if getattr(args, "dir", None) else Path.cwd()
    if p.name == ".workgraph":
        p = p.parent
    return p


def _repo_dict(rep: RepoUpgradeReport) -> dict:
    d = asdict(rep)
    d["changed"] = rep.changed
    return d


def _print_repo(rep: RepoUpgradeReport) -> None:
    tag = "[dry-run] " if rep.dry_run else ""
    if rep.ran and rep.changed_files:
        print(f"{tag}Upgraded {rep.repo}: applied {', '.join(rep.ran)}")
        for f in rep.changed_files:
            print(f"  changed: {f}")
    elif rep.ran:
        print(f"{tag}Upgraded {rep.repo}: no changes needed (ran {', '.join(rep.ran)})")
    else:
        print(f"{tag}{rep.repo}: up to date")
    if rep.skipped:
        print(f"  skipped (already applied): {', '.join(rep.skipped)}")
    for mid in rep.reviews:
        print(f"  ⚠ {mid}: needs manual review", file=sys.stderr)
    for err in rep.errors:
        print(f"  ✗ {err}", file=sys.stderr)


def _print_fleet(fr: FleetReport, *, json_out: bool) -> None:
    if json_out:
        payload = {
            "root": fr.root,
            "dry_run": fr.dry_run,
            "total": fr.total,
            "changed": [r.repo for r in fr.changed],
            "errors": [r.repo for r in fr.with_errors],
            "reviews": [r.repo for r in fr.with_reviews],
            "repos": [_repo_dict(r) for r in fr.repos],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    tag = "[dry-run] " if fr.dry_run else ""
    print(f"{tag}Fleet upgrade over {fr.total} repo(s) under {fr.root}")
    changed = fr.changed
    if changed:
        print(f"  changed ({len(changed)}):")
        for r in changed:
            files = ", ".join(r.changed_files)
            print(f"    • {Path(r.repo).name}: {files}")
    else:
        print("  no changes required")
    if fr.with_reviews:
        print(f"  ⚠ needs review ({len(fr.with_reviews)}):", file=sys.stderr)
        for r in fr.with_reviews:
            names = ", ".join(r.reviews)
            print(f"    • {Path(r.repo).name}: {names}", file=sys.stderr)
    if fr.with_errors:
        print(f"  ✗ errors ({len(fr.with_errors)}):", file=sys.stderr)
        for r in fr.with_errors:
            print(f"    • {Path(r.repo).name}: {'; '.join(r.errors)}", file=sys.stderr)


def cmd_upgrade(args: argparse.Namespace) -> int:
    if getattr(args, "fleet", False):
        root = Path(args.root) if getattr(args, "root", None) else Path.cwd()
        fr = run_fleet(root, dry_run=bool(getattr(args, "dry_run", False)))
        _print_fleet(fr, json_out=bool(getattr(args, "json", False)))
        return 1 if fr.with_errors else 0

    repo = _project_dir(args)
    rep = apply_pending(repo, dry_run=bool(getattr(args, "dry_run", False)))
    if getattr(args, "json", False):
        print(json.dumps(_repo_dict(rep), indent=2, sort_keys=True))
    else:
        _print_repo(rep)
    return 1 if rep.errors else 0
