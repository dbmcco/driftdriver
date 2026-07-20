# ABOUTME: Migration 001 — remove the dead ECOSYSTEM_HUB_AUTOSTART block.
# ABOUTME: Strips the self-contained if/fi block driftdriver's ecosystem_hub
# ABOUTME: (decommissioned) added to .workgraph/handlers/session-start.sh.

from __future__ import annotations

from pathlib import Path

ID = "001"
DESCRIPTION = (
    "Remove the dead ECOSYSTEM_HUB_AUTOSTART block from "
    ".workgraph/handlers/session-start.sh (ecosystem_hub was decommissioned)."
)

# Byte-exact block emitted by driftdriver's install template: the marker comment,
# the outer if/fi (with one nested if/fi), and the trailing blank line that
# separated it from the following "# Prime agent" block.
_BLOCK = '''# Ensure ecosystem hub automation only starts when explicitly requested.
if [[ "${ECOSYSTEM_HUB_AUTOSTART:-0}" == "1" ]] && command -v driftdriver >/dev/null 2>&1; then
  HUB_ARGS=(ecosystem-hub --project-dir "$PROJECT_DIR" automate --host "${ECOSYSTEM_HUB_HOST:-0.0.0.0}" --port "${ECOSYSTEM_HUB_PORT:-8777}")
  if [[ -n "${ECOSYSTEM_HUB_CENTRAL_REPO:-}" ]]; then
    HUB_ARGS+=(--central-repo "$ECOSYSTEM_HUB_CENTRAL_REPO")
  fi
  driftdriver "${HUB_ARGS[@]}" >/dev/null 2>&1 || true
fi

'''

_MARKER = "# Ensure ecosystem hub automation only starts"
_VAR = "ECOSYSTEM_HUB_AUTOSTART"
_TARGET_REL = ".workgraph/handlers/session-start.sh"


def apply(repo_dir: Path, *, dry_run: bool = False) -> dict:
    repo_dir = Path(repo_dir)
    target = repo_dir / _TARGET_REL
    if not target.exists():
        return {
            "id": ID,
            "changed": False,
            "files": [],
            "note": "no session-start.sh present",
        }

    original = target.read_text()

    if _BLOCK not in original:
        # No exact match. If the ecosystem marker/var still present, the block
        # shape differs from the known template -> flag for manual review rather
        # than silently skipping (do not stamp).
        if _MARKER in original or _VAR in original:
            return {
                "id": ID,
                "changed": False,
                "files": [],
                "needs_review": True,
                "note": "ecosystem hook marker present but block shape differs "
                "from template; manual review required",
            }
        return {
            "id": ID,
            "changed": False,
            "files": [],
            "note": "block not present (already clean)",
        }

    updated = original.replace(_BLOCK, "", 1)
    if not dry_run:
        target.write_text(updated)

    try:
        rel = str(target.relative_to(repo_dir))
    except ValueError:
        rel = _TARGET_REL

    return {
        "id": ID,
        "changed": True,
        "files": [rel],
        "note": "stripped ECOSYSTEM_HUB_AUTOSTART block",
    }
