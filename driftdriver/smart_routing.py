# ABOUTME: Evidence gathering for model-mediated smart lane routing
# ABOUTME: Assembles change context, file classifications, and project knowledge

from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional
import os
import subprocess
import json


@dataclass
class EvidencePackage:
    """Structured evidence for model-mediated lane routing decisions."""
    changed_files: dict[str, str]  # path -> change_type (added/modified/deleted)
    file_classifications: dict[str, list[str]]  # path -> suggested lanes
    task_description: str
    task_contract: dict
    project_context: list[dict]  # knowledge entries from Lessons MCP
    prior_drift_findings: list[dict]  # recent drift results
    installed_lanes: list[str]
    pattern_hints: dict[str, list[str]] = field(default_factory=dict)  # lane -> glob patterns

    def classify_files(self) -> dict[str, list[str]]:
        """Classify changed files into lane suggestions based on glob patterns."""
        classifications: dict[str, list[str]] = {}
        for filepath in self.changed_files:
            lanes: list[str] = []
            for lane, patterns in self.pattern_hints.items():
                for pattern in patterns:
                    if fnmatch(filepath, pattern) or fnmatch(Path(filepath).name, pattern):
                        lanes.append(lane)
                        break
            classifications[filepath] = lanes
        self.file_classifications = classifications
        return classifications

    def suggest_lanes(self) -> set[str]:
        """Return unique lanes suggested by file classification."""
        if not self.file_classifications:
            self.classify_files()
        lanes: set[str] = set()
        for file_lanes in self.file_classifications.values():
            lanes.update(file_lanes)
        return lanes

    def to_prompt_context(self) -> str:
        """Format evidence for model consumption."""
        if not self.file_classifications:
            self.classify_files()

        sections = []
        sections.append(f"## Task\n{self.task_description}")

        if self.task_contract:
            sections.append(f"## Task Contract\n{json.dumps(self.task_contract, indent=2)}")

        sections.append("## Changed Files")
        for path, change_type in self.changed_files.items():
            lane_hints = self.file_classifications.get(path, [])
            hint_str = f" (hints: {', '.join(lane_hints)})" if lane_hints else ""
            sections.append(f"- {path}: {change_type}{hint_str}")

        sections.append(f"\n## Installed Lanes\n{', '.join(self.installed_lanes)}")
        sections.append(f"\n## Pattern-Suggested Lanes\n{', '.join(self.suggest_lanes()) or 'none'}")

        if self.prior_drift_findings:
            sections.append("## Prior Drift Findings")
            for finding in self.prior_drift_findings[-5:]:
                sections.append(f"- {finding}")

        if self.project_context:
            sections.append("## Project Context (from Lessons MCP)")
            for entry in self.project_context[:5]:
                sections.append(f"- [{entry.get('category', '?')}] {entry.get('content', '')[:200]}")

        return "\n".join(sections)


def parse_git_diff_stat(diff_output: str) -> dict[str, str]:
    """Parse `git diff --stat --name-status` output into {path: change_type}."""
    result: dict[str, str] = {}
    for line in diff_output.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, path = parts
            change_map = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed"}
            result[path] = change_map.get(status[0], "unknown")
    return result


def load_pattern_hints(policy_path: Path) -> dict[str, list[str]]:
    """Load lane routing patterns from drift-policy.toml."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    if not policy_path.exists():
        return {}

    with open(policy_path, "rb") as f:
        config = tomllib.load(f)

    return config.get("lane-routing", {}).get("patterns", {})


def gather_evidence(
    workgraph_dir: Path,
    task_id: Optional[str] = None,
) -> EvidencePackage:
    """Assemble evidence package from project state."""
    # Get git diff
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD~1"],
            capture_output=True, text=True, cwd=workgraph_dir.parent
        )
        changed_files = parse_git_diff_stat(diff_result.stdout)
    except Exception:
        changed_files = {}

    # Load task info from workgraph
    task_description = ""
    task_contract = {}
    if task_id:
        try:
            wg_result = subprocess.run(
                ["wg", "show", task_id, "--json"],
                capture_output=True, text=True, cwd=workgraph_dir.parent
            )
            if wg_result.returncode == 0:
                task_data = json.loads(wg_result.stdout)
                task_description = task_data.get("description", "")
                task_contract = task_data.get("contract", {})
        except Exception:
            pass

    # Lane wrappers are executable scripts directly in .workgraph/
    # (e.g., .workgraph/coredrift, .workgraph/specdrift)
    from driftdriver.routing_models import KNOWN_LANES
    installed_lanes = [
        p.name for p in workgraph_dir.iterdir()
        if p.is_file() and p.name in KNOWN_LANES and os.access(p, os.X_OK)
    ]

    # Load pattern hints from policy
    policy_path = workgraph_dir.parent / "drift-policy.toml"
    pattern_hints = load_pattern_hints(policy_path)

    return EvidencePackage(
        changed_files=changed_files,
        file_classifications={},
        task_description=task_description,
        task_contract=task_contract,
        project_context=[],
        prior_drift_findings=[],
        installed_lanes=installed_lanes,
        pattern_hints=pattern_hints,
    )
