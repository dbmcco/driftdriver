# ABOUTME: Tests for the `driftdriver self-update` subcommand.
# ABOUTME: Covers install→upgrade→freshness composition, --check mode, and
# ABOUTME: template convergence (session-start.sh no longer emits the
# ABOUTME: decommissioned ECOSYSTEM_HUB_AUTOSTART block).

from __future__ import annotations

import argparse
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class SelfUpdateCompositionTests(unittest.TestCase):
    """cmd_self_update composes install → upgrade → freshness in order."""

    def _make_args(self, project_dir: Path, check: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            dir=str(project_dir),
            check=check,
        )

    def test_install_called_before_upgrade_in_normal_mode(self) -> None:
        from driftdriver.cli.self_update_cmd import cmd_self_update

        order: list[str] = []

        def fake_cmd_install(args: argparse.Namespace) -> int:
            order.append("install")
            return 0

        def fake_apply_pending(repo_dir: Path, *, dry_run: bool = False):
            order.append("upgrade")
            from driftdriver.upgrade.engine import RepoUpgradeReport
            return RepoUpgradeReport(repo=str(repo_dir), dry_run=dry_run)

        def fake_fetch(*a, **kw):
            order.append("freshness")
            return ("abc123def456", "2026-08-03T12:00:00Z")

        import driftdriver.updates as updates_mod

        with (
            patch("driftdriver.cli.install_cmd.cmd_install", fake_cmd_install),
            patch("driftdriver.cli.self_update_cmd.apply_pending", fake_apply_pending),
            patch.object(updates_mod, "fetch_github_head", fake_fetch),
        ):
            _suppress = io.StringIO()
            old = sys.stdout
            sys.stdout = _suppress
            try:
                cmd_self_update(self._make_args(Path("/tmp/fake-repo")))
            finally:
                sys.stdout = old

        self.assertEqual(order, ["install", "upgrade", "freshness"])

    def test_check_mode_does_not_call_install(self) -> None:
        from driftdriver.cli.self_update_cmd import cmd_self_update

        install_called = False

        def fake_cmd_install(args: argparse.Namespace) -> int:
            nonlocal install_called
            install_called = True
            return 0

        from driftdriver.upgrade.engine import RepoUpgradeReport

        def fake_apply_pending(repo_dir: Path, *, dry_run: bool = False):
            return RepoUpgradeReport(repo=str(repo_dir), dry_run=True)

        with (
            patch("driftdriver.cli.install_cmd.cmd_install", fake_cmd_install),
            patch("driftdriver.cli.self_update_cmd.apply_pending", fake_apply_pending),
        ):
            _suppress = io.StringIO()
            old = sys.stdout
            sys.stdout = _suppress
            try:
                cmd_self_update(self._make_args(Path("/tmp/fake-repo"), check=True))
            finally:
                sys.stdout = old

        self.assertFalse(install_called)

    def test_check_mode_calls_apply_pending_with_dry_run(self) -> None:
        from driftdriver.cli.self_update_cmd import cmd_self_update

        captured_dry_run = None

        def fake_apply_pending(repo_dir: Path, *, dry_run: bool = False):
            nonlocal captured_dry_run
            captured_dry_run = dry_run
            from driftdriver.upgrade.engine import RepoUpgradeReport
            return RepoUpgradeReport(repo=str(repo_dir), dry_run=dry_run)

        def fake_cmd_install(args: argparse.Namespace) -> int:
            return 0

        with (
            patch("driftdriver.cli.install_cmd.cmd_install", fake_cmd_install),
            patch("driftdriver.cli.self_update_cmd.apply_pending", fake_apply_pending),
        ):
            _suppress = io.StringIO()
            old = sys.stdout
            sys.stdout = _suppress
            try:
                cmd_self_update(self._make_args(Path("/tmp/fake-repo"), check=True))
            finally:
                sys.stdout = old

        self.assertTrue(captured_dry_run)

    def test_freshness_network_failure_produces_no_error(self) -> None:
        from driftdriver.cli.self_update_cmd import cmd_self_update

        from driftdriver.upgrade.engine import RepoUpgradeReport

        def fake_apply_pending(repo_dir: Path, *, dry_run: bool = False):
            return RepoUpgradeReport(repo=str(repo_dir), dry_run=dry_run)

        def fake_cmd_install(args: argparse.Namespace) -> int:
            return 0

        def fake_fetch(*a, **kw):
            raise RuntimeError("network error")

        captured = io.StringIO()
        old = sys.stdout
        old_err = sys.stderr
        sys.stdout = captured
        sys.stderr = io.StringIO()
        try:
            import driftdriver.updates as updates_mod

            with (
                patch("driftdriver.cli.install_cmd.cmd_install", fake_cmd_install),
                patch("driftdriver.cli.self_update_cmd.apply_pending", fake_apply_pending),
                patch.object(updates_mod, "fetch_github_head", fake_fetch),
            ):
                rc = cmd_self_update(self._make_args(Path("/tmp/fake-repo")))
        finally:
            sys.stdout = old
            sys.stderr = old_err

        # rc should be 0 (freshness failure is not an error)
        self.assertEqual(rc, 0)
        # No freshness line should appear in output
        self.assertNotIn("freshness", captured.getvalue().lower())

    def test_freshness_success_prints_sha_and_hint(self) -> None:
        from driftdriver.cli.self_update_cmd import cmd_self_update

        from driftdriver.upgrade.engine import RepoUpgradeReport

        def fake_apply_pending(repo_dir: Path, *, dry_run: bool = False):
            return RepoUpgradeReport(repo=str(repo_dir), dry_run=dry_run)

        def fake_cmd_install(args: argparse.Namespace) -> int:
            return 0

        def fake_fetch(*a, **kw):
            return ("abc123def456789", "2026-08-03T12:00:00Z")

        captured = io.StringIO()
        old = sys.stdout
        sys.stdout = captured
        try:
            import driftdriver.updates as updates_mod

            with (
                patch("driftdriver.cli.install_cmd.cmd_install", fake_cmd_install),
                patch("driftdriver.cli.self_update_cmd.apply_pending", fake_apply_pending),
                patch.object(updates_mod, "fetch_github_head", fake_fetch),
            ):
                rc = cmd_self_update(self._make_args(Path("/tmp/fake-repo")))
        finally:
            sys.stdout = old

        out = captured.getvalue()
        self.assertIn("abc123def4", out)  # short sha
        self.assertIn("uv tool install", out)

    def test_upgrade_applied_reported(self) -> None:
        from driftdriver.cli.self_update_cmd import cmd_self_update

        from driftdriver.upgrade.engine import RepoUpgradeReport

        def fake_apply_pending(repo_dir: Path, *, dry_run: bool = False):
            return RepoUpgradeReport(
                repo=str(repo_dir),
                ran=["001"],
                changed_files=[".workgraph/handlers/session-start.sh"],
            )

        def fake_cmd_install(args: argparse.Namespace) -> int:
            return 0

        captured = io.StringIO()
        old = sys.stdout
        sys.stdout = captured
        try:
            import driftdriver.updates as updates_mod

            with (
                patch("driftdriver.cli.install_cmd.cmd_install", fake_cmd_install),
                patch("driftdriver.cli.self_update_cmd.apply_pending", fake_apply_pending),
                patch.object(updates_mod, "fetch_github_head", lambda *a, **kw: ("sha", "date")),
            ):
                cmd_self_update(self._make_args(Path("/tmp/fake-repo")))
        finally:
            sys.stdout = old

        out = captured.getvalue()
        self.assertIn("001", out)
        self.assertIn("session-start.sh", out)


class TemplateConvergenceTests(unittest.TestCase):
    """The session-start template must not re-emit the decommissioned block."""

    TEMPLATE = Path(__file__).resolve().parents[1] / "driftdriver" / "templates" / "handlers" / "session-start.sh"

    def test_template_contains_upgrade_invocation(self) -> None:
        content = self.TEMPLATE.read_text()
        self.assertIn("upgrade", content)
        self.assertIn("self-update", content)

    def test_template_no_longer_contains_ecosystem_hub_autostart(self) -> None:
        content = self.TEMPLATE.read_text()
        self.assertNotIn("ECOSYSTEM_HUB_AUTOSTART", content)

    def test_template_no_longer_contains_ecosystem_marker(self) -> None:
        content = self.TEMPLATE.read_text()
        self.assertNotIn("Ensure ecosystem hub automation", content)

    def test_migration_001_block_no_longer_in_template(self) -> None:
        """The byte-exact block migration 001 strips must not appear."""
        import importlib.util

        mig_path = (
            Path(__file__).resolve().parents[1]
            / "driftdriver" / "upgrade" / "migrations"
            / "001_strip_ecosystem_hook.py"
        )
        spec = importlib.util.spec_from_file_location("mig_001", mig_path)
        assert spec and spec.loader
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        content = self.TEMPLATE.read_text()
        self.assertNotIn(mig._BLOCK.strip(), content)


if __name__ == "__main__":
    unittest.main()
