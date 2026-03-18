# Service Management UI — Design Spec
**Date:** 2026-03-18
**Status:** Draft

---

## Overview

The ecosystem hub currently has one service control: a "Start Workgraph Service" button per repo that fires `POST /api/repo/:name/start`. There is no stop, restart, or status-check beyond this. There is no visibility into launchd services at all, and no cron detection.

This spec adds a **Services section** to the per-repo detail page (Sub-project 2). It surfaces one card per service type detected for a repo, with status indicators and start/stop/restart controls. The detail page is the natural home because service controls are repo-scoped, not hub-level.

The design is intentionally narrow. No log streaming, no service dependency ordering, no health checks beyond running/stopped. Those belong in later iterations once the basics are used.

---

## Service Types & Detection

### 1. Workgraph Service

**Detection:**
- Check if `<repo_path>/.workgraph/` exists. If not, service type is not present.
- If `.workgraph/` exists, call `wg service status` in the repo directory, or read `heartbeat_age_seconds` from the current snapshot's per-repo data if already available.
- Status: `running` if the service is alive, `stopped` if the directory exists but the service is not running.

**Controls:** Start, Stop.

**Notes:**
- `wg service start` already exists as `POST /api/repo/:name/start`. The new endpoint (`/service/workgraph/start`) supersedes it but the old route is kept for backward compatibility in v1. Both hit the same underlying logic.
- `wg service stop` is new. The wg CLI supports it — the hub just hasn't exposed it yet.

### 2. launchd Service

**Detection:**
- Scan `~/Library/LaunchAgents/` for plist files whose `WorkingDirectory` value or filename contains the repo's name or absolute path. This is the primary matching signal, grounded in real plist structure (confirmed: plists use `WorkingDirectory` pointing at the repo path).
- Parse each candidate plist file using Python's built-in `plistlib` to extract `Label` and `WorkingDirectory`. Match if `WorkingDirectory` is the repo path or if the filename contains the repo name as a word segment (e.g., `com.paia.shell.plist` matches repo `paia-shell` by normalizing hyphens and dots).
- To determine running vs. stopped: run `launchctl list` once, parse output into a dict of `label → pid_or_dash`. A service is **running** if its label has a numeric PID column; **stopped** if the column is `-`.
- A repo may have multiple plists (e.g., `com.lfw.graph.crm.plist` and `com.lfw.graph.crm.proactive-worker.plist`). Return each as a separate entry.

**`launchctl list` output format:**
```
PID    LastExitCode    Label
96464  143             com.paia.media
-      0               com.braydon.driftdriver-ecosystem
```
A running service has a numeric PID. A stopped/unloaded-but-listed service shows `-`.

**Controls:** Start (`launchctl load <plist_path>`), Stop (`launchctl unload <plist_path>`), Restart (unload + load).

**Detection is best-effort.** If the plist file uses an unusual directory structure or its `WorkingDirectory` doesn't match the repo path exactly, it won't be detected. That's acceptable for v1.

### 3. Cron Jobs

**Detection:**
- Run `crontab -l` and grep for the repo's absolute path. Return any matching lines.
- Display only — no start/stop controls in v1.
- If no cron jobs found, the card is omitted entirely rather than shown as empty.

**Controls:** None.

---

## API Endpoints

### `GET /api/repo/:name/services`

Returns all detected services for a repo with current status.

**Implementation notes:**
- `wg service status` is a subprocess call; run with `timeout=5`. On timeout or non-zero return, treat as `stopped`.
- `launchctl list` is run once per request and cached for the duration of the request (not globally — it's fast enough). Parsed into `{label: pid_or_none}`.
- Plist parsing uses `plistlib.load()` — built-in, no dependencies. Parse errors on a given plist are silently skipped (log at DEBUG level).
- `crontab -l` is run with `timeout=5`. Non-zero return code (no crontab) yields empty list.
- All subprocess calls use explicit argument lists (no shell=True). See Security section.

**Response shape:**

```json
{
  "repo": "paia-shell",
  "services": {
    "workgraph": {
      "present": true,
      "status": "running"
    },
    "launchd": [
      {
        "label": "com.paia.shell",
        "plist_path": "/Users/braydon/Library/LaunchAgents/com.paia.shell.plist",
        "status": "running",
        "pid": 49062
      }
    ],
    "cron": {
      "jobs": []
    }
  }
}
```

For a repo with no launchd plist detected, `launchd` is an empty list `[]`.
For a repo with no workgraph, `workgraph` is `{"present": false, "status": null}`.

**Route placement in `api.py`:**

Add to `do_GET` before the final `not_found` fallback:

```python
if route.startswith("/api/repo/") and route.endswith("/services"):
    repo_name = route[len("/api/repo/"):-len("/services")]
    # ... detect and return
```

---

### `POST /api/repo/:name/service/workgraph/start`

Runs `wg service start` in the repo directory. Identical behavior to the existing `/api/repo/:name/start`. The old route is preserved as an alias in v1.

**Response:**
```json
{"repo": "paia-shell", "action": "workgraph/start", "returncode": 0, "stdout": "...", "stderr": "..."}
```

---

### `POST /api/repo/:name/service/workgraph/stop`

Runs `wg service stop` in the repo directory.

**Validation:** Same as start — confirm `.workgraph/` exists before running the command.

---

### `POST /api/repo/:name/service/launchd/start`

Runs `launchctl load <plist_path>`.

**Request body:**
```json
{"plist_path": "/Users/braydon/Library/LaunchAgents/com.paia.shell.plist"}
```

The `plist_path` must be validated server-side — see Security section.

---

### `POST /api/repo/:name/service/launchd/stop`

Runs `launchctl unload <plist_path>`.

Same request body and validation as start.

---

### `POST /api/repo/:name/service/launchd/restart`

Runs `launchctl unload <plist_path>` then `launchctl load <plist_path>` sequentially.

Same request body and validation. If `unload` fails, do not proceed to `load`. Return both step results.

**Response:**
```json
{
  "repo": "paia-shell",
  "action": "launchd/restart",
  "plist_path": "/Users/braydon/Library/LaunchAgents/com.paia.shell.plist",
  "unload": {"returncode": 0, "stdout": "", "stderr": ""},
  "load": {"returncode": 0, "stdout": "", "stderr": ""}
}
```

---

## UI (Service Cards on the Detail Page)

Service cards live in the **Services section** of the per-repo detail page (Section 3 of the detail page spec). They replace the simple 3-column status row described in the detail page spec with richer cards that include action buttons.

### Layout

One card per service type detected. Cards are arranged vertically within the Services section. If no services are detected for a repo, the section shows "No services detected" in muted text.

The section loads asynchronously: on `openRepoDetail(name)`, fire `GET /api/repo/:name/services` and render cards when the response arrives. Show a subtle "Checking services…" placeholder in the meantime.

### Workgraph Service Card

```
┌─────────────────────────────────────────────┐
│  ⚙ Workgraph Service              ● Running │
│                                             │
│  [ Stop ]                                   │
└─────────────────────────────────────────────┘
```

- Status badge: `● Running` (green dot) or `○ Stopped` (muted).
- If running: show **Stop** button only.
- If stopped: show **Start** button only.
- Button click fires the appropriate POST endpoint; on response, re-fetch `/api/repo/:name/services` to update status (no optimistic UI — wait for the actual result).
- If `workgraph.present` is false: card is not rendered.

### launchd Service Card(s)

One card per entry in `launchd` array. If a repo has two launchd plists, two cards are shown.

```
┌─────────────────────────────────────────────┐
│  ■ launchd                        ● Running │
│  com.paia.shell                             │
│                                             │
│  [ Stop ]  [ Restart ]                      │
└─────────────────────────────────────────────┘
```

- Title: "launchd" with the plist label shown in muted mono below.
- Status badge: `● Running` (green) / `○ Stopped` (muted).
- If running: show **Stop** and **Restart** buttons.
- If stopped: show **Start** button only.
- `plist_path` is sent in the POST body by the JS from the services response data — not from user input.

### Cron Card

Only rendered if `cron.jobs` is non-empty.

```
┌─────────────────────────────────────────────┐
│  ⏱ Cron Jobs                   (read-only)  │
│                                             │
│  0 8 * * 1   /path/to/repo/scripts/weekly  │
└─────────────────────────────────────────────┘
```

- Cron entries listed as `<code>` lines.
- No buttons. A subtle "(read-only)" label in the corner.

### Button States

- Buttons are disabled while an action is in flight (set `disabled` attribute + change label to "Working…").
- On error response, show a small inline error below the card: `Error: <stderr[:100]>`. Clear on next successful action.
- On success, re-fetch and re-render the card.

### Styling

Cards use the same border/shadow pattern as other cards in the hub SPA. Status colors:
- Running: `#22c55e` (green-500)
- Stopped: `#6b7280` (gray-500, muted)

No new CSS classes needed beyond what exists. Use `style` attributes on the status badge dot inline for v1.

---

## Security Considerations (Subprocess Safety)

The service management endpoints run subprocesses as the hub process user. This is inherently privileged. The following constraints apply:

### Argument allowlisting — no shell=True, ever

All subprocess calls use explicit argument lists. The following patterns are the only permitted forms:

```python
# Workgraph
["wg", "service", "start"]   # cwd=repo_path
["wg", "service", "stop"]    # cwd=repo_path

# launchd
["launchctl", "load", validated_plist_path]
["launchctl", "unload", validated_plist_path]

# Detection
["launchctl", "list"]
["crontab", "-l"]
```

`shell=False` is the default for `subprocess.run` — this just means never use `shell=True` or pass a string command.

### Plist path validation

The `plist_path` parameter in launchd POST requests must be validated before use:

1. Resolve to an absolute path using `Path(plist_path).resolve()`.
2. Confirm it exists and is a regular file.
3. Confirm it is within `Path.home() / "Library" / "LaunchAgents"` — i.e., `resolved_path.parts` starts with the LaunchAgents directory. Reject anything outside this directory with HTTP 400.
4. Confirm the resolved path was present in the services response for this repo (i.e., it was discovered by the hub's own detection, not supplied arbitrarily). This prevents the client from supplying a path to an unrelated plist. Implementation: when handling the POST, re-run detection for the repo and confirm `plist_path` is in the returned list before proceeding.

This makes it impossible for a compromised browser session to trigger `launchctl load` on an arbitrary plist.

### Repo path validation

All workgraph endpoints use `_find_repo_path(repo_name)` (already implemented in `api.py`) to resolve the repo name to a path. This path comes from the snapshot — not from user input — so it is already trusted. No additional validation needed beyond the existing `is_dir()` check.

### Rate limiting

Not implemented in v1. The hub is localhost-only; rate limiting is deferred.

### Timeout enforcement

All subprocess calls use `timeout=15` (workgraph) or `timeout=10` (launchctl). On `subprocess.TimeoutExpired`, return HTTP 504 with `{"error": "timeout"}`.

---

## Implementation Notes

### New helper: `_detect_services(repo_name, repo_path) -> dict`

A private function in `api.py` that runs detection for all three service types and returns the structured payload. Called by both `GET /api/repo/:name/services` and the validation step in launchd POST handlers.

```python
def _detect_services(repo_name: str, repo_path: str) -> dict[str, Any]:
    # 1. Workgraph: check .workgraph/ dir, run `wg service status`
    # 2. launchd: scan ~/Library/LaunchAgents/, parse plists, run launchctl list
    # 3. Cron: run crontab -l, grep for repo_path
    ...
```

### `plistlib` for plist parsing

Python's built-in `plistlib` parses the XML plist format. Read with `plistlib.load(fp)`. This avoids any external dependency. Parse errors (malformed plists) are caught and the file is skipped.

```python
import plistlib
with open(plist_path, "rb") as fp:
    data = plistlib.load(fp)
working_dir = data.get("WorkingDirectory", "")
```

### `launchctl list` parsing

Output is whitespace-separated with three columns: PID, LastExitCode, Label. A PID of `-` means not running.

```python
def _parse_launchctl_list(output: str) -> dict[str, int | None]:
    result = {}
    for line in output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3:
            pid_str, _, label = parts
            result[label] = int(pid_str) if pid_str != "-" else None
    return result
```

### Matching plist files to repos

Two matching strategies, applied in order:
1. `WorkingDirectory` field in parsed plist equals `repo_path` (exact string match after normalization).
2. Filename heuristic: normalize repo name (replace `-` with `.`, lowercase) and check if the plist filename (without `.plist` extension) contains it as a substring. E.g., `com.paia.shell.plist` matches `paia-shell` via `"com.paia.shell".find("paia.shell") >= 0`.

Strategy 1 is preferred and more reliable. Strategy 2 catches plists that lack `WorkingDirectory` or use a slightly different path.

---

## File Changeset Summary

| File | Change |
|------|--------|
| `driftdriver/ecosystem_hub/api.py` | Add `GET /api/repo/:name/services`; add `POST /api/repo/:name/service/workgraph/start` (alias), `stop`; add `POST /api/repo/:name/service/launchd/start`, `stop`, `restart`; add `_detect_services()` helper; add `_parse_launchctl_list()` helper |
| `driftdriver/ecosystem_hub/dashboard.py` | Replace Services section's 3-column row in the detail page with dynamic service cards; add `loadServiceCards(repoName)` JS function; add `renderServiceCard(service)` JS function; add action button handlers for each service type |

No changes to `snapshot.py`, `models.py`, `discovery.py`, `server.py`, `websocket.py`, or `activity_cache.py`.

---

## Non-Goals

- **Log streaming** — no `stdout` tail, no live log view. Start/stop returns exit code and truncated output only.
- **Cron management** — cron detection is display-only. No `crontab -e` or job removal.
- **Service dependency ordering** — no sequenced start (e.g., "start paia-events before paia-shell"). Each service is controlled independently.
- **Health checks beyond running/stopped** — no HTTP probe, no process CPU/memory, no uptime tracking. Status is binary.
- **Non-LaunchAgents plists** — `/Library/LaunchDaemons` (system-level) and per-session launch agents are out of scope. `~/Library/LaunchAgents/` only.
- **Global service view** — the hub-level table already shows `service_running` for workgraph. This spec does not add a global launchd overview to the main table; that's a future dashboard enhancement.
- **Restart for workgraph** — `wg service` has `start` and `stop` but the restart semantics (stop-then-start idempotency) are unclear without testing. Omit restart from the workgraph card in v1; add if wg restart semantics are confirmed clean.
- **Optimistic UI** — button state does not update until the action response arrives and a re-fetch completes. No speculative "Stopping…" badge.
