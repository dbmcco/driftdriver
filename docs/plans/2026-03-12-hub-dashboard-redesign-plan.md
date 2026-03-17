# Hub Dashboard Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Compress the ecosystem hub dashboard from 7 sections / 15,000px to 4 zones in ~2 viewports with triage-first layout.

**Architecture:** Rewrite the single `render_dashboard_html()` function in `dashboard.py` (2641 lines). The snapshot data model and transport layer (WebSocket + HTTP polling) are unchanged. All changes are in the HTML/CSS/JS template string. The file is a Python function returning one big HTML string — CSS, HTML body, and JavaScript are all inline.

**Tech Stack:** Python (template host), HTML/CSS/JS (inline SPA), existing snapshot JSON data model

**Design doc:** `docs/plans/2026-03-12-hub-dashboard-redesign-design.md`

---

## Context for Implementer

### File Structure
- **`driftdriver/ecosystem_hub/dashboard.py`** — The only file you modify. Contains `render_dashboard_html()` which returns a complete HTML document as a Python string (lines 4-2641).
  - Lines 11-576: `<style>` block (CSS)
  - Lines 577-780: `<body>` HTML structure (7 sections)
  - Lines 781-2641: `<script>` block (JavaScript renderers + WebSocket/polling)

### Data Model (unchanged)
The JS `render(data, source)` function receives the full snapshot object. Key fields used by each new zone:
- **Briefing Bar**: `data.narrative`, `data.overview.attention_repos[]` (each has `.repo`, `.score`, `.reasons[]`)
- **Attention Queue**: `data.overview.attention_repos[]`, `data.repos[]` (for stalled/blocked/aging enrichment)
- **Repo Table**: `data.repos[]` (each has `.name`, `.northstar`, `.git_dirty`, `.in_progress[]`, `.stale_open[]`, `.stalled`, `.service_running`, `.activity_state`, `.task_graph_nodes[]`, `.task_graph_edges[]`, role from `.source`)
- **Graphs Panel**: `data.repo_dependencies` (cross-repo), per-repo `.task_graph_nodes[]` and `.task_graph_edges[]`
- **Sparklines**: `data.northstardrift.history.points[]` (each has `.overall_score` and `.axes{}`)

### Existing JS Helpers to Preserve
These utility functions (currently around lines 795-870) must be kept — they're used across all renderers:
- `el(id)` — `document.getElementById`
- `esc(text)` — HTML entity escaping
- `n(value)` — safe number coercion
- `repoByName(name)` — lookup repo from `currentData.repos[]`
- `sparkline(values, color)` — SVG sparkline generator (lines 1436-1449)
- WebSocket/polling functions (lines 2430-2479) — unchanged
- `drawRepoDependencyOverview()` (lines 1196-1402) — kept for Graphs Panel
- Graph zoom/pan state and handlers (lines 2549-2634) — kept for Graphs Panel

### What Gets Removed
- `renderNorthstar()` (lines 1404-1503) — KPI cards killed, sparklines move to repo table
- `renderOverviewCards()` (lines 1505-1534) — folded into briefing bar
- `renderRepoCards()` (lines 1660-1728) — replaced by repo table
- `renderAging()` (lines 1574-1658) — merged into attention queue
- `renderNext()`, `renderUpstream()`, `renderSecurity()`, `renderQuality()` — removed (factory handles)
- `renderActionSummary()` — removed
- `buildAgentPrompt()`, `renderActionItemHtml()` — removed (copy-prompt buttons gone)
- All 7 HTML `<section>` blocks in the body (lines 584-779)
- CSS for `.repo-grid`, `.repo-card`, `.action-grid`, `.action-panel`, `.trend-grid`, `.trend-panel`, North Star card styles

### Tests
- **`tests/test_ecosystem_hub.py`** — Integration tests for snapshot collection and hub behavior. Most don't test HTML output directly, but some assert on `render_dashboard_html()` return value.
- **Manual QA** — After each task, restart the hub daemon and verify at `http://127.0.0.1:8777/`

### How to restart the hub for manual QA
```bash
cd /Users/braydon/projects/experiments/driftdriver
# Kill existing hub (launchd will restart it with new code)
pkill -f "ecosystem_hub.server" || true
# Wait 2-3 seconds for launchd restart, then open browser
sleep 3
open http://127.0.0.1:8777/
```

---

### Task 1: New CSS Layout

Replace the `<style>` block (lines 11-576) with the new 4-zone flex layout. Keep the color palette and typography variables. Remove all old section-specific styles (repo cards, action grid, trend panels, north star cards).

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py:11-576`

**Step 1: Read current CSS block**

Read lines 11-576 of `dashboard.py` to understand all current CSS rules.

**Step 2: Write new CSS**

Replace lines 11-576 with new CSS that provides:

```css
:root {
  /* Keep existing color palette and font variables (lines 12-25) */
}
body { /* Keep existing body styles */ }
header { /* Keep existing sticky header */ }

/* NEW: 4-zone layout */
.hub-layout {
  display: flex;
  flex-direction: column;
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 1rem;
  gap: 1rem;
}

/* Zone 1: Briefing Bar */
.briefing-bar { /* compact card, 2-3 lines */ }
.briefing-bar .briefing-text { /* paragraph style */ }
.briefing-bar .briefing-expander { /* clickable repo name */ }
.briefing-bar .briefing-detail { /* hidden detail block */ }
.briefing-bar .briefing-detail.open { /* shown detail block */ }

/* Zone 2: Attention Queue */
.attention-queue { /* card wrapper */ }
.attention-table { /* sortable table */ }
.attention-table th { /* sortable header with cursor pointer */ }
.attention-table .severity-high { /* red dot */ }
.attention-table .severity-medium { /* orange dot */ }
.attention-table .severity-low { /* gray dot */ }
.attention-empty { /* "nothing needs attention" state */ }

/* Zone 3+4: Split panel (repo table + graphs) */
.split-panel {
  display: flex;
  gap: 1rem;
  min-height: 500px;
}
.repo-panel { flex: 3; /* left side, wider */ }
.graphs-panel { flex: 2; /* right side */ }

/* Repo table */
.repo-filter-bar { /* dropdown pills row */ }
.repo-table { width: 100%; border-collapse: collapse; }
.repo-table th { /* column headers */ }
.repo-table tr { cursor: pointer; }
.repo-table tr:hover { background: var(--accent-soft); }
.repo-table .status-dot { /* inline status indicator */ }
.repo-table .spark { /* inline sparkline SVG */ }
.repo-expanded { /* expanded row detail panel */ }
.repo-expanded .task-dag { /* mini DAG container */ }

/* Graphs panel */
.dep-map { /* cross-repo dependency SVG */ }

/* Chat stub (hidden, Approach C) */
#chat-panel { display: none; }

/* Responsive: stack on narrow */
@media (max-width: 900px) {
  .split-panel { flex-direction: column; }
  .graphs-panel { order: -1; /* graphs on top on mobile */ }
}
```

Preserve the sparkline SVG styles, `.meta` styles, and the graph legend/zoom control styles from the old CSS since the graphs panel reuses them.

**Step 3: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')" `
Expected: `OK` (no syntax errors in the template string)

**Step 4: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "refactor(hub): replace dashboard CSS with 4-zone flex layout"
```

---

### Task 2: New HTML Body Structure

Replace the 7 `<section>` blocks (lines 584-779) with the 4-zone HTML skeleton. Add the hidden chat panel stub. The new elements will be empty containers that renderers populate.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py:584-779`

**Step 1: Replace HTML body sections**

Replace lines 584-779 with:

```html
<main class="hub-layout">
  <!-- Zone 1: Briefing Bar -->
  <section class="briefing-bar card" id="briefing-bar">
    <p class="briefing-text" id="briefing-text">Loading ecosystem state...</p>
    <div id="briefing-details"></div>
  </section>

  <!-- Zone 2: Attention Queue -->
  <section class="attention-queue card" id="attention-section">
    <div class="section-header">
      <h2>Attention Queue</h2>
      <span class="badge" id="attention-count">0</span>
    </div>
    <table class="attention-table" id="attention-table">
      <thead>
        <tr>
          <th data-sort="repo">Repo</th>
          <th data-sort="issue">Issue</th>
          <th data-sort="severity">Severity</th>
          <th data-sort="age">Age</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="attention-body"></tbody>
    </table>
    <p class="attention-empty" id="attention-empty" style="display:none">
      Nothing needs your attention right now.
    </p>
  </section>

  <!-- Zone 3+4: Split panel -->
  <div class="split-panel">
    <!-- Repo Table (left) -->
    <section class="repo-panel card" id="repo-section">
      <div class="section-header">
        <h2>Repos</h2>
        <span class="badge" id="repo-count">0</span>
      </div>
      <div class="repo-filter-bar" id="repo-filters">
        <input type="text" id="repo-search" placeholder="Search repos..." />
        <select id="repo-role-filter">
          <option value="all">all roles</option>
          <option value="orchestrator">orchestrator</option>
          <option value="baseline">baseline</option>
          <option value="lane">lane</option>
          <option value="product">product</option>
        </select>
        <select id="repo-status-filter">
          <option value="all">all status</option>
          <option value="active">active</option>
          <option value="idle">idle</option>
          <option value="missing">missing</option>
        </select>
        <select id="repo-drift-filter">
          <option value="all">all drift</option>
          <option value="has-drift">has drift</option>
          <option value="clean">clean</option>
        </select>
      </div>
      <table class="repo-table" id="repo-table">
        <thead>
          <tr>
            <th data-sort="name">Repo</th>
            <th data-sort="role">Role</th>
            <th>Status</th>
            <th data-sort="drift">Drift</th>
            <th data-sort="tasks">Tasks</th>
            <th>Trend</th>
            <th data-sort="activity">Last Activity</th>
          </tr>
        </thead>
        <tbody id="repo-body"></tbody>
      </table>
    </section>

    <!-- Graphs Panel (right) -->
    <section class="graphs-panel card" id="graphs-section">
      <div class="section-header">
        <h2>Dependencies</h2>
        <div class="graph-controls">
          <button id="dep-zoom-out" type="button">-</button>
          <button id="dep-zoom-in" type="button">+</button>
          <button id="dep-zoom-reset" type="button">reset</button>
        </div>
      </div>
      <svg id="repo-dep-graph" viewBox="0 0 800 500" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="graph-legend">
        <span><span class="dot" style="background:#2f6e39"></span>Done</span>
        <span><span class="dot" style="background:#0f6f7c"></span>In progress</span>
        <span><span class="dot" style="background:#a26c13"></span>Open</span>
        <span><span class="dot" style="background:#9c2525"></span>Blocked</span>
      </div>
    </section>

    <!-- Chat Panel stub (Approach C) -->
    <aside id="chat-panel" hidden>
      <div class="section-header"><h2>Chat</h2></div>
      <div id="chat-messages"></div>
      <input type="text" id="chat-input" placeholder="Ask about your ecosystem..." />
    </aside>
  </div>
</main>
```

**Step 2: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print(len(render_dashboard_html()))"`
Expected: prints a number (no syntax errors)

**Step 3: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "refactor(hub): replace 7 HTML sections with 4-zone layout skeleton"
```

---

### Task 3: Briefing Bar Renderer

Write the `renderBriefing(data)` JS function that generates the 2-3 sentence briefing with expandable repo details.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS section)

**Step 1: Write renderBriefing function**

Add this function in the `<script>` block, replacing the old `renderNorthstar()` and narrative rendering:

```javascript
function renderBriefing(data) {
  const attentionRepos = (data.overview || {}).attention_repos || [];
  const repoCount = (data.repos || []).length;
  const ns = (data.northstardrift || {}).summary || {};
  const trend = ns.overall_trend || 'flat';
  const activeCount = (data.repos || []).filter(r =>
    String(r.activity_state || '').toLowerCase() === 'active'
  ).length;

  // Build briefing sentence
  let briefing = '';
  if (!attentionRepos.length) {
    briefing = `All ${repoCount} repos are running smoothly. `;
  } else {
    const top3 = attentionRepos.slice(0, 3);
    const names = top3.map(r =>
      `<span class="briefing-expander" data-repo="${esc(r.repo)}">${esc(r.repo)} &#9656;</span>`
    );
    if (names.length === 1) {
      briefing = `1 repo needs attention \u2014 ${names[0]}. `;
    } else {
      const last = names.pop();
      briefing = `${attentionRepos.length} repos need attention \u2014 ${names.join(', ')}, and ${last}. `;
    }
  }
  briefing += `Ecosystem trend: ${esc(trend)} across ${activeCount} active repos.`;

  el('briefing-text').innerHTML = briefing;

  // Build expandable details
  const detailsContainer = el('briefing-details');
  detailsContainer.innerHTML = '';
  attentionRepos.slice(0, 3).forEach(item => {
    const repo = repoByName(item.repo) || {};
    const reasons = Array.isArray(item.reasons) ? item.reasons : [];
    const stalledInfo = repo.stalled ? 'Repo is stalled. ' : '';
    const taskInfo = Array.isArray(repo.in_progress) && repo.in_progress.length
      ? `${repo.in_progress.length} task(s) in progress. `
      : 'No tasks in progress. ';
    const div = document.createElement('div');
    div.className = 'briefing-detail';
    div.id = `briefing-detail-${item.repo}`;
    div.innerHTML = `<strong>${esc(item.repo)}</strong>: ${stalledInfo}${taskInfo}` +
      `${esc(reasons.slice(0, 3).join('. '))}. ` +
      `<em>Score: ${n(item.score)}</em>`;
    detailsContainer.appendChild(div);
  });
}
```

**Step 2: Wire up click-to-expand on briefing expanders**

Add event delegation for `.briefing-expander` clicks:

```javascript
document.addEventListener('click', (event) => {
  const expander = event.target.closest('.briefing-expander');
  if (expander) {
    const repo = expander.getAttribute('data-repo');
    const detail = document.getElementById(`briefing-detail-${repo}`);
    if (detail) detail.classList.toggle('open');
    return;
  }
  // ... existing click handlers ...
});
```

**Step 3: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')"`

**Step 4: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(hub): add briefing bar renderer with expandable repo details"
```

---

### Task 4: Attention Queue Renderer

Write the `renderAttentionQueue(data)` JS function that builds a sortable table of human-decision-needed items.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS section)

**Step 1: Write renderAttentionQueue function**

This merges data from `attention_repos` + stalled/blocked repos into one sortable table. Replaces the old `renderAttention()` and `renderAging()`.

```javascript
let attentionSortCol = 'severity';
let attentionSortAsc = false;

function renderAttentionQueue(data) {
  const items = [];
  const attentionRepos = (data.overview || {}).attention_repos || [];

  // Attention items (high-pressure repos)
  attentionRepos.forEach(item => {
    const score = n(item.score);
    items.push({
      repo: String(item.repo || ''),
      issue: Array.isArray(item.reasons) ? item.reasons[0] || 'High pressure score' : 'High pressure score',
      severity: score >= 22 ? 'high' : (score >= 10 ? 'medium' : 'low'),
      severityNum: score >= 22 ? 3 : (score >= 10 ? 2 : 1),
      age: '—',
      ageDays: 0,
      action: score >= 22 ? 'Investigate' : 'Review',
    });
  });

  // Stalled repos not already in attention
  const attentionNames = new Set(attentionRepos.map(r => r.repo));
  (data.repos || []).forEach(repo => {
    if (attentionNames.has(repo.name)) return;
    if (repo.stalled) {
      const reasons = Array.isArray(repo.stall_reasons) ? repo.stall_reasons : [];
      items.push({
        repo: repo.name,
        issue: 'Stalled: ' + (reasons[0] || 'unknown reason'),
        severity: 'high',
        severityNum: 3,
        age: '—',
        ageDays: 0,
        action: 'Investigate',
      });
    }
    // Aging in-progress tasks (> 3 days)
    (repo.stale_in_progress || []).forEach(task => {
      const age = n(task.age_days);
      if (age < 3) return;
      items.push({
        repo: repo.name,
        issue: `Aging task: ${task.title || task.id || 'unknown'}`,
        severity: age >= 7 ? 'high' : 'medium',
        severityNum: age >= 7 ? 3 : 2,
        age: `${age}d`,
        ageDays: age,
        action: 'Review',
      });
    });
  });

  // Sort
  items.sort((a, b) => {
    let cmp = 0;
    if (attentionSortCol === 'severity') cmp = b.severityNum - a.severityNum;
    else if (attentionSortCol === 'age') cmp = b.ageDays - a.ageDays;
    else if (attentionSortCol === 'repo') cmp = a.repo.localeCompare(b.repo);
    else if (attentionSortCol === 'issue') cmp = a.issue.localeCompare(b.issue);
    return attentionSortAsc ? -cmp : cmp;
  });

  el('attention-count').textContent = String(items.length);

  if (!items.length) {
    el('attention-table').style.display = 'none';
    el('attention-empty').style.display = '';
    return;
  }
  el('attention-table').style.display = '';
  el('attention-empty').style.display = 'none';

  el('attention-body').innerHTML = items.slice(0, 15).map(item => `
    <tr>
      <td><code>${esc(item.repo)}</code></td>
      <td>${esc(item.issue)}</td>
      <td><span class="severity-${item.severity}">${esc(item.severity)}</span></td>
      <td>${esc(item.age)}</td>
      <td><span class="action-stub">${esc(item.action)}</span></td>
    </tr>
  `).join('');
}
```

**Step 2: Wire up sortable column headers**

```javascript
document.querySelectorAll('.attention-table th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.getAttribute('data-sort');
    if (attentionSortCol === col) attentionSortAsc = !attentionSortAsc;
    else { attentionSortCol = col; attentionSortAsc = false; }
    if (currentData) renderAttentionQueue(currentData);
  });
});
```

**Step 3: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')"`

**Step 4: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(hub): add attention queue renderer with sortable table"
```

---

### Task 5: Repo Table Renderer

Write the `renderRepoTable(data)` JS function with filtering, inline sparklines, and expandable rows showing task-loop DAGs.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS section)

**Step 1: Write repo table state and filter logic**

```javascript
let repoSearchText = '';
let repoRoleFilter = 'all';
let repoStatusFilter = 'all';
let repoDriftFilter = 'all';
let repoSortCol = 'name';
let repoSortAsc = true;
let expandedRepo = null;  // currently expanded repo name

function repoMatchesFilters(repo) {
  if (repoSearchText && !repo.name.toLowerCase().includes(repoSearchText.toLowerCase())) return false;
  if (repoRoleFilter !== 'all') {
    const role = String((repo.source || '').split(':')[0] || 'unknown');
    if (role !== repoRoleFilter) return false;
  }
  if (repoStatusFilter !== 'all') {
    const state = String(repo.activity_state || 'idle').toLowerCase();
    const isActive = state === 'active';
    const isMissing = !repo.path || repo.missing;
    if (repoStatusFilter === 'active' && !isActive) return false;
    if (repoStatusFilter === 'idle' && (isActive || isMissing)) return false;
    if (repoStatusFilter === 'missing' && !isMissing) return false;
  }
  if (repoDriftFilter !== 'all') {
    const driftCount = n((repo.northstar || {}).priority_score) > 0 ? 1 : 0;
    if (repoDriftFilter === 'has-drift' && !driftCount) return false;
    if (repoDriftFilter === 'clean' && driftCount) return false;
  }
  return true;
}
```

**Step 2: Write renderRepoTable function**

```javascript
function renderRepoTable(data) {
  const allRepos = data.repos || [];
  const filtered = allRepos.filter(repoMatchesFilters);

  // Sort
  filtered.sort((a, b) => {
    let cmp = 0;
    if (repoSortCol === 'name') cmp = (a.name || '').localeCompare(b.name || '');
    else if (repoSortCol === 'role') cmp = (a.source || '').localeCompare(b.source || '');
    else if (repoSortCol === 'drift') cmp = n((b.northstar||{}).priority_score) - n((a.northstar||{}).priority_score);
    else if (repoSortCol === 'tasks') {
      const aTotal = (a.in_progress||[]).length + (a.ready||[]).length + n(a.blocked_open);
      const bTotal = (b.in_progress||[]).length + (b.ready||[]).length + n(b.blocked_open);
      cmp = bTotal - aTotal;
    }
    else if (repoSortCol === 'activity') cmp = n(a.heartbeat_age_seconds) - n(b.heartbeat_age_seconds);
    return repoSortAsc ? cmp : -cmp;
  });

  el('repo-count').textContent = `${filtered.length}/${allRepos.length}`;

  // Build sparkline data from northstardrift history
  const history = ((data.northstardrift || {}).history || {}).points || [];
  const repoSparklines = {};
  if (history.length) {
    allRepos.forEach(repo => {
      const values = history.map(pt => {
        const repoScore = ((pt.repo_scores || []).find(rs => rs.repo === repo.name) || {});
        return Number(repoScore.score || 0);
      }).filter(v => Number.isFinite(v));
      if (values.length > 1) repoSparklines[repo.name] = sparkline(values, '#0f6f7c');
    });
  }

  // Relative time helper
  const relTime = (seconds) => {
    if (!seconds && seconds !== 0) return '—';
    const s = Number(seconds);
    if (s < 60) return 'now';
    if (s < 3600) return `${Math.floor(s/60)}m ago`;
    if (s < 86400) return `${Math.floor(s/3600)}h ago`;
    return `${Math.floor(s/86400)}d ago`;
  };

  const rows = filtered.map(repo => {
    const name = esc(repo.name || '');
    const role = esc(String(repo.source || '').split(':')[0] || '—');
    const isActive = String(repo.activity_state || '').toLowerCase() === 'active';
    const isMissing = !repo.path || repo.missing;
    const statusDot = isMissing ? '<span class="status-dot missing"></span>'
      : isActive ? '<span class="status-dot active"></span>'
      : '<span class="status-dot idle"></span>';
    const driftScore = n((repo.northstar || {}).priority_score);
    const driftText = driftScore > 0 ? `<span class="drift-count">${driftScore}</span>` : '0';
    const done = (repo.task_graph_nodes || []).filter(nd => nd.status === 'done').length;
    const total = (repo.task_graph_nodes || []).length;
    const tasksText = total > 0 ? `${done}/${total}` : '—';
    const spark = repoSparklines[repo.name] || '';
    const activity = relTime(repo.heartbeat_age_seconds);
    const isExpanded = expandedRepo === repo.name;

    let expandedHtml = '';
    if (isExpanded) {
      expandedHtml = renderRepoExpanded(repo);
    }

    return `<tr class="repo-row${isExpanded ? ' expanded' : ''}" data-repo-name="${esc(repo.name)}">
      <td><strong>${name}</strong></td>
      <td>${role}</td>
      <td>${statusDot}</td>
      <td>${driftText}</td>
      <td>${tasksText}</td>
      <td>${spark}</td>
      <td>${activity}</td>
    </tr>${expandedHtml}`;
  }).join('');

  el('repo-body').innerHTML = rows;
}
```

**Step 3: Write renderRepoExpanded function for expandable row details**

```javascript
function renderRepoExpanded(repo) {
  const inProgress = repo.in_progress || [];
  const ready = repo.ready || [];
  const staleOpen = repo.stale_open || [];
  const blocked = n(repo.blocked_open);
  const nodes = repo.task_graph_nodes || [];
  const edges = repo.task_graph_edges || [];

  // Task summary
  let taskSummary = `<strong>Tasks:</strong> ${inProgress.length} in progress, ${ready.length} ready, ${blocked} blocked`;
  if (staleOpen.length) taskSummary += `, ${staleOpen.length} aging`;

  // Git state
  const gitInfo = repo.git_dirty
    ? `<strong>Git:</strong> ${esc(repo.git_branch || '?')} (dirty, +${n(repo.ahead)} -${n(repo.behind)})`
    : `<strong>Git:</strong> ${esc(repo.git_branch || '?')} (clean)`;

  // Mini task DAG (inline SVG)
  let dagHtml = '';
  if (nodes.length > 0 && nodes.length <= 80) {
    dagHtml = `<div class="task-dag" id="task-dag-${esc(repo.name)}"></div>`;
  }

  // Recent drift findings
  const driftFindings = [];
  if (repo.stalled) driftFindings.push('Repo is stalled');
  (repo.stall_reasons || []).forEach(r => driftFindings.push(r));
  const driftHtml = driftFindings.length
    ? `<strong>Drift:</strong> ${driftFindings.map(f => esc(f)).join('; ')}`
    : '<strong>Drift:</strong> clean';

  return `<tr class="repo-expanded-row" data-repo-expanded="${esc(repo.name)}">
    <td colspan="7">
      <div class="repo-expanded">
        <div>${taskSummary}</div>
        <div>${gitInfo}</div>
        <div>${driftHtml}</div>
        ${dagHtml}
      </div>
    </td>
  </tr>`;
}
```

**Step 4: Wire up filter/sort/expand event listeners**

```javascript
el('repo-search').addEventListener('input', (e) => {
  repoSearchText = e.target.value;
  if (currentData) renderRepoTable(currentData);
});
el('repo-role-filter').addEventListener('change', (e) => {
  repoRoleFilter = e.target.value;
  if (currentData) renderRepoTable(currentData);
});
el('repo-status-filter').addEventListener('change', (e) => {
  repoStatusFilter = e.target.value;
  if (currentData) renderRepoTable(currentData);
});
el('repo-drift-filter').addEventListener('change', (e) => {
  repoDriftFilter = e.target.value;
  if (currentData) renderRepoTable(currentData);
});

// Sortable column headers
document.querySelectorAll('.repo-table th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.getAttribute('data-sort');
    if (repoSortCol === col) repoSortAsc = !repoSortAsc;
    else { repoSortCol = col; repoSortAsc = true; }
    if (currentData) renderRepoTable(currentData);
  });
});

// Row expand/collapse
document.addEventListener('click', (e) => {
  const row = e.target.closest('.repo-row');
  if (row) {
    const name = row.getAttribute('data-repo-name');
    expandedRepo = expandedRepo === name ? null : name;
    if (currentData) renderRepoTable(currentData);
    // If expanded and has task DAG, draw it
    if (expandedRepo) {
      const repo = repoByName(expandedRepo);
      if (repo) drawTaskDag(repo);
    }
  }
});
```

**Step 5: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')"`

**Step 6: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(hub): add repo table renderer with filters, sparklines, expandable rows"
```

---

### Task 6: Task DAG and Graphs Panel

Wire the existing `drawRepoDependencyOverview()` to the new Graphs Panel SVG. Add a `drawTaskDag(repo)` function for per-repo mini DAGs shown in expanded repo rows.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS section)

**Step 1: Adapt drawRepoDependencyOverview for new SVG element**

The existing function (lines 1196-1402) already targets `#repo-dep-graph`. The new HTML keeps this ID, but the SVG viewBox changed from `0 0 1200 520` to `0 0 800 500`. Update the function's layout constants to fit the narrower panel:

- Change `svgW` from 1200 to 800
- Change `svgH` from 520 to 500
- Reduce node radius from 22 to 18
- Reduce font size from 10 to 9

**Step 2: Write drawTaskDag function**

This renders a mini DAG inside expanded repo rows. Uses the existing `task_graph_nodes` and `task_graph_edges` data. Detects loops (break-fix cycles) and highlights them.

```javascript
function drawTaskDag(repo) {
  const container = document.getElementById(`task-dag-${repo.name}`);
  if (!container) return;

  const nodes = repo.task_graph_nodes || [];
  const edges = repo.task_graph_edges || [];
  if (!nodes.length) { container.innerHTML = '<em>No task graph</em>'; return; }

  const width = 600;
  const height = Math.min(300, Math.max(120, nodes.length * 25));

  // Simple left-to-right layout by topological order
  const nodeMap = {};
  nodes.forEach((nd, i) => {
    nodeMap[nd.id] = { ...nd, x: 0, y: 0, col: 0, index: i };
  });

  // Detect back-edges (loops)
  const forwardEdges = [];
  const backEdges = [];
  const visited = new Set();
  const inStack = new Set();
  function dfs(nodeId) {
    visited.add(nodeId);
    inStack.add(nodeId);
    edges.filter(e => e.from === nodeId).forEach(e => {
      if (inStack.has(e.to)) backEdges.push(e);
      else if (!visited.has(e.to)) { forwardEdges.push(e); dfs(e.to); }
      else forwardEdges.push(e);
    });
    inStack.delete(nodeId);
  }
  nodes.forEach(nd => { if (!visited.has(nd.id)) dfs(nd.id); });

  // Assign columns via topological sort (ignoring back edges)
  // ... (use BFS from roots, assign x = col * spacing, y = row * spacing)

  const statusColor = (s) => {
    if (s === 'done') return '#2f6e39';
    if (s === 'in_progress') return '#0f6f7c';
    if (s === 'blocked') return '#9c2525';
    return '#a26c13';
  };

  // Render SVG
  let svg = `<svg viewBox="0 0 ${width} ${height}" class="task-dag-svg">`;
  // Draw edges (forward = solid, back = dashed red for loops)
  forwardEdges.forEach(e => {
    const from = nodeMap[e.from];
    const to = nodeMap[e.to];
    if (from && to) svg += `<line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" stroke="#999" stroke-width="1.5"/>`;
  });
  backEdges.forEach(e => {
    const from = nodeMap[e.from];
    const to = nodeMap[e.to];
    if (from && to) svg += `<line x1="${from.x}" y1="${from.y}" x2="${to.x}" y2="${to.y}" stroke="#9c2525" stroke-width="2" stroke-dasharray="4,3"/>`;
  });
  // Draw nodes
  nodes.forEach(nd => {
    const node = nodeMap[nd.id];
    if (!node) return;
    svg += `<circle cx="${node.x}" cy="${node.y}" r="8" fill="${statusColor(nd.status)}" />`;
    svg += `<text x="${node.x + 12}" y="${node.y + 4}" font-size="9" fill="#333">${esc((nd.title || nd.id).substring(0, 20))}</text>`;
  });
  svg += '</svg>';

  if (backEdges.length) {
    svg += `<div class="loop-indicator">${backEdges.length} break-fix loop(s) detected</div>`;
  }

  container.innerHTML = svg;
}
```

Note: The actual x/y layout logic needs proper topological sorting with column assignment. The implementer should use a simple layered layout: BFS from root nodes, assign columns left-to-right, distribute rows within each column evenly.

**Step 3: Wire graph rendering into render() function**

In the main `render(data, source)` function, call:
```javascript
drawRepoDependencyOverview(data);  // existing function, targets #repo-dep-graph
```

And in the repo-row expand handler, call `drawTaskDag(repo)` after expanding.

**Step 4: Update zoom controls for new element IDs**

Replace old zoom handler IDs (`repo-dep-zoom-in`, etc.) with new IDs (`dep-zoom-in`, etc.). The zoom logic stays the same.

**Step 5: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')"`

**Step 6: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(hub): wire graphs panel with dependency map and per-repo task DAGs"
```

---

### Task 7: Wire render() and Remove Dead Code

Update the main `render(data, source)` function to call the new renderers. Remove all old renderer functions and dead HTML/CSS/JS.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py`

**Step 1: Rewrite render() function**

Replace the current `render()` (lines 2401-2428) with:

```javascript
function render(data, source) {
  currentData = data;
  window.currentData = data;
  el('meta').textContent =
    `Generated: ${data.generated_at || 'n/a'} | repos: ${data.repo_count || 0} | transport: ${source}`;

  renderBriefing(data);
  renderAttentionQueue(data);
  renderRepoTable(data);
  drawRepoDependencyOverview(data);

  // Re-draw expanded repo's task DAG if one is open
  if (expandedRepo) {
    const repo = repoByName(expandedRepo);
    if (repo) drawTaskDag(repo);
  }
}
```

**Step 2: Remove dead functions**

Delete these functions entirely:
- `renderNorthstar()`
- `renderOverviewCards()`
- `renderRepoCards()`
- `renderAttention()`
- `renderAging()`
- `renderNext()`
- `renderUpstream()`
- `renderSecurity()`
- `renderQuality()`
- `renderActionSummary()`
- `buildAgentPrompt()`
- `renderActionItemHtml()`
- `refreshRepoSummary()`
- `setActionCount()` (for old action panels)
- `actionRowAllowed()`, `compareActionRows()` (old action filtering)
- `repoHealthAllowed()`, `repoDirtyAllowed()`, `repoServiceAllowed()`, `compareRepos()` (old repo card filtering)
- `refreshActionRepoFilter()` (old action repo dropdown)

**Step 3: Remove dead event listeners**

Delete listeners for old elements:
- `#action-repo-filter`, `#action-sort`, `#action-priority-filter`, `#action-dirty-filter`
- `#repo-sort`, `#repo-health-filter`, `#repo-dirty-filter`, `#repo-service-filter`
- `#graph-mode`, `#graph-repo` (old per-repo graph selector — replaced by expand-in-table)
- Copy-prompt click handler (`data-copy-prompt`)

**Step 4: Remove dead CSS**

Delete CSS rules for:
- `.repo-grid`, `.repo-card`, `.repo-card.active-running`, `@keyframes repoCardPulse`
- `.action-grid`, `.action-panel`, `.action-head`, `.action-list`, `.action-item`, `.action-count`
- `.trend-grid`, `.trend-panel`
- `.cards .card` (old KPI card style — keep if reused, otherwise remove)
- `.repo-toolbar` (old sort/filter bar)

**Step 5: Remove dead state variables**

Delete:
- `actionRepoFilter`, `actionSortMode`, `actionPriorityFilter`, `actionDirtyFilter`
- `repoSortMode`, `repoHealthFilter`, `repoDirtyFilter`, `repoServiceFilter`
- `selectedRepo`, `graphMode`, `selectedNodeId` (if no longer used)

**Step 6: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')"`

**Step 7: Run existing tests**

Run: `cd /Users/braydon/projects/experiments/driftdriver && python -m pytest tests/test_ecosystem_hub.py -x -q`
Expected: all tests pass. If any fail because they assert on old HTML element IDs, update the assertions.

**Step 8: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "refactor(hub): wire new renderers, remove all dead dashboard code"
```

---

### Task 8: URL Param Persistence for Filters

Add URL parameter persistence so filter state survives page refreshes and can be bookmarked.

**Files:**
- Modify: `driftdriver/ecosystem_hub/dashboard.py` (JS section)

**Step 1: Write URL param sync functions**

```javascript
function syncFiltersToUrl() {
  const params = new URLSearchParams();
  if (repoSearchText) params.set('q', repoSearchText);
  if (repoRoleFilter !== 'all') params.set('role', repoRoleFilter);
  if (repoStatusFilter !== 'all') params.set('status', repoStatusFilter);
  if (repoDriftFilter !== 'all') params.set('drift', repoDriftFilter);
  const qs = params.toString();
  const url = qs ? `?${qs}` : window.location.pathname;
  window.history.replaceState({}, '', url);
}

function loadFiltersFromUrl() {
  const params = new URLSearchParams(window.location.search);
  repoSearchText = params.get('q') || '';
  repoRoleFilter = params.get('role') || 'all';
  repoStatusFilter = params.get('status') || 'all';
  repoDriftFilter = params.get('drift') || 'all';
  // Sync UI elements
  el('repo-search').value = repoSearchText;
  el('repo-role-filter').value = repoRoleFilter;
  el('repo-status-filter').value = repoStatusFilter;
  el('repo-drift-filter').value = repoDriftFilter;
}
```

**Step 2: Call loadFiltersFromUrl on page load and syncFiltersToUrl on every filter change**

Add `loadFiltersFromUrl()` before the first `refreshHttp()` call. Add `syncFiltersToUrl()` at the end of each filter event handler.

**Step 3: Verify Python syntax**

Run: `python -c "from driftdriver.ecosystem_hub.dashboard import render_dashboard_html; print('OK')"`

**Step 4: Commit**

```bash
git add driftdriver/ecosystem_hub/dashboard.py
git commit -m "feat(hub): persist repo table filters in URL params"
```

---

### Task 9: Manual QA and Test Fixes

Restart the hub daemon, verify the new dashboard renders correctly, fix any test failures.

**Files:**
- Modify (if needed): `driftdriver/ecosystem_hub/dashboard.py`
- Modify (if needed): `tests/test_ecosystem_hub.py`

**Step 1: Restart hub daemon**

```bash
cd /Users/braydon/projects/experiments/driftdriver
pkill -f "ecosystem_hub.server" || true
sleep 3
```

**Step 2: Verify hub is running**

```bash
curl -s http://127.0.0.1:8777/api/status | python -m json.tool | head -5
```
Expected: JSON snapshot output

**Step 3: Verify dashboard HTML loads**

```bash
curl -s http://127.0.0.1:8777/ | head -20
```
Expected: HTML with new 4-zone structure

**Step 4: Run full test suite**

```bash
cd /Users/braydon/projects/experiments/driftdriver
python -m pytest tests/test_ecosystem_hub.py -x -q
```

Fix any failures. Common issues:
- Tests asserting old HTML element IDs (e.g., `#narrative`, `#northstar-cards`) — update to new IDs
- Tests calling `render_dashboard_html()` and checking for old section headings — update expected strings

**Step 5: Run broader test suite to check for regressions**

```bash
python -m pytest tests/ -x -q --timeout=30
```

**Step 6: Visual QA with Playwright**

Use Playwright to take a screenshot of the new dashboard and verify layout:

```bash
# Navigate to dashboard and screenshot
```

Verify:
- Briefing bar shows 2-3 sentences at top
- Attention queue table is visible below briefing
- Repo table shows all repos in dense rows with sparklines
- Graphs panel shows dependency map on right side
- Page fits in ~2 viewports
- Expanders work (click repo name in briefing, click repo row)

**Step 7: Fix any issues found during QA**

Address layout, rendering, or data issues.

**Step 8: Commit fixes**

```bash
git add -A
git commit -m "fix(hub): address QA issues from dashboard redesign"
```

---

## Summary

| Task | What | Key Files |
|------|------|-----------|
| 1 | New CSS layout | dashboard.py:11-576 |
| 2 | New HTML body structure | dashboard.py:584-779 |
| 3 | Briefing Bar renderer | dashboard.py (JS) |
| 4 | Attention Queue renderer | dashboard.py (JS) |
| 5 | Repo Table renderer | dashboard.py (JS) |
| 6 | Graphs Panel + Task DAG | dashboard.py (JS) |
| 7 | Wire render(), remove dead code | dashboard.py (all sections) |
| 8 | URL param filter persistence | dashboard.py (JS) |
| 9 | Manual QA + test fixes | dashboard.py, test_ecosystem_hub.py |

Total: 9 tasks, ~9 commits, single file modified (dashboard.py) plus test fixes.
