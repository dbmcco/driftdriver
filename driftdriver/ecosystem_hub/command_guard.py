# ABOUTME: Allowlisted command boundary for the ecosystem chat agent.
# ABOUTME: Replaces arbitrary `bash -c` execution. Enforces dispatch authority for
# ABOUTME: wg service/spawn/claim. Preserves read-only diagnostic commands.
# ABOUTME: Pure-stdlib and independently testable (no Anthropic/network deps).

"""Command boundary for :class:`driftdriver.ecosystem_hub.chat_agent.EcosystemAgent`.

The ecosystem chat agent historically ran ``run_command`` through
``bash -c <arbitrary>``. This module replaces that with a deterministic
allowlist boundary:

* only a small set of binaries may be invoked at all (``ALLOWED_BINARIES``);
* shell-control metacharacters are rejected up front (no chaining/redirects);
* commands are parsed into an explicit argv and executed *without* a shell;
* ``wg service``/``wg spawn``/``wg claim`` (dispatch verbs) are structurally
  allowed but flagged ``requires_authority=True`` so the caller must confirm
  lease-gated dispatch authority before executing them;
* read-only diagnostics (``wg list``, ``git status``, ``driftdriver ... status``,
  ``gh pr list`` ...) are preserved;
* everything else is rejected — the boundary fails closed.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

__all__ = [
    "ALLOWED_BINARIES",
    "DISPATCH_WG_SUBCOMMANDS",
    "CommandDecision",
    "classify",
]


# Binaries that may be invoked at all. Anything else is rejected outright.
# Deliberately minimal: no shells, no interpreters, no network/file-destructive tools.
ALLOWED_BINARIES: frozenset[str] = frozenset(
    {
        "wg",
        "driftdriver",
        "git",
        "gh",
        "echo",
    }
)

# Global flags (per binary) that consume the following token as their value and
# therefore must be skipped when locating the first positional subcommand.
_VALUE_FLAGS: frozenset[str] = frozenset({"--dir", "-d", "-C"})

# Shell-control metacharacters that would allow chaining, redirection, or
# substitution past the boundary. Rejected as substrings of the raw command.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    ";",
    "&",
    "|",
    "$",
    "`",
    "(",
    ")",
    "<",
    ">",
    "\n",
    "\r",
)

# wg subcommands that mutate runtime by spawning/claiming work or managing the
# service daemon. These require lease-gated dispatch authority before execution.
# The contract pins this set to exactly service/spawn/claim.
DISPATCH_WG_SUBCOMMANDS: frozenset[str] = frozenset({"service", "spawn", "claim"})

# wg subcommands that are safe read-only / coordination diagnostics.
_WG_DIAGNOSTIC: frozenset[str] = frozenset(
    {
        "list",
        "show",
        "status",
        "ready",
        "log",
        "help",
        "inbox",
        "outbox",
    }
)

# wg subcommands that perform benign task-lifecycle writes (not dispatch).
_WG_BENIGN_WRITE: frozenset[str] = frozenset(
    {
        "add",
        "done",
        "fail",
        "assign",
    }
)

# git read-only diagnostics.
_GIT_DIAGNOSTIC: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "branch",
        "remote",
        "rev-parse",
        "blame",
        "describe",
        "shortlog",
        "name-rev",
    }
)

# Flags that grant write/exec capability under otherwise-read git verbs. Even
# on ``git show``/``git diff``/``git log`` these can write arbitrary files
# (``--output``/``-o``) or execute arbitrary commands (``--ext-diff``, ``-c``),
# so they are rejected for any git diagnostic.
_GIT_DANGEROUS_FLAGS: tuple[str, ...] = (
    "--output",
    "-o",
    "--ext-diff",
    "-c",
    "--config-env",
    "-e",
)

# gh (subcommand, sub-subcommand) read-only pairs.
_GH_READ: frozenset[tuple[str, str]] = frozenset(
    {
        ("issue", "list"),
        ("issue", "view"),
        ("issue", "status"),
        ("pr", "list"),
        ("pr", "view"),
        ("pr", "status"),
        ("pr", "checks"),
        ("pr", "diff"),
        ("run", "list"),
        ("run", "view"),
        ("repo", "view"),
        ("release", "list"),
        ("release", "view"),
    }
)

# driftdriver read-only diagnostics (first positional subcommand).
_DRIFTS_DIAGNOSTIC: frozenset[str] = frozenset(
    {
        "check",
        "tmux-monitor",
        "upstream-tracker",
        "updates",
        "doctor",
        "ready",
        "quality",
        "report",
        "scope-check",
        "peer-list",
        "peer-health",
        "health-workers",
        "brain-status",
        "brain-roster",
        "brain-log",
        "factory-report",
        "intent",
        "decisions",
        "llm-spend",
        "model-route-audit",
        "presence",
        "profile",
    }
)


@dataclass(frozen=True)
class CommandDecision:
    """Outcome of classifying a command against the boundary.

    ``allowed`` is the structural verdict. ``requires_authority`` is only
    meaningful when ``allowed`` is True and signals that the caller must still
    confirm lease-gated dispatch authority before executing ``argv``.
    """

    allowed: bool
    requires_authority: bool
    reason: str
    argv: tuple[str, ...]
    binary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "requires_authority": self.requires_authority,
            "reason": self.reason,
            "binary": self.binary,
            "argv": list(self.argv),
        }


def _first_subcommand(argv: tuple[str, ...]) -> str:
    """Return the first positional token after the binary, skipping global flags.

    Handles ``--dir <val>``/``-d <val>``/``-C <val>`` (value flags), attached
    forms like ``--dir=<val>``/``-d<val>``, and boolean global flags (``--json``).
    """
    rest = argv[1:]
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in _VALUE_FLAGS:
            i += 2  # consume flag + its value
            continue
        if tok.startswith("--"):
            i += 1  # boolean global flag or --flag=value (self-contained)
            continue
        if tok.startswith("-") and tok != "-":
            i += 1  # short flag (possibly attached value); no separate value token
            continue
        return tok
    return ""


def _has_flag(argv: tuple[str, ...], names: tuple[str, ...]) -> bool:
    """True if any token is one of ``names`` or an attached ``name=value`` form.

    CLIs accept both ``--flag value`` and ``--flag=value``; matching must cover
    the attached form or mode-mutation flags (e.g. ``--set-mode=autonomous``)
    would slip past an exact-token check.
    """
    for tok in argv:
        for name in names:
            if tok == name or tok.startswith(name + "="):
                return True
    return False


def _classify_wg(argv: tuple[str, ...]) -> CommandDecision:
    sub = _first_subcommand(argv)
    binary = "wg"
    if sub in DISPATCH_WG_SUBCOMMANDS:
        return CommandDecision(
            True,
            True,
            f"wg {sub} is a dispatch verb — requires lease-gated dispatch authority",
            argv,
            binary,
        )
    if sub in _WG_DIAGNOSTIC:
        return CommandDecision(True, False, "wg diagnostic command", argv, binary)
    if sub in _WG_BENIGN_WRITE:
        return CommandDecision(True, False, "wg benign task-lifecycle command", argv, binary)
    return CommandDecision(
        False,
        False,
        f"wg subcommand not allowlisted: {sub!r}",
        argv,
        binary,
    )


def _classify_git(argv: tuple[str, ...]) -> CommandDecision:
    binary = "git"
    sub = _first_subcommand(argv)
    if sub in _GIT_DIAGNOSTIC:
        if _has_flag(argv, _GIT_DANGEROUS_FLAGS):
            return CommandDecision(
                False,
                False,
                "git diagnostic carries a write/exec-capable flag",
                argv,
                binary,
            )
        return CommandDecision(True, False, "git read-only diagnostic", argv, binary)
    return CommandDecision(
        False,
        False,
        f"git subcommand not allowlisted: {sub!r}",
        argv,
        binary,
    )


def _classify_gh(argv: tuple[str, ...]) -> CommandDecision:
    binary = "gh"
    sub = _first_subcommand(argv)
    # Require an explicit read sub-subcommand; bare `gh <sub>` is rejected.
    subsub = ""
    for tok in argv[2:]:
        if tok in _VALUE_FLAGS:
            continue
        if tok.startswith("-"):
            continue
        subsub = tok
        break
    if sub and subsub and (sub, subsub) in _GH_READ:
        return CommandDecision(True, False, "gh read-only diagnostic", argv, binary)
    return CommandDecision(
        False,
        False,
        f"gh subcommand not allowlisted: {sub!r} {subsub!r}".strip(),
        argv,
        binary,
    )


def _classify_driftdriver(argv: tuple[str, ...]) -> CommandDecision:
    binary = "driftdriver"
    sub = _first_subcommand(argv)
    if sub == "speedriftd":
        # `speedriftd status` (read) is diagnostic; mode mutations route to
        # the dedicated arm_repo/disarm_repo tools.
        if _has_flag(argv, ("--set-mode", "--release-lease")):
            return CommandDecision(
                False,
                False,
                "driftdriver speedriftd mode mutation rejected — use arm_repo/disarm_repo",
                argv,
                binary,
            )
        return CommandDecision(True, False, "driftdriver speedriftd status diagnostic", argv, binary)
    if sub == "attractor":
        if "run" in argv:
            return CommandDecision(
                False,
                False,
                "driftdriver attractor run rejected — use run_attractor",
                argv,
                binary,
            )
        return CommandDecision(True, False, "driftdriver attractor status diagnostic", argv, binary)
    if sub in _DRIFTS_DIAGNOSTIC:
        return CommandDecision(True, False, f"driftdriver {sub} diagnostic", argv, binary)
    return CommandDecision(
        False,
        False,
        f"driftdriver subcommand not allowlisted: {sub!r}",
        argv,
        binary,
    )


def classify(command: str) -> CommandDecision:
    """Parse and classify ``command`` against the allowlist boundary.

    Never raises for boundary violations; callers inspect the returned
    :class:`CommandDecision`. Returns ``allowed=False`` for anything outside
    the boundary (empty, unparseable, shell-control chars, unknown binary, or
    an unrecognized subcommand of an allowlisted binary).
    """
    if command is None or not command.strip():
        return CommandDecision(False, False, "empty command", (), "")

    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in command:
            return CommandDecision(
                False,
                False,
                f"command boundary rejects shell metacharacter: {needle!r}",
                (),
                "",
            )

    try:
        argv = tuple(shlex.split(command, posix=True))
    except ValueError as exc:
        return CommandDecision(False, False, f"unparseable command: {exc}", (), "")

    if not argv:
        return CommandDecision(False, False, "empty command after parse", (), "")

    binary = argv[0]
    if binary not in ALLOWED_BINARIES:
        return CommandDecision(
            False,
            False,
            f"binary not allowlisted: {binary!r}",
            argv,
            binary,
        )

    if binary == "wg":
        return _classify_wg(argv)
    if binary == "git":
        return _classify_git(argv)
    if binary == "gh":
        return _classify_gh(argv)
    if binary == "driftdriver":
        return _classify_driftdriver(argv)
    if binary == "echo":
        return CommandDecision(True, False, "echo diagnostic", argv, binary)

    # Defensive: allowlisted binary with no classifier (should not happen).
    return CommandDecision(
        False,
        False,
        f"no classifier for allowlisted binary: {binary!r}",
        argv,
        binary,
    )
