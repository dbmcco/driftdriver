"""Shared scanning + deviation-register helpers for internal drift lanes.

modelrift and surfacedrift both walk a project's Python source and suppress
findings against the model-mediated deviation register. Centralizing that
machinery here keeps the lanes thin and keeps suppression semantics identical
across lanes — one register, many lanes, one definition of "covered".
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# Directories never scanned (build output, deps, caches, the graph itself).
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".workgraph", ".wg", "dist", "build", ".eggs", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "site-packages",
}
PY_EXT = ".py"
MAX_FILE_BYTES = 200_000  # skip minified/generated blobs

# --- deviation register --------------------------------------------------------
# The model-mediated deviation register is the owned, reviewable record of
# intentional deterministic exceptions. Any lane may suppress a finding by
# matching its file:line against a logged Location entry here.
DEVIATION_FILES = (
    "docs/model-mediated/deviation-register.md",
    "docs/model-mediated/MODEL_MEDIATED_DEVIATION_REGISTER.md",
)
# `path:line` or `path:start-end` inside backticks anywhere in a Location field.
LOCATION_TOKEN_RE = re.compile(
    r"`(?P<path>[^`]+?\.py):(?P<a>\d+)(?:\s*-\s*(?P<b>\d+))?(?:[^`]*)`"
)


def _is_separate_workcopy(path: Path) -> bool:
    """True if ``path`` is itself a separate git working copy (don't descend).

    A child directory carrying its own ``.git`` — a file (git linked worktree)
    or a directory (submodule / nested clone) — marks a working-copy boundary,
    not a subtree of the current checkout. The scan root itself is never tested,
    so running a lane from *inside* a worktree still scans that worktree's code.
    This is the isolation invariant: scan exactly one working copy.
    """
    g = path / ".git"
    return g.is_file() or g.is_dir()


def walk_py_files(project_dir: Path) -> Iterable[Path]:
    """Yield ``.py`` files under ``project_dir``.

    Prunes build/deps/cache dirs by name and stops at any nested separate
    working copy (a git boundary: linked worktree, submodule, nested clone) so
    a scan covers exactly one working copy rather than recursively swallowing
    sibling copies nested on disk (e.g. under ``.worktrees/``).
    """
    for root, dirs, files in project_dir.walk():  # py3.12+: yields (Path, dirs, files)
        # prune in place: ignored names first (cheap), then git boundaries
        dirs[:] = [
            d for d in dirs
            if d not in IGNORED_DIRS and not _is_separate_workcopy(root / d)
        ]
        for name in files:
            if name.endswith(PY_EXT):
                yield root / name


def read_py_source(path: Path) -> str | None:
    """Read a ``.py`` file as text, or ``None`` if unreadable / over the size limit."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text) > MAX_FILE_BYTES:
        return None
    return text


def load_deviations(project_dir: Path) -> list[tuple[str, int, int]]:
    """Parse deviation-register Location fields into ``(rel_path, start, end)``."""
    entries: list[tuple[str, int, int]] = []
    for cand in DEVIATION_FILES:
        reg = project_dir / cand
        if not reg.exists():
            continue
        try:
            text = reg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "**Location:**" not in line:
                continue
            for m in LOCATION_TOKEN_RE.finditer(line):
                p = m.group("path").strip()
                a = int(m.group("a"))
                b = int(m.group("b")) if m.group("b") else a
                entries.append((p.replace("\\", "/"), min(a, b), max(a, b)))
    return entries


def covered(rel_path: str, lineno: int, deviations: list[tuple[str, int, int]]) -> bool:
    """True if a logged deviation covers ``(rel_path, lineno)``."""
    rp = rel_path.replace("\\", "/")
    for path, start, end in deviations:
        if rp == path and start <= lineno <= end:
            return True
    return False
