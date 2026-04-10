"""Helpers for reading the canonical PAIA repo topology from paia-program."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class PaiaTopology:
    """Structured view of the PAIA canonical repo map."""

    workspace_root: Path
    config_path: Path
    repos: dict[str, Path]
    canonical_targets: frozenset[str]
    agent_family_root: str
    agent_members: tuple[str, ...]


def _string_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in raw.items()
        if str(key).strip() and str(value).strip()
    }


def _string_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def find_paia_workspace_root(start_path: Path) -> Path | None:
    """Return the workspace root that contains paia-program/config.toml."""
    start = start_path.expanduser().resolve(strict=False)
    for candidate in (start, *start.parents):
        if candidate.name == "paia-program" and (candidate / "config.toml").exists():
            return candidate.parent
        if (candidate / "paia-program" / "config.toml").exists():
            return candidate
    return None


def load_paia_topology(workspace_root: Path) -> PaiaTopology | None:
    """Load PAIA topology metadata from paia-program/config.toml if present."""
    root = find_paia_workspace_root(workspace_root)
    if root is None:
        return None
    config_path = root / "paia-program" / "config.toml"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None

    topology = data.get("topology", {})
    if not isinstance(topology, dict):
        topology = {}
    canonical = topology.get("canonical", {})
    if not isinstance(canonical, dict):
        canonical = {}
    agent_family = topology.get("agent_family", {})
    if not isinstance(agent_family, dict):
        agent_family = {}

    repos: dict[str, Path] = {}
    for name, raw_path in _string_map(data.get("repos", {})).items():
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (config_path.parent / candidate).resolve(strict=False)
        repos[name] = candidate
    return PaiaTopology(
        workspace_root=root,
        config_path=config_path,
        repos=repos,
        canonical_targets=frozenset(_string_list(canonical.get("target_repos", []))),
        agent_family_root=str(agent_family.get("target_repo", "")).strip(),
        agent_members=_string_list(agent_family.get("members", [])),
    )


def is_shadowed_legacy_repo(repo_path: Path, *, workspace_root: Path | None = None) -> bool:
    """Return True when a repo path is shadowed by a different canonical PAIA path."""
    resolved_repo = repo_path.expanduser().resolve(strict=False)
    topology = load_paia_topology(workspace_root or resolved_repo)
    if topology is None:
        return False

    try:
        if not resolved_repo.is_relative_to(topology.workspace_root):
            return False
    except ValueError:
        return False

    canonical = topology.repos.get(resolved_repo.name)
    if canonical is None or canonical == resolved_repo:
        return False
    try:
        return canonical.is_relative_to(topology.workspace_root)
    except ValueError:
        return False


def is_noncanonical_paia_repo(repo_path: Path, *, workspace_root: Path | None = None) -> bool:
    """Return True when a PAIA repo path should not be judged as a first-class factory surface."""
    resolved_repo = repo_path.expanduser().resolve(strict=False)
    topology = load_paia_topology(workspace_root or resolved_repo)
    if topology is None:
        return False

    try:
        if not resolved_repo.is_relative_to(topology.workspace_root):
            return False
    except ValueError:
        return False

    if is_shadowed_legacy_repo(resolved_repo, workspace_root=topology.workspace_root):
        return True

    repo_name = resolved_repo.name
    canonical = topology.repos.get(repo_name)
    if canonical is not None and canonical.expanduser().resolve(strict=False) == resolved_repo:
        try:
            if canonical.is_relative_to(topology.workspace_root) and canonical.parent != topology.workspace_root:
                return True
        except ValueError:
            pass

    if repo_name in topology.agent_members and topology.agent_family_root:
        family_root = topology.repos.get(topology.agent_family_root)
        if family_root is not None and family_root.expanduser().resolve(strict=False) != resolved_repo:
            return True

    return False
