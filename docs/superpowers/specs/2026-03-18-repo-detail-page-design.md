# Per-Repo Detail Page — Design Spec
**Date:** 2026-03-18
**Status:** Draft

---

## Overview

The ecosystem hub currently shows every repo as a flat table row. All per-repo signals (git activity, workgraph task graph, services, presence actors, dependencies, health) exist in the snapshot but are either hidden, buried in hover states, or require expanding a row to access. There is no single place to look at one repo's full picture.

This spec adds a detail panel — a second "page" within the existing SPA — reachable by clicking any repo name in the main table. It shows all collected signals for one repo in a structured layout, with each section directly backed by data already in the snapshot or the activity-digests cache.

This panel is the anchor for future sub-projects: agent history timelines, service management controls, and session launch buttons will be added here as separate features.

---

## Architecture

### SPA panel, not a new Python route

The detail page is a full-screen overlay panel rendered in the existing SPA, not a separate HTML document served by Python.

**Rationale:**

- `dashboard.py` generates one large HTML string. Adding a second Python route for `/repo/:name` would require `api.py` to serve a second HTML template and handle parameterized routing — significant churn for no user-facing benefit.
- The existing `#task-graph-drawer` pattern already demonstrates that full-width panels work inside the SPA. The detail page is the same concept, larger in scope.
- All data the detail page needs is already fetched on load via the WebSocket snapshot. A new `GET /api/repo/:name` endpoint can serve a focused refresh, but the initial render costs nothing extra.
- URL-based navigation (`/repo/lodestar`) can still work via `history.pushState` + `popstate` without Python involvement, so bookmarkability is preserved if desired in a future iteration.

### View model

Two top-level "views" exist in the SPA:

- **`hub`** — the current dashboard (everything currently visible)
- **`repo-detail`** — the new per-repo detail panel

A JS variable `currentView` tracks which is active (`'hub'` by default). A JS variable `detailRepo` holds the currently viewed repo name when `currentView === 'repo-detail'`.

Switching views shows/hides the two root containers:

```html
<div id="view-hub">  <!-- existing hub layout -->
<div id="view-repo-detail" hidden>  <!-- new detail panel -->
```

No CSS transitions needed for v1 — show/hide is sufficient.

### Navigation model

- Clicking a repo name `<a>` in the main table calls `openRepoDetail(name)`.
- `openRepoDetail` sets `detailRepo = name`, `currentView = 'repo-detail'`, hides `#view-hub`, shows `#view-repo-detail`, renders all sections, and calls `history.pushState({view: 'repo-detail', repo: name}, '', '/repo/' + encodeURIComponent(name))`.
- A "← Back to hub" link at the top of the detail panel calls `closeRepoDetail()`, which reverses the state and calls `history.pushState({view: 'hub'}, '', '/')`.
- `window.addEventListener('popstate', ...)` handles browser back/forward.
- On initial page load, if `window.location.pathname` matches `/repo/<name>`, `openRepoDetail` is called immediately after the first data render.

The Python server does not need to handle `/repo/:name` — the browser fetches `/` (the hub root) for any navigation that hits the server, and the SPA handles the path client-side. This is the standard SPA pattern and works correctly with direct links as long as the server returns the hub HTML for unrecognized routes (which it currently does via the 404 JSON fallback — this needs one change: unrecognized GET paths that don't start with `/api/` or `/ws` should return the dashboard HTML rather than a JSON 404, enabling direct `/repo/<name>` links to work).

---

## API: GET /api/repo/:name

A new read-only endpoint that returns all per-repo signals in one payload. This is used for:

1. A focused refresh when the user is on the detail panel (avoids pulling the full 45-repo snapshot).
2. Future programmatic access (agent history, external tools).

### Route

```
GET /api/repo/:name
```

### Response shape

```json
{
  "name": "lodestar",
  "path": "/Users/braydon/projects/experiments/lodestar",
  "exists": true,
  "source": "ecosystem-toml",
  "tags": ["personal", "active-project", "lodestar"],
  "ecosystem_role": "product",
  "git": {
    "branch": "main",
    "dirty": false,
    "dirty_file_count": 0,
    "untracked_file_count": 0,
    "ahead": 0,
    "behind": 0
  },
  "activity": {
    "last_commit_at": "2026-03-18T13:45:00Z",
    "summary": "Braydon extended the scenario engine with regret scoring…",
    "timeline": [
      {
        "hash": "abc123",
        "timestamp": "2026-03-18T13:45:00Z",
        "subject": "feat: add regret scoring",
        "author": "Braydon McConaghy"
      }
    ]
  },
  "services": {
    "workgraph_service_running": true,
    "launchd_plist_loaded": null,
    "cron_jobs": []
  },
  "workgraph": {
    "exists": true,
    "task_counts": {"open": 12, "ready": 3, "in_progress": 2, "done": 147},
    "in_progress": [
      {"id": "t-42", "title": "Add regret scoring to scenario engine", "status": "in-progress"}
    ],
    "ready": [
      {"id": "t-43", "title": "Wire briefing history to UI"}
    ]
  },
  "presence_actors": [
    {"id": "claude-code", "name": "Claude Code", "last_seen": "2026-03-18T14:00:00Z"}
  ],
  "dependencies": {
    "depends_on": ["paia-memory", "paia-events"],
    "depended_on_by": ["paia-shell"]
  },
  "health": {
    "drift_score": 0.74,
    "drift_tier": "healthy",
    "security_findings": [],
    "quality_findings": [
      {"severity": "medium", "message": "Missing type hints in 3 functions"}
    ],
    "stalled": false,
    "stall_reasons": [],
    "narrative": "Lodestar is in active development…"
  }
}
```

### Implementation

The handler assembles data from two sources:

1. **Main snapshot** — reads `self.snapshot_path` (already done by `_read_snapshot()`), finds the matching repo entry, and projects it into the response shape.
2. **Activity digest** — reads `activity_path` (if configured) and finds the matching repo entry by name to populate `activity.timeline` and `activity.summary`.

The `services.launchd_plist_loaded` and `services.cron_jobs` fields require filesystem reads (described below). These are done inline on the request path — they are fast local reads, not subprocess calls.

The route is handled in `_HubHandler.do_GET`:

```python
if route.startswith("/api/repo/") and not route.endswith("/start"):
    repo_name = route[len("/api/repo/"):].strip("/")
    # assemble and return per-repo payload
```

The existing `/api/repo/:name/start` POST route takes precedence because it ends with `/start` and is matched first.

### launchd detection

Check for a plist in `~/Library/LaunchAgents/` matching the pattern `*<repo-name>*`. Read only — no subprocess calls.

```python
import glob, os
plist_pattern = str(Path.home() / "Library" / "LaunchAgents" / f"*{repo_name}*")
loaded = bool(glob.glob(plist_pattern))
```

This is advisory — `loaded` means a plist file exists, not that the service is currently running. Accurate enough for v1.

### Cron detection

Not collected in v1. `cron_jobs` is always `[]` in the API response. Cron detection (reading `crontab -l`) is deferred — it requires a subprocess call and the signal value is low.

---

## Detail Page Sections

The detail panel is a single scrolling `<div>` with a sticky header. Sections are rendered top to bottom. All data comes from the repo's entry in `currentData.repos` (already in memory), supplemented by a fresh `GET /api/repo/:name` call on panel open for activity timeline data not in the main snapshot.

### 1. Header

Sticky at the top of the panel. Contains:

- `← Back` link (calls `closeRepoDetail()`)
- Repo name (large, `<h1>`-weight)
- Role badge (e.g. `product`, `orchestrator`, `baseline`) — same `repoRole()` function used in the table
- Tag badges — same `.repo-tag-badge` style from the tagging spec, displayed inline
- GitHub/external URL link — shown only if `repo.source` is `"github"` or `"vibez"` or if the snapshot carries a `url` field; links open in a new tab
- "Open in editor" button — renders as `<a href="cursor://..." target="_blank">` using the `cursor://open?path=<repo.path>` URI scheme. Falls back to a `<code>` path display if the path is unavailable.

The header is `position: sticky; top: 0; z-index: 10` so it stays visible while scrolling through sections below.

### 2. Git Activity

Shows what has been happening in the repo at the code level.

**Content:**

- AI-generated prose summary (`activity.summary`) if available — displayed as a block quote or subtle prose paragraph. If null, shows "Summary pending next scan cycle."
- Last N commits (N=10) as a compact list:
  ```
  abc123f  feat: add regret scoring to scenario engine   3h ago   Braydon McConaghy
  ```
  Timestamps rendered as relative ("3h ago", "2 days ago") using the same `relTime()` helper already in dashboard.js.
- "Last commit: X ago" summary line if the list is empty but `activity.last_commit_at` is set.
- "No recent git activity (7+ days)" if no activity data.

**Data source:** `GET /api/repo/:name` response, `activity` field. The main snapshot does not carry the commit timeline — this is the primary reason for the focused API call on panel open.

**Loading state:** sections render a subtle "Loading…" placeholder while the API call is in flight.

### 3. Services

What infrastructure is running for this repo.

**Content — three signals, displayed as a 3-column status row:**

| Signal | Source | Display |
|--------|--------|---------|
| Workgraph service | `repo.service_running` | "Running" (green) / "Stopped" (muted) |
| launchd plist | `services.launchd_plist_loaded` from API | "Loaded" (green) / "Not found" (muted) / "Unknown" (muted, if API doesn't support it yet) |
| Cron jobs | `services.cron_jobs` | "None detected" (muted) in v1 |

If `service_running` is false and `workgraph_exists` is true, show the existing "Start service" button (already implemented via `POST /api/repo/:name/start`).

### 4. Workgraph

Task graph overview for this repo.

**Content:**

- Task count pills: `Open: 12  Ready: 3  In progress: 2  Done: 147` — same pill style used in the existing expanded-row view.
- In-progress task list (up to 5 tasks):
  ```
  ● feat: Add regret scoring to scenario engine   [in-progress]
  ● fix: Wire briefing history to UI              [ready]
  ```
  Task IDs shown in muted mono alongside titles. Clicking a task ID copies it to clipboard (small UX convenience, no routing needed).
- "Start service" button — same as Section 3, deduplicated if already shown there. The button appears here only if the service is stopped.
- "No workgraph" message if `repo.workgraph_exists` is false.

**Data source:** `repo.task_counts`, `repo.in_progress`, `repo.ready` from main snapshot. No extra API call needed for this section.

### 5. Active Agents (Presence)

Who is in this repo right now.

**Content:**

- If `presence_actors` is non-empty: list of actor cards, each showing actor name, type (claude-code, cursor, human), and "last seen X ago".
- If empty: "No active agents" in muted text.

Format:

```
● Claude Code    claude-code    last seen 4 min ago
● Braydon        human          last seen 12 min ago
```

A pulsing green dot (`:before` pseudo-element animation) indicates recently-active actors (last seen < 5 minutes).

**Data source:** `repo.presence_actors` from main snapshot.

### 6. Repo Dependencies

Cross-repo relationship graph for this repo.

**Content — two lists, side by side:**

- **Depends on** (repos this one depends on): listed with name links. Clicking a name navigates to that repo's detail panel.
- **Depended on by** (repos that depend on this one): same treatment.

If both lists are empty: "No cross-repo dependencies recorded."

**Data source:** `repo.cross_repo_dependencies` from main snapshot. This field is a list of `{repo, direction, type}` objects. Split into two lists by `direction` field (`"upstream"` vs `"downstream"`, or similar — check actual field shape at implementation time).

The full repo-dependency graph (the force-directed canvas) is not embedded here — it's a hub-level view. The detail page shows only the edges touching this repo, as text lists.

### 7. Health

Drift and quality signals.

**Content:**

- **Drift score** — single number and tier badge (`healthy` / `watch` / `critical`) from `repo.repo_north_star` or `repo.northstar`. If no score: "No drift data."
- **Security findings** — count and severity summary. If any: expandable list showing severity + message per finding (up to 5, collapsed by default). If zero: "No security findings."
- **Quality findings** — same pattern as security. Up to 5 shown, collapsible.
- **Stall indicator** — if `repo.stalled` is true, show a red "STALLED" badge with `stall_reasons` as a bulleted list.
- **Narrative** — `repo.narrative` as plain prose, if non-empty.

**Data source:** all from main snapshot (`repo_north_star`, `northstar`, `security_findings`, `quality_findings`, `stalled`, `stall_reasons`, `narrative`).

---

## Navigation & Routing

### Clicking a repo name

Currently repo names in the table are rendered as `<strong>` elements inside `<td>` cells. They need to become clickable.

Change in `renderRepoTable`: wrap the repo name in an `<a>` tag styled to look like the current `<strong>` (no underline by default, underline on hover):

```js
'<a class="repo-name-link" href="/repo/' + encodeURIComponent(repoName) + '" '
+ 'onclick="openRepoDetail(\'' + escAttr(repoName) + '\'); return false;">'
+ esc(repoName) + '</a>'
```

The `onclick` intercepts navigation for in-page transitions; the `href` provides a real URL for middle-click / Cmd+click to open in a new tab (where Python's unrecognized-route fallback will return the dashboard HTML and the SPA will open the correct detail panel via `popstate`).

### URL state

| URL | View | JS state |
|-----|------|----------|
| `/` | Hub | `currentView = 'hub'` |
| `/?q=paia&role=product` | Hub with filters | `currentView = 'hub'` + filter vars |
| `/repo/lodestar` | Detail panel for lodestar | `currentView = 'repo-detail'`, `detailRepo = 'lodestar'` |

The Python server does not need to parse `/repo/:name`. It returns the full dashboard HTML for any non-API, non-WS GET request. This is a one-line change to `do_GET`:

```python
# Before (returns JSON 404 for unknown routes):
self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)

# After (returns dashboard HTML for non-API routes, enabling SPA deep links):
if not route.startswith("/api/") and not route.startswith("/ws"):
    self._send_html(render_dashboard_html())
    return
self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
```

### Back navigation

The `←` link at the top of the detail panel and the browser back button both call `closeRepoDetail()`. This restores the hub view and scrolls the previously-selected repo row into view.

---

## File Changeset Summary

| File | Change |
|------|--------|
| `driftdriver/ecosystem_hub/api.py` | Add `GET /api/repo/:name` handler; fix unrecognized-route fallback to serve dashboard HTML |
| `driftdriver/ecosystem_hub/dashboard.py` | Add `#view-hub` / `#view-repo-detail` wrappers; add detail panel HTML structure; add `openRepoDetail`, `closeRepoDetail`, `renderRepoDetail` JS functions; make repo name cells into links; handle `popstate` for browser navigation |

No changes to `snapshot.py`, `models.py`, `discovery.py`, `server.py`, or `activity_cache.py`. All data is already collected — this is purely a presentation layer.

---

## Non-Goals

- **No editing from the detail page** — read-only in v1. Editing repo metadata means editing `ecosystem.toml` directly.
- **No comment or annotation system** — no user-authored notes on repos.
- **No repo comparison view** — one repo at a time; side-by-side is a separate feature.
- **Agent history timeline** — a future sub-project. The presence actors section shows current state only; historical actor traces are not shown.
- **Session launch** — a future sub-project. The "Open in editor" button is a static URI link, not a managed session launch workflow.
- **Cron job detection** — deferred. The `cron_jobs` field in the API returns `[]` in v1.
- **Task graph visualization** — the existing `#task-graph-drawer` already serves this. The detail page shows task counts and the in-progress list, not the SVG DAG. The drawer can be opened from the hub table view as before; a link from the detail page to the drawer is a future convenience.
- **Live WebSocket updates on the detail panel** — the detail panel refreshes once on open via `GET /api/repo/:name`. Real-time push updates on the detail panel are deferred; the hub WebSocket continues to serve the main table only.
