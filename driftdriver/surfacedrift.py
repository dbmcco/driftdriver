"""surfacedrift — model-operable interface-compliance drift lane.

Layer 1 (this module): STATIC structural gate. It discovers classes that
declare themselves model-operable — via the ``@model_operable`` decorator or by
subclassing a ``ModelOperable*`` base — and validates they declare every field
in the contract's :data:`~driftdriver.contracts.REQUIRED_FIELDS` (stable error
code, message, expected, valid_examples, retryable, next_step). This is cheap,
deterministic, and needs no model calls and no imports of the target code — it
reads source via the AST, so there are no side effects.

Posture: ADVISORY (``exit_code`` always 0), mirroring modelrift. Interface gaps
surface as reviewable findings (severity warning+), not gate blocks — the same
shape as the planforge ``override_reason`` and the driftdriver ``--gate`` escape
hatch. Findings covered by a logged deviation in the model-mediated deviation
register are suppressed.

Discovery is explicit (marker/base-class), not route scanning. Scanning every
FastAPI route or MCP tool and guessing which are "model-facing" would itself be
a model-agency violation — code inferring intent. The marker makes opt-in owned
by the surface, exactly as the deviation register makes exceptions owned by the
author.

Layer 2 (deferred): Instructor-mediated LLM judgment of whether a surface's
guidance is *actually* adequate — valid examples, genuinely helpful error text,
useful next steps. Semantic quality that structure cannot decide and code must
not hardcode. Reuses the model route registry surfacedrift shares with uxdrift.
"""
from __future__ import annotations

import ast
from pathlib import Path

from driftdriver._lanecommon import covered, load_deviations, read_py_source, walk_py_files
from driftdriver.contracts import MARKER_BASE_PREFIX, MARKER_NAME, REQUIRED_FIELDS
from driftdriver.lane_contract import LaneFinding, LaneResult

LANE = "surfacedrift"
INTERFACE_TAG = "interface-violation"


def run_as_lane(project_dir: Path) -> LaneResult:
    """Validate model-operable surfaces in ``project_dir`` against the contract.

    Returns an advisory ``LaneResult`` (exit_code 0). Findings suppressed where a
    logged deviation covers the class location.
    """
    project_dir = Path(project_dir)
    deviations = load_deviations(project_dir)
    findings: list[LaneFinding] = []

    for rel_path, lineno, cls_name, declared in _iter_marked_classes(project_dir):
        missing = [f for f in REQUIRED_FIELDS if f not in declared]
        if not missing:
            continue
        if covered(rel_path, lineno, deviations):
            continue
        findings.append(LaneFinding(
            message=(
                f"Interface violation: model-operable surface '{cls_name}' is "
                f"missing required contract field(s): {', '.join(missing)}. "
                f"A model-facing error surface must carry a stable error code, "
                f"message, expected, valid_examples, retryable, and next_step "
                f"(see model-mediated-development skill). If this is an "
                f"intentional deviation, log it in the deviation register."
            ),
            severity="warning",
            file=rel_path,
            line=lineno,
            tags=[INTERFACE_TAG, "missing-fields"],
        ))

    return LaneResult(
        lane=LANE,
        findings=findings,
        exit_code=0,  # advisory: never blocks the gate
        summary=f"{len(findings)} model-operable interface gap(s)"
        if findings
        else "no model-operable interface gaps detected",
    )


# --- discovery (static AST) ----------------------------------------------------

def _iter_marked_classes(project_dir: Path):
    """Yield (rel_path, lineno, class_name, declared_field_names set) for each
    class marked model-operable (decorator or ModelOperable* base)."""
    for path in walk_py_files(project_dir):
        text = read_py_source(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(project_dir).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_marked(node):
                yield rel, node.lineno, node.name, _declared_fields(node)


def _is_marked(cls_node: ast.ClassDef) -> bool:
    """True if the class carries the model-operable marker.

    Recognized: a decorator named ``model_operable`` (bare, dotted, or call
    form), or any base class whose name starts with ``ModelOperable``.
    """
    for dec in cls_node.decorator_list:
        if _dotted_name(dec) == MARKER_NAME or _attr_name(dec) == MARKER_NAME:
            return True
    for base in cls_node.bases:
        bn = _dotted_name(base) or _attr_name(base)
        if bn and bn.startswith(MARKER_BASE_PREFIX):
            return True
    return False


def _dotted_name(node) -> str | None:
    """Fully-qualified name for a Name/Attribute node, else None."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return None


def _attr_name(node) -> str | None:
    """Rightmost attribute name for Name/Attribute (or for a Call wrapping one)."""
    if isinstance(node, ast.Call):
        return _attr_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _declared_fields(cls_node: ast.ClassDef) -> set[str]:
    """Field names declared in the class body: annotated names plus simple
    assignment targets (defaults). Covers pydantic-style ``x: T`` / ``x: T = v``
    and plain ``x = v`` defaults."""
    fields: set[str] = set()
    for stmt in cls_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            fields.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name):
                    fields.add(tgt.id)
    return fields


if __name__ == "__main__":  # pragma: no cover - CLI entry
    import json
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    result = run_as_lane(target)
    print(json.dumps({
        "lane": result.lane,
        "findings": [
            {"message": f.message, "severity": f.severity, "file": f.file,
             "line": f.line, "tags": list(f.tags)}
            for f in result.findings
        ],
        "exit_code": result.exit_code,
        "summary": result.summary,
    }, indent=2))
    sys.exit(result.exit_code)
