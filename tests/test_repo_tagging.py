# ABOUTME: Tests for repo tagging — models, discovery metadata loader, snapshot population.
# ABOUTME: Uses real TOML fixtures in tempfiles; no mocks.
from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from driftdriver.ecosystem_hub.models import RepoSnapshot
from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repo_meta


class TestRepoSnapshotTagsField(unittest.TestCase):
    def test_tags_defaults_to_empty_list(self):
        snap = RepoSnapshot(name="foo", path="/tmp/foo", exists=True)
        self.assertEqual(snap.tags, [])

    def test_tags_round_trips_through_asdict(self):
        snap = RepoSnapshot(name="foo", path="/tmp/foo", exists=True)
        snap.tags = ["company", "active-project", "paia"]
        d = asdict(snap)
        self.assertEqual(d["tags"], ["company", "active-project", "paia"])

    def test_tags_field_is_after_source(self):
        """tags should appear right after source in field order (per spec)."""
        from dataclasses import fields
        field_names = [f.name for f in fields(RepoSnapshot)]
        source_idx = field_names.index("source")
        tags_idx = field_names.index("tags")
        self.assertEqual(tags_idx, source_idx + 1)


class TestLoadEcosystemRepoMeta(unittest.TestCase):
    def _make_toml(self, content: str, tmp: Path) -> Path:
        p = tmp / "ecosystem.toml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_returns_tags_for_repo_with_tags(self):
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.paia-shell]\nrole = "product"\npath = "../paia-shell"\ntags = ["company", "active-project", "paia"]\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertEqual(meta["paia-shell"]["tags"], ["company", "active-project", "paia"])

    def test_returns_empty_tags_for_repo_without_tags(self):
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.coredrift]\nrole = "baseline"\nurl = "https://github.com/dbmcco/coredrift"\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertEqual(meta["coredrift"]["tags"], [])

    def test_returns_empty_dict_for_missing_file(self):
        meta = _load_ecosystem_repo_meta(Path("/nonexistent/ecosystem.toml"))
        self.assertEqual(meta, {})

    def test_preserves_path_and_url_keys(self):
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.lodestar]\nrole = "product"\npath = "../lodestar"\ntags = ["personal"]\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertIn("path", meta["lodestar"])
            self.assertIn("tags", meta["lodestar"])
            self.assertEqual(meta["lodestar"]["tags"], ["personal"])

    def test_tags_must_be_list_of_strings_non_list_ignored(self):
        """If tags is not a list, treat as empty — no crash."""
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.bad-tags]\nrole = "product"\npath = "../bad"\ntags = "not-a-list"\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertEqual(meta["bad-tags"]["tags"], [])


class TestSnapshotPopulatesTags(unittest.TestCase):
    def _make_ecosystem_toml(self, tmp: Path) -> Path:
        eco_dir = tmp / "speedrift-ecosystem"
        eco_dir.mkdir()
        toml = eco_dir / "ecosystem.toml"
        toml.write_text(
            'schema = 1\n'
            '[repos.my-repo]\n'
            'role = "product"\n'
            'path = "../my-repo"\n'
            'tags = ["company", "active-project"]\n',
            encoding="utf-8",
        )
        return toml

    def test_tags_populated_on_repo_snapshot(self):
        from driftdriver.ecosystem_hub.snapshot import collect_ecosystem_snapshot

        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            repo_dir = workspace / "my-repo"
            repo_dir.mkdir()
            eco_toml = self._make_ecosystem_toml(workspace)

            result = collect_ecosystem_snapshot(
                project_dir=workspace / "speedrift-ecosystem",
                workspace_root=workspace,
                ecosystem_toml=eco_toml,
            )
            repos = {r["name"]: r for r in result.get("repos", [])}
            self.assertIn("my-repo", repos)
            self.assertEqual(repos["my-repo"]["tags"], ["company", "active-project"])

    def test_tags_empty_for_repo_without_tags_in_toml(self):
        from driftdriver.ecosystem_hub.snapshot import collect_ecosystem_snapshot

        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            repo_dir = workspace / "bare-repo"
            repo_dir.mkdir()
            eco_dir = workspace / "speedrift-ecosystem"
            eco_dir.mkdir()
            toml = eco_dir / "ecosystem.toml"
            toml.write_text(
                'schema = 1\n'
                '[repos.bare-repo]\n'
                'role = "product"\n'
                'path = "../bare-repo"\n',
                encoding="utf-8",
            )
            result = collect_ecosystem_snapshot(
                project_dir=eco_dir,
                workspace_root=workspace,
                ecosystem_toml=toml,
            )
            repos = {r["name"]: r for r in result.get("repos", [])}
            self.assertIn("bare-repo", repos)
            self.assertEqual(repos["bare-repo"]["tags"], [])
