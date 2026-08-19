# ABOUTME: Contract assertion normalization for planned Speedrift nodes —
# ABOUTME: bounded, deterministic patterns only; no fuzzy synonyms, no models.
"""Bounded contract-assertion normalization for planned Speedrift nodes.

The vocabulary normalizes only mechanically recognizable assertions: class
declarations and contains/no-occurrence assertions (``symbol``), import
success/failure, AST present/absent, and backticked command pass/fail.

Absent-symbol vocabulary — hard precondition: only contains/no-occurrence
phrasings normalize as ``symbol`` absent assertions — ``must (not) contain
[no] X``, ``forbid every occurrence of X``, ``contains no occurrence of X``.
Weaker phrasings that merely mention, reference, or use a symbol ("The tests
must not reference X", "no mentions of X") are NOT normalized: scoped to a
subset of the artifact they are satisfiable alongside a class declaration,
so treating them as absent would reject provably satisfiable contracts.

File-scope assumption — hard precondition: the patterns cannot parse scope
qualifiers, so any contains/no-occurrence phrasing — including an unscoped
bare "must not contain X" — is read as a whole-artifact prohibition: ANY
delivered file containing an occurrence of the symbol violates it. That
whole-artifact reading is what makes it provably contradictory with a class
declaration in the same task. Callers must treat every normalized
absent-symbol assertion as carrying this reading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from driftdriver.planner_core import PlannedNode


# ---------------------------------------------------------------------------
# Normalized contract surface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedAssertion:
    """One mechanically recognized contract assertion from a planned node.

    ``kind`` is one of ``symbol``, ``import``, ``ast``, or ``command``.
    ``polarity`` is kind-specific (``present``/``absent`` for symbol and ast,
    ``succeed``/``fail`` for import, ``pass``/``fail`` for command).
    ``source`` names the node field (``description``, ``verify``,
    ``acceptance``); ``source_index`` is the character offset for string
    fields and the entry position for ``acceptance``.
    """

    kind: str
    subject: str
    polarity: str
    source: str
    source_index: int


@dataclass(frozen=True)
class ContractFinding:
    """A validation finding for one planned node contract."""

    category: str
    task_id: str
    title: str
    message: str
    source: str
    source_index: int = -1
    related_source: str = ""
    related_index: int = -1
    severity: str = "error"


# ---------------------------------------------------------------------------
# Bounded assertion vocabulary
# ---------------------------------------------------------------------------

# Symbol subjects (classes, AST names) are capitalized identifiers; the scoped
# (?-i:...) group keeps that restriction while the surrounding phrasing
# matches case-insensitively (e.g. sentence-initial "Define class Evaluator").
_SYMBOL = r"(?-i:[A-Z_][A-Za-z0-9_]*)"
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"

# (pattern, kind, polarity) — absent patterns run before present patterns so
# the more specific negated phrasings win when both could match.
_ASSERTION_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # symbol must be absent (no occurrence anywhere, including comments/strings)
    (re.compile(
        rf"\bmust\s+(?:contain|include)\s+(?:no|zero)\s+"
        rf"occurrences?\s+(?:of|to)\s+(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "absent"),
    (re.compile(
        rf"\bmust\s+not\s+(?:contain|include)\s+"
        rf"(?:any\s+|every\s+|all\s+)?"
        rf"(?:occurrences?\s+(?:of|to)\s+)?(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "absent"),
    (re.compile(
        rf"\b(?:forbid|forbids|forbidden)\s+(?:every|any|all|the)?\s*"
        rf"occurrences?\s+(?:of|to)\s+(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "absent"),
    (re.compile(
        rf"\b(?:have|has|contains?|includes?)\s+no\s+"
        rf"occurrences?\s+(?:of|to)\s+(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "absent"),
    # symbol must be present (class declaration, or contains assertion)
    (re.compile(
        rf"\b(?:defines?|defined|defining|creates?|created|creating|declares?|declared|declaring|"
        rf"adds?|added|adding|implements?|implemented|implementing)\s+"
        rf"(?:an?\s+|the\s+)?(?:new\s+)?class\s+(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "present"),
    (re.compile(
        rf"\bmust\s+(?:contain|include)\s+(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "present"),
    (re.compile(
        rf"\bmust\s+(?:contain|include)\s+(?:an?\s+|the\s+)?"
        rf"(?:occurrences?|mentions?|references?)\s+(?:of|to)\s+(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "symbol", "present"),
    # import must succeed
    (re.compile(
        rf"\b(?:imports?\s+of|importing|imports?)\s+(?P<subject>{_IDENTIFIER})\b"
        rf"[^.;\n]{{0,30}}?\bsucceeds?\b",
        re.IGNORECASE,
    ), "import", "succeed"),
    (re.compile(
        rf"\b(?:imports?\s+of|importing|imports?)\s+(?P<subject>{_IDENTIFIER})\b\s+successfully\b",
        re.IGNORECASE,
    ), "import", "succeed"),
    (re.compile(
        rf"\b(?:imports?\s+of|importing|imports?)\s+(?P<subject>{_IDENTIFIER})\b"
        rf"[^.;\n]{{0,30}}?\bmust\s+be\s+importable\b",
        re.IGNORECASE,
    ), "import", "succeed"),
    # import must fail
    (re.compile(
        rf"\b(?:imports?\s+of|importing|imports?)\s+(?P<subject>{_IDENTIFIER})\b"
        rf"[^.;\n]{{0,30}}?\b(?:must\s+fail|fails)\b",
        re.IGNORECASE,
    ), "import", "fail"),
    (re.compile(
        rf"\b(?:imports?\s+of|importing|imports?)\s+(?P<subject>{_IDENTIFIER})\b"
        rf"[^.;\n]{{0,30}}?\bmust\s+not\s+(?:succeed|be\s+importable|work)\b",
        re.IGNORECASE,
    ), "import", "fail"),
    # AST symbol must be absent — the present patterns cannot cross a "not"
    (re.compile(
        rf"\bast\b[^.\n]{{0,80}}?\bmust\s+not\s+(?:contain|include|define|have)\b"
        rf"[^.\n]{{0,80}}?\b(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "ast", "absent"),
    (re.compile(
        rf"\bast\b[^.\n]{{0,80}}?\b(?:must\s+)?(?:contains?|has)\s+no\b"
        rf"[^.\n]{{0,80}}?\b(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "ast", "absent"),
    # AST symbol must be present
    (re.compile(
        rf"\bast\b(?:(?!not)[^.\n]){{0,80}}?\b(?:must\s+|should\s+)?"
        rf"(?:contains?|includes?|defines?|has)\b(?:(?!not)[^.\n]){{0,80}}?\b(?P<subject>{_SYMBOL})\b",
        re.IGNORECASE,
    ), "ast", "present"),
)

# A backticked command followed by an explicit pass/fail polarity.
_COMMAND_ASSERTION_RE = re.compile(r"`(?P<subject>[^`\n]{1,200})`\s+must\s+(?P<polarity>pass|fail)\b")

# A command pass/fail requirement whose command cannot be resolved (no
# backticked command between "command" and the polarity).
_MALFORMED_COMMAND_RE = re.compile(r"\b(?:the|this)\s+command\b[^.`\n]{0,100}\bmust\s+(?:pass|fail)\b", re.IGNORECASE)

# kind -> the two mutually exclusive polarity values for that kind
_OPPOSITE_POLARITIES: dict[str, tuple[str, str]] = {
    "symbol": ("present", "absent"),
    "import": ("succeed", "fail"),
    "ast": ("present", "absent"),
    "command": ("pass", "fail"),
}

_POLARITY_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("symbol", "present"): "be present",
    ("symbol", "absent"): "have no occurrence",
    ("import", "succeed"): "import successfully",
    ("import", "fail"): "fail to import",
    ("ast", "present"): "be present in the AST",
    ("ast", "absent"): "be absent from the AST",
    ("command", "pass"): "pass",
    ("command", "fail"): "fail",
}


def _scan_text(text: str, source: str, base_index: int = 0) -> list[NormalizedAssertion]:
    """Normalize every bounded assertion found in one source string.

    ``base_index`` is added to each match's character offset (0 for the
    description/verify strings). Acceptance entries override the index with
    their entry position in ``normalize_node_assertions``.
    """
    matches: list[tuple[int, int, str, str, str]] = []
    for pattern, kind, polarity in _ASSERTION_PATTERNS:
        for m in pattern.finditer(text):
            matches.append((m.start(), m.end(), kind, m.group("subject").strip(), polarity))
    for m in _COMMAND_ASSERTION_RE.finditer(text):
        matches.append((m.start(), m.end(), "command", m.group("subject").strip(), m.group("polarity")))
    # Deduplicate: drop exact repeats, then skip matches whose span overlaps an
    # already-accepted match (a longer phrasing and its fragment, e.g.
    # "must contain no occurrence of X" vs "contain no occurrence of X").
    accepted: list[tuple[int, int, str, str, str]] = []
    spans: list[tuple[int, int]] = []
    for start, end, kind, subject, polarity in sorted(set(matches)):
        if any(start < e and s < end for s, e in spans):
            continue
        accepted.append((start, end, kind, subject, polarity))
        spans.append((start, end))
    return [
        NormalizedAssertion(kind=kind, subject=subject, polarity=polarity, source=source,
                            source_index=base_index + start)
        for start, _end, kind, subject, polarity in accepted
    ]


def normalize_node_assertions(node: PlannedNode) -> list[NormalizedAssertion]:
    """Normalize the bounded assertions in a node's description, verify, and acceptance.

    ``source_index`` is the character offset of the assertion for the
    description and verify strings, and the entry position for acceptance.
    """
    assertions: list[NormalizedAssertion] = []
    if isinstance(node.description, str) and node.description:
        assertions.extend(_scan_text(node.description, "description"))
    if isinstance(node.verify, str) and node.verify:
        assertions.extend(_scan_text(node.verify, "verify"))
    if isinstance(node.acceptance, list):
        for index, entry in enumerate(node.acceptance):
            if isinstance(entry, str) and entry:
                assertions.extend(
                    replace(a, source_index=index)
                    for a in _scan_text(entry, "acceptance")
                )
    return assertions


def _malformed_findings(node: PlannedNode) -> list[ContractFinding]:
    """Report command contracts that advertise pass/fail but cannot be resolved."""
    findings: list[ContractFinding] = []
    sources: list[tuple[str, str, int, bool]] = []
    if isinstance(node.description, str) and node.description:
        sources.append((node.description, "description", 0, True))
    if isinstance(node.verify, str) and node.verify:
        sources.append((node.verify, "verify", 0, True))
    if isinstance(node.acceptance, list):
        for index, entry in enumerate(node.acceptance):
            if isinstance(entry, str) and entry:
                sources.append((entry, "acceptance", index, False))
    for text, source, base_index, offset_indexed in sources:
        for m in _MALFORMED_COMMAND_RE.finditer(text):
            source_index = base_index + m.start() if offset_indexed else base_index
            findings.append(ContractFinding(
                category="malformed-contract",
                task_id=node.id,
                title=node.title,
                message=(
                    f"Command contract in {source} is malformed: "
                    f"'{m.group(0)}' requires a command to pass or fail but no "
                    f"backticked command is given."
                ),
                source=source,
                source_index=source_index,
            ))
    return findings


def validate_node_contract(node: PlannedNode, repo_path: Path) -> list[ContractFinding]:
    """Validate one planned node's contract for mechanically provable contradictions.

    Only bounded, deterministic assertion patterns are normalized (see
    ``_ASSERTION_PATTERNS``); assertions are paired by ``(kind, subject)`` and
    one ``contract-contradiction`` finding is emitted when both polarities of a
    kind apply to the same subject. Unresolvable command pass/fail phrases and
    a non-string ``verify`` field yield ``malformed-contract`` findings.
    ``repo_path`` is accepted for path-aware checks in later tasks and is not
    used by the textual checks here.
    """
    findings: list[ContractFinding] = _malformed_findings(node)

    if not isinstance(node.verify, str):
        findings.append(ContractFinding(
            category="malformed-contract",
            task_id=node.id,
            title=node.title,
            message=(
                "verify field must be a string, got "
                f"{type(node.verify).__name__}."
            ),
            source="verify",
        ))

    groups: dict[tuple[str, str], dict[str, NormalizedAssertion]] = {}
    for assertion in normalize_node_assertions(node):
        group = groups.setdefault((assertion.kind, assertion.subject), {})
        group.setdefault(assertion.polarity, assertion)

    for (kind, subject), by_polarity in groups.items():
        first, second = _OPPOSITE_POLARITIES[kind]
        if first in by_polarity and second in by_polarity:
            a = by_polarity[first]
            b = by_polarity[second]
            earlier, later = sorted((a, b), key=lambda x: x.source_index)
            findings.append(ContractFinding(
                category="contract-contradiction",
                task_id=node.id,
                title=node.title,
                message=(
                    f"Contradictory contract: {kind} '{subject}' must both "
                    f"{_POLARITY_DESCRIPTIONS[(kind, a.polarity)]} and "
                    f"{_POLARITY_DESCRIPTIONS[(kind, b.polarity)]} "
                    f"({a.source}[{a.source_index}] vs {b.source}[{b.source_index}])."
                ),
                source=earlier.source,
                source_index=earlier.source_index,
                related_source=later.source,
                related_index=later.source_index,
            ))
    return findings
