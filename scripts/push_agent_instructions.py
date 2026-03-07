#!/usr/bin/env python3
# ABOUTME: Pushes updated CLAUDE.md and AGENTS.md speedrift blocks to all workgraph-enabled repos.
# ABOUTME: Uses sentinel comments to replace only the managed block, preserving repo-specific content.

from __future__ import annotations

import re
from pathlib import Path

EXPERIMENTS_DIR = Path(__file__).resolve().parent.parent.parent

CLAUDE_BLOCK = """\
<!-- driftdriver-claude:start -->
## Speedrift Ecosystem

**Speedrift** is the development quality system across this workspace. It combines
[Workgraph](https://github.com/graphwork/workgraph) (task spine) with
[Driftdriver](https://github.com/dbmcco/driftdriver) (drift orchestrator) to keep
code, specs, and intent in sync without hard-blocking work.

Use `/speedrift` (or `/rifts`) to invoke the full protocol skill.

### Quick Reference

```bash
# Drift-check a task (run at start + before completion)
./.workgraph/drifts check --task <id> --write-log --create-followups

# Ecosystem dashboard (40+ repos, pressure scores, action queue)
# Local:     http://127.0.0.1:8777/
# Tailscale: http://100.77.214.44:8777/

# Create tasks with current wg flags
wg add "Title" --after <dep-id> --immediate --verify "test command"
```

### Runtime Authority
- Workgraph is the task/dependency source of truth. `speedriftd` is the repo-local supervisor.
- Sessions default to `observe`. Do not use `wg service start` as a generic kickoff.
- Refresh state: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- Arm repo: `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent> --reason "reason"`
- Disarm: `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "done"`

### What Happens Automatically
- **Drift task guard**: follow-up tasks are deduped + capped at 3 per lane per repo
- **Notifications**: significant findings alert via terminal/webhook/wg-notify
- **Prompt evolution**: recurring drift patterns trigger `wg evolve` to teach agents
- **Outcome learning**: resolution rates feed back into notification significance scoring
<!-- driftdriver-claude:end -->"""

AGENTS_BLOCK = """\
<!-- driftdriver-codex:start -->
## Speedrift Ecosystem

**Speedrift** is the development quality system across this workspace. It combines
[Workgraph](https://github.com/graphwork/workgraph) (task spine) with
[Driftdriver](https://github.com/dbmcco/driftdriver) (drift orchestrator) to keep
code, specs, and intent in sync without hard-blocking work.

### Quick Reference

```bash
# Drift-check a task (run at start + before completion)
./.workgraph/drifts check --task <id> --write-log --create-followups

# Ecosystem dashboard
# Local:     http://127.0.0.1:8777/
# Tailscale: http://100.77.214.44:8777/

# Create tasks with current wg flags
wg add "Title" --after <dep-id> --immediate --verify "test command"
```

### Lifecycle Hooks
- Session start: `./.workgraph/handlers/session-start.sh --cli codex`
- Task claimed: `./.workgraph/handlers/task-claimed.sh --cli codex`
- Before completion: `./.workgraph/handlers/task-completing.sh --cli codex`
- On error: `./.workgraph/handlers/agent-error.sh --cli codex`

### Runtime Authority
- Workgraph is the task/dependency source of truth. `speedriftd` is the repo-local supervisor.
- Sessions default to `observe`. Do not use `wg service start` as a generic kickoff.
- Refresh state: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- Arm repo: `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent> --reason "reason"`
- Disarm: `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "done"`

### What Happens Automatically
- **Drift task guard**: follow-up tasks are deduped + capped at 3 per lane per repo
- **Notifications**: significant findings alert via terminal/webhook/wg-notify
- **Prompt evolution**: recurring drift patterns trigger `wg evolve` to teach agents
- **Outcome learning**: resolution rates feed back into notification significance scoring
<!-- driftdriver-codex:end -->"""

SKIP_PREFIXES = ("speedrift-ecosystem-v2-run",)


def update_block(filepath: Path, start_marker: str, end_marker: str, block: str) -> str:
    """Replace or append a managed block in a file. Returns action taken."""
    if not filepath.exists():
        filepath.write_text(block + "\n", encoding="utf-8")
        return "created"

    content = filepath.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )
    if pattern.search(content):
        new_content = pattern.sub(block, content)
        if new_content != content:
            filepath.write_text(new_content, encoding="utf-8")
            return "replaced"
        return "unchanged"
    else:
        filepath.write_text(content.rstrip() + "\n\n" + block + "\n", encoding="utf-8")
        return "appended"


def main() -> None:
    updated = 0
    skipped = 0

    for repo_dir in sorted(EXPERIMENTS_DIR.iterdir()):
        if not repo_dir.is_dir():
            continue
        if not (repo_dir / ".workgraph").is_dir():
            continue

        name = repo_dir.name
        if any(name.startswith(p) for p in SKIP_PREFIXES):
            skipped += 1
            continue

        print(f"[{name}]")

        action_c = update_block(
            repo_dir / "CLAUDE.md",
            "<!-- driftdriver-claude:start -->",
            "<!-- driftdriver-claude:end -->",
            CLAUDE_BLOCK,
        )
        print(f"  CLAUDE.md: {action_c}")

        action_a = update_block(
            repo_dir / "AGENTS.md",
            "<!-- driftdriver-codex:start -->",
            "<!-- driftdriver-codex:end -->",
            AGENTS_BLOCK,
        )
        print(f"  AGENTS.md: {action_a}")

        updated += 1

    print(f"\nDone: {updated} repos updated, {skipped} skipped")


if __name__ == "__main__":
    main()
