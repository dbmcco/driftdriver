# ABOUTME: existdrift — model-mediated pre-build grounding lane.
# ABOUTME: Three layers: evidence (pure code), interpretation (model-mediated),
# ABOUTME: findings (deterministic policy). No heuristic intent inference.
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, Callable

from driftdriver.drift_task_guard import guarded_add_drift_task
from driftdriver.local_llm import call_ollama as _default_caller, _DEFAULT_MODEL

_SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}

_ACTIVE_STATES = {"open", "ready", "in-progress"}

_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".workgraph", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "target", "Cargo",
}

_WG_CONTRACT_RE = re.compile(
    r"```wg-contract\s*\n(.*?)```",
    re.DOTALL,
)

_SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{3,})`")

_DOC_FILES = ("NORTH_STAR.md", "AGENTS.md", "CLAUDE.md", "README.md")

_VALID_JUDGMENTS = frozenset({
    "create-intended", "grounding-error", "collision-risk", "acceptable",
})


# ---------------------------------------------------------------------------
# Timestamp and fingerprint helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(parts: list[str]) -> str:
    key = "|".join(str(part or "").strip().lower() for part in parts)
    return sha1(key.encode("utf-8")).hexdigest()  # noqa: S324


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _default_existdrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_seconds": 14400,
        "severity_grounding_error": "warning",
        "severity_collision": "high",
        "severity_outside_repo": "high",
        "max_findings": 40,
        "symbol_check": True,
        "min_symbol_len": 4,
        "interpretation_model": _DEFAULT_MODEL,
        "emit_followups": False,
    }


def _normalize_cfg(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = dict(raw) if isinstance(raw, dict) else {}
    return {
        "enabled": bool(r.get("enabled", True)),
        "interval_seconds": max(0, int(r.get("interval_seconds", 14400))),
        "severity_grounding_error": str(
            r.get("severity_grounding_error", "warning") or "warning"
        ),
        "severity_collision": str(
            r.get("severity_collision", "high") or "high"
        ),
        "severity_outside_repo": str(
            r.get("severity_outside_repo", "high") or "high"
        ),
        "max_findings": max(1, int(r.get("max_findings", 40))),
        "symbol_check": bool(r.get("symbol_check", True)),
        "min_symbol_len": max(2, int(r.get("min_symbol_len", 4))),
        "interpretation_model": str(
            r.get("interpretation_model", _DEFAULT_MODEL) or _DEFAULT_MODEL
        ),
        "emit_followups": bool(r.get("emit_followups", False)),
    }


# ---------------------------------------------------------------------------
# Workgraph task reader (mirrors plandrift's approach)
# ---------------------------------------------------------------------------


def _read_workgraph_tasks(
    repo_path: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read workgraph tasks from graph.jsonl."""
    try:
        from driftdriver.plandrift import _read_workgraph_tasks as _pdr

        return _pdr(repo_path)
    except Exception:
        pass

    graph = repo_path / ".workgraph" / "graph.jsonl"
    if not graph.exists():
        return {}, [".workgraph/graph.jsonl missing"]

    tasks: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        lines = graph.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {}, [f"could not read graph.jsonl: {exc}"]

    for idx, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            if len(errors) < 12:
                errors.append(f"invalid json at line {idx}")
            continue
        if not isinstance(row, dict):
            continue
        row_type = str(row.get("type") or "").strip().lower()
        if row_type and row_type != "task":
            continue
        task_id = str(row.get("id") or "").strip()
        if not task_id:
            continue
        tasks[task_id] = {
            "id": task_id,
            "title": str(row.get("title") or ""),
            "description": str(row.get("description") or row.get("desc") or ""),
            "status": str(row.get("status") or "open").strip().lower(),
        }

    return tasks, errors[:12]


# ---------------------------------------------------------------------------
# Contract extraction (generalized for touch and creates)
# ---------------------------------------------------------------------------


def _extract_contract_key(description: str, key: str) -> list[str]:
    """Extract a list-valued key from a wg-contract fenced block.

    Falls back to regex parsing if TOML parsing fails.
    """
    match = _WG_CONTRACT_RE.search(description)
    body = match.group(1) if match else description

    try:
        import tomllib

        parsed = tomllib.loads(body)
        raw = parsed.get(key)
        if isinstance(raw, list):
            return [str(p).strip() for p in raw if str(p).strip()]
    except Exception:
        pass

    # Regex fallback: key = ["a", "b"]
    key_match = re.search(
        rf'{key}\s*=\s*\[([^\]]*)\]',
        body,
    )
    if key_match:
        return re.findall(r'"([^"]+)"', key_match.group(1))
    return []


def _extract_symbols(
    description: str,
    *,
    min_len: int = 4,
    cap: int = 20,
) -> list[str]:
    """Extract backticked identifiers from a description, deduped and capped."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _SYMBOL_RE.finditer(description):
        sym = m.group(1)
        if len(sym) >= min_len and sym not in seen:
            seen.add(sym)
            result.append(sym)
            if len(result) >= cap:
                break
    return result


def _check_symbol_exists(symbol: str, repo_path: Path) -> bool:
    """Check whether a symbol appears in any source file under the repo."""
    rg = shutil.which("rg")
    if rg:
        try:
            result = subprocess.run(
                [rg, "--fixed-strings", "--files-with-matches",
                 "--no-hidden", "-g", "!.git", "-g", "!node_modules",
                 "-g", "!__pycache__", "-g", "!.venv", "-g", "!*.pyc",
                 symbol],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    grep = shutil.which("grep")
    if grep:
        try:
            result = subprocess.run(
                [grep, "-rl", "--include=*",
                 "-r", symbol, str(repo_path)],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    try:
        for src_dir in [repo_path / "src", repo_path, repo_path / "lib"]:
            if not src_dir.exists():
                continue
            for f in src_dir.rglob("*.py"):
                if any(part in _EXCLUDE_DIRS for part in f.parts):
                    continue
                try:
                    if symbol in f.read_text(encoding="utf-8", errors="replace"):
                        return True
                except OSError:
                    continue
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Path checking helpers
# ---------------------------------------------------------------------------


def _resolve_path(repo_path: Path, touch_path: str) -> Path:
    return (repo_path / touch_path).resolve()


def _is_outside_repo(repo_path: Path, touch_path: str) -> bool:
    resolved = _resolve_path(repo_path, touch_path)
    try:
        resolved.relative_to(repo_path.resolve())
        return False
    except ValueError:
        return True


def _nearest_existing_parent(repo_path: Path, touch_path: str) -> str:
    """Return the nearest existing parent directory relative to repo, or ''."""
    full = repo_path / touch_path
    parent = full.parent
    if parent.exists() and parent != repo_path:
        try:
            return str(parent.relative_to(repo_path.resolve()))
        except ValueError:
            return str(parent)
    return ""


# ---------------------------------------------------------------------------
# Layer 1: Evidence collection (pure code, zero judgment)
# ---------------------------------------------------------------------------


def collect_evidence(
    repo_path: Path,
    tasks: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Collect pure filesystem facts for each active task with a wg-contract.

    Produces a list of evidence rows, one per qualifying task. Each row
    contains touch_facts, creates_facts, and symbol_facts — all boolean
    facts with no severity, no recommendation, no inference.
    """
    min_symbol_len = int(cfg.get("min_symbol_len", 4))
    do_symbols = bool(cfg.get("symbol_check", True))
    rows: list[dict[str, Any]] = []

    for task in tasks.values():
        status = str(task.get("status") or "open").strip().lower()
        if status not in _ACTIVE_STATES:
            continue

        description = str(task.get("description") or "")
        touch_paths = _extract_contract_key(description, "touch")
        creates_paths = _extract_contract_key(description, "creates")

        # --- touch_facts ---
        touch_facts: list[dict[str, Any]] = []
        for tp in touch_paths:
            outside = _is_outside_repo(repo_path, tp)
            exists = (repo_path / tp).exists() if not outside else False
            touch_facts.append({
                "path": tp,
                "exists": exists,
                "outside_repo": outside,
                "nearest_existing_parent": _nearest_existing_parent(repo_path, tp),
            })

        # --- creates_facts ---
        creates_facts: list[dict[str, Any]] = []
        for cp in creates_paths:
            creates_facts.append({
                "path": cp,
                "already_exists": (repo_path / cp).exists(),
            })

        # --- symbol_facts ---
        symbol_facts: list[dict[str, Any]] = []
        if do_symbols:
            symbols = _extract_symbols(description, min_len=min_symbol_len)
            for sym in symbols:
                symbol_facts.append({
                    "symbol": sym,
                    "found": _check_symbol_exists(sym, repo_path),
                })

        rows.append({
            "task_id": str(task.get("id") or ""),
            "title": str(task.get("title") or ""),
            "declared_touch": touch_paths,
            "declared_creates": creates_paths,
            "touch_facts": touch_facts,
            "creates_facts": creates_facts,
            "symbol_facts": symbol_facts,
        })

    return rows


# ---------------------------------------------------------------------------
# Layer 2: Interpretation (model-mediated)
# ---------------------------------------------------------------------------


def _select_items_for_interpretation(row: dict[str, Any]) -> list[dict[str, str]]:
    """Select items that need model interpretation from an evidence row.

    An item needs interpretation iff:
    - touch fact: exists=False AND path NOT in declared_creates
    - creates fact: already_exists=True
    - symbol fact: found=False

    outside_repo facts are excluded — they go straight to the findings layer.
    """
    declared_creates = set(row.get("declared_creates", []))
    items: list[dict[str, str]] = []

    for tf in row.get("touch_facts", []):
        if tf["outside_repo"]:
            continue
        if not tf["exists"] and tf["path"] not in declared_creates:
            fact_str = "Path does not exist on disk (not declared in creates)"
            if tf.get("nearest_existing_parent"):
                fact_str += f"; nearest existing parent: {tf['nearest_existing_parent']}"
            items.append({
                "task_id": row["task_id"],
                "type": "touch-path-missing",
                "item": tf["path"],
                "fact": fact_str,
            })

    for cf in row.get("creates_facts", []):
        if cf["already_exists"]:
            items.append({
                "task_id": row["task_id"],
                "type": "creates-collision",
                "item": cf["path"],
                "fact": "File already exists but task declares creating it",
            })

    for sf in row.get("symbol_facts", []):
        if not sf["found"]:
            items.append({
                "task_id": row["task_id"],
                "type": "unknown-symbol",
                "item": sf["symbol"],
                "fact": "Symbol not found in any source file in the repo",
            })

    return items


def _build_interpretation_prompt(
    evidence_rows: list[dict[str, Any]],
    all_items: list[dict[str, str]],
) -> str:
    """Build the prompt that asks the model to interpret grounding facts."""
    lines: list[str] = [
        "You are a grounding analyst for a workgraph task planner. Review the "
        "following filesystem facts and classify each item that needs attention.",
        "",
        "For each item, provide a judgment from these categories:",
        '- "create-intended": The path/symbol will be created by this task\'s own work.',
        '- "grounding-error": The reference appears to be a mistake (wrong path, typo, hallucinated API).',
        '- "collision-risk": The task declares creating a file that already exists; may overwrite.',
        '- "acceptable": The reference is fine (e.g., a symbol defined by a prerequisite task).',
        "",
        "TASKS AND FACTS:",
        "",
    ]

    for row in evidence_rows:
        tid = row["task_id"]
        title = row.get("title", "")
        lines.append(f"Task: {title} (task_id: {tid})")
        lines.append(f"  Declared touch paths (files to modify): {row.get('declared_touch', [])}")
        lines.append(f"  Declared creates paths (files to create): {row.get('declared_creates', [])}")
        lines.append("")
        task_items = [i for i in all_items if i["task_id"] == tid]
        if task_items:
            lines.append("  Items needing judgment:")
            for idx, item in enumerate(task_items, 1):
                lines.append(f"  {idx}. {item['item']} — {item['fact']}")
        else:
            lines.append("  (no items need judgment)")
        lines.append("")

    lines.extend([
        "Respond with ONLY a JSON array. Each element:",
        '{"task_id": "...", "item": "...", "judgment": "create-intended|grounding-error|collision-risk|acceptable", "rationale": "brief explanation", "suggested_fix": "what to do (empty if acceptable)"}',
    ])
    return "\n".join(lines)


def _validate_interp_response(raw: str) -> list[dict[str, Any]] | None:
    """Parse and validate the model's interpretation response.

    Returns a list of judgment dicts on success, or None if the response
    is unparseable or schema-invalid.
    """
    text = raw.strip()
    if not text:
        return None

    # Try direct parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try extracting from code fence
        if "```" in text:
            start = text.find("```")
            end = text.rfind("```")
            candidate = text[start:end]
            # Strip fence markers and language tags
            candidate = re.sub(r"^```[a-z]*\n?", "", candidate)
            candidate = re.sub(r"\n?```$", "", candidate)
            try:
                data = json.loads(candidate.strip())
            except json.JSONDecodeError:
                return None
        else:
            # Try brace/bracket span
            first_bracket = text.find("[")
            last_bracket = text.rfind("]")
            if first_bracket >= 0 and last_bracket > first_bracket:
                try:
                    data = json.loads(text[first_bracket:last_bracket + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

    if not isinstance(data, list):
        return None

    for item in data:
        if not isinstance(item, dict):
            return None
        judgment = item.get("judgment")
        if judgment not in _VALID_JUDGMENTS:
            return None
        if "item" not in item or "task_id" not in item:
            return None

    return data


def _mark_uninterpreted(
    items: list[dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    """Mark all items uninterpreted when the model is unavailable or invalid.

    Not a substitute judgment: code never decides; it labels the absence of
    interpretation so the findings layer reports raw facts at info severity.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        tid = item["task_id"]
        result.setdefault(tid, []).append({
            "task_id": tid,
            "item": item["item"],
            "type": item.get("type", ""),
            "judgment": "uninterpreted",
            "rationale": "model interpretation unavailable",
            "suggested_fix": "",
            "raw_facts": item.get("fact", ""),
        })
    return result


def interpret_evidence(
    evidence_rows: list[dict[str, Any]],
    *,
    cfg: dict[str, Any],
    caller: Callable[..., str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Interpret grounding evidence using a local model.

    Item selection is mechanical. The model provides judgments. On invalid
    response, one repair round-trip is attempted. Still invalid → uninterpreted.

    Returns a dict mapping task_id to a list of judgment dicts.
    """
    # Select items that need interpretation
    all_items: list[dict[str, str]] = []
    for row in evidence_rows:
        all_items.extend(_select_items_for_interpretation(row))

    if not all_items:
        return {}

    if caller is None:
        caller = _default_caller

    model = cfg.get("interpretation_model", _DEFAULT_MODEL)
    prompt = _build_interpretation_prompt(evidence_rows, all_items)

    # Initial call
    try:
        raw = caller(model, prompt)
    except Exception:
        return _mark_uninterpreted(all_items)

    parsed = _validate_interp_response(raw)
    if parsed is not None:
        return _group_by_task(parsed)

    # Repair round-trip
    repair_prompt = (
        prompt
        + "\n\nYour previous response was invalid (not valid JSON array or "
        "missing required fields/judgment values). Please respond with ONLY "
        "a valid JSON array using the exact schema specified above."
    )
    try:
        raw = caller(model, repair_prompt)
    except Exception:
        return _mark_uninterpreted(all_items)

    parsed = _validate_interp_response(raw)
    if parsed is not None:
        return _group_by_task(parsed)

    # Still invalid → uninterpreted
    return _mark_uninterpreted(all_items)


def _group_by_task(
    judgments: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group a flat list of judgment dicts by task_id."""
    result: dict[str, list[dict[str, Any]]] = {}
    for j in judgments:
        tid = str(j.get("task_id") or "")
        result.setdefault(tid, []).append(j)
    return result


# ---------------------------------------------------------------------------
# Layer 3: Findings (deterministic policy)
# ---------------------------------------------------------------------------


def scan_grounding(
    repo_path: Path,
    *,
    cfg: dict[str, Any] | None = None,
    caller: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Scan workgraph tasks for plan↔reality mismatches.

    Three-layer pipeline: collect evidence → interpret via model → map to
    findings via deterministic policy. No heuristic intent inference.
    """
    c = _normalize_cfg(cfg)
    if not c["enabled"]:
        return {
            "repo": repo_path.name,
            "path": str(repo_path),
            "generated_at": _iso_now(),
            "enabled": False,
            "summary": {
                "findings_total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "info": 0,
                "at_risk": False,
                "narrative": "existdrift disabled by policy",
            },
            "findings": [],
            "top_findings": [],
            "errors": [],
        }

    repo_name = repo_path.name
    tasks, errors = _read_workgraph_tasks(repo_path)

    # Layer 1: Collect evidence
    evidence_rows = collect_evidence(repo_path, tasks, c)

    # Layer 2: Interpret via model
    interpretations = interpret_evidence(evidence_rows, cfg=c, caller=caller)

    # Layer 3: Map to findings (deterministic policy)
    findings: list[dict[str, Any]] = []

    for row in evidence_rows:
        task_id = row["task_id"]
        task_title = row.get("title", "")

        # --- outside-repo paths: direct finding, no model needed ---
        outside_paths = [
            tf["path"] for tf in row.get("touch_facts", [])
            if tf["outside_repo"]
        ]
        if outside_paths:
            findings.append({
                "fingerprint": _fingerprint(
                    [repo_name, task_id, "outside-repo-path", ",".join(sorted(outside_paths))]
                ),
                "category": "outside-repo-path",
                "severity": c["severity_outside_repo"],
                "title": "Task touches paths outside repo boundary",
                "evidence": (
                    f"task={task_id} ({task_title}); "
                    f"outside={outside_paths}"
                ),
                "recommendation": (
                    f"Move `{outside_paths[0]}` into the repo or use "
                    f"`wg add --repo <peer>` for cross-repo work."
                ),
            })

        # --- interpreted items: map judgment → severity per cfg ---
        task_interps = interpretations.get(task_id, [])
        for interp in task_interps:
            judgment = str(interp.get("judgment") or "")
            item = str(interp.get("item") or "")

            if judgment in ("create-intended", "acceptable"):
                continue  # no finding

            if judgment == "uninterpreted":
                raw_facts = interp.get("raw_facts", "")
                findings.append({
                    "fingerprint": _fingerprint(
                        [repo_name, task_id, "uninterpreted-grounding", item]
                    ),
                    "category": "uninterpreted-grounding",
                    "severity": "info",
                    "title": "Grounding item needs manual review",
                    "evidence": (
                        f"task={task_id} ({task_title}); item={item}; "
                        f"facts={raw_facts}"
                    ),
                    "recommendation": (
                        "model interpretation unavailable — review manually"
                    ),
                })
            elif judgment == "grounding-error":
                suggested = str(interp.get("suggested_fix") or "")
                findings.append({
                    "fingerprint": _fingerprint(
                        [repo_name, task_id, "grounding-error", item]
                    ),
                    "category": "grounding-error",
                    "severity": c["severity_grounding_error"],
                    "title": "Task references potentially non-existent path or symbol",
                    "evidence": f"task={task_id} ({task_title}); item={item}",
                    "recommendation": suggested or "Verify this reference against the codebase.",
                })
            elif judgment == "collision-risk":
                suggested = str(interp.get("suggested_fix") or "")
                findings.append({
                    "fingerprint": _fingerprint(
                        [repo_name, task_id, "creates-collision", item]
                    ),
                    "category": "creates-collision",
                    "severity": c["severity_collision"],
                    "title": "Task declares creating a file that already exists",
                    "evidence": f"task={task_id} ({task_title}); item={item}",
                    "recommendation": suggested or (
                        "File already exists; update the contract to use "
                        "`touch` instead of `creates`."
                    ),
                })

    # Deduplicate by fingerprint
    deduped: dict[str, dict[str, Any]] = {}
    for f in findings:
        fp = str(f.get("fingerprint") or "").strip()
        if fp and fp not in deduped:
            deduped[fp] = f

    ordered = sorted(
        deduped.values(),
        key=lambda row: (
            -_SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0),
            str(row.get("category") or ""),
            str(row.get("evidence") or ""),
        ),
    )

    max_f = int(c["max_findings"])
    top_findings = ordered[:max_f]

    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for f in ordered:
        sev = str(f.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1

    total = len(ordered)
    at_risk = counts["critical"] > 0 or counts["high"] > 0
    narrative = (
        f"existdrift reviewed `{repo_name}`: {total} grounding findings "
        f"(critical={counts['critical']}, high={counts['high']}, "
        f"medium={counts['medium']}, low={counts['low']}, "
        f"info={counts['info']}). "
        f"Checked {len(tasks)} tasks."
    )

    return {
        "repo": repo_name,
        "path": str(repo_path),
        "generated_at": _iso_now(),
        "enabled": True,
        "summary": {
            "findings_total": total,
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "info": counts["info"],
            "at_risk": at_risk,
            "narrative": narrative,
        },
        "findings": ordered,
        "top_findings": top_findings,
        "errors": errors[:20],
    }


# ---------------------------------------------------------------------------
# Evidence bundle (for planner prompt grounding) — pure filesystem
# ---------------------------------------------------------------------------


def build_evidence_bundle(
    repo_path: Path,
    *,
    hint_text: str = "",
    max_paths: int = 30,
) -> str:
    """Build a markdown grounding brief from the repo filesystem.

    Pure filesystem: directory tree, test setup detection, doc-file
    presence, and hint-noun path matching. No LLM, no network.
    """
    sections: list[str] = ["## Repository Evidence\n"]

    # --- Directory tree (2 levels deep) ---
    sections.append("### Directory Layout\n")
    for entry in sorted(repo_path.iterdir()):
        if entry.name in _EXCLUDE_DIRS or entry.name.startswith("."):
            continue
        if entry.is_dir():
            sections.append(f"- **{entry.name}/**")
            try:
                children = sorted(entry.iterdir())[:8]
                for child in children:
                    if child.name in _EXCLUDE_DIRS or child.name.startswith("."):
                        continue
                    name = child.name + "/" if child.is_dir() else child.name
                    sections.append(f"  - {name}")
            except PermissionError:
                pass
        elif entry.is_file():
            sections.append(f"- {entry.name}")
    sections.append("")

    # --- Test setup detection ---
    sections.append("### Test Setup\n")
    test_findings: list[str] = []
    if (repo_path / "pytest.ini").exists():
        test_findings.append("pytest.ini found")
    if (repo_path / "pyproject.toml").exists():
        try:
            import tomllib

            data = tomllib.loads(
                (repo_path / "pyproject.toml").read_text(encoding="utf-8")
            )
            if "tool" in data and "pytest" in data.get("tool", {}):
                test_findings.append("[tool.pytest] in pyproject.toml")
        except Exception:
            pass
    if (repo_path / "package.json").exists():
        try:
            pkg = json.loads(
                (repo_path / "package.json").read_text(encoding="utf-8")
            )
            if isinstance(pkg.get("scripts"), dict) and "test" in pkg["scripts"]:
                test_findings.append(
                    f'package.json scripts.test = "{pkg["scripts"]["test"]}"'
                )
        except Exception:
            pass
    if test_findings:
        for f in test_findings:
            sections.append(f"- {f}")
    else:
        sections.append("- No recognized test setup found")
    sections.append("")

    # --- Doc files ---
    sections.append("### Project Docs\n")
    for doc in _DOC_FILES:
        if (repo_path / doc).exists():
            sections.append(f"- `{doc}` present")
    sections.append("")

    # --- Hint-noun path matching ---
    if hint_text:
        sections.append("### Paths Matching Your Goal\n")
        words = set()
        for token in re.findall(r"[a-z]{4,}", hint_text.lower()):
            words.add(token)
        if words:
            matches: list[str] = []
            try:
                for f in repo_path.rglob("*"):
                    if any(part in _EXCLUDE_DIRS for part in f.parts):
                        continue
                    if f.is_dir():
                        continue
                    rel = str(f.relative_to(repo_path))
                    for word in words:
                        if word in rel.lower():
                            matches.append(rel)
                            break
                    if len(matches) >= max_paths:
                        break
            except Exception:
                pass
            if matches:
                for m in sorted(matches):
                    sections.append(f"- `{m}`")
            else:
                sections.append("- No matching paths found")
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def run_existdrift_check(
    *,
    repo_name: str,
    repo_path: Path,
    repo_snapshot: dict[str, Any] | None = None,
    policy_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run existdrift grounding check."""
    report = scan_grounding(repo_path, cfg=policy_cfg)
    report["repo"] = repo_name
    return report


# ---------------------------------------------------------------------------
# Lane wrapper
# ---------------------------------------------------------------------------

_LANE_SEVERITY_MAP = {
    "critical": "critical",
    "high": "error",
    "medium": "warning",
    "low": "info",
    "info": "info",
}


def _map_severity(finding: dict[str, Any]) -> str:
    raw = str(finding.get("severity") or "").lower()
    return _LANE_SEVERITY_MAP.get(raw, "info")


def run_as_lane(project_dir: Path) -> Any:
    """Run existdrift and return results in the standard lane contract format."""
    from driftdriver.lane_contract import LaneFinding, LaneResult

    try:
        report = run_existdrift_check(
            repo_name=project_dir.name,
            repo_path=project_dir,
        )
    except Exception as exc:
        return LaneResult(
            lane="existdrift",
            findings=[LaneFinding(message=f"existdrift error: {exc}", severity="error")],
            exit_code=1,
            summary=f"existdrift failed: {exc}",
        )

    findings = []
    for f in report.get("findings", []):
        findings.append(LaneFinding(
            message=str(f.get("title") or f.get("category") or "grounding finding"),
            severity=_map_severity(f),
            file="",
            line=0,
            tags=[str(f.get("category") or "grounding")],
        ))

    summary_data = report.get("summary", {})
    summary_text = str(summary_data.get("narrative") or f"{len(findings)} findings")
    exit_code = 1 if findings else 0
    return LaneResult(
        lane="existdrift",
        findings=findings,
        exit_code=exit_code,
        summary=summary_text,
    )


# ---------------------------------------------------------------------------
# Followup task emission
# ---------------------------------------------------------------------------


def emit_grounding_followups(
    *,
    repo_path: Path,
    report: dict[str, Any],
    max_tasks: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": True,
        "attempted": 0,
        "created": 0,
        "existing": 0,
        "skipped": 0,
        "errors": [],
        "tasks": [],
    }
    wg_dir = repo_path / ".workgraph"
    if not wg_dir.exists():
        out["errors"].append(f"{repo_path.name}: .workgraph missing")
        return out

    for row in (report.get("top_findings") or [])[:max(1, int(max_tasks))]:
        fingerprint = str(row.get("fingerprint") or "").strip()
        if not fingerprint:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        task_id = f"existdrift-{fingerprint[:14]}"
        title = f"existdrift: {str(row.get('severity') or 'info')} {str(row.get('category') or 'finding')}"

        out["attempted"] = int(out["attempted"]) + 1
        result = guarded_add_drift_task(
            wg_dir=wg_dir,
            task_id=task_id,
            title=title,
            description=(
                f"Grounding finding: {row.get('title')}\n"
                f"Severity: {row.get('severity')}\n"
                f"Evidence: {row.get('evidence')}\n"
                f"Recommendation: {row.get('recommendation')}\n"
            ),
            lane_tag="existdrift",
            extra_tags=["grounding"],
            cwd=repo_path,
        )
        if result == "created":
            out["created"] = int(out["created"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "created"})
        elif result == "existing":
            out["existing"] = int(out["existing"]) + 1
        else:
            out["skipped"] = int(out["skipped"]) + 1

    out["tasks"] = list(out.get("tasks") or [])[:80]
    out["errors"] = list(out.get("errors") or [])[:80]
    return out
