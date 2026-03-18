# Git Activity Tracking — Design Spec
**Date:** 2026-03-18
**Status:** Approved for implementation

---

## Overview

Extend the Speedrift Ecosystem Hub with per-repo git activity tracking. The hub currently shows WorkGraph-driven activity (agents, tasks, pressure scores) but is blind to direct coding work in repos that lack active WorkGraph tasks. This feature adds a git-commit-backed activity layer: a cross-repo timeline of recent commits and an LLM-generated prose summary per repo, updated every 15 minutes, surfaced in two places — a top-level timeline panel and inline per-repo rows in the existing pressure grid.

New repos registered in `ecosystem.toml` are picked up automatically on the next scan cycle with no manual intervention.

---

## Architecture

### New files

| File | Responsibility |
|------|---------------|
| `driftdriver/ecosystem_hub/activity.py` | Git scanner. Iterates all known repos, runs `git log`, groups commits into time windows, writes `activity-digests.json`. No LLM. |
| `driftdriver/ecosystem_hub/activity_summarizer.py` | LLM digest. Calls Claude Haiku for repos where `last_commit_hash != summary_hash`. Stores prose summary back into the digest. |
| `driftdriver/ecosystem_hub/activity_cache.py` | Thin read/write wrapper for `activity-digests.json`. Same pattern as the snapshot cache. |

### Modified files

| File | Change |
|------|--------|
| `server.py` | Add `activity-scanner` background thread running every 15 minutes. |
| `api.py` | Add `GET /api/activity` endpoint with `?window=24h\|48h\|72h\|7d` param. |
| `dashboard.py` | Add timeline panel (top of hub) and inline activity row per repo card. |

### Cache location

```
.workgraph/service/ecosystem-hub/activity-digests.json
```

Written by the scanner thread. Read by the API handler on every request. If the file does not exist (hub started before first scan), the API returns an empty-but-valid response.

---

## Git Scanner

Runs every 15 minutes in a dedicated daemon thread inside the hub process.

For each repo in the hub's registry, executes two git commands:

```
git log --since="8 days ago" --format="%H|%ai|%s|%an|%ae" HEAD
git log --since="8 days ago" --name-only --format="" HEAD
```

The first yields: hash, ISO timestamp, subject, author name, author email.
The second yields changed filenames (used as LLM context, not displayed raw).

Commits are stored for the full 8-day window and filtered to the four display windows (24h / 48h / 72h / 7d) at read time — no per-window git calls.

### Error handling

- Repo not a git repo → silently skipped (no entry in digest)
- `git log` fails → silently skipped, previous digest entry preserved
- Repo has commits but all are older than 8 days → entry written with all window counts at 0

### New repo auto-tracking

No special handling required. The scanner iterates whatever repos the hub knows about on each cycle. Any repo added to `ecosystem.toml` appears automatically on the next 15-minute pass.

---

## Digest Data Model

Per-repo entry in `activity-digests.json`:

```python
{
  "name": "lodestar",
  "path": "/Users/braydon/projects/experiments/lodestar",
  "last_commit_at": "2026-03-18T13:45:00Z",   # ISO UTC, null if no commits in window
  "last_commit_hash": "abc123",                 # null if no commits
  "summary": "Braydon extended the scenario...", # LLM prose, null until summarizer runs
  "summary_hash": "abc123",                      # commit hash the summary was built from
  "windows": {
    "24h": {"count": 4, "subjects": ["feat: add regret scoring", ...]},
    "48h": {"count": 9, "subjects": [...]},
    "72h": {"count": 9, "subjects": [...]},
    "7d":  {"count": 14, "subjects": [...]}
  }
}
```

Top-level digest file:

```json
{
  "generated_at": "2026-03-18T14:30:00Z",
  "repos": [ ... ]
}
```

---

## LLM Summarization

Runs after each scanner cycle. Processes only repos where `last_commit_hash != summary_hash` (new commits since last summary).

**Model:** Claude Haiku via `anthropic` SDK (already a dependency in the intelligence module).

**Prompt per repo (~200 input tokens):**

```
Repo: lodestar
Recent commits (last 7 days):
- feat: add regret scoring to scenario engine (2026-03-18)
- fix: briefing page 404 on empty history (2026-03-17)
Changed files: src/scenario/, src/briefings/, src/decisions/

Write 2-3 sentences describing what's been happening in this repo.
Be specific about what was built or fixed. No filler.
```

**Output:** plain prose, stored as `summary`. No JSON parsing.

**Cache key:** `(repo_name, last_commit_hash)` — same input always produces the same summary, so re-running is safe.

**Fallback:** if the Haiku call fails, `summary` remains null. Scanner results are still valid and displayed without prose.

**Cost:** ~$0.002 per cycle for 10 active repos. Negligible.

**Deliberate constraint:** one summary per repo covers the full 7-day window. The same summary is shown regardless of which time filter (24h / 48h / 72h / 7d) is active. It answers "what's been happening here" — not "what happened in exactly the last 48 hours."

---

## API

### `GET /api/activity?window=48h`

Accepted window values: `24h`, `48h`, `72h`, `7d`. Default: `48h`.

Response:

```json
{
  "generated_at": "2026-03-18T14:30:00Z",
  "window": "48h",
  "timeline": [
    {
      "repo": "lodestar",
      "hash": "abc123",
      "timestamp": "2026-03-18T13:45:00Z",
      "subject": "feat: add regret scoring",
      "author": "Braydon McConaghy"
    }
  ],
  "repos": [
    {
      "name": "lodestar",
      "last_commit_at": "2026-03-18T13:45:00Z",
      "summary": "Braydon extended the scenario engine...",
      "window_count": 4
    }
  ]
}
```

`timeline`: all individual commits across all repos in the requested window, sorted newest-first.
`repos`: per-repo digest entries that have at least one commit in the requested window, sorted by `last_commit_at` descending.

---

## UI

### Timeline panel (top of hub)

A new "Recent Activity" section above the repo pressure grid. Compact commit feed:

```
[lodestar]  feat: add regret scoring to scenario engine  · 3h ago
[paia-os]   fix: session handler race condition          · 5h ago
[lfw-ai]    feat: engagement scoring v2                  · 8h ago
```

- Default window: 48h. Tab pills to switch: 24h / 48h / 72h / 7d.
- Clicking a row scrolls to and highlights that repo in the grid below.
- Repos with no commits in the selected window are absent from the feed.

### Inline per-repo (pressure grid)

Each repo card in the pressure grid gets a bottom row:

- **Has summary:** `Last active: 3h ago · "Extended scenario engine with regret scoring and briefing fixes"`
- **Has commits, no summary yet:** `4 commits in last 24h`
- **No commits in 7d:** `No recent git activity`

The active window pill (from the timeline panel) controls which count is shown in the fallback case. Summary is always the full-window prose regardless of selected window.

---

## Testing

### `tests/test_activity_scanner.py`

Uses a real `git init` fixture in `tmp_path` with staged commits at controlled timestamps. Verifies:
- Commits appear in correct time windows
- Zero-commit repos produce empty-but-valid entries
- Git errors are silently skipped, previous entry preserved
- 8-day cutoff is respected

No subprocess mocking — real `git` commands against the fixture repo.

### `tests/test_activity_summarizer.py`

Mocks the `anthropic` client. Verifies:
- Summary requested only when `last_commit_hash != summary_hash`
- Fallback to `summary=None` on API error (no exception raised)
- Prompt includes expected commit subjects and changed filenames

### `tests/test_activity_api.py`

Loads a pre-baked `activity-digests.json` fixture. Calls the API handler directly. Verifies:
- `?window=48h` filters timeline to correct commits
- `repos` sorted by `last_commit_at` descending
- Missing digest file returns `{"timeline": [], "repos": []}` not an error

---

## Non-Goals

- No per-author filtering — all commits in the repo are shown (these are Braydon's repos)
- No diff content in the LLM prompt — filenames only (keeps tokens low, avoids sensitive code in API calls)
- No historical archive beyond 8 days — this is a "what's fresh" view, not a full history
- No write-back to WorkGraph — activity data is read-only, advisory
