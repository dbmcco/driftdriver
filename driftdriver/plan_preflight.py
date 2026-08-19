# ABOUTME: All-or-nothing graph and scope preflight for planner batches —
# ABOUTME: deterministic, filesystem-free validation before any wg add.
"""Whole-plan preflight for Speedrift planner batches.

``preflight_plan`` validates the complete node list before publication:
ID presence and uniqueness, dependency closure (batch ids plus explicitly
supplied existing ids), self-dependencies, cycles, per-node contract
contradictions, and touch/creates scope coverage. It is deliberately
filesystem- and subprocess-free: findings derive only from the planned
nodes themselves, so provider, daemon, authentication, and rate-limit
conditions — which remain execution failures — can never surface as
contract findings here.
"""
from __future__ import annotations

import fnmatch
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePath

from driftdriver.contract_validator import ContractFinding, validate_node_contract
from driftdriver.planner_core import PlannedNode


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of a whole-plan preflight.

    ``ok`` is False exactly when ``findings`` is non-empty; any finding
    blocks publication of the entire batch.
    """

    ok: bool
    findings: list[ContractFinding]


# ---------------------------------------------------------------------------
# Shared path matcher
# ---------------------------------------------------------------------------


def path_matches(path: str, pattern: str) -> bool:
    """Return whether a repository-relative path is covered by a scope pattern.

    Supports exact paths and glob patterns. ``**`` crosses directory
    separators the same way ``*`` does in fnmatch, so ``src/**`` covers
    both ``src/evaluator.py`` and ``src/pkg/deep/mod.py``.
    """
    normalized_path = str(PurePath(path))
    normalized_pattern = pattern.replace("\\", "/").strip().rstrip("/")
    if not normalized_pattern:
        return False
    return normalized_path == normalized_pattern or fnmatch.fnmatchcase(
        normalized_path, normalized_pattern
    )


# ---------------------------------------------------------------------------
# Contract fence parsing (fail-closed)
# ---------------------------------------------------------------------------

_CONTRACT_OPEN_RE = re.compile(r"```wg-contract[ \t]*\r?\n")


def _parse_contract_description(
    description: str,
) -> tuple[dict[str, object] | None, str | None]:
    """Parse an optional ``wg-contract`` fence without failing open.

    Plain descriptions are not contracts. Once a contract fence is
    advertised, however, both its closing fence and its TOML document are
    mandatory so scope validation cannot silently degrade into an
    empty-scope check.
    """
    opening = _CONTRACT_OPEN_RE.search(description)
    if opening is None:
        return None, None
    closing = description.find("```", opening.end())
    if closing < 0:
        return None, "wg-contract fence is missing a closing fence"
    body = description[opening.end() : closing]
    try:
        parsed = tomllib.loads(body)
    except (tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        return None, f"wg-contract TOML is malformed: {exc}"
    if not isinstance(parsed, dict):
        return None, "wg-contract TOML must decode to a table"
    for key in ("touch", "creates"):
        if key in parsed and not isinstance(parsed[key], list):
            return None, f"wg-contract {key} must be a list"
    return parsed, None


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def _finding(
    category: str,
    node: PlannedNode,
    message: str,
    *,
    source: str = "graph",
    source_index: int = -1,
) -> ContractFinding:
    return ContractFinding(
        category=category,
        task_id=node.id if isinstance(node.id, str) else "",
        title=node.title,
        message=message,
        source=source,
        source_index=source_index,
    )


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def _cycle_findings(nodes: list[PlannedNode], node_ids: set[str]) -> list[ContractFinding]:
    """Find each dependency cycle once, in the order its first node appears.

    Self-dependencies are reported by the dependency checks and skipped
    here so a self-loop is never double-reported as a cycle.
    """
    by_id = {node.id: node for node in nodes}
    state: dict[str, int] = {}
    stack: list[str] = []
    findings: list[ContractFinding] = []
    emitted: set[frozenset[str]] = set()

    def visit(node_id: str) -> None:
        state[node_id] = 1
        stack.append(node_id)
        node = by_id[node_id]
        for dep_index, dependency in enumerate(node.after):
            if dependency not in node_ids or dependency == node_id:
                continue
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                members = stack[stack.index(dependency) :]
                member_set = frozenset(members)
                if member_set not in emitted:
                    emitted.add(member_set)
                    cycle = " -> ".join([*members, dependency])
                    findings.append(
                        _finding(
                            "dependency-cycle",
                            node,
                            f"Dependency cycle detected: {cycle}.",
                            source="after",
                            source_index=dep_index,
                        )
                    )
        stack.pop()
        state[node_id] = 2

    for node in nodes:
        if node.id in node_ids and state.get(node.id, 0) == 0:
            visit(node.id)
    return findings


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight_plan(
    nodes: list[PlannedNode],
    repo_path: Path,
    *,
    existing_ids: set[str] | None = None,
) -> PreflightResult:
    """Validate a complete plan without touching the filesystem or a subprocess.

    Checks run in a fixed order — ID presence/uniqueness, dependency
    closure, self-dependencies, cycles, per-node contracts, then
    touch/creates scope coverage — and findings stay stable in node order,
    then source order. ``repo_path`` flows to ``validate_node_contract``
    for its path-aware checks; no check here reads it.
    """
    findings: list[ContractFinding] = []
    seen_ids: set[str] = set()
    node_ids: set[str] = set()
    existing = existing_ids or set()

    # 1. ID validation is deliberately first so malformed graph keys cannot
    #    hide later dependency or contract findings.
    for node in nodes:
        if not isinstance(node.id, str) or not node.id.strip():
            findings.append(
                _finding("missing-task-id", node, "Node id must not be blank.")
            )
        elif node.id in seen_ids:
            findings.append(
                _finding(
                    "duplicate-task-id", node, f"Duplicate node id: {node.id}."
                )
            )
        else:
            seen_ids.add(node.id)
            node_ids.add(node.id)

    # 2-3. Dependencies may name batch siblings or — only when explicitly
    #      supplied — ids already present in the graph.
    allowed_ids = node_ids | existing
    for node in nodes:
        for dep_index, dependency in enumerate(node.after):
            if dependency == node.id:
                findings.append(
                    _finding(
                        "self-dependency",
                        node,
                        f"Node cannot depend on itself: {dependency}.",
                        source="after",
                        source_index=dep_index,
                    )
                )
            elif dependency not in allowed_ids:
                findings.append(
                    _finding(
                        "unknown-dependency",
                        node,
                        "Dependency is absent from this plan and existing ids: "
                        f"{dependency}.",
                        source="after",
                        source_index=dep_index,
                    )
                )

    # 4. Topological validation: one finding per distinct cycle.
    findings.extend(_cycle_findings(nodes, node_ids))

    # 5-6. Contract and scope findings stay in node order, then source
    #      order, after the graph findings above.
    for node in nodes:
        findings.extend(validate_node_contract(node, repo_path))

        description = node.description if isinstance(node.description, str) else ""
        contract, contract_error = _parse_contract_description(description)
        if contract_error:
            findings.append(
                _finding(
                    "malformed-contract", node, contract_error, source="description"
                )
            )
            continue
        declared_patterns: list[str] = []
        # Plain descriptions are not contracts: without an advertised fence
        # there is no declared scope to conflict with (canonical fences arrive
        # with structured verification). An advertised contract, however, is
        # checked fail-closed — including an empty declared scope.
        if contract is not None:
            for key in ("touch", "creates"):
                values = contract.get(key)
                if isinstance(values, list):
                    declared_patterns.extend(
                        str(value).strip() for value in values if str(value).strip()
                    )
            for path_index, required_path in enumerate(node.touch):
                if not isinstance(required_path, str):
                    continue
                if not any(path_matches(required_path, p) for p in declared_patterns):
                    findings.append(
                        _finding(
                            "scope-contract-conflict",
                            node,
                            (
                                f"Required path {required_path!r} is not covered by "
                                f"the contract touch/creates scope: {declared_patterns}."
                            ),
                            source="touch",
                            source_index=path_index,
                        )
                    )

    return PreflightResult(ok=not findings, findings=findings)
