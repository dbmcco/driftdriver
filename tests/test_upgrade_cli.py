# ABOUTME: Tests for the `driftdriver upgrade` CLI subcommand.
from __future__ import annotations

import argparse
import json
from pathlib import Path

from driftdriver.cli.upgrade_cmd import cmd_upgrade
from driftdriver.cli import _build_parser


def _ns(**kw) -> argparse.Namespace:
    base = dict(dir=None, json=False, dry_run=False, fleet=False, root=None)
    base.update(kw)
    return argparse.Namespace(**base)


def test_cmd_upgrade_single_repo_reports_up_to_date(tmp_path, capsys):
    rc = cmd_upgrade(_ns(dir=str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "up to date" in out or "no changes" in out


def test_cmd_upgrade_fleet_json_output(tmp_path, capsys):
    repo = tmp_path / "x"
    (repo / ".workgraph/handlers").mkdir(parents=True)
    (repo / ".workgraph/handlers/session-start.sh").write_text(
        "#!/usr/bin/env bash\nfi\n\n# Prime\n"
    )

    rc = cmd_upgrade(_ns(fleet=True, root=str(tmp_path), json=True))

    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["total"] == 1


def test_parser_registers_upgrade_subcommand():
    p = _build_parser()

    ns = p.parse_args(["upgrade", "--dry-run", "--fleet", "--root", "/tmp"])

    assert ns.cmd == "upgrade"
    assert ns.dry_run is True
    assert ns.fleet is True
    assert ns.root == "/tmp"
    assert callable(ns.func)


def test_parser_upgrade_help_text(tmp_path):
    p = _build_parser()
    # ensure 'upgrade' appears in help without error
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            p.parse_args(["upgrade", "--help"])
        except SystemExit:
            pass
    assert "upgrade" in buf.getvalue()
