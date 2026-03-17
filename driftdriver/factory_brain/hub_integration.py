# ABOUTME: FactoryBrain controller — integrates brain tick loop into the ecosystem hub.
# ABOUTME: Manages roster lifecycle, enrollment/unenrollment directives, and dispatch-loop installation.
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from driftdriver.factory_brain.roster import (
    Roster,
    active_repos,
    enroll_repo,
    load_roster,
    save_roster,
    unenroll_repo,
)
from driftdriver.factory_brain.router import BrainState, run_brain_tick

_log = logging.getLogger(__name__)

_DISPATCH_LOOP_TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dispatch-loop.sh"


class FactoryBrain:
    """Controller that bridges the factory brain tick into the ecosystem hub collector loop."""

    def __init__(
        self,
        *,
        hub_data_dir: Path,
        workspace_roots: list[Path],
        dry_run: bool = False,
    ) -> None:
        self.hub_data_dir = hub_data_dir
        self.workspace_roots = workspace_roots
        self.dry_run = dry_run

        self.roster_file = hub_data_dir / "roster.json"
        self.roster: Roster = load_roster(self.roster_file)
        self.state = BrainState()
        self.log_dir = hub_data_dir / "brain-logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tick(
        self,
        *,
        snapshot: dict | None = None,
        heuristic_recommendation: dict | None = None,
    ) -> list[dict]:
        """Run one brain tick. Returns list of tier invocation results."""
        repos = active_repos(self.roster)
        if not repos:
            return []

        roster_repos = [Path(v["path"]) for v in repos.values()]

        results = run_brain_tick(
            state=self.state,
            roster_repos=roster_repos,
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            log_dir=self.log_dir,
            dry_run=self.dry_run,
        )

        # Process enrollment/unenrollment directives from results
        roster_changed = False
        for result in results:
            for attempt in result.get("results", []):
                action = attempt.get("action")
                status = attempt.get("status")
                if status != "deferred":
                    continue
                params = attempt.get("params", {})
                if action == "enroll" and "repo_path" in params:
                    try:
                        self._handle_enroll(params["repo_path"])
                        roster_changed = True
                    except Exception:
                        _log.exception("Failed to enroll %s", params["repo_path"])
                elif action == "unenroll" and "repo_name" in params:
                    try:
                        self._handle_unenroll(params["repo_name"])
                        roster_changed = True
                    except Exception:
                        _log.exception("Failed to unenroll %s", params["repo_name"])

        if roster_changed:
            save_roster(self.roster, self.roster_file)

        return results

    def _handle_enroll(self, repo_path_str: str) -> None:
        """Enroll a repo: verify it exists and has .workgraph/, install dispatch-loop if missing."""
        repo_path = Path(repo_path_str)
        if not repo_path.is_dir():
            _log.warning("Cannot enroll %s — directory does not exist", repo_path_str)
            return
        if not (repo_path / ".workgraph").is_dir():
            _log.warning("Cannot enroll %s — no .workgraph/ directory", repo_path_str)
            return

        enroll_repo(self.roster, path=repo_path_str, target="onboarded")

        # Install dispatch-loop.sh from template if missing
        dispatch_dest = repo_path / ".workgraph" / "dispatch-loop.sh"
        if not dispatch_dest.exists() and _DISPATCH_LOOP_TEMPLATE.exists():
            try:
                shutil.copy2(_DISPATCH_LOOP_TEMPLATE, dispatch_dest)
                dispatch_dest.chmod(0o755)
                _log.info("Installed dispatch-loop.sh in %s", repo_path_str)
            except OSError:
                _log.exception("Failed to install dispatch-loop.sh in %s", repo_path_str)

    def _handle_unenroll(self, repo_name: str) -> None:
        """Unenroll a repo by name."""
        if repo_name not in self.roster.repos:
            _log.warning("Cannot unenroll %s — not in roster", repo_name)
            return
        unenroll_repo(self.roster, name=repo_name)
