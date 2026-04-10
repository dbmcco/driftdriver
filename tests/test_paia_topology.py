# ABOUTME: Tests for PAIA canonical-topology helpers used to retire legacy repo paths.
# ABOUTME: Verifies driftdriver can load paia-program config.toml and detect shadowed repos.

from __future__ import annotations

from pathlib import Path

from driftdriver.paia_topology import is_noncanonical_paia_repo, is_shadowed_legacy_repo, load_paia_topology


def test_load_paia_topology_reads_paia_program_config(tmp_path: Path) -> None:
    config = tmp_path / "paia-program" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        """
[repos]
paia-agents = "/tmp/paia-agents"
derek = "/tmp/paia-agents/derek"

[topology]
schema = 1

[topology.canonical]
target_repos = ["paia-agents"]

[topology.agent_family]
target_repo = "paia-agents"
members = ["derek"]
deployment = "separate-services"
migration_style = "dependency-led"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    topology = load_paia_topology(tmp_path)

    assert topology is not None
    assert topology.repos["derek"] == Path("/tmp/paia-agents/derek")
    assert topology.canonical_targets == {"paia-agents"}
    assert topology.agent_family_root == "paia-agents"
    assert topology.agent_members == ("derek",)


def test_is_shadowed_legacy_repo_detects_old_agent_repo(tmp_path: Path) -> None:
    config = tmp_path / "paia-program" / "config.toml"
    config.parent.mkdir(parents=True)
    canonical = tmp_path / "paia-agents" / "derek"
    legacy = tmp_path / "derek"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    config.write_text(
        f"""
[repos]
derek = "{canonical}"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert is_shadowed_legacy_repo(legacy, workspace_root=tmp_path) is True
    assert is_shadowed_legacy_repo(canonical, workspace_root=tmp_path) is False


def test_is_noncanonical_paia_repo_marks_agent_member_surfaces(tmp_path: Path) -> None:
    config = tmp_path / "paia-program" / "config.toml"
    config.parent.mkdir(parents=True)
    family_root = tmp_path / "paia-agents"
    canonical = family_root / "derek"
    canonical.mkdir(parents=True)
    config.write_text(
        f"""
[repos]
paia-agents = "{family_root}"
derek = "{canonical}"

[topology.canonical]
target_repos = ["paia-agents"]

[topology.agent_family]
target_repo = "paia-agents"
members = ["derek"]
""".strip()
        + "\n",
        encoding="utf-8",
    )

    assert is_noncanonical_paia_repo(canonical, workspace_root=tmp_path) is True
    assert is_noncanonical_paia_repo(family_root, workspace_root=tmp_path) is False
