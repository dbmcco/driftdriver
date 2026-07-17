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

Layer 2 (implemented, opt-in): for surfaces that pass Layer 1, an
Instructor-mediated model judges whether the *guidance* is actually adequate —
valid examples, genuinely helpful error text, useful next steps. Semantic
quality that structure cannot decide and code must not hardcode. Reuses the
model route registry (``route_for('surfacedrift')``). Dormant unless armed via
``DRIFTDRIVER_SURFACEDRIFT_LAYER2`` and degrades gracefully when
instructor/openai/a route/an API key is unavailable, so it never breaks the
cheap structural gate.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

from driftdriver._lanecommon import covered, load_deviations, read_py_source, walk_py_files
from driftdriver.contracts import MARKER_BASE_PREFIX, MARKER_NAME, REQUIRED_FIELDS
from driftdriver.lane_contract import LaneFinding, LaneResult

LANE = "surfacedrift"
INTERFACE_TAG = "interface-violation"


def run_as_lane(project_dir: Path) -> LaneResult:
    """Validate model-operable surfaces in ``project_dir`` against the contract.

    Layer 1 (always on, static): marked classes missing required contract
    fields surface as ``missing-fields`` findings.

    Layer 2 (opt-in via ``DRIFTDRIVER_SURFACEDRIFT_LAYER2``): for structurally
    complete surfaces, an Instructor-mediated model judges whether the guidance
    is *adequate*, surfacing ``inadequate-guidance`` findings. Dormant unless
    enabled AND instructor/openai/a model route are available; degrades
    gracefully at every failure point.

    Returns an advisory ``LaneResult`` (exit_code 0). Findings suppressed where a
    logged deviation covers the class location.
    """
    project_dir = Path(project_dir)
    deviations = load_deviations(project_dir)
    findings: list[LaneFinding] = []
    layer2_candidates: list[tuple[str, int, str, str]] = []

    for rel_path, lineno, cls_name, declared, class_source in _iter_marked_classes(project_dir):
        missing = [f for f in REQUIRED_FIELDS if f not in declared]
        if missing:
            if not covered(rel_path, lineno, deviations):
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
            continue
        # Structurally complete — a Layer 2 candidate if Layer 2 is armed.
        layer2_candidates.append((rel_path, lineno, cls_name, class_source))

    if _layer2_enabled():
        findings.extend(_run_layer2(layer2_candidates, deviations))

    return LaneResult(
        lane=LANE,
        findings=findings,
        exit_code=0,  # advisory: never blocks the gate
        summary=f"{len(findings)} model-operable interface gap(s)"
        if findings
        else "no model-operable interface gaps detected",
    )


# --- Layer 2: Instructor-mediated guidance-adequacy judgment (opt-in) ---------

LAYER2_ENV = "DRIFTDRIVER_SURFACEDRIFT_LAYER2"
LAYER2_ROUTE_ID = "surfacedrift"

# Provider -> env var holding the API key. All resolve to OpenAI-compatible
# clients; unknown providers fall back to OPENAI_API_KEY.
_PROVIDER_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "zai": "ZAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
}


def _layer2_enabled() -> bool:
    return os.environ.get(LAYER2_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _run_layer2(candidates, deviations) -> list[LaneFinding]:
    """Judge guidance adequacy for structurally-complete surfaces.

    Returns findings for inadequate guidance. Degrades gracefully: if enabled
    with candidates but none could be judged (instructor/openai/route/api-key
    unavailable), emits a single info-level note so the operator sees Layer 2
    was a no-op rather than silently passing.
    """
    findings: list[LaneFinding] = []
    if not candidates:
        return findings
    judged = 0
    for rel, lineno, name, class_source in candidates:
        verdict = _judge_guidance(class_source)
        if verdict is None:
            continue
        judged += 1
        adequate, reasoning, gaps = verdict
        if adequate:
            continue
        if covered(rel, lineno, deviations):
            continue
        gap_text = "; ".join(gaps) if gaps else "guidance judged inadequate"
        findings.append(LaneFinding(
            message=(
                f"Interface violation: model-operable surface '{name}' declares "
                f"all required fields, but a model judged the guidance inadequate: "
                f"{gap_text}. Structure is present; meaning is not. "
                f"(Layer 2 reasoning: {reasoning})"
            ),
            severity="warning",
            file=rel,
            line=lineno,
            tags=[INTERFACE_TAG, "inadequate-guidance"],
        ))
    if not judged:
        findings.append(LaneFinding(
            message=(
                "Layer 2 enabled but could not judge any surface — "
                "instructor/openai, a 'surfacedrift' model route, or an API key "
                "is unavailable. Install instructor+openai and configure the "
                "route to activate Instructor-mediated guidance review."
            ),
            severity="info",
            file="",
            line=0,
            tags=[INTERFACE_TAG, "layer2-unavailable"],
        ))
    return findings


def _judge_guidance(class_source: str) -> tuple[bool, str, list[str]] | None:
    """Ask the model whether ``class_source``'s guidance is adequate.

    Returns ``(adequate, reasoning, gaps)`` or ``None`` if Layer 2 cannot run
    (instructor/openai/pydantic missing, no route, no API key, or call failure).
    All degradation is silent per-class; ``_run_layer2`` surfaces a single
    summary note if nothing could be judged.
    """
    try:
        import instructor
        from pydantic import BaseModel, Field
    except ImportError:
        return None

    route = _resolve_layer2_route()
    if route is None:
        return None
    client = _build_client(route)
    if client is None:
        return None

    class _Verdict(BaseModel):
        adequate: bool
        reasoning: str
        gaps: list[str] = Field(default_factory=list)

    try:
        wrapped = instructor.from_openai(client)
        resp = wrapped.chat.completions.create(
            model=route.model,
            response_model=_Verdict,
            messages=[{"role": "user", "content": _build_guidance_prompt(class_source)}],
            max_tokens=512,
        )
        return bool(resp.adequate), str(resp.reasoning), list(resp.gaps)
    except Exception:
        return None


def _resolve_layer2_route():
    try:
        from driftdriver.model_routes import route_for
        return route_for(LAYER2_ROUTE_ID)
    except Exception:
        return None


def _build_client(route):
    try:
        from openai import OpenAI
    except ImportError:
        return None
    api_key = os.environ.get(
        _PROVIDER_API_KEY_ENV.get(route.provider, "OPENAI_API_KEY")
    ) or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        return OpenAI(base_url=route.base_url, api_key=api_key)
    except Exception:
        return None


def _build_guidance_prompt(class_source: str) -> str:
    return (
        "You are auditing a model-operable error surface — an error class an "
        "LLM agent will encounter and must handle correctly. Judge whether its "
        "guidance fields are ADEQUATE for the model to understand the failure, "
        "trust the examples, and recover.\n\n"
        "Consider: is `message` specific rather than generic? are "
        "`valid_examples` genuinely valid inputs the model could retry with? "
        "is `retryable` correct? is `next_step` a concrete, actionable "
        "instruction rather than vague?\n\n"
        f"Error surface source:\n```python\n{class_source}\n```\n\n"
        "Return: adequate (bool), reasoning (str), gaps (list of specific field "
        "deficiencies; empty if adequate)."
    )


# --- discovery (static AST) ----------------------------------------------------

def _iter_marked_classes(project_dir: Path):
    """Yield (rel_path, lineno, class_name, declared_field_names, class_source)
    for each class marked model-operable (decorator or ModelOperable* base).
    ``class_source`` is the source text of the class body, for Layer 2 judgment."""
    for path in walk_py_files(project_dir):
        text = read_py_source(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(project_dir).as_posix()
        lines = text.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _is_marked(node):
                end = getattr(node, "end_lineno", None) or node.lineno
                seg = "\n".join(lines[node.lineno - 1: end])
                yield rel, node.lineno, node.name, _declared_fields(node), seg


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
