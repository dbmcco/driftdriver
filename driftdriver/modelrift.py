"""modelrift — model-agency-violation drift lane.

Detects deterministic code patterns that may be making semantic judgments the
model-mediated architecture reserves for the model. This realizes the
`model-mediated-development` skill's mandate that Speedrift/Driftdriver "run
model-mediation drift checks and emit findings such as possible model agency
violations."

Posture: ADVISORY. Findings are `warning` severity but the lane always returns
``exit_code=0``, so they create reviewable follow-ups (severity warning+) without
failing the gate or blocking the graph. Model-agency violations are genuinely
ambiguous, so they are surfaced as evidence for review, not hard blocks — the
same shape as the planforge ``override_reason`` and the driftdriver ``--gate``
escape hatch.

Escape hatch: findings covered by a logged deviation in the repo's model-mediated
deviation register are suppressed. The register is the owned, reviewable record
of intentional deterministic exceptions (see
``docs/model-mediated/deviation-register.md``).

v1 detectors (lowest false-positive, mechanically detectable):
  - keyword/marker intent gates (string collections bound to INTENT/MARKER/
    TRIGGER/KEYWORD names and used for routing/validation)  — "heuristic violation"
  - hardcoded float thresholds on semantic dimensions (score/priority/relevance/
    risk/confidence/weight/similarity)                                    — "judgment violation"

Deferred to v2 (need more context to avoid false positives):
  - model-output fallback/override branches  ("fallback violation")
  - category -> route/agent/model maps       ("routing violation")
"""
from __future__ import annotations

import re
from pathlib import Path

from driftdriver._lanecommon import covered, load_deviations, read_py_source, walk_py_files
from driftdriver.lane_contract import LaneFinding, LaneResult

LANE = "modelrift"
AGENCY_TAG = "model-agency"

# --- Detector A: keyword / marker intent gates --------------------------------
# An assignment whose target name ends (case-insensitively) in one of the
# marker/intent suffixes, AND whose line carries a string literal — the
# signature of a word/substring gate over user or model text.
_MARKER_SUFFIX_RE = re.compile(
    r"(?:markers?|intents?|triggers?|keywords?)\s*[:=]",
    re.IGNORECASE,
)
_STRING_LITERAL_RE = re.compile(r"""(['"][^'"]{1,80}['"])""")
# Test code is definitionally out of model-agency scope: deterministic
# assertions are the *point* of tests, so flagging keyword-gates or numeric
# thresholds there is pure noise. Skip test trees at scan time (not via the
# per-site deviation register, which is for reviewed production exceptions).
_TEST_DIR_NAMES = {"tests", "test"}


def _is_test_path(rel_posix: str) -> bool:
    """True if ``rel_posix`` sits under a test directory."""
    return any(part in _TEST_DIR_NAMES for part in rel_posix.split("/"))

# --- Detector B: hardcoded semantic thresholds --------------------------------
# A float-literal comparison against a variable whose name carries a semantic
# judgment dimension. Float cutoffs (0.7, 0.85) are the classic model-score
# gates; plain integer counters (`i > 5`) are mechanical and must NOT fire.
_SEMANTIC_DIMENSION_RE = re.compile(
    r"\b((?:[A-Za-z_]\w*)?(?:score|priority|relevance|risk|confidence|weight|similarity)\w*)"
    r"\s*(?:>=|<=|==|>|<)\s*(0?\.\d+)",
    re.IGNORECASE,
)


def run_as_lane(project_dir: Path) -> LaneResult:
    """Scan ``project_dir`` for possible model-agency violations.

    Returns an advisory ``LaneResult`` (exit_code 0). Findings suppressed where
    a logged deviation covers the location.
    """
    project_dir = Path(project_dir)
    deviations = load_deviations(project_dir)
    findings: list[LaneFinding] = []

    for path in walk_py_files(project_dir):
        rel = path.relative_to(project_dir).as_posix()
        if _is_test_path(rel):
            continue
        text = read_py_source(path)
        if text is None:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            for subkind, message in _scan_line(line):
                if covered(rel, idx, deviations):
                    continue
                findings.append(LaneFinding(
                    message=message,
                    severity="warning",
                    file=rel,
                    line=idx,
                    tags=[AGENCY_TAG, subkind],
                ))

    return LaneResult(
        lane=LANE,
        findings=findings,
        exit_code=0,  # advisory: never blocks the gate
        summary=f"{len(findings)} possible model-agency violation(s)"
        if findings
        else "no model-agency violations detected",
    )


# --- scanning ------------------------------------------------------------------

def _scan_line(line: str) -> list[tuple[str, str]]:
    """Return [(subkind, message), ...] for violations found in ``line``."""
    hits: list[tuple[str, str]] = []
    if _MARKER_SUFFIX_RE.search(line) and _STRING_LITERAL_RE.search(line):
        hits.append((
            "keyword-gate",
            "Possible model-agency violation: keyword/marker intent gate "
            "(string collection used for routing/validation). If this is an "
            "intentional deterministic exception, log it in the deviation "
            "register (docs/model-mediated/deviation-register.md).",
        ))
    if _SEMANTIC_DIMENSION_RE.search(line):
        hits.append((
            "semantic-threshold",
            "Possible model-agency violation: hardcoded threshold on a semantic "
            "dimension (score/priority/relevance/risk/confidence/weight/"
            "similarity). If intentional, log it in the deviation register.",
        ))
    return hits


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
