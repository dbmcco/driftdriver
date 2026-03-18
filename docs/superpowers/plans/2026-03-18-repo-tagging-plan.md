# Repo Tagging & Categorization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight tagging system to the ecosystem hub — tags declared in `ecosystem.toml`, flowed through the discovery pipeline into `RepoSnapshot`, and surfaced on the dashboard as a tag filter dropdown and per-row badges.

**Architecture:** Four targeted file edits with a thin parallel metadata loader in `discovery.py` (Option A from the spec: add `_load_ecosystem_repo_meta()` alongside the existing `_load_ecosystem_repos()` to avoid caller churn). `RepoSnapshot` gets one new field `tags: list[str]`. `collect_ecosystem_snapshot()` in `snapshot.py` calls the metadata loader once per snapshot run and populates tags on each `RepoSnapshot`. The dashboard gets a fifth filter `<select>` built dynamically from observed tags, tag badges on each repo row (up to 3 + overflow pill), and a `repoTagFilter` JS state variable persisted in `?tag=` URL params.

**Tech Stack:** Python 3.14, `tomllib` (stdlib), vanilla JS + HTML already in `dashboard.py`, `unittest` + `tempfile` for tests (no mocks, real TOML fixtures).

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml` | Add `tags` arrays to existing repos as editorial example |
| Modify | `driftdriver/ecosystem_hub/models.py` | Add `tags: list[str] = field(default_factory=list)` to `RepoSnapshot` after `source` field |
| Modify | `driftdriver/ecosystem_hub/discovery.py` | Add `_load_ecosystem_repo_meta()` — returns `dict[str, dict]` with `path`, `url`, `tags` per repo |
| Modify | `driftdriver/ecosystem_hub/snapshot.py` | Import `_load_ecosystem_repo_meta`; call it once in `collect_ecosystem_snapshot()` and stamp `tags` on each `RepoSnapshot` |
| Modify | `driftdriver/ecosystem_hub/dashboard.py` | Tag filter `<select>`, `repoTagFilter` JS var, URL param persistence, `repoMatchesFilters` extension, tag badges on rows, CSS |
| Modify | `driftdriver/ecosystem_hub/__init__.py` | Export `_load_ecosystem_repo_meta` |
| Create | `tests/test_repo_tagging.py` | All Python-layer tests for the tagging feature |

---

## Task 1: `RepoSnapshot` — add `tags` field

**Files:**
- Modify: `driftdriver/ecosystem_hub/models.py`
- Create: `tests/test_repo_tagging.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_repo_tagging.py
# ABOUTME: Tests for repo tagging — models, discovery metadata loader, snapshot population.
# ABOUTME: Uses real TOML fixtures in tempfiles; no mocks.
from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from driftdriver.ecosystem_hub.models import RepoSnapshot


class TestRepoSnapshotTagsField(unittest.TestCase):
    def test_tags_defaults_to_empty_list(self):
        snap = RepoSnapshot(name="foo", path="/tmp/foo", exists=True)
        self.assertEqual(snap.tags, [])

    def test_tags_round_trips_through_asdict(self):
        snap = RepoSnapshot(name="foo", path="/tmp/foo", exists=True)
        snap.tags = ["company", "active-project", "paia"]
        d = asdict(snap)
        self.assertEqual(d["tags"], ["company", "active-project", "paia"])

    def test_tags_field_is_after_source(self):
        """tags should appear right after source in field order (per spec)."""
        from dataclasses import fields
        field_names = [f.name for f in fields(RepoSnapshot)]
        source_idx = field_names.index("source")
        tags_idx = field_names.index("tags")
        self.assertEqual(tags_idx, source_idx + 1)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py::TestRepoSnapshotTagsField -v
```

Expected: `FAILED` — `test_tags_defaults_to_empty_list` fails because `RepoSnapshot` has no `tags` field yet.

- [ ] **Step 3: Add `tags` field to `RepoSnapshot`**

In `driftdriver/ecosystem_hub/models.py`, add the field immediately after `source: str = ""` (currently line 23):

```python
    source: str = ""
    tags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py::TestRepoSnapshotTagsField -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/models.py tests/test_repo_tagging.py
git commit -m "feat: add tags field to RepoSnapshot"
```

---

## Task 2: `_load_ecosystem_repo_meta()` — metadata loader in discovery.py

**Files:**
- Modify: `driftdriver/ecosystem_hub/discovery.py`
- Modify: `driftdriver/ecosystem_hub/__init__.py`
- Modify: `tests/test_repo_tagging.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_repo_tagging.py`:

```python
from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repo_meta


class TestLoadEcosystemRepoMeta(unittest.TestCase):
    def _make_toml(self, content: str, tmp: Path) -> Path:
        p = tmp / "ecosystem.toml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_returns_tags_for_repo_with_tags(self):
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.paia-shell]\nrole = "product"\npath = "../paia-shell"\ntags = ["company", "active-project", "paia"]\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertEqual(meta["paia-shell"]["tags"], ["company", "active-project", "paia"])

    def test_returns_empty_tags_for_repo_without_tags(self):
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.coredrift]\nrole = "baseline"\nurl = "https://github.com/dbmcco/coredrift"\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertEqual(meta["coredrift"]["tags"], [])

    def test_returns_empty_dict_for_missing_file(self):
        meta = _load_ecosystem_repo_meta(Path("/nonexistent/ecosystem.toml"))
        self.assertEqual(meta, {})

    def test_preserves_path_and_url_keys(self):
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.lodestar]\nrole = "product"\npath = "../lodestar"\ntags = ["personal"]\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertIn("path", meta["lodestar"])
            self.assertIn("tags", meta["lodestar"])
            self.assertEqual(meta["lodestar"]["tags"], ["personal"])

    def test_tags_must_be_list_of_strings_non_list_ignored(self):
        """If tags is not a list, treat as empty — no crash."""
        with tempfile.TemporaryDirectory() as td:
            toml_path = self._make_toml(
                '[repos.bad-tags]\nrole = "product"\npath = "../bad"\ntags = "not-a-list"\n',
                Path(td),
            )
            meta = _load_ecosystem_repo_meta(toml_path)
            self.assertEqual(meta["bad-tags"]["tags"], [])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py::TestLoadEcosystemRepoMeta -v
```

Expected: `ImportError` or `AttributeError` — `_load_ecosystem_repo_meta` does not exist yet.

- [ ] **Step 3: Implement `_load_ecosystem_repo_meta` in `discovery.py`**

Add this function directly after `_load_ecosystem_repos` (after line 400 in the current file):

```python
def _load_ecosystem_repo_meta(ecosystem_toml: Path) -> dict[str, dict[str, Any]]:
    """Return {name: {"path": str, "url": str, "tags": list[str]}} for all registered repos."""
    if not ecosystem_toml.exists():
        return {}
    try:
        data = tomllib.loads(ecosystem_toml.read_text(encoding="utf-8"))
    except Exception:
        return {}
    repos = data.get("repos")
    if not isinstance(repos, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, value in repos.items():
        key = str(name).strip()
        if not key:
            continue
        if not isinstance(value, dict):
            out[key] = {"path": "", "url": "", "tags": []}
            continue
        raw_tags = value.get("tags")
        tags: list[str] = (
            [str(t) for t in raw_tags if isinstance(t, str)]
            if isinstance(raw_tags, list)
            else []
        )
        out[key] = {
            "path": str(value.get("path") or ""),
            "url": str(value.get("url") or ""),
            "tags": tags,
        }
    return out
```

- [ ] **Step 4: Export the new function from `__init__.py`**

In `driftdriver/ecosystem_hub/__init__.py`, add `_load_ecosystem_repo_meta` to the `from .discovery import (...)` block:

```python
from .discovery import (
    _age_days,
    ...
    _load_ecosystem_repo_meta,   # add this line
    _load_ecosystem_repos,
    ...
)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py::TestLoadEcosystemRepoMeta -v
```

Expected: 5 PASSED.

- [ ] **Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/discovery.py driftdriver/ecosystem_hub/__init__.py tests/test_repo_tagging.py
git commit -m "feat: add _load_ecosystem_repo_meta to discovery pipeline"
```

---

## Task 3: Populate `tags` in `collect_ecosystem_snapshot`

**Files:**
- Modify: `driftdriver/ecosystem_hub/snapshot.py`
- Modify: `tests/test_repo_tagging.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_repo_tagging.py`:

```python
from driftdriver.ecosystem_hub.snapshot import collect_ecosystem_snapshot


class TestSnapshotPopulatesTags(unittest.TestCase):
    def _make_ecosystem_toml(self, tmp: Path) -> Path:
        eco_dir = tmp / "speedrift-ecosystem"
        eco_dir.mkdir()
        toml = eco_dir / "ecosystem.toml"
        toml.write_text(
            'schema = 1\n'
            '[repos.my-repo]\n'
            'role = "product"\n'
            'path = "../my-repo"\n'
            'tags = ["company", "active-project"]\n',
            encoding="utf-8",
        )
        return toml

    def test_tags_populated_on_repo_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            # create a minimal repo that will be found via ecosystem.toml
            repo_dir = workspace / "my-repo"
            repo_dir.mkdir()
            eco_toml = self._make_ecosystem_toml(workspace)

            result = collect_ecosystem_snapshot(
                project_dir=workspace / "speedrift-ecosystem",
                workspace_root=workspace,
                ecosystem_toml=eco_toml,
            )
            repos = {r["name"]: r for r in result.get("repos", [])}
            self.assertIn("my-repo", repos)
            self.assertEqual(repos["my-repo"]["tags"], ["company", "active-project"])

    def test_tags_empty_for_repo_without_tags_in_toml(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            repo_dir = workspace / "bare-repo"
            repo_dir.mkdir()
            eco_dir = workspace / "speedrift-ecosystem"
            eco_dir.mkdir()
            toml = eco_dir / "ecosystem.toml"
            toml.write_text(
                'schema = 1\n'
                '[repos.bare-repo]\n'
                'role = "product"\n'
                'path = "../bare-repo"\n',
                encoding="utf-8",
            )
            result = collect_ecosystem_snapshot(
                project_dir=eco_dir,
                workspace_root=workspace,
                ecosystem_toml=toml,
            )
            repos = {r["name"]: r for r in result.get("repos", [])}
            self.assertIn("bare-repo", repos)
            self.assertEqual(repos["bare-repo"]["tags"], [])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py::TestSnapshotPopulatesTags -v
```

Expected: FAILED — `repos["my-repo"]["tags"]` is absent or `[]` even for tagged repos (the field is not yet populated from metadata).

- [ ] **Step 3: Wire `_load_ecosystem_repo_meta` into `collect_ecosystem_snapshot`**

In `driftdriver/ecosystem_hub/snapshot.py`:

1. Add `_load_ecosystem_repo_meta` to the `from .discovery import (...)` block:

```python
from .discovery import (
    ...
    _load_ecosystem_repo_meta,
    _load_ecosystem_repos,
    ...
)
```

2. Inside `collect_ecosystem_snapshot`, after the line `repo_map = _load_ecosystem_repos(ecosystem_file, workspace_root)` (currently around line 694), add:

```python
    repo_map = _load_ecosystem_repos(ecosystem_file, workspace_root)
    repo_meta = _load_ecosystem_repo_meta(ecosystem_file)   # ← add this line
    repo_sources: dict[str, str] = {name: "ecosystem-toml" for name in repo_map}
```

3. Inside the `_collect_one` closure (currently around line 727), after `snap.source = repo_sources.get(name, "ecosystem-toml")`, add:

```python
        snap.source = repo_sources.get(name, "ecosystem-toml")
        snap.tags = repo_meta.get(name, {}).get("tags") or []   # ← add this line
        return snap
```

4. Do the same for the two `RepoSnapshot` fallback constructors in the `try/except` blocks around lines 745–751 (the timeout and exception cases):

```python
            except _FutTimeout:
                repo_snap = RepoSnapshot(name=name, path=str(path), exists=path.exists())
                repo_snap.errors.append("snapshot_timeout: repo scan exceeded 15s")
                repo_snap.source = repo_sources.get(name, "ecosystem-toml")
                repo_snap.tags = repo_meta.get(name, {}).get("tags") or []   # ← add
            except Exception as exc:
                repo_snap = RepoSnapshot(name=name, path=str(path), exists=path.exists())
                repo_snap.errors.append(f"snapshot_error: {str(exc)[:200]}")
                repo_snap.source = repo_sources.get(name, "ecosystem-toml")
                repo_snap.tags = repo_meta.get(name, {}).get("tags") or []   # ← add
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py::TestSnapshotPopulatesTags -v
```

Expected: 2 PASSED.

- [ ] **Step 5: Run the full test file to confirm no regressions**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py -v
```

Expected: All PASSED.

- [ ] **Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/snapshot.py tests/test_repo_tagging.py
git commit -m "feat: populate tags on RepoSnapshot from ecosystem.toml metadata"
```

---

## Task 4: Annotate `ecosystem.toml` with example tags

**Files:**
- Modify: `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml`

This is pure editorial work — no code change, no test. It makes the tags real and visible in the live dashboard immediately after the dashboard changes land.

- [ ] **Step 1: Add `tags` to existing repos in ecosystem.toml**

Edit `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml` — add `tags` arrays to the existing repo blocks:

```toml
schema = 1
suite = "speedrift"
orchestrator = "driftdriver"

[repos.driftdriver]
role = "orchestrator"
url = "https://github.com/dbmcco/driftdriver"
tags = ["personal", "tool", "speedrift"]

[repos.coredrift]
role = "baseline"
url = "https://github.com/dbmcco/coredrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.specdrift]
role = "lane"
url = "https://github.com/dbmcco/specdrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.datadrift]
role = "lane"
url = "https://github.com/dbmcco/datadrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.archdrift]
role = "lane"
url = "https://github.com/dbmcco/archdrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.depsdrift]
role = "lane"
url = "https://github.com/dbmcco/depsdrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.uxdrift]
role = "lane"
url = "https://github.com/dbmcco/uxdrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.therapydrift]
role = "lane"
url = "https://github.com/dbmcco/therapydrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.yagnidrift]
role = "lane"
url = "https://github.com/dbmcco/yagnidrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.redrift]
role = "lane"
url = "https://github.com/dbmcco/redrift"
tags = ["personal", "infrastructure", "speedrift"]

[repos.atlas_product]
role = "product"
path = "../../work/synth/atlas_product"
tags = ["company", "active-project"]

[repos.lodestar]
role = "product"
path = "../lodestar"
tags = ["personal", "active-project", "lodestar"]

[repos.training-assistant]
role = "product"
path = "../training-assistant"
tags = ["personal", "exploring"]

[repos.news-briefing]
role = "product"
path = "../news-briefing"
tags = ["personal", "archived"]

[repos.vibez-monitor]
role = "product"
path = "../../personal/vibez-monitor"
tags = ["personal", "active-project"]
```

- [ ] **Step 2: Commit**

```bash
cd /Users/braydon/projects/experiments/speedrift-ecosystem
git add ecosystem.toml
git commit -m "chore: annotate ecosystem.toml repos with tags"
```

---

## Task 5: Dashboard — tag filter `<select>` and URL state

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

This task adds the `repoTagFilter` JS variable, the `<select id="repo-tag-filter">` element, URL parameter persistence, and the `repoMatchesFilters` extension. The badge rendering and CSS come in Task 6 so each task stays focused.

- [ ] **Step 1: Add `repoTagFilter` state variable**

In `dashboard.py`, find the block at lines 984–990:

```python
    let repoSearchText = '';
    let repoRoleFilter = 'all';
    let repoStatusFilter = 'all';
    let repoDriftFilter = 'all';
    let repoHealthFilter = 'all';
    let repoSortCol = 'git';
    let repoSortAsc = false;
```

Add `let repoTagFilter = 'all';` after `repoHealthFilter`:

```python
    let repoSearchText = '';
    let repoRoleFilter = 'all';
    let repoStatusFilter = 'all';
    let repoDriftFilter = 'all';
    let repoHealthFilter = 'all';
    let repoTagFilter = 'all';
    let repoSortCol = 'git';
    let repoSortAsc = false;
```

- [ ] **Step 2: Add the `<select id="repo-tag-filter">` to the filter bar HTML**

Find the `#repo-filters` div (lines 792–818). It ends with the health filter `</select>` and then `</div>`. Add the tag select immediately after the health filter's closing `</select>`, before the `</div>`:

```python
          <select id="repo-tag-filter">
            <option value="all">all tags</option>
          </select>
```

The full updated block looks like:

```html
        <div class="repo-filter-bar" id="repo-filters">
          <input type="text" id="repo-search" placeholder="Search repos..." />
          <select id="repo-role-filter">
            ...
          </select>
          <select id="repo-status-filter">
            ...
          </select>
          <select id="repo-drift-filter">
            ...
          </select>
          <select id="repo-health-filter">
            <option value="all">all health</option>
            <option value="risk">risk</option>
            <option value="watch">watch</option>
            <option value="healthy">healthy</option>
          </select>
          <select id="repo-tag-filter">
            <option value="all">all tags</option>
          </select>
        </div>
```

- [ ] **Step 3: Populate the tag select dynamically in `renderRepoTable`**

Find `renderRepoTable` (line 1667). At the top of the function, after `var allRepos = ...`, add code to build the sorted, deduplicated tag option list and inject it into the select:

```python
    function renderRepoTable(data) {
      var allRepos = Array.isArray(data.repos) ? data.repos : [];

      // Build sorted unique tag list from all repos
      var tagSet = {};
      allRepos.forEach(function(repo) {
        var tags = Array.isArray(repo.tags) ? repo.tags : [];
        tags.forEach(function(t) { tagSet[String(t)] = true; });
      });
      var allTags = Object.keys(tagSet).sort();
      var tagSelect = el('repo-tag-filter');
      var currentTagVal = tagSelect.value || 'all';
      tagSelect.innerHTML = '<option value="all">all tags</option>'
        + allTags.map(function(t) {
            return '<option value="' + escAttr(t) + '"' + (currentTagVal === t ? ' selected' : '') + '>' + esc(t) + '</option>';
          }).join('');

      var filtered = allRepos.filter(repoMatchesFilters);
      ...
```

- [ ] **Step 4: Extend `repoMatchesFilters` with tag filtering**

Find `repoMatchesFilters` (lines 1594–1615). Add the tag check as the last condition before `return true`:

```python
    function repoMatchesFilters(repo) {
      if (repoSearchText && String(repo.name || '').toLowerCase().indexOf(repoSearchText.toLowerCase()) === -1) {
        return false;
      }
      if (repoRoleFilter !== 'all') {
        var role = repoRole(repo);
        if (role !== repoRoleFilter) return false;
      }
      if (repoStatusFilter !== 'all') {
        if (repoStatus(repo) !== repoStatusFilter) return false;
      }
      if (repoDriftFilter !== 'all') {
        var hasDrift = repoHasDrift(repo);
        if (repoDriftFilter === 'has-drift' && !hasDrift) return false;
        if (repoDriftFilter === 'clean' && hasDrift) return false;
      }
      if (repoHealthFilter !== 'all') {
        var health = qualityPill(repo)[0];
        if (health !== repoHealthFilter) return false;
      }
      if (repoTagFilter !== 'all') {
        var tags = Array.isArray(repo.tags) ? repo.tags : [];
        if (tags.indexOf(repoTagFilter) === -1) return false;
      }
      return true;
    }
```

- [ ] **Step 5: Add `?tag=` URL persistence**

In `syncFiltersToUrl` (lines 1071–1081), add the tag param alongside the others:

```python
    function syncFiltersToUrl() {
      var params = new URLSearchParams();
      if (repoSearchText) params.set('q', repoSearchText);
      if (repoRoleFilter !== 'all') params.set('role', repoRoleFilter);
      if (repoStatusFilter !== 'all') params.set('status', repoStatusFilter);
      if (repoDriftFilter !== 'all') params.set('drift', repoDriftFilter);
      if (repoHealthFilter !== 'all') params.set('health', repoHealthFilter);
      if (repoTagFilter !== 'all') params.set('tag', repoTagFilter);
      var qs = params.toString();
      var url = qs ? '?' + qs : window.location.pathname;
      window.history.replaceState({}, '', url);
    }
```

In `loadFiltersFromUrl` (lines 1083–1095), read back the tag param and set the select:

```python
    function loadFiltersFromUrl() {
      var params = new URLSearchParams(window.location.search);
      repoSearchText = params.get('q') || '';
      repoRoleFilter = params.get('role') || 'all';
      repoStatusFilter = params.get('status') || 'all';
      repoDriftFilter = params.get('drift') || 'all';
      repoHealthFilter = params.get('health') || 'all';
      repoTagFilter = params.get('tag') || 'all';
      el('repo-search').value = repoSearchText;
      el('repo-role-filter').value = repoRoleFilter;
      el('repo-status-filter').value = repoStatusFilter;
      el('repo-drift-filter').value = repoDriftFilter;
      el('repo-health-filter').value = repoHealthFilter;
      el('repo-tag-filter').value = repoTagFilter;
    }
```

- [ ] **Step 6: Add change event listener for the tag select**

Find the block of `addEventListener` calls for the existing filter selects (lines 2316–2340). Add the tag filter handler after the health filter handler:

```python
    el('repo-health-filter').addEventListener('change', function(e) {
      repoHealthFilter = String(e.target.value || 'all');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });
    el('repo-tag-filter').addEventListener('change', function(e) {
      repoTagFilter = String(e.target.value || 'all');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });
```

- [ ] **Step 7: Smoke-test the filter in browser**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python -m driftdriver.ecosystem_hub.server --dir /Users/braydon/projects/experiments --port 8778
```

Open `http://127.0.0.1:8778/` — the "all tags" select should appear in the filter bar. Selecting a tag should filter the table. The URL should update with `?tag=speedrift` etc. Kill server with Ctrl-C.

- [ ] **Step 8: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add tag filter select and URL state to dashboard"
```

---

## Task 6: Dashboard — tag badges on repo rows

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

- [ ] **Step 1: Add tag badge CSS**

In `dashboard.py`, find the CSS block (somewhere before line 200). Add the badge styles right before or after the existing `.badge` rule (around line 199):

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
    .repo-tag-overflow {
      display: inline-block;
      margin-left: 0.3rem;
      padding: 0.08rem 0.38rem;
      border-radius: 999px;
      background: var(--line);
      color: var(--muted);
      font-size: 0.72rem;
      font-family: var(--mono);
      vertical-align: middle;
      white-space: nowrap;
    }
```

- [ ] **Step 2: Add a `tagBadgesHtml` helper function**

In `dashboard.py`, add this function near `needsHumanBadge` (around line 1097) — immediately after it:

```python
    function tagBadgesHtml(repo) {
      var tags = Array.isArray(repo.tags) ? repo.tags : [];
      if (!tags.length) return '';
      var visible = tags.slice(0, 3);
      var overflow = tags.length - visible.length;
      var html = visible.map(function(t) {
        return '<span class="repo-tag-badge" data-tag="' + escAttr(t) + '">' + esc(t) + '</span>';
      }).join('');
      if (overflow > 0) {
        html += '<span class="repo-tag-overflow">+' + overflow + '</span>';
      }
      return html;
    }
```

- [ ] **Step 3: Append tag badges to the Repo column cell in `renderRepoTable`**

Find the row construction in `renderRepoTable` (around line 1748–1760). The current Repo cell is:

```python
          '<td><strong>' + esc(repoName) + '</strong>' + needsHumanBadge(repo) + '</td>'
```

Change it to:

```python
          '<td><strong>' + esc(repoName) + '</strong>' + needsHumanBadge(repo) + tagBadgesHtml(repo) + '</td>'
```

- [ ] **Step 4: Add click handler for tag badges — clicking a badge sets the filter**

Find the `el('repo-body').addEventListener('click', ...)` block (around line 2369). Inside its handler, before the existing `expander` logic, add a check for badge clicks:

```python
    el('repo-body').addEventListener('click', function(e) {
      var badge = e.target.closest('.repo-tag-badge');
      if (badge) {
        var tag = String(badge.getAttribute('data-tag') || '');
        if (tag) {
          repoTagFilter = tag;
          el('repo-tag-filter').value = tag;
          if (currentData) renderRepoTable(currentData);
          syncFiltersToUrl();
          return;
        }
      }
      // ... existing expander click logic continues below ...
```

- [ ] **Step 5: Smoke-test badges in browser**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run python -m driftdriver.ecosystem_hub.server --dir /Users/braydon/projects/experiments --port 8778
```

Open `http://127.0.0.1:8778/` — each repo row should show up to 3 tag pills after the repo name. Clicking a pill should set the filter dropdown and re-filter the table. Kill server with Ctrl-C.

- [ ] **Step 6: Commit**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat: add tag badges to repo rows with click-to-filter"
```

---

## Task 7: Run the full test suite and confirm clean

- [ ] **Step 1: Run all tagging tests**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_repo_tagging.py -v
```

Expected: All PASSED.

- [ ] **Step 2: Run the existing ecosystem hub tests to confirm no regressions**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest tests/test_ecosystem_hub.py -v
```

Expected: Same pass count as before this feature (no new failures).

- [ ] **Step 3: Run the full test suite**

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest --tb=short -q
```

Expected: No new failures introduced by this feature.

- [ ] **Step 4: Commit if any adjustments were needed**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add -p
git commit -m "fix: address test failures from tagging integration"
```

(Only needed if Step 3 revealed issues. Skip if all clean.)

---

## Summary of Changes

| File | What changes |
|------|-------------|
| `/Users/braydon/projects/experiments/speedrift-ecosystem/ecosystem.toml` | Add `tags` arrays to all existing repo blocks |
| `driftdriver/ecosystem_hub/models.py` | `tags: list[str] = field(default_factory=list)` after `source` field in `RepoSnapshot` |
| `driftdriver/ecosystem_hub/discovery.py` | New function `_load_ecosystem_repo_meta()` after `_load_ecosystem_repos` |
| `driftdriver/ecosystem_hub/__init__.py` | Export `_load_ecosystem_repo_meta` |
| `driftdriver/ecosystem_hub/snapshot.py` | Import + call `_load_ecosystem_repo_meta` once in `collect_ecosystem_snapshot`; stamp `snap.tags` in `_collect_one` and both fallback branches |
| `driftdriver/ecosystem_hub/dashboard.py` | `repoTagFilter` var, `<select id="repo-tag-filter">`, dynamic option population in `renderRepoTable`, `repoMatchesFilters` extension, `syncFiltersToUrl`/`loadFiltersFromUrl` updates, change event listener, `.repo-tag-badge` CSS, `tagBadgesHtml()` helper, row cell update, click-to-filter handler |
| `tests/test_repo_tagging.py` | New file — three test classes covering models, discovery, and snapshot integration |

**No changes to `api.py`** — `tags` flows through `asdict(snap)` automatically once the field exists on `RepoSnapshot`.
