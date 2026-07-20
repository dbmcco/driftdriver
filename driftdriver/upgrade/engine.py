# ABOUTME: Migration engine: discovery, version stamping, idempotent apply loop.
# ABOUTME: Stamps applied migrations in .workgraph/upgrade-state.json per repo.

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

STATE_REL = ".workgraph/upgrade-state.json"


@dataclass
class Migration:
    """A single ordered, idempotent migration shipped with driftdriver."""

    id: str
    description: str
    apply: Callable[[Path], dict]


@dataclass
class MigrationResult:
    id: str
    changed: bool
    files: list[str] = field(default_factory=list)
    note: str = ""
    needs_review: bool = False


@dataclass
class RepoUpgradeReport:
    repo: str
    ran: list[str] = field(default_factory=list)  # executed this run
    skipped: list[str] = field(default_factory=list)  # already stamped
    changed_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)  # flagged, not stamped
    dry_run: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changed_files)


def load_migrations() -> list[Migration]:
    """Discover bundled migrations from the migrations package, sorted by id."""
    from driftdriver.upgrade import migrations as mig_pkg

    mig_dir = Path(mig_pkg.__file__).resolve().parent
    out: list[Migration] = []
    for path in sorted(mig_dir.glob("[0-9][0-9][0-9]_*.py")):
        mod = _load_module(path)
        mid = getattr(mod, "ID", None)
        apply_fn = getattr(mod, "apply", None)
        if not mid or not callable(apply_fn):
            continue
        out.append(
            Migration(
                id=str(mid),
                description=str(getattr(mod, "DESCRIPTION", "")),
                apply=apply_fn,
            )
        )
    out.sort(key=lambda m: m.id)
    return out


def _load_module(path: Path):
    name = f"driftdriver_upgrade_migration_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load migration module {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _state_path(repo_dir: Path) -> Path:
    return Path(repo_dir) / STATE_REL


def read_state(repo_dir: Path) -> dict:
    """Return the per-repo migration state. Missing/corrupt -> empty."""
    sp = _state_path(repo_dir)
    if not sp.exists():
        return {"applied": []}
    try:
        data = json.loads(sp.read_text())
    except Exception:
        return {"applied": []}
    if not isinstance(data, dict):
        return {"applied": []}
    data.setdefault("applied", [])
    if not isinstance(data["applied"], list):
        data["applied"] = []
    return data


def write_state(repo_dir: Path, applied: list[str]) -> None:
    sp = _state_path(repo_dir)
    sp.parent.mkdir(parents=True, exist_ok=True)
    payload = {"applied": sorted(set(str(a) for a in applied)), "version": 1}
    sp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def apply_pending(
    repo_dir: Path,
    *,
    dry_run: bool = False,
    migrations: Optional[list[Migration]] = None,
) -> RepoUpgradeReport:
    """Apply all pending migrations to one repo. Idempotent + stampable.

    - Already-stamped migrations are skipped.
    - A migration that runs without error is stamped (whether or not it changed
      anything), so re-runs are no-ops.
    - A migration returning needs_review is NOT stamped (will rerun after a fix).
    - On error, processing stops for this repo so state stays consistent; earlier
      successful migrations in this run are still stamped.
    """
    repo_dir = Path(repo_dir).resolve()
    report = RepoUpgradeReport(repo=str(repo_dir), dry_run=dry_run)
    migs = migrations if migrations is not None else load_migrations()
    state = read_state(repo_dir)
    applied: list[str] = list(state.get("applied", []))

    for m in migs:
        if m.id in applied:
            report.skipped.append(m.id)
            continue
        try:
            res = m.apply(repo_dir, dry_run=dry_run)
        except Exception as exc:  # pragma: no cover - defensive
            report.errors.append(f"{m.id}: {exc}")
            break
        report.ran.append(m.id)
        if not isinstance(res, dict):
            report.errors.append(f"{m.id}: migration returned non-dict")
            break
        if res.get("needs_review"):
            report.reviews.append(m.id)
            continue
        if not dry_run:
            applied.append(m.id)
        if res.get("changed"):
            report.changed_files.extend(str(f) for f in res.get("files", []))

    if not dry_run and (report.ran or report.skipped or report.reviews):
        # Stamp even when only reviews occurred? No: reviews aren't applied. But
        # we still persist any successful stamps from this run.
        if applied != list(state.get("applied", [])):
            write_state(repo_dir, applied)

    return report
