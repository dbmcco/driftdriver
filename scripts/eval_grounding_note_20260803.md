# Grounding eval — existdrift pre-plan evidence (2026-08-03)

Heartbeat goal planned via the live quality path, zai/GLM-5.2, with and without
the existdrift evidence bundle. Touch paths classified against the workspace.

| run | paths | exact-exist | parent-dir-exists | wrong-dir |
|---|---|---|---|---|
| ungrounded | 15 | 0 | 3 | 12 (80% structurally fictional) |
| grounded | 20 | 13 | 19 | 1 (5%) |

Grounded plan extras: repo-aware workspace-relative paths; discovered
paia-contracts as the shared-schema home; remaining nonexistent paths are
legitimate new-file creations in verified directories.
