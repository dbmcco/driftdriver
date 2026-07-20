# ABOUTME: Fleet runner — apply pending migrations across many driftdriver repos.
# ABOUTME: Discovers repos under a root by their .workgraph/ dir and runs the
# ABOUTME: engine on each; resilient (one repo error never aborts the rest).

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from driftdriver.upgrade.engine import RepoUpgradeReport, apply_pending

_EXCLUDE_PARTS = {
    "node_modules",
    ".wg-worktrees",
    ".worktrees",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".tox",
}


@dataclass
class FleetReport:
    root: str
    dry_run: bool
    repos: list[RepoUpgradeReport] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.repos)

    @property
    def changed(self) -> list[RepoUpgradeReport]:
        return [r for r in self.repos if r.changed_files]

    @property
    def with_errors(self) -> list[RepoUpgradeReport]:
        return [r for r in self.repos if r.errors]

    @property
    def with_reviews(self) -> list[RepoUpgradeReport]:
        return [r for r in self.repos if r.reviews]


def discover_repos(root: Path) -> list[Path]:
    """Find repos with a .workgraph/ dir anywhere under root (bounded walk).

    Descends until it finds a ``.workgraph/`` dir, then stops (does not recurse
    into a discovered repo). Skips node_modules, .wg-worktrees, .worktrees,
    venvs, build dirs, and git internals. Dedupes by real path.
    """
    root = Path(root)
    found: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > 8:
            return
        try:
            entries = list(d.iterdir())
        except (PermissionError, OSError):
            return
        # Record this dir as a repo if it contains a .workgraph.
        if any(e.name == ".workgraph" and e.is_dir() for e in entries):
            found.append(d)
        # Keep descending so repos nested inside another repo's tree (e.g.
        # client projects under work/lfw) are still found. Never descend INTO
        # a .workgraph dir itself, and skip noise dirs.
        for e in entries:
            if not e.is_dir():
                continue
            if e.name in _EXCLUDE_PARTS or e.name == ".workgraph":
                continue
            walk(e, depth + 1)

    walk(root, 0)

    seen: set[Path] = set()
    out: list[Path] = []
    for p in sorted(found):
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        out.append(p)
    return out


def run_fleet(root: Path, *, dry_run: bool = False) -> FleetReport:
    """Apply pending migrations to every discovered repo. Never raises."""
    report = FleetReport(root=str(Path(root).resolve()), dry_run=dry_run)
    for repo in discover_repos(root):
        try:
            r = apply_pending(repo, dry_run=dry_run)
        except Exception as exc:  # pragma: no cover - defensive isolation
            r = RepoUpgradeReport(repo=str(repo), dry_run=dry_run)
            r.errors.append(f"fleet: {exc}")
        report.repos.append(r)
    return report
