# ABOUTME: Tests for the attractor CLI subcommand.
# ABOUTME: Covers status, list, plan, and set commands.

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from driftdriver.cli import main


def test_attractor_list(capsys):
    """List should show built-in attractor IDs."""
    with patch("sys.argv", ["driftdriver", "attractor", "list"]):
        try:
            main()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    assert "onboarded" in out
    assert "production-ready" in out
    assert "hardened" in out


def test_attractor_list_json(capsys):
    """List --json should return parseable JSON with attractor entries."""
    with patch("sys.argv", ["driftdriver", "attractor", "list", "--json"]):
        try:
            main()
        except SystemExit:
            pass
    out = capsys.readouterr().out
    data = json.loads(out)
    ids = {entry["id"] for entry in data}
    assert "onboarded" in ids
    assert "production-ready" in ids


def test_attractor_status_no_workgraph(capsys):
    """Status in a dir with no .workgraph should report no target and no run."""
    with TemporaryDirectory() as tmp:
        with patch("sys.argv", ["driftdriver", "--dir", tmp, "attractor", "status", "--json"]):
            try:
                main()
            except SystemExit:
                pass
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["configured_target"] == ""
    assert data["last_run"] is None


def test_attractor_status_with_policy(capsys):
    """Status should read the configured target from drift-policy.toml."""
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        policy = wg_dir / "drift-policy.toml"
        policy.write_text(
            'schema = 1\nmode = "redirect"\norder = ["coredrift"]\n\n'
            '[attractor]\ntarget = "production-ready"\n',
            encoding="utf-8",
        )
        with patch("sys.argv", ["driftdriver", "--dir", tmp, "attractor", "status", "--json"]):
            try:
                main()
            except SystemExit:
                pass
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["configured_target"] == "production-ready"


def test_attractor_set(capsys):
    """Set should write the attractor target into drift-policy.toml."""
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        policy = wg_dir / "drift-policy.toml"
        policy.write_text(
            'schema = 1\nmode = "redirect"\norder = ["coredrift"]\n',
            encoding="utf-8",
        )
        with patch("sys.argv", ["driftdriver", "--dir", tmp, "attractor", "set", "production-ready"]):
            try:
                main()
            except SystemExit:
                pass
        out = capsys.readouterr().out
        assert "production-ready" in out
        content = policy.read_text(encoding="utf-8")
        assert 'target = "production-ready"' in content


def test_attractor_set_missing_target(capsys):
    """Set without a target name should error."""
    with TemporaryDirectory() as tmp:
        ret = main(["--dir", tmp, "attractor", "set"])
    assert ret == 1


def test_attractor_set_unknown_target(capsys):
    """Set with a non-existent attractor should error."""
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir()
        policy = wg_dir / "drift-policy.toml"
        policy.write_text('schema = 1\nmode = "redirect"\n', encoding="utf-8")
        ret = main(["--dir", tmp, "attractor", "set", "nonexistent-attractor"])
    assert ret == 1


def test_attractor_status_with_current_run(capsys):
    """Status should display data from current-run.json when it exists."""
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp) / ".workgraph"
        attractor_dir = wg_dir / "service" / "attractor"
        attractor_dir.mkdir(parents=True)
        run_data = {
            "repo": "test-repo",
            "attractor": "onboarded",
            "status": "converged",
            "passes": [{"pass_number": 0, "findings_before": 3, "findings_after": 0}],
            "escalation_count": 0,
        }
        (attractor_dir / "current-run.json").write_text(
            json.dumps(run_data), encoding="utf-8"
        )
        policy = wg_dir / "drift-policy.toml"
        policy.write_text(
            'schema = 1\nmode = "redirect"\n\n[attractor]\ntarget = "onboarded"\n',
            encoding="utf-8",
        )
        with patch("sys.argv", ["driftdriver", "--dir", tmp, "attractor", "status", "--json"]):
            try:
                main()
            except SystemExit:
                pass
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["configured_target"] == "onboarded"
    assert data["last_run"]["status"] == "converged"


def test_policy_template_has_attractor_section():
    """The default policy template should include the [attractor] section."""
    from driftdriver.policy import _default_policy_text
    text = _default_policy_text()
    assert "[attractor]" in text
    assert 'target = "onboarded"' in text
    assert "[attractor.breakers]" in text
    assert "max_passes = 3" in text
    assert "plateau_threshold = 2" in text


def test_ensure_drift_policy_includes_attractor():
    """ensure_drift_policy should write a file containing the attractor section."""
    from driftdriver.policy import ensure_drift_policy
    with TemporaryDirectory() as tmp:
        wg_dir = Path(tmp)
        ensure_drift_policy(wg_dir)
        content = (wg_dir / "drift-policy.toml").read_text(encoding="utf-8")
        assert "[attractor]" in content
        assert 'target = "onboarded"' in content
