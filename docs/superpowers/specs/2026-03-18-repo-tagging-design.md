# Repo Tagging & Categorization — Design Spec
**Date:** 2026-03-18
**Status:** Draft

---

## Overview

The ecosystem hub shows 45+ repos in a single flat list. There is no way to answer questions like "show me only paia repos" or "what external dependencies am I tracking?" without reading every row.

This feature adds a lightweight tagging system: tags are declared in `ecosystem.toml` alongside the existing `role` and `path`/`url` fields, flowed through the discovery pipeline into `RepoSnapshot` and the API response, and surfaced on the dashboard as a new tag filter dropdown plus small tag badges on each repo row.

There is no tag management UI. The canonical workflow is: edit `ecosystem.toml`, restart or wait for the next snapshot cycle.

---

## Data Model (ecosystem.toml schema)

Tags live inside each repo's existing table block as a TOML array of strings. No new top-level sections.

```toml
[repos.paia-shell]
role = "product"
path = "../paia-shell"
tags = ["company", "active-project", "paia"]

[repos.lfw-ai-graph-crm]
role = "product"
path = "../lfw-ai-graph-crm"
tags = ["company", "active-project", "lfw"]

[repos.driftdriver]
role = "orchestrator"
url = "https://github.com/dbmcco/driftdriver"
tags = ["personal", "tool", "speedrift"]

[repos.lodestar]
role = "product"
path = "../lodestar"
tags = ["personal", "active-project", "lodestar"]

[repos.coredrift]
role = "baseline"
url = "https://github.com/dbmcco/coredrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.some-upstream-lib]
role = "lane"
url = "https://github.com/example/lib"
tags = ["external-dependency", "exploring"]
```

### Tag vocabulary (soft convention, not enforced by code)

**Ownership** — exactly one expected per repo:
- `personal`
- `company`
- `client`

**Type** — exactly one expected per repo:
- `active-project`
- `external-dependency`
- `exploring`
- `archived`
- `tool`
- `infrastructure`

**Effort groupings** — zero or more free-form strings:
- `paia`, `lfw`, `speedrift`, `lodestar`, etc.

Tags are plain strings. There is no namespace prefix or structural enforcement in v1 — the vocabulary above is convention only. Mixing or omitting dimensions is allowed without error.

### Repos without a `tags` field

Treated as having an empty tag list. No backfill, no default tags inferred. The dashboard filter "all tags" (default) includes them.

---

## Discovery Changes

### `_load_ecosystem_repos` in `discovery.py`

Currently returns `dict[str, Path]` (name → local path). Tags live in the TOML but are thrown away.

We need tags to travel alongside the path. Two options:

**Option A (preferred):** Add a parallel `_load_ecosystem_repo_meta` function that returns `dict[str, dict]` with `path` and `tags` keys. Keeps the existing `_load_ecosystem_repos` signature unchanged (avoids churn in callers that only need paths) and adds a separate metadata loader called by the snapshot builder.

**Option B:** Change `_load_ecosystem_repos` return type to `dict[str, dict]`. Requires updating all callers.

Go with Option A. The existing function has many callers; a separate metadata function is lower risk.

```python
def _load_ecosystem_repo_meta(ecosystem_toml: Path) -> dict[str, dict[str, Any]]:
    """Returns {name: {"path": Path | None, "url": str, "tags": list[str]}} for all registered repos."""
```

### `RepoSnapshot` in `models.py`

Add one field:

```python
tags: list[str] = field(default_factory=list)
```

Position it after `source` (line 24 in current file) — both are ecosystem-level metadata, not runtime observations.

### Snapshot builder in `snapshot.py`

When building each `RepoSnapshot`, call `_load_ecosystem_repo_meta` once per snapshot run (cache the result in a local variable), then populate `tags` from the result for the matching repo name. If the repo was discovered dynamically (not from ecosystem.toml), `tags` stays empty.

The `asdict()` call that serializes snapshots to JSON already handles new dataclass fields automatically — no changes needed to the serialization path.

---

## API Changes

### `GET /api/repos`

No route change needed. The `tags` field will be present in the serialized `RepoSnapshot` dict and automatically included in the existing response payload once the dataclass field is added.

Example repo object after this change:

```json
{
  "name": "paia-shell",
  "source": "ecosystem:product",
  "tags": ["company", "active-project", "paia"],
  "path": "/Users/braydon/projects/experiments/paia-shell",
  "exists": true,
  ...
}
```

Repos without tags return `"tags": []`.

### `GET /api/status`

No change. Tags ride along in the embedded `repos` array.

### No new endpoints

Tag filtering is client-side (matching the pattern of existing role/status/drift/health filters). No server-side filter query param needed in v1.

---

## Dashboard Changes

### New filter: tag select dropdown

Add a fifth `<select>` to the `#repo-filters` bar, after the existing four:

```html
<select id="repo-tag-filter">
  <option value="all">all tags</option>
  <!-- populated dynamically from observed tag values -->
</select>
```

The option list is built dynamically on each data load from the union of all tag values seen across all repos. This avoids hardcoding the tag vocabulary in the dashboard HTML and handles new tags added to `ecosystem.toml` automatically.

Build order: sort tags alphabetically, ownership values first (`company`, `personal`, `client`), then type values, then effort groupings. In practice alphabetical sort is fine for v1 — the filter works regardless of order.

### JS state variable

```js
let repoTagFilter = 'all';
```

Persisted in URL params as `tag=` (same pattern as existing `role=`, `status=`, etc.).

### `repoMatchesFilters` extension

```js
if (repoTagFilter !== 'all') {
  var tags = Array.isArray(repo.tags) ? repo.tags : [];
  if (tags.indexOf(repoTagFilter) === -1) return false;
}
```

Filtering by a tag returns repos that have that exact tag string. Multi-tag AND is not supported in v1 (YAGNI). The select is single-value.

### Tag badges on repo rows

In the `Repo` column cell (the `<td>` that currently holds `<strong>repoName</strong> + needsHumanBadge`), append tag badges after the repo name:

```js
var tagBadges = (Array.isArray(repo.tags) ? repo.tags : [])
  .map(function(t) {
    return '<span class="repo-tag-badge">' + esc(t) + '</span>';
  }).join('');
```

Render up to 3 tags inline; if more exist, show `+N` badge. Clicking a tag badge sets `repoTagFilter` to that tag value and re-renders the table — makes the badges double as filter shortcuts.

### CSS for tag badges

```css
.repo-tag-badge {
  display: inline-block;
  margin-left: 0.3rem;
  padding: 0.08rem 0.38rem;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 0.72rem;
  font-family: var(--mono);
  cursor: pointer;
  vertical-align: middle;
  white-space: nowrap;
}
.repo-tag-badge:hover {
  background: var(--accent);
  color: #fff;
}
```

This reuses existing CSS variables and matches the visual register of the existing `.badge` and `.activity-pill` components.

---

## File Changeset Summary

| File | Change |
|------|--------|
| `driftdriver/ecosystem_hub/models.py` | Add `tags: list[str]` field to `RepoSnapshot` |
| `driftdriver/ecosystem_hub/discovery.py` | Add `_load_ecosystem_repo_meta()` function |
| `driftdriver/ecosystem_hub/snapshot.py` | Populate `tags` on each `RepoSnapshot` from metadata |
| `driftdriver/ecosystem_hub/dashboard.py` | Tag filter select, badge rendering, `repoMatchesFilters` extension, CSS |
| `speedrift-ecosystem/ecosystem.toml` | Annotate existing repos with `tags` arrays (editorial work, not code) |

No changes to `api.py` — tags flow through automatically.

---

## Non-Goals

- No tag editing from the dashboard UI (edit `ecosystem.toml` directly)
- No tag hierarchy or parent/child relationships
- No per-tag analytics, counts, or rollup panels
- No tag inference from repo content, role, or path
- No enforcement of the ownership/type/effort vocabulary
- No multi-tag AND/OR filter logic
- No tag history or audit trail
- No tags on dynamically-discovered repos (auto-discovered repos that aren't in `ecosystem.toml` get `tags: []`)
