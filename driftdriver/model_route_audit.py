from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


MODEL_LITERAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<model>"
    r"(?:(?:anthropic|openai|google|x-ai)/)?"
    r"(?:claude|gpt|gemini|glm|grok|qwen|deepseek|mistral|llama|text-embedding|dall-e|imagen|veo)"
    r"[A-Za-z0-9._:/+-]*"
    r")"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".claude",
    ".hg",
    ".mypy_cache",
    ".next",
    ".playwright-mcp",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    ".tox",
    ".uxdrift",
    ".venv",
    ".wg-worktrees",
    ".workgraph",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "dist",
    "migrations",
    "node_modules",
    "output",
    "site-packages",
    "target",
    "vendor",
    "venv",
}
DEFAULT_EXCLUDED_DIR_SUFFIXES = (".egg-info",)

DEFAULT_DOC_DIRS = {"doc", "docs", "documentation"}
DEFAULT_TEST_DIRS = {"__tests__", "e2e", "test", "tests"}
DEFAULT_MAX_FILE_BYTES = 1_000_000

DEFAULT_INCLUDED_SUFFIXES = {
    ".bash",
    ".cfg",
    ".env",
    ".go",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mdx",
    ".mjs",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
    ".zsh",
}

DEFAULT_ALLOWED_PATHS = {
    "config/cognition-presets.toml",
    "paia-agent-runtime/config/cognition-presets.toml",
    "paia-agent-runtime/docs/model-route-registry.md",
}

DEFAULT_ALLOWED_PATTERNS = {
    "workgraph/src/config.rs",
    "workgraph/src/model_benchmarks.rs",
    "workgraph/tests/*",
    "driftdriver/llm_meter.py",
    "driftdriver/driftdriver/llm_meter.py",
    "grok-aurora-cli/src/grok_aurora_cli/*client.py",
    "grok-aurora-cli/src/grok_aurora_cli/model_routes.py",
}


@dataclass(frozen=True)
class ModelRouteAuditFinding:
    path: Path
    line: int
    model: str
    category: str
    snippet: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path.as_posix(),
            "line": self.line,
            "model": self.model,
            "category": self.category,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class ModelRouteAuditReport:
    root: Path
    findings: list[ModelRouteAuditFinding]
    checked_file_count: int
    skipped_file_count: int

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self, *, mode: str = "advisory") -> dict[str, object]:
        return {
            "ok": self.ok,
            "mode": mode,
            "root": str(self.root),
            "finding_count": self.finding_count,
            "checked_file_count": self.checked_file_count,
            "skipped_file_count": self.skipped_file_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def scan_model_route_literals(
    root: str | Path,
    *,
    include_docs: bool = False,
    include_tests: bool = False,
    allowed_paths: set[str] | None = None,
    allowed_patterns: set[str] | None = None,
) -> ModelRouteAuditReport:
    """Find hardcoded model IDs outside approved registry/provider catalog locations."""

    root_path = Path(root).resolve()
    effective_allowed_paths = DEFAULT_ALLOWED_PATHS | (allowed_paths or set())
    effective_allowed_patterns = DEFAULT_ALLOWED_PATTERNS | (allowed_patterns or set())
    gitignored_paths = _gitignored_files(root_path)
    findings: list[ModelRouteAuditFinding] = []
    checked = 0
    skipped = 0

    for current_root, dirnames, filenames in os.walk(root_path):
        current_path = Path(current_root)
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if not _should_skip_dir(
                current_path.joinpath(dirname).relative_to(root_path),
                include_docs=include_docs,
                include_tests=include_tests,
            )
        )

        for filename in sorted(filenames):
            path = current_path / filename
            file_checked, file_skipped = _scan_file(
                path,
                root_path=root_path,
                include_docs=include_docs,
                include_tests=include_tests,
                gitignored_paths=gitignored_paths,
                effective_allowed_paths=effective_allowed_paths,
                effective_allowed_patterns=effective_allowed_patterns,
                findings=findings,
            )
            checked += file_checked
            skipped += file_skipped

    findings.sort(key=lambda finding: (finding.path.as_posix(), finding.line, finding.model))
    return ModelRouteAuditReport(
        root=root_path,
        findings=findings,
        checked_file_count=checked,
        skipped_file_count=skipped,
    )


def _scan_file(
    path: Path,
    *,
    root_path: Path,
    include_docs: bool,
    include_tests: bool,
    gitignored_paths: set[Path],
    effective_allowed_paths: set[str],
    effective_allowed_patterns: set[str],
    findings: list[ModelRouteAuditFinding],
) -> tuple[int, int]:
    relative_path = path.relative_to(root_path)
    if relative_path in gitignored_paths:
        return (0, 1)
    if _should_skip(relative_path, include_docs=include_docs, include_tests=include_tests):
        return (0, 1)
    if _is_allowed(relative_path, effective_allowed_paths, effective_allowed_patterns):
        return (0, 1)
    if not _has_included_suffix(relative_path):
        return (0, 1)

    try:
        if path.stat().st_size > DEFAULT_MAX_FILE_BYTES:
            return (0, 1)
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return (0, 1)
    except OSError:
        return (0, 1)

    findings.extend(_find_literals(relative_path, text))
    return (1, 0)


def render_model_route_audit_text(report: ModelRouteAuditReport, *, mode: str = "advisory") -> str:
    plural = "" if report.finding_count == 1 else "s"
    lines = [
        f"model-route-audit found {report.finding_count} hardcoded model literal{plural} ({mode})",
        f"root: {report.root}",
        f"checked: {report.checked_file_count}; skipped: {report.skipped_file_count}",
    ]
    for finding in report.findings:
        lines.append(
            f"{finding.path.as_posix()}:{finding.line}: {finding.model} "
            f"[{finding.category}] {finding.snippet}"
        )
    return "\n".join(lines)


def _find_literals(path: Path, text: str) -> list[ModelRouteAuditFinding]:
    findings: list[ModelRouteAuditFinding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        seen_on_line: set[str] = set()
        for match in MODEL_LITERAL_PATTERN.finditer(line):
            model = match.group("model")
            if not _looks_like_model_id(model):
                continue
            if model.lower() in seen_on_line:
                continue
            seen_on_line.add(model.lower())
            findings.append(
                ModelRouteAuditFinding(
                    path=path,
                    line=line_number,
                    model=model,
                    category="runtime_literal",
                    snippet=line.strip()[:160],
                )
            )
    return findings


def _gitignored_files(root_path: Path) -> set[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root_path), "ls-files", "--others", "-i", "--exclude-standard"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return set()
    if result.returncode != 0:
        return set()
    return {Path(line.strip()) for line in result.stdout.splitlines() if line.strip()}


def _should_skip(path: Path, *, include_docs: bool, include_tests: bool) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".bin", ".gif", ".jpeg", ".jpg", ".lock", ".pdf", ".png", ".sqlite", ".webp", ".zip"}:
        return True
    if not include_docs and suffix in {".md", ".mdx"}:
        return True

    parts = set(path.parts)
    if parts & DEFAULT_EXCLUDED_DIRS:
        return True
    if not include_docs and parts & DEFAULT_DOC_DIRS:
        return True
    if not include_tests and parts & DEFAULT_TEST_DIRS:
        return True
    return False


def _has_included_suffix(path: Path) -> bool:
    if path.name in {"Dockerfile", "Makefile"}:
        return True
    return path.suffix.lower() in DEFAULT_INCLUDED_SUFFIXES


def _looks_like_model_id(model: str) -> bool:
    if any(character.isdigit() for character in model):
        return True
    if "/" in model:
        provider = model.split("/", 1)[0].lower()
        return provider in {"anthropic", "google", "openai", "x-ai"}
    return False


def _should_skip_dir(path: Path, *, include_docs: bool, include_tests: bool) -> bool:
    parts = set(path.parts)
    basename = path.name
    if basename.startswith(".") and basename not in {".env"}:
        return True
    if basename.startswith(".venv"):
        return True
    if basename.endswith(DEFAULT_EXCLUDED_DIR_SUFFIXES):
        return True
    if basename in DEFAULT_EXCLUDED_DIRS or parts & DEFAULT_EXCLUDED_DIRS:
        return True
    if not include_docs and (basename in DEFAULT_DOC_DIRS or parts & DEFAULT_DOC_DIRS):
        return True
    if not include_tests and (basename in DEFAULT_TEST_DIRS or parts & DEFAULT_TEST_DIRS):
        return True
    return False


def _is_allowed(path: Path, allowed_paths: set[str], allowed_patterns: set[str]) -> bool:
    posix_path = path.as_posix()
    if posix_path in allowed_paths:
        return True
    return any(fnmatch.fnmatch(posix_path, pattern) for pattern in allowed_patterns)
