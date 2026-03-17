# ABOUTME: HTML generation, template rendering, and CSS for the ecosystem hub dashboard.
# ABOUTME: Contains the single-page application served at the hub root URL.

def render_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Speedrift Ecosystem Hub</title>
  <style>
    :root {
      --bg: #f5f2ea;
      --panel: #fffcf5;
      --ink: #1d2421;
      --muted: #5f6f66;
      --line: #d7cfbf;
      --accent: #0f6f7c;
      --accent-soft: #d8eef2;
      --warn: #934e1c;
      --bad: #9c2525;
      --good: #2f6e39;
      --mono: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
      --sans: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
    }
    body {
      margin: 0;
      font-family: var(--sans);
      background:
        radial-gradient(circle at 80% -10%, #e8efe9 0%, transparent 46%),
        radial-gradient(circle at 15% 0%, #f0e7d5 0%, transparent 50%),
        var(--bg);
      color: var(--ink);
    }
    header {
      padding: 1rem 1.2rem;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 252, 245, 0.8);
      backdrop-filter: blur(6px);
      position: sticky;
      top: 0;
      z-index: 2;
    }
    h1 {
      margin: 0;
      font-size: 1.06rem;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .meta {
      margin-top: 0.35rem;
      color: var(--muted);
      font-size: 0.86rem;
    }
    h2 {
      margin: 0 0 0.65rem;
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--ink);
    }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .good { color: var(--good); }
    code {
      font-family: var(--mono);
      font-size: 0.82rem;
    }
    select {
      font: inherit;
      padding: 0.25rem 0.38rem;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
    }
    .spark {
      display: block;
      width: 100%;
      height: 34px;
      margin-top: 0.34rem;
    }

    /* --- Graph-related styles (kept) --- */
    @keyframes taskPulseHalo {
      0% { opacity: 0.9; transform: scale(0.82); }
      70% { opacity: 0; transform: scale(1.72); }
      100% { opacity: 0; transform: scale(1.8); }
    }
    @keyframes repoPulseHalo {
      0% { opacity: 0.88; transform: scale(0.86); }
      72% { opacity: 0; transform: scale(1.62); }
      100% { opacity: 0; transform: scale(1.66); }
    }
    .graph-node .pulse-halo {
      display: none;
      pointer-events: none;
      transform-origin: center;
      transform-box: fill-box;
    }
    .graph-node.status-in-progress .pulse-halo {
      display: block;
      animation: taskPulseHalo 1.7s ease-out infinite;
    }
    .graph-node.status-in-progress .base-node {
      filter: drop-shadow(0 0 4px rgba(15, 111, 124, 0.5));
    }
    .graph-legend {
      display: flex;
      gap: 0.6rem;
      flex-wrap: wrap;
      margin-top: 0.5rem;
      font-size: 0.76rem;
      color: var(--muted);
    }
    .dot {
      display: inline-block;
      width: 0.65rem;
      height: 0.65rem;
      border-radius: 999px;
      margin-right: 0.28rem;
      vertical-align: baseline;
    }
    .repo-dep-node .repo-pulse {
      display: none;
      pointer-events: none;
      transform-origin: center;
      transform-box: fill-box;
    }
    .repo-dep-node.active .repo-pulse {
      display: block;
      animation: repoPulseHalo 1.85s ease-out infinite;
    }
    .repo-dep-node.active .repo-main {
      filter: drop-shadow(0 0 5px rgba(15, 111, 124, 0.45));
    }
    #repo-dep-graph {
      width: 100%;
      height: 520px;
      border: 1px solid #e2dacb;
      border-radius: 8px;
      background: #fffcf8;
      display: block;
      overflow: hidden;
      touch-action: none;
      cursor: grab;
    }
    #repo-dep-graph.dragging {
      cursor: grabbing;
    }
    .graph-mini {
      border: 1px solid var(--line);
      border-radius: 9px;
      background: #fff;
      padding: 0.35rem;
    }
    .graph-mini h4 {
      margin: 0 0 0.25rem;
      font-size: 0.78rem;
      color: #344a42;
      display: flex;
      justify-content: space-between;
      gap: 0.4rem;
      align-items: center;
    }
    .graph-mini svg {
      width: 100%;
      height: 150px;
      border: 1px solid #e4ddcf;
      border-radius: 7px;
      background: #fffcf8;
      display: block;
    }

    /* --- New 4-zone layout --- */
    .hub-layout {
      display: flex;
      flex-direction: column;
      max-width: 1400px;
      margin: 0 auto;
      padding: 1rem 1.2rem 2rem;
      gap: 0.95rem;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 0.9rem;
      box-shadow: 0 6px 12px rgba(24, 34, 28, 0.06);
    }
    .section-header {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      margin-bottom: 0.65rem;
    }
    .section-header h2 {
      margin: 0;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.5rem;
      padding: 0.02rem 0.4rem;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel);
      font-family: var(--mono);
      font-size: 0.72rem;
      color: var(--muted);
    }

    /* Zone 1: Briefing Bar */
    .briefing-bar {
      padding: 0.75rem 0.9rem;
    }
    .briefing-text {
      margin: 0;
      line-height: 1.45;
      font-size: 0.95rem;
      color: var(--ink);
    }
    .briefing-expander {
      cursor: pointer;
      text-decoration: underline;
      color: var(--accent);
      font-weight: 600;
    }
    .briefing-detail {
      display: none;
    }
    .briefing-detail.open {
      display: block;
      margin-top: 0.5rem;
      padding: 0.5rem 0.65rem;
      background: var(--accent-soft);
      border-left: 3px solid var(--accent);
      border-radius: 0 8px 8px 0;
      font-size: 0.88rem;
      line-height: 1.4;
    }

    .severity-high { color: var(--bad); font-weight: 600; }
    .severity-medium { color: var(--warn); font-weight: 600; }
    .severity-low { color: var(--good); }
    .start-btn {
      font: inherit;
      font-size: 0.82rem;
      padding: 0.3rem 0.7rem;
      border: 1px solid var(--accent);
      border-radius: 8px;
      background: var(--accent-soft);
      color: var(--accent);
      cursor: pointer;
      font-weight: 600;
    }
    .start-btn:hover {
      background: var(--accent);
      color: #fff;
    }
    .start-btn:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .stall-badge {
      display: inline-block;
      font-size: 0.74rem;
      font-weight: 700;
      color: var(--bad);
      background: #f7dfdf;
      padding: 0.1rem 0.45rem;
      border-radius: 6px;
      margin-left: 0.5rem;
    }

    /* Zone 3: Dependencies */
    .dependency-panel {
      min-height: 420px;
    }

    /* Repo filter bar */
    .repo-filter-bar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-bottom: 0.55rem;
    }
    .repo-filter-bar input {
      font: inherit;
      padding: 0.3rem 0.5rem;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
      min-width: 140px;
    }
    .repo-filter-bar select {
      font: inherit;
      padding: 0.25rem 0.38rem;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
    }

    /* Repo table */
    .repo-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.86rem;
    }
    .repo-table th,
    .repo-table td {
      padding: 0.4rem 0.5rem;
      text-align: left;
      border-bottom: 1px solid var(--line);
    }
    .repo-table th {
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
    }
    .repo-table th[data-sort] {
      cursor: pointer;
      user-select: none;
    }
    .repo-table th[data-sort]:hover {
      color: var(--accent);
    }
    .repo-table tr:hover {
      background: var(--accent-soft);
    }
    .repo-row {
      cursor: pointer;
    }
    .repo-row.selected {
      background: #e6f1ef;
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .status-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 999px;
      vertical-align: middle;
    }
    .status-dot.active { background: var(--good); }
    .status-dot.idle { background: #999; }
    .status-dot.missing { background: var(--bad); }
    .drift-count {
      display: inline-block;
      padding: 0.05rem 0.35rem;
      border-radius: 999px;
      font-family: var(--mono);
      font-size: 0.72rem;
      font-weight: 600;
      color: #fff;
      background: var(--warn);
    }
    .drift-count.high {
      background: var(--bad);
    }

    /* Expanded repo row */
    .repo-expanded-row td {
      padding: 0;
    }
    .repo-expanded {
      padding: 0.65rem 0.75rem;
      border-top: 1px solid var(--line);
      background: #faf7f0;
    }
    .repo-expanded-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 0.8rem 1.4rem;
      align-items: baseline;
    }
    .task-dag {
      margin-top: 0.75rem;
    }
    .repo-graph-wrap {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fffcf8;
      overflow: hidden;
    }
    .task-dag-svg {
      width: 100%;
      min-height: 340px;
      display: block;
      cursor: grab;
      touch-action: none;
    }
    .task-dag-svg.dragging {
      cursor: grabbing;
    }
    /* Task Graph Drawer (full-width below repo table) */
    .task-graph-drawer {
      display: none;
    }
    .task-graph-drawer.open {
      display: block;
    }
    .task-graph-drawer .repo-graph-wrap {
      resize: vertical;
      overflow: hidden;
      min-height: 300px;
      height: 800px;
    }
    .task-graph-drawer .task-dag-svg {
      height: 100%;
      min-height: 0;
    }
    .drawer-repo-name {
      font-weight: 400;
      color: var(--accent);
    }
    .repo-graph-empty {
      color: var(--muted);
      font-size: 0.84rem;
      font-style: italic;
      padding: 0.4rem 0;
    }
    .loop-indicator {
      color: var(--bad);
      font-size: 0.76rem;
      font-weight: 600;
    }

    /* Graph controls */
    .graph-controls {
      display: flex;
      gap: 0.3rem;
      margin-left: auto;
    }
    .graph-controls button {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.2rem 0.48rem;
      background: #fff;
      cursor: pointer;
    }
    .graph-controls select {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.2rem 0.35rem;
      background: #fff;
    }
    .graph-controls button:hover {
      background: #f5efe2;
    }

    .graph-meta {
      margin-top: 0.45rem;
      font-family: var(--mono);
      font-size: 0.76rem;
      color: var(--muted);
    }
    .graph-note {
      margin-top: 0.28rem;
      font-size: 0.82rem;
      color: var(--muted);
      line-height: 1.45;
    }
    .graph-path { font-family: var(--mono); font-size: 0.78rem; }

    /* Chat panel stub */
    #chat-panel[hidden] {
      display: none;
    }

    /* Responsive */
    @media (max-width: 900px) {
      .repo-expanded-meta {
        flex-direction: column;
        gap: 0.35rem;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>Speedrift Ecosystem Hub</h1>
    <div class="meta" id="meta">Loading ecosystem state…</div>
  </header>
  <main class="hub-layout">
    <!-- Zone 1: Briefing Bar -->
    <section class="briefing-bar card" id="briefing-bar">
      <p class="briefing-text" id="briefing-text">Loading ecosystem state...</p>
      <div id="briefing-details"></div>
    </section>

    <!-- Zone 2: Dependencies -->
    <section class="dependency-panel card" id="dependencies-section">
      <div class="section-header">
        <h2>Dependencies</h2>
        <div class="graph-controls">
          <button id="dep-zoom-out" type="button">-</button>
          <button id="dep-zoom-in" type="button">+</button>
          <button id="dep-zoom-reset" type="button">reset</button>
        </div>
      </div>
      <svg id="repo-dep-graph" viewBox="0 0 800 500" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="graph-meta" id="repo-dep-meta">Loading repo dependency graph...</div>
      <div class="graph-note" id="repo-dep-note">Click a repo node to focus and expand that repo below.</div>
      <div class="graph-legend">
        <span><span class="dot" style="background:#2f6e39"></span>Done</span>
        <span><span class="dot" style="background:#0f6f7c"></span>In progress</span>
        <span><span class="dot" style="background:#a26c13"></span>Open</span>
        <span><span class="dot" style="background:#9c2525"></span>Blocked</span>
      </div>
    </section>

    <!-- Zone 4: Repo Table -->
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
          <select id="repo-health-filter">
            <option value="all">all health</option>
            <option value="risk">risk</option>
            <option value="watch">watch</option>
            <option value="healthy">healthy</option>
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
              <th data-sort="health">Health</th>
              <th data-sort="activity">Last Activity</th>
            </tr>
          </thead>
          <tbody id="repo-body"></tbody>
        </table>
    </section>

    <!-- Task Graph Drawer (full-width, opens when repo expanded) -->
    <section class="task-graph-drawer card" id="task-graph-drawer">
      <div class="section-header">
        <h2>Task Graph: <span class="drawer-repo-name" id="drawer-repo-name"></span></h2>
        <div class="graph-controls">
          <select id="drawer-graph-mode">
            <option value="active">active + blocked</option>
            <option value="full">full graph</option>
            <option value="focus">focus chain</option>
          </select>
          <button type="button" id="drawer-zoom-out">&#x2212;</button>
          <button type="button" id="drawer-zoom-in">+</button>
          <button type="button" id="drawer-zoom-reset">reset</button>
        </div>
      </div>
      <div class="repo-graph-wrap">
        <svg class="task-dag-svg" id="drawer-graph-svg" viewBox="0 0 1000 600" preserveAspectRatio="xMidYMin meet"></svg>
      </div>
      <div class="graph-legend" style="margin-top:0.35rem">
        <span><span class="dot" style="background:#2f6e39"></span>Done</span>
        <span><span class="dot" style="background:#0f6f7c"></span>In progress</span>
        <span><span class="dot" style="background:#a26c13"></span>Open</span>
        <span><span class="dot" style="background:#b85c1c"></span>Aging 3d+</span>
        <span><span class="dot" style="background:#8c2f2f"></span>Aging 7d+</span>
        <span><span class="dot" style="background:#9c2525"></span>Blocked</span>
        <span>Pulsing = active runtime task</span>
      </div>
      <div class="graph-path" id="drawer-graph-path" style="margin-top:0.3rem">Select a repo to view its task graph.</div>
    </section>

    <!-- Chat Panel stub (Approach C) -->
    <aside id="chat-panel" hidden>
      <div class="section-header"><h2>Chat</h2></div>
      <div id="chat-messages"></div>
      <input type="text" id="chat-input" placeholder="Ask about your ecosystem..." />
    </aside>
  </main>
  <script>
    let ws = null;
    let pollTimer = null;
    let reconnectTimer = null;
    let currentData = null;
    let selectedRepo = '';
    let expandedRepo = '';
    let graphMode = 'active';
    let selectedNodeId = '';
    const graphView = {
      scale: 1,
      tx: 0,
      ty: 0,
      drag: false,
      dragStartX: 0,
      dragStartY: 0,
      dragBaseX: 0,
      dragBaseY: 0,
    };

    let repoSearchText = '';
    let repoRoleFilter = 'all';
    let repoStatusFilter = 'all';
    let repoDriftFilter = 'all';
    let repoHealthFilter = 'all';
    let repoSortCol = 'name';
    let repoSortAsc = true;

    function el(id) { return document.getElementById(id) || document.createElement('div'); }
    function n(value) { return Number.isFinite(Number(value)) ? Number(value) : 0; }
    function esc(value) {
      return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;');
    }
    function escAttr(value) {
      return esc(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    }
    function repoDomId(name) {
      return String(name || '').replace(/[^a-zA-Z0-9_-]/g, '-');
    }
    function repoByName(name) {
      return (currentData && currentData.repos || []).find((repo) => String(repo.name || '') === String(name || '')) || null;
    }
    function resetGraphViewState() {
      graphView.scale = 1;
      graphView.tx = 0;
      graphView.ty = 0;
      graphView.drag = false;
    }
    function scrollRepoIntoView(name) {
      if (!name) return;
      const domId = repoDomId(name);
      const expanded = document.getElementById('repo-expanded-' + domId);
      const row = Array.from(document.querySelectorAll('.repo-row')).find(function(item) {
        return String(item.getAttribute('data-repo-name') || '') === name;
      }) || null;
      const target = expanded || row;
      if (target && target.scrollIntoView) {
        target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }
    function drawExpandedRepoGraph() {
      var drawer = el('task-graph-drawer');
      if (!currentData || !expandedRepo) {
        drawer.classList.remove('open');
        return;
      }
      drawer.classList.add('open');
      var repo = repoByName(expandedRepo);
      var nameEl = el('drawer-repo-name');
      nameEl.innerHTML = esc(expandedRepo)
        + (repo && repo.stalled ? '<span class="stall-badge">STALLED</span>' : '');
      var modeSelect = el('drawer-graph-mode');
      if (modeSelect) modeSelect.value = graphMode;
      var repo = repoByName(expandedRepo);
      if (repo) drawTaskDag(repo, el('drawer-graph-svg'), el('drawer-graph-path'));
    }
    function selectRepo(name, options) {
      const nextRepo = String(name || '');
      const toggleExpanded = !!(options && options.toggleExpanded);
      const forceExpanded = !!(options && options.forceExpanded);
      const shouldScroll = !!(options && options.scrollIntoView);
      const nextExpanded = toggleExpanded
        ? (expandedRepo === nextRepo ? '' : nextRepo)
        : (forceExpanded ? nextRepo : expandedRepo);
      const repoChanged = nextRepo !== selectedRepo;
      const expansionChanged = nextExpanded !== expandedRepo;
      if (repoChanged || expansionChanged) {
        selectedNodeId = '';
        resetGraphViewState();
      }
      selectedRepo = nextRepo;
      expandedRepo = nextExpanded;
      if (currentData) {
        renderRepoTable(currentData);
        drawRepoDependencyOverview(currentData);
        drawExpandedRepoGraph();
      }
      if (shouldScroll) {
        window.requestAnimationFrame(function() {
          scrollRepoIntoView(expandedRepo || selectedRepo);
        });
      }
    }

    function syncFiltersToUrl() {
      var params = new URLSearchParams();
      if (repoSearchText) params.set('q', repoSearchText);
      if (repoRoleFilter !== 'all') params.set('role', repoRoleFilter);
      if (repoStatusFilter !== 'all') params.set('status', repoStatusFilter);
      if (repoDriftFilter !== 'all') params.set('drift', repoDriftFilter);
      if (repoHealthFilter !== 'all') params.set('health', repoHealthFilter);
      var qs = params.toString();
      var url = qs ? '?' + qs : window.location.pathname;
      window.history.replaceState({}, '', url);
    }

    function loadFiltersFromUrl() {
      var params = new URLSearchParams(window.location.search);
      repoSearchText = params.get('q') || '';
      repoRoleFilter = params.get('role') || 'all';
      repoStatusFilter = params.get('status') || 'all';
      repoDriftFilter = params.get('drift') || 'all';
      repoHealthFilter = params.get('health') || 'all';
      el('repo-search').value = repoSearchText;
      el('repo-role-filter').value = repoRoleFilter;
      el('repo-status-filter').value = repoStatusFilter;
      el('repo-drift-filter').value = repoDriftFilter;
      el('repo-health-filter').value = repoHealthFilter;
    }

    function needsHumanBadge(repo) {
      var ci = repo.continuation_intent || {};
      if (String(ci.intent || '') !== 'needs_human') return '';
      var reason = esc(String(ci.reason || 'decision needed').substring(0, 60));
      return '<span class="stall-badge" title="' + escAttr(reason) + '" style="background:#f3e8d0;color:#934e1c;margin-left:0.4rem">NEEDS HUMAN</span>';
    }

    function qualityPill(repo) {
      const north = repo.northstar || {};
      const northTier = String(north.tier || '').toLowerCase();
      if (northTier === 'at-risk') return ['risk', 'bad'];
      if (northTier === 'watch') return ['watch', 'warn'];
      const sec = repo.security || {};
      const qa = repo.quality || {};
      const secCritical = n(sec.critical);
      const secHigh = n(sec.high);
      const qaCritical = n(qa.critical);
      const qaHigh = n(qa.high);
      const qaScore = n(qa.quality_score || 100);
      const score = n(repo.blocked_open) + n(repo.missing_dependencies) + n((repo.stale_open || []).length) + n((repo.stale_in_progress || []).length);
      if ((repo.errors || []).length || secCritical > 0 || qaCritical > 0 || secHigh >= 2 || qaHigh >= 2 || qaScore < 72 || score >= 5 || (repo.stalled && score >= 2)) return ['risk', 'bad'];
      if (score >= 2 || repo.stalled || (repo.workgraph_exists && !repo.service_running) || secHigh > 0 || qaHigh > 0 || qaScore < 88) return ['watch', 'warn'];
      return ['healthy', 'good'];
    }

    function fallbackRepoDependencyOverview(data) {
      const repos = Array.isArray(data.repos) ? data.repos : [];
      const nodes = repos.map((repo) => ({
        id: String(repo.name || ''),
        source: String(repo.source || ''),
        workgraph_exists: !!repo.workgraph_exists,
        service_running: !!repo.service_running,
        risk_score: 0,
        outbound: 0,
        inbound: 0,
        outbound_weight: 0,
        inbound_weight: 0,
      }));
      const nodeMap = new Map(nodes.map((row) => [String(row.id || ''), row]));
      const edgeMap = new Map();

      repos.forEach((repo) => {
        const source = String(repo.name || '');
        const deps = Array.isArray(repo.cross_repo_dependencies) ? repo.cross_repo_dependencies : [];
        deps.forEach((dep) => {
          if (!dep || typeof dep !== 'object') return;
          const target = String(dep.repo || '');
          if (!source || !target || source === target || !nodeMap.has(target)) return;
          const weight = Math.max(1, Number(dep.score || 0));
          const key = `${source}->${target}`;
          const prev = edgeMap.get(key) || { source, target, weight: 0, reasons: [] };
          prev.weight = Math.min(24, Number(prev.weight || 0) + weight);
          const reasons = Array.isArray(dep.reasons) ? dep.reasons.map((item) => String(item || '')).filter(Boolean) : [];
          reasons.forEach((reason) => {
            if (!prev.reasons.includes(reason)) prev.reasons.push(reason);
          });
          edgeMap.set(key, prev);
        });
      });

      const edges = Array.from(edgeMap.values()).sort((a, b) => (
        Number(b.weight || 0) - Number(a.weight || 0) ||
        String(a.source || '').localeCompare(String(b.source || '')) ||
        String(a.target || '').localeCompare(String(b.target || ''))
      ));
      edges.forEach((edge) => {
        const sourceNode = nodeMap.get(String(edge.source || ''));
        const targetNode = nodeMap.get(String(edge.target || ''));
        if (sourceNode) {
          sourceNode.outbound = Number(sourceNode.outbound || 0) + 1;
          sourceNode.outbound_weight = Number(sourceNode.outbound_weight || 0) + Number(edge.weight || 0);
        }
        if (targetNode) {
          targetNode.inbound = Number(targetNode.inbound || 0) + 1;
          targetNode.inbound_weight = Number(targetNode.inbound_weight || 0) + Number(edge.weight || 0);
        }
      });

      const isolated = nodes.filter((row) => !Number(row.outbound || 0) && !Number(row.inbound || 0));
      const topOutbound = nodes
        .slice()
        .sort((a, b) => (
          Number(b.outbound_weight || 0) - Number(a.outbound_weight || 0) ||
          Number(b.outbound || 0) - Number(a.outbound || 0) ||
          String(a.id || '').localeCompare(String(b.id || ''))
        ))
        .slice(0, 3)
        .map((row) => ({ repo: row.id, weight: row.outbound_weight, count: row.outbound }));
      const topInbound = nodes
        .slice()
        .sort((a, b) => (
          Number(b.inbound_weight || 0) - Number(a.inbound_weight || 0) ||
          Number(b.inbound || 0) - Number(a.inbound || 0) ||
          String(a.id || '').localeCompare(String(b.id || ''))
        ))
        .slice(0, 3)
        .map((row) => ({ repo: row.id, weight: row.inbound_weight, count: row.inbound }));

      return {
        nodes: nodes.sort((a, b) => String(a.id || '').localeCompare(String(b.id || ''))),
        edges,
        summary: {
          repo_count: nodes.length,
          edge_count: edges.length,
          linked_repos: nodes.length - isolated.length,
          isolated_repos: isolated.length,
          top_outbound: topOutbound,
          top_inbound: topInbound,
        },
      };
    }

    function drawRepoDependencyOverview(data) {
      const svg = el('repo-dep-graph');
      const meta = el('repo-dep-meta');
      const note = el('repo-dep-note');
      if (!svg || !meta || !note) return;

      const payload = data && data.repo_dependency_overview;
      const overview = payload && Array.isArray(payload.nodes) && Array.isArray(payload.edges)
        ? payload
        : fallbackRepoDependencyOverview(data || {});
      const nodes = Array.isArray(overview.nodes) ? overview.nodes.slice() : [];
      const edges = Array.isArray(overview.edges) ? overview.edges.slice() : [];
      const summary = overview.summary && typeof overview.summary === 'object' ? overview.summary : {};

      if (!nodes.length) {
        svg.dataset.baseWidth = '800';
        svg.dataset.baseHeight = '280';
        svg.setAttribute('viewBox', '0 0 800 280');
        svg.innerHTML = '<text x="24" y="48" fill="#5f6f66" font-size="16">No repo dependency signals available yet.</text>';
        meta.textContent = 'repo dependencies unavailable';
        note.textContent = 'As repos reference each other in task IDs/titles/dependency links, this map will populate.';
        return;
      }

      const rankedNodes = nodes
        .slice()
        .sort((a, b) => (
          Number(b.outbound_weight || 0) - Number(a.outbound_weight || 0) ||
          Number(b.inbound_weight || 0) - Number(a.inbound_weight || 0) ||
          String(a.id || '').localeCompare(String(b.id || ''))
        ));
      const width = Math.max(800, 300 + rankedNodes.length * 38);
      const height = 500;
      svg.dataset.baseWidth = String(width);
      svg.dataset.baseHeight = String(height);
      const centerX = width / 2;
      const centerY = height / 2;

      // Force-directed layout: start with circle, then simulate springs
      const pos = {};
      const nodeCount = Math.max(1, rankedNodes.length);
      const initRadius = Math.max(100, Math.min(width, height) * 0.38);
      rankedNodes.forEach((node, idx) => {
        const id = String(node.id || '');
        if (!id) return;
        const theta = (Math.PI * 2 * idx) / nodeCount;
        pos[id] = {
          x: centerX + initRadius * Math.cos(theta),
          y: centerY + initRadius * Math.sin(theta),
          vx: 0, vy: 0,
          node,
        };
      });

      // Build edge lookup for spring forces
      const edgeLookup = {};
      edges.forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (pos[s] && pos[t]) {
          edgeLookup[s] = edgeLookup[s] || [];
          edgeLookup[s].push(t);
          edgeLookup[t] = edgeLookup[t] || [];
          edgeLookup[t].push(s);
        }
      });

      // Run force simulation (simple: repulsion between all nodes, attraction along edges)
      const iterations = 80;
      const repulsion = 8000;
      const attraction = 0.008;
      const damping = 0.88;
      const padding = 40;
      const ids = Object.keys(pos);
      for (let iter = 0; iter < iterations; iter++) {
        // Repulsion: all pairs
        for (let i = 0; i < ids.length; i++) {
          for (let j = i + 1; j < ids.length; j++) {
            const a = pos[ids[i]];
            const b = pos[ids[j]];
            let dx = a.x - b.x;
            let dy = a.y - b.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = repulsion / (dist * dist);
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            a.vx += fx; a.vy += fy;
            b.vx -= fx; b.vy -= fy;
          }
        }
        // Attraction: connected pairs
        edges.forEach((edge) => {
          const s = String(edge.source || '');
          const t = String(edge.target || '');
          if (!pos[s] || !pos[t]) return;
          const a = pos[s];
          const b = pos[t];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = attraction * dist;
          const fx = (dx / dist) * force;
          const fy = (dy / dist) * force;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        });
        // Gravity toward center
        ids.forEach((id) => {
          const p = pos[id];
          p.vx += (centerX - p.x) * 0.002;
          p.vy += (centerY - p.y) * 0.002;
        });
        // Apply velocities with damping and bounds
        ids.forEach((id) => {
          const p = pos[id];
          p.vx *= damping;
          p.vy *= damping;
          p.x += p.vx;
          p.y += p.vy;
          p.x = Math.max(padding, Math.min(width - padding, p.x));
          p.y = Math.max(padding, Math.min(height - padding, p.y));
        });
      }

      const related = new Set();
      if (selectedRepo && selectedRepo !== "__all__") {
        related.add(selectedRepo);
        edges.forEach((edge) => {
          const source = String(edge.source || '');
          const target = String(edge.target || '');
          if (source === selectedRepo) related.add(target);
          if (target === selectedRepo) related.add(source);
        });
      }

      const edgeSvg = edges
        .filter((edge) => pos[String(edge.source || '')] && pos[String(edge.target || '')])
        .map((edge) => {
          const source = String(edge.source || '');
          const target = String(edge.target || '');
          const a = pos[source];
          const b = pos[target];
          const mx = (a.x + b.x) / 2;
          const my = (a.y + b.y) / 2;
          // Gentle curve perpendicular to edge direction
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const cx = mx + (-dy / dist) * 18;
          const cy = my + (dx / dist) * 18;
          const weight = Number(edge.weight || 1);
          const emphasis = selectedRepo && related.size ? (source === selectedRepo || target === selectedRepo) : false;
          const opacity = selectedRepo && related.size ? (emphasis ? 0.96 : 0.25) : 0.8;
          const stroke = emphasis ? '#0f6f7c' : '#b7ad9b';
          const strokeWidth = Math.max(1.1, Math.min(4.2, 1 + weight * 0.15));
          const reasons = Array.isArray(edge.reasons) ? edge.reasons.join(', ') : '';
          const label = `${source} -> ${target} | weight=${Math.round(weight)}${reasons ? ` | ${reasons}` : ''}`;
          return `<path d="M ${a.x} ${a.y} Q ${cx} ${cy}, ${b.x} ${b.y}" stroke="${stroke}" stroke-width="${strokeWidth}" opacity="${opacity}" fill="none" marker-end="url(#repo-dep-arrow)"><title>${esc(label)}</title></path>`;
        })
        .join('');

      const nodeSvg = rankedNodes
        .map((node) => {
          const id = String(node.id || '');
          const entry = pos[id];
          if (!entry) return '';
          const row = repoByName(id) || {};
          const [_pill, kind] = qualityPill(row);
          const activeCount = Array.isArray(row.in_progress) ? row.in_progress.length : 0;
          const isRepoActive = String(row.activity_state || '').toLowerCase() === 'active';
          const connected = !selectedRepo || selectedRepo === "__all__" || !related.size || related.has(id);
          const isSelected = selectedRepo === id;
          const fill = kind === 'bad' ? '#f7dfdf' : (kind === 'warn' ? '#f9ead7' : '#e2f0e4');
          const radiusNode = Math.max(12, Math.min(21, 12 + Number(node.inbound_weight || 0) * 0.08 + Number(node.outbound_weight || 0) * 0.08));
          const stroke = isSelected ? '#0f6f7c' : '#385148';
          const strokeW = isSelected ? 3 : 1.4;
          const opacity = connected ? 1 : 0.34;
          const hint = `${id} | out=${n(node.outbound)} (${n(node.outbound_weight)}) | in=${n(node.inbound)} (${n(node.inbound_weight)}) | in-progress=${activeCount}`;
          return `
            <g class="repo-dep-node ${isRepoActive ? 'active' : ''}" data-focus-repo="${escAttr(id)}" data-scroll-graph="1" style="cursor:pointer; opacity:${opacity}">
              ${isRepoActive ? `<circle class="repo-pulse" cx="${entry.x}" cy="${entry.y}" r="${radiusNode + 6}" fill="none" stroke="#0f6f7c" stroke-width="2.2" />` : ''}
              <circle class="repo-main" cx="${entry.x}" cy="${entry.y}" r="${radiusNode}" fill="${fill}" stroke="${stroke}" stroke-width="${strokeW}" />
              <text x="${entry.x + radiusNode + 6}" y="${entry.y + 4}" fill="#2e3d35" font-size="12">${esc(id)}</text>
              <title>${esc(hint)}</title>
            </g>
          `;
        })
        .join('');

      svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
      svg.innerHTML = `
        <defs>
          <marker id="repo-dep-arrow" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto">
            <polygon points="0 0, 9 3.5, 0 7" fill="#b7ad9b"></polygon>
          </marker>
        </defs>
        <rect x="0" y="0" width="${width}" height="${height}" fill="#fffcf8"></rect>
        ${edgeSvg}
        ${nodeSvg}
      `;
      applyRepoDepZoom();

      meta.textContent =
        `repo dependencies | repos=${n(summary.repo_count || nodes.length)} | links=${n(summary.edge_count || edges.length)} | isolated=${n(summary.isolated_repos || 0)}`;

      const topOut = Array.isArray(summary.top_outbound) && summary.top_outbound.length ? summary.top_outbound[0] : null;
      const topIn = Array.isArray(summary.top_inbound) && summary.top_inbound.length ? summary.top_inbound[0] : null;
      const outText = topOut && topOut.repo ? `${topOut.repo} (${n(topOut.weight)})` : 'n/a';
      const inText = topIn && topIn.repo ? `${topIn.repo} (${n(topIn.weight)})` : 'n/a';
      note.innerHTML =
        `Edge A -> B means repo A has dependency signals pointing to repo B. Pulsing repo nodes have in-progress tasks. Top outbound: <code>${esc(outText)}</code>. Top inbound: <code>${esc(inText)}</code>.`;
    }

    function drawTaskDag(repo, targetSvg, targetOut) {
      var svg = targetSvg || null;
      var out = targetOut || null;
      if (!svg || !out) return;
      var baseModel = normalizeGraph(repo);
      if (!baseModel.nodes.length) {
        svg.setAttribute('viewBox', '0 0 1000 340');
        svg.innerHTML = '<text x="40" y="60" fill="#5f6f66" font-size="16">No task graph for selected repo.</text>';
        out.textContent = 'No graph data for selected repo. This usually means tasks have not been written to .workgraph/graph.jsonl yet.';
        return;
      }

      var shaped = subgraphForMode(baseModel, graphMode, selectedNodeId);
      var model = layoutGraph(shaped);
      if (selectedNodeId && !model.pos[selectedNodeId]) {
        selectedNodeId = '';
      }
      var activeNodeId = selectedNodeId || shaped.seed || '';
      var traversal = activeNodeId ? traverseSelection(model, activeNodeId) : null;
      var cycleEdges = detectCycleEdges(baseModel.edges);

      var edgeSvg = model.edges
        .filter(function(edge) { return model.pos[edge.source] && model.pos[edge.target]; })
        .map(function(edge) {
          var a = model.pos[edge.source];
          var b = model.pos[edge.target];
          var cx1 = a.x + Math.max(24, Math.abs(b.x - a.x) * 0.35);
          var cx2 = b.x - Math.max(24, Math.abs(b.x - a.x) * 0.35);
          var edgeKey = String(edge.source || '') + '->' + String(edge.target || '');
          var inPath = traversal ? traversal.pathEdges.has(edgeKey) : false;
          var isCycle = cycleEdges.has(edgeKey);
          var stroke = inPath ? '#0f6f7c' : (isCycle ? '#8c2f2f' : '#b8b0a3');
          var opacity = inPath ? 1.0 : (traversal ? 0.2 : 0.82);
          var dash = isCycle ? ' stroke-dasharray="6 4"' : '';
          var width = inPath ? 2.1 : 1.4;
          return '<path d="M ' + a.x + ' ' + a.y + ' C ' + cx1 + ' ' + a.y + ', ' + cx2 + ' ' + b.y + ', ' + b.x + ' ' + b.y + '" stroke="' + stroke + '" stroke-width="' + width + '" fill="none" opacity="' + opacity + '"' + dash + ' />';
        })
        .join('');

      var repoRuntime = repo.runtime && typeof repo.runtime === 'object' ? repo.runtime : {};
      var activeTaskIds = Array.isArray(repoRuntime.active_task_ids) && repoRuntime.active_task_ids.length
        ? new Set(repoRuntime.active_task_ids.map(function(value) { return String(value); }))
        : null;
      var isRepoActive = String(repo.activity_state || '').toLowerCase() === 'active';
      var nodeSvg = Object.values(model.pos).map(function(entry) {
        var label = String(entry.node.label || entry.node.title || entry.node.id || '').slice(0, 28);
        var status = String(entry.node.status || '').toLowerCase();
        var statusClass = status.replace(/[^a-z0-9]+/g, '-');
        var isInProgress = status === 'in-progress';
        var isRuntimeActive = activeTaskIds ? activeTaskIds.has(String(entry.node.id || '')) : false;
        var shouldPulse = activeTaskIds ? isRuntimeActive : (isInProgress && isRepoActive);
        var age = Number.isFinite(Number(entry.node.age_days)) ? String(entry.node.age_days) + 'd' : '';
        var isSelected = activeNodeId && String(entry.node.id) === String(activeNodeId);
        var inPath = traversal ? traversal.pathNodes.has(String(entry.node.id)) : false;
        var stroke = isSelected ? '#0f6f7c' : (inPath ? '#1b5f69' : '#fff');
        var strokeW = isSelected ? 3 : (inPath ? 2 : 1);
        var opacity = traversal ? (inPath ? 1 : 0.34) : 1;
        return '<g class="graph-node status-' + escAttr(statusClass) + '" data-node-id="' + escAttr(entry.node.id) + '" style="opacity:' + opacity + '; cursor:pointer;">'
          + (shouldPulse ? '<circle class="pulse-halo" cx="' + entry.x + '" cy="' + entry.y + '" r="14" fill="none" stroke="#0f6f7c" stroke-width="2.3" />' : '')
          + '<circle class="base-node" cx="' + entry.x + '" cy="' + entry.y + '" r="10" fill="' + colorFor(entry.node) + '" stroke="' + stroke + '" stroke-width="' + strokeW + '" />'
          + '<text x="' + (entry.x + 16) + '" y="' + (entry.y + 5) + '" fill="#2b3932" font-size="12">' + esc(entry.node.id) + '</text>'
          + '<text x="' + (entry.x + 16) + '" y="' + (entry.y + 20) + '" fill="#6b776f" font-size="10">' + esc(label) + (age ? ' ' + esc(age) : '') + '</text>'
          + '</g>';
      }).join('');

      var depthLabels = Array.from({ length: Math.max(1, model.maxDepth + 1) }, function(_v, idx) { return idx; })
        .map(function(depth) {
          return '<text x="' + (120 + depth * 230 - 16) + '" y="32" fill="#6b776f" font-size="12">D' + depth + '</text>';
        })
        .join('');

      svg.setAttribute('viewBox', '0 0 ' + model.width + ' ' + model.height);
      svg.innerHTML =
        '<rect x="0" y="0" width="' + model.width + '" height="' + model.height + '" fill="#fffdfa" pointer-events="none" />'
        + '<g transform="translate(' + graphView.tx + ' ' + graphView.ty + ') scale(' + graphView.scale + ')">'
        + depthLabels + edgeSvg + nodeSvg
        + '</g>';

      setGraphPathText(
        out,
        model,
        activeNodeId,
        traversal || { ancestors: new Set(), descendants: new Set(), pathNodes: new Set(), pathEdges: new Set() },
        cycleEdges,
        graphMode,
        shaped.seed,
        baseModel,
      );
    }

    function renderBriefing(data) {
      var attentionRepos = (data.overview && Array.isArray(data.overview.attention_repos))
        ? data.overview.attention_repos : [];
      var repos = Array.isArray(data.repos) ? data.repos : [];
      var activeCount = repos.filter(function(r) {
        return String(r.activity_state || '').toLowerCase() === 'active';
      }).length;
      var trend = (data.northstardrift && data.northstardrift.summary)
        ? String(data.northstardrift.summary.overall_trend || 'stable') : 'stable';

      var briefingHtml = '';
      if (!attentionRepos.length) {
        briefingHtml = 'All ' + esc(String(activeCount || repos.length)) + ' repos are running smoothly.';
      } else {
        var count = attentionRepos.length;
        var displayRepos = attentionRepos.slice(0, 3);
        var names = displayRepos.map(function(ar) {
          var rName = esc(String(ar.repo || ''));
          var safeRepo = escAttr(String(ar.repo || ''));
          return '<span class="briefing-expander" data-repo="' + safeRepo + '">' + rName + ' &#9656;</span>';
        });
        var nameStr = names.join(', ');
        if (count > 3) nameStr += ' (+' + esc(String(count - 3)) + ' more)';
        briefingHtml = esc(String(count)) + ' repos need attention \\u2014 ' + nameStr + '.';
        briefingHtml += ' Ecosystem trend: ' + esc(trend) + ' across ' + esc(String(activeCount || repos.length)) + ' active repos.';
      }
      el('briefing-text').innerHTML = briefingHtml;

      var detailsContainer = el('briefing-details');
      detailsContainer.innerHTML = '';
      var detailRepos = attentionRepos.slice(0, 3);
      detailRepos.forEach(function(ar) {
        var repoName = String(ar.repo || '');
        var repoObj = repoByName(repoName);
        var div = document.createElement('div');
        div.className = 'briefing-detail';
        div.id = 'briefing-detail-' + repoName.replace(/[^a-zA-Z0-9_-]/g, '-');
        var stalledText = repoObj && repoObj.stalled ? 'Stalled' : 'Active';
        var taskCount = repoObj && Array.isArray(repoObj.in_progress)
          ? repoObj.in_progress.length : 0;
        var reasons = Array.isArray(ar.reasons) ? ar.reasons.slice(0, 3) : [];
        var reasonsText = reasons.length
          ? reasons.map(function(r) { return esc(String(r)); }).join('; ')
          : 'elevated pressure score';
        var stallReasons = repoObj && Array.isArray(repoObj.stall_reasons)
          ? repoObj.stall_reasons.slice(0, 2) : [];
        var stallText = stallReasons.length
          ? ' (' + stallReasons.map(function(r) { return esc(String(r)); }).join(', ') + ')'
          : '';
        var score = n(ar.score);
        var inner = '<strong>' + esc(repoName) + '</strong> \\u2014 '
          + esc(stalledText) + stallText
          + ' | ' + esc(String(taskCount)) + ' in-progress task' + (taskCount !== 1 ? 's' : '')
          + ' | Reasons: ' + reasonsText
          + ' | Score: ' + esc(String(score));
        div.innerHTML = inner;
        detailsContainer.appendChild(div);
      });
    }

    function repoRole(repo) {
      var src = String(repo.source || '');
      if (repo.ecosystem_role) return String(repo.ecosystem_role).toLowerCase();
      var match = src.match(/^(orchestrator|baseline|lane|product)[:]/i);
      if (match) return match[1].toLowerCase();
      if (/orchestrator/i.test(src)) return 'orchestrator';
      if (/baseline/i.test(src)) return 'baseline';
      if (/lane/i.test(src)) return 'lane';
      if (/product/i.test(src)) return 'product';
      return src ? src.split(':')[0].toLowerCase() : '';
    }

    function repoStatus(repo) {
      if (!repo.path || repo.missing) return 'missing';
      if (String(repo.activity_state || '').toLowerCase() === 'active') return 'active';
      return 'idle';
    }

    function repoHasDrift(repo) {
      return n((repo.northstar || {}).priority_score) > 0;
    }

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
      return true;
    }

    function relativeTime(seconds) {
      if (seconds == null || !Number.isFinite(Number(seconds))) return '\u2014';
      var s = Number(seconds);
      if (s < 60) return 'now';
      if (s < 3600) return String(Math.floor(s / 60)) + 'm ago';
      if (s < 86400) return String(Math.floor(s / 3600)) + 'h ago';
      return String(Math.floor(s / 86400)) + 'd ago';
    }

    function repoTaskCounts(repo) {
      var nodes = Array.isArray(repo.task_graph_nodes) ? repo.task_graph_nodes : [];
      var done = 0;
      var total = nodes.length;
      nodes.forEach(function(nd) {
        if (String(nd.status || '').toLowerCase() === 'done') done++;
      });
      return { done: done, total: total };
    }

    function repoSparklineData(data) {
      var map = {};
      var history = (data.northstardrift && data.northstardrift.history && Array.isArray(data.northstardrift.history.points))
        ? data.northstardrift.history.points : [];
      history.forEach(function(point) {
        var scores = Array.isArray(point.repo_scores) ? point.repo_scores : [];
        scores.forEach(function(rs) {
          var name = String(rs.repo || '');
          if (!name) return;
          if (!map[name]) map[name] = [];
          map[name].push(Number(rs.score || 0));
        });
      });
      return map;
    }

    function repoSparklineSvg(values, color) {
      if (!values || !values.length) return '';
      var width = 80;
      var height = 24;
      var min = Math.min.apply(null, values);
      var max = Math.max.apply(null, values);
      var span = Math.max(1, max - min);
      var pts = values.map(function(value, idx) {
        var x = values.length <= 1 ? 0 : (idx / (values.length - 1)) * width;
        var y = height - (((value - min) / span) * (height - 4)) - 2;
        return x.toFixed(1) + ',' + y.toFixed(1);
      }).join(' ');
      return '<svg class="spark" viewBox="0 0 ' + width + ' ' + height + '" preserveAspectRatio="none" style="width:80px;height:24px;display:inline-block;vertical-align:middle"><polyline fill="none" stroke="' + color + '" stroke-width="1.8" points="' + pts + '" /></svg>';
    }

    function renderRepoTable(data) {
      var allRepos = Array.isArray(data.repos) ? data.repos : [];
      var filtered = allRepos.filter(repoMatchesFilters);

      filtered.sort(function(a, b) {
        var dir = repoSortAsc ? 1 : -1;
        if (repoSortCol === 'name') {
          return String(a.name || '').localeCompare(String(b.name || '')) * dir;
        }
        if (repoSortCol === 'role') {
          return repoRole(a).localeCompare(repoRole(b)) * dir;
        }
        if (repoSortCol === 'drift') {
          var da = n((a.northstar || {}).priority_score);
          var db = n((b.northstar || {}).priority_score);
          var d = db - da;
          return d !== 0 ? d * dir : String(a.name || '').localeCompare(String(b.name || ''));
        }
        if (repoSortCol === 'tasks') {
          var ta = repoTaskCounts(a);
          var tb = repoTaskCounts(b);
          var dt = tb.total - ta.total;
          return dt !== 0 ? dt * dir : String(a.name || '').localeCompare(String(b.name || ''));
        }
        if (repoSortCol === 'health') {
          var healthOrder = { risk: 0, watch: 1, healthy: 2 };
          var ha = healthOrder[qualityPill(a)[0]] || 2;
          var hb = healthOrder[qualityPill(b)[0]] || 2;
          var dh = ha - hb;
          return dh !== 0 ? dh * dir : String(a.name || '').localeCompare(String(b.name || ''));
        }
        if (repoSortCol === 'activity') {
          var aa = (a.heartbeat_age_seconds != null) ? Number(a.heartbeat_age_seconds) : 999999;
          var ab = (b.heartbeat_age_seconds != null) ? Number(b.heartbeat_age_seconds) : 999999;
          var dAct = aa - ab;
          return dAct !== 0 ? dAct * dir : String(a.name || '').localeCompare(String(b.name || ''));
        }
        return String(a.name || '').localeCompare(String(b.name || '')) * dir;
      });

      var sparkData = repoSparklineData(data);

      var rows = [];
      filtered.forEach(function(repo) {
        var repoName = String(repo.name || '');
        var role = repoRole(repo);
        var status = repoStatus(repo);
        var driftScore = n((repo.northstar || {}).priority_score);
        var tc = repoTaskCounts(repo);
        var sparkValues = sparkData[repoName] || [];
        var sparkColor = driftScore > 0 ? '#934e1c' : '#2f6e39';
        var sparkSvg = repoSparklineSvg(sparkValues, sparkColor);
        var lastActivity = relativeTime(repo.heartbeat_age_seconds);

        var driftHtml = driftScore > 0
          ? '<span class="drift-count' + (driftScore >= 20 ? ' high' : '') + '">' + esc(String(driftScore)) + '</span>'
          : '<span style="color:var(--muted)">0</span>';
        var selectedClass = selectedRepo === repoName ? ' selected' : '';
        var selectedAttr = selectedRepo === repoName ? ' aria-selected="true"' : ' aria-selected="false"';

        var healthPill = qualityPill(repo);
        var healthLabel = healthPill[0];
        var healthClass = 'severity-' + (healthPill[1] === 'bad' ? 'high' : (healthPill[1] === 'warn' ? 'medium' : 'low'));

        rows.push(
          '<tr class="repo-row' + selectedClass + '" data-repo-name="' + escAttr(repoName) + '"' + selectedAttr + '>'
          + '<td><strong>' + esc(repoName) + '</strong>' + needsHumanBadge(repo) + '</td>'
          + '<td>' + esc(role || '\u2014') + '</td>'
          + '<td><span class="status-dot ' + status + '"></span></td>'
          + '<td>' + driftHtml + '</td>'
          + '<td>' + esc(String(tc.done)) + '/' + esc(String(tc.total)) + '</td>'
          + '<td>' + sparkSvg + '</td>'
          + '<td><span class="' + healthClass + '">' + esc(healthLabel) + '</span></td>'
          + '<td>' + esc(lastActivity) + '</td>'
          + '</tr>'
        );

        if (expandedRepo === repoName) {
          rows.push(renderRepoExpanded(repo));
        }
      });

      el('repo-body').innerHTML = rows.join('');
      el('repo-count').textContent = String(filtered.length) + '/' + String(allRepos.length);
    }

    function renderRepoExpanded(repo) {
      var repoName = String(repo.name || '');
      var domId = repoDomId(repoName);
      var nodes = Array.isArray(repo.task_graph_nodes) ? repo.task_graph_nodes : [];
      var edges = Array.isArray(repo.task_graph_edges) ? repo.task_graph_edges : [];
      var inProgress = 0;
      var ready = 0;
      var blocked = 0;
      var aging = 0;
      var now = Date.now();
      nodes.forEach(function(nd) {
        var st = String(nd.status || '').toLowerCase();
        if (st === 'in-progress' || st === 'in_progress') inProgress++;
        else if (st === 'ready' || st === 'open') ready++;
        else if (st === 'blocked') blocked++;
        var created = nd.created || nd.started || '';
        if (created && st !== 'done') {
          var ms = new Date(created).getTime();
          if (!isNaN(ms) && (now - ms) > 3 * 86400000) aging++;
        }
      });

      var taskSummary = esc(String(inProgress)) + ' in progress, '
        + esc(String(ready)) + ' ready, '
        + esc(String(blocked)) + ' blocked, '
        + esc(String(aging)) + ' aging';

      var branch = esc(String(repo.git_branch || 'n/a'));
      var dirtyClean = repo.git_dirty ? 'dirty' : 'clean';
      var ahead = n(repo.ahead);
      var behind = n(repo.behind);
      var gitState = 'branch: <code>' + branch + '</code> ('
        + esc(dirtyClean)
        + (ahead ? ', +' + esc(String(ahead)) + ' ahead' : '')
        + (behind ? ', -' + esc(String(behind)) + ' behind' : '')
        + ')';

      var stalledText = '';
      if (repo.stalled) {
        var stallReasons = Array.isArray(repo.stall_reasons) ? repo.stall_reasons : [];
        stalledText = '<div style="margin-top:0.25rem;display:flex;align-items:center;gap:0.6rem">'
          + '<span style="color:var(--bad);font-weight:600">Stalled'
          + (stallReasons.length ? ': ' + stallReasons.map(function(r) { return esc(String(r)); }).join('; ') : '')
          + '</span>'
          + '<button class="start-btn" data-start-repo="' + escAttr(repoName) + '">Start Service</button>'
          + '</div>';
      } else if (!repo.service_running && repo.workgraph_exists) {
        stalledText = '<div style="margin-top:0.25rem;display:flex;align-items:center;gap:0.6rem">'
          + '<span style="color:var(--muted)">Service not running</span>'
          + '<button class="start-btn" data-start-repo="' + escAttr(repoName) + '">Start Service</button>'
          + '</div>';
      }

      return '<tr class="repo-expanded-row" id="repo-expanded-' + domId + '" data-repo-expanded="' + escAttr(repoName) + '">'
        + '<td colspan="8">'
        + '<div class="repo-expanded">'
        + '<div class="repo-expanded-meta">'
        + '<div><strong>Tasks:</strong> ' + taskSummary + '</div>'
        + '<div><strong>Graph:</strong> ' + esc(String(nodes.length)) + ' nodes / ' + esc(String(edges.length)) + ' edges</div>'
        + '<div><strong>Git:</strong> ' + gitState + '</div>'
        + '</div>'
        + stalledText
        + '</div>'
        + '</td>'
        + '</tr>';
    }

    function laneFor(node) {
      const status = String(node.status || '').toLowerCase();
      if (node.blocked) return 3;
      if (status === 'done') return 0;
      if (status === 'in-progress') return 1;
      if (status === 'open' || status === 'ready') return 2;
      return 3;
    }

    function colorFor(node) {
      const status = String(node.status || '').toLowerCase();
      if (node.blocked) return '#9c2525';
      if (status === 'done') return '#2f6e39';
      if (status === 'in-progress') return '#0f6f7c';
      var ageDays = Number(node.age_days || 0);
      if ((status === 'open' || status === 'ready') && ageDays >= 7) return '#8c2f2f';
      if ((status === 'open' || status === 'ready') && ageDays >= 3) return '#b85c1c';
      if (status === 'open' || status === 'ready') return '#a26c13';
      return '#5f6f66';
    }

    function detectCycleEdges(edges) {
      const out = new Set();
      const adj = new Map();
      (edges || []).forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (!s || !t) return;
        if (!adj.has(s)) adj.set(s, []);
        adj.get(s).push(t);
      });

      const visit = new Map();
      function dfs(node, stack) {
        visit.set(node, 1);
        const children = adj.get(node) || [];
        for (const child of children) {
          if (visit.get(child) === 1) {
            const start = stack.indexOf(child);
            if (start >= 0) {
              for (let i = start; i < stack.length - 1; i += 1) {
                out.add(`${stack[i]}->${stack[i + 1]}`);
              }
              out.add(`${stack[stack.length - 1]}->${child}`);
            }
            continue;
          }
          if (visit.get(child) === 2) continue;
          dfs(child, [...stack, child]);
        }
        visit.set(node, 2);
      }

      const keys = new Set();
      (edges || []).forEach((edge) => {
        keys.add(String(edge.source || ''));
        keys.add(String(edge.target || ''));
      });
      keys.forEach((key) => {
        if (!key) return;
        if (!visit.has(key)) dfs(key, [key]);
      });
      return out;
    }

    function normalizeGraph(repo) {
      const nodes = Array.isArray(repo.task_graph_nodes) ? repo.task_graph_nodes : [];
      const edges = Array.isArray(repo.task_graph_edges) ? repo.task_graph_edges : [];
      return { nodes, edges };
    }

    function buildAdjacency(edges) {
      const forward = new Map();
      const reverse = new Map();
      (edges || []).forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (!s || !t) return;
        if (!forward.has(s)) forward.set(s, []);
        if (!reverse.has(t)) reverse.set(t, []);
        forward.get(s).push(t);
        reverse.get(t).push(s);
      });
      return { forward, reverse };
    }

    function boundedReach(seed, map, maxDepth) {
      const seen = new Set();
      const queue = [{ id: seed, depth: 0 }];
      while (queue.length) {
        const row = queue.shift();
        const next = map.get(row.id) || [];
        next.forEach((id) => {
          if (seen.has(id)) return;
          seen.add(id);
          if (row.depth + 1 < maxDepth) {
            queue.push({ id, depth: row.depth + 1 });
          }
        });
      }
      return seen;
    }

    function chooseFocusSeed(nodes) {
      const ranked = (nodes || []).slice().sort((a, b) => {
        const laneDelta = laneFor(a) - laneFor(b);
        if (laneDelta !== 0) return laneDelta;
        const ageA = Number(a.age_days || 0);
        const ageB = Number(b.age_days || 0);
        if (ageB !== ageA) return ageB - ageA;
        return String(a.id || '').localeCompare(String(b.id || ''));
      });
      return ranked.length ? String(ranked[0].id || '') : '';
    }

    function subgraphForMode(model, mode, explicitNodeId) {
      const nodes = model.nodes || [];
      const edges = model.edges || [];
      const ids = new Set(nodes.map((node) => String(node.id || '')));
      const validSelected = explicitNodeId && ids.has(explicitNodeId) ? explicitNodeId : '';
      const seed = validSelected || chooseFocusSeed(nodes);
      if (!seed || mode === 'full') {
        return { nodes, edges, seed };
      }

      const { forward, reverse } = buildAdjacency(edges);
      let selectedIds = new Set([seed]);
      if (mode === 'focus') {
        const up = boundedReach(seed, reverse, 4);
        const down = boundedReach(seed, forward, 4);
        selectedIds = new Set([seed, ...up, ...down]);
        if (selectedIds.size <= 2 && nodes.length > selectedIds.size) {
          const ranked = nodes
            .slice()
            .sort((a, b) => (
              laneFor(a) - laneFor(b) ||
              Number(b.age_days || 0) - Number(a.age_days || 0) ||
              String(a.id || '').localeCompare(String(b.id || ''))
            ))
            .slice(0, Math.min(20, nodes.length));
          ranked.forEach((node) => selectedIds.add(String(node.id || '')));
        }
      } else {
        // active mode: prioritize in-progress/blocked/open and their immediate deps.
        selectedIds = new Set();
        nodes.forEach((node) => {
          const status = String(node.status || '').toLowerCase();
          if (status === 'in-progress' || status === 'open' || status === 'ready' || node.blocked) {
            selectedIds.add(String(node.id || ''));
          }
        });
        Array.from(selectedIds).forEach((id) => {
          (forward.get(id) || []).forEach((next) => selectedIds.add(next));
          (reverse.get(id) || []).forEach((prev) => selectedIds.add(prev));
        });
        if (!selectedIds.size) selectedIds.add(seed);
      }

      const limited = Array.from(selectedIds);
      if (limited.length > 90) {
        limited.sort((a, b) => a.localeCompare(b));
        selectedIds = new Set([seed, ...limited.slice(0, 89)]);
      }
      const subNodes = nodes.filter((node) => selectedIds.has(String(node.id || '')));
      const subEdges = edges.filter((edge) => selectedIds.has(String(edge.source || '')) && selectedIds.has(String(edge.target || '')));
      return { nodes: subNodes, edges: subEdges, seed };
    }

    function layoutGraph(model) {
      const nodes = model.nodes || [];
      const edges = model.edges || [];
      const nodeIds = new Set(nodes.map((node) => String(node.id || '')));
      const { forward, reverse } = buildAdjacency(edges);
      const indegree = new Map();
      nodes.forEach((node) => indegree.set(String(node.id || ''), 0));
      edges.forEach((edge) => {
        const t = String(edge.target || '');
        const s = String(edge.source || '');
        if (!nodeIds.has(s) || !nodeIds.has(t)) return;
        indegree.set(t, (indegree.get(t) || 0) + 1);
      });

      const queue = [];
      indegree.forEach((deg, id) => {
        if (deg === 0) queue.push(id);
      });
      const depth = new Map();
      nodes.forEach((node) => depth.set(String(node.id || ''), 0));

      while (queue.length) {
        const cur = queue.shift();
        const children = forward.get(cur) || [];
        children.forEach((child) => {
          if (!nodeIds.has(child)) return;
          const nextDepth = Math.max(depth.get(child) || 0, (depth.get(cur) || 0) + 1);
          depth.set(child, nextDepth);
          const nextDeg = (indegree.get(child) || 0) - 1;
          indegree.set(child, nextDeg);
          if (nextDeg === 0) queue.push(child);
        });
      }

      // Relax again so remaining cycle-connected nodes get a readable placement.
      for (let pass = 0; pass < nodes.length; pass += 1) {
        let changed = false;
        edges.forEach((edge) => {
          const s = String(edge.source || '');
          const t = String(edge.target || '');
          if (!nodeIds.has(s) || !nodeIds.has(t)) return;
          const candidate = (depth.get(s) || 0) + 1;
          if (candidate > (depth.get(t) || 0)) {
            depth.set(t, candidate);
            changed = true;
          }
        });
        if (!changed) break;
      }

      const byDepth = new Map();
      nodes.forEach((node) => {
        const id = String(node.id || '');
        const d = Math.max(0, Number(depth.get(id) || 0));
        if (!byDepth.has(d)) byDepth.set(d, []);
        byDepth.get(d).push(node);
      });
      const depthKeys = Array.from(byDepth.keys()).sort((a, b) => a - b);
      depthKeys.forEach((key) => {
        byDepth.get(key).sort((a, b) => {
          const rankDelta = laneFor(a) - laneFor(b);
          if (rankDelta !== 0) return rankDelta;
          const ageA = Number(a.age_days || 0);
          const ageB = Number(b.age_days || 0);
          if (ageB !== ageA) return ageB - ageA;
          return String(a.id || '').localeCompare(String(b.id || ''));
        });
      });

      const maxDepth = depthKeys.length ? Math.max(...depthKeys) : 0;
      const maxRows = depthKeys.length ? Math.max(...depthKeys.map((key) => byDepth.get(key).length)) : 1;
      const width = Math.max(1200, 260 + (maxDepth + 1) * 230);
      const height = Math.max(420, 130 + maxRows * 72);

      const pos = {};
      depthKeys.forEach((key) => {
        const list = byDepth.get(key) || [];
        list.forEach((node, idx) => {
          const id = String(node.id || '');
          pos[id] = {
            x: 120 + key * 230,
            y: 70 + idx * 72,
            node,
            depth: key,
            indegree: (reverse.get(id) || []).length,
            outdegree: (forward.get(id) || []).length,
          };
        });
      });
      return { nodes, edges, pos, width, height, maxDepth };
    }

    function traverseSelection(model, startId) {
      const { forward, reverse } = buildAdjacency(model.edges || []);
      function bfs(seed, map) {
        const seen = new Set();
        const queue = [seed];
        while (queue.length) {
          const cur = queue.shift();
          const next = map.get(cur) || [];
          next.forEach((item) => {
            if (seen.has(item)) return;
            seen.add(item);
            queue.push(item);
          });
        }
        return seen;
      }
      const ancestors = bfs(startId, reverse);
      const descendants = bfs(startId, forward);
      const pathNodes = new Set([startId, ...ancestors, ...descendants]);
      const pathEdges = new Set();
      (model.edges || []).forEach((edge) => {
        const s = String(edge.source || '');
        const t = String(edge.target || '');
        if (!s || !t) return;
        if (pathNodes.has(s) && pathNodes.has(t)) pathEdges.add(`${s}->${t}`);
      });
      return { ancestors, descendants, pathNodes, pathEdges };
    }

    function setGraphPathText(out, model, activeNodeId, traversal, cycleEdges, mode, seed, baseModel) {
      if ((model.edges || []).length === 0) {
        if (!activeNodeId) {
          out.textContent = `Mode: ${mode}. No dependency edges found for this repo yet (tasks may not define "after" links).`;
          return;
        }
      }
      const totalNodes = Number((baseModel && baseModel.nodes || []).length);
      const totalEdges = Number((baseModel && baseModel.edges || []).length);
      const scope = mode === 'full'
        ? `${model.nodes.length} nodes, ${model.edges.length} edges`
        : `${model.nodes.length}/${totalNodes} nodes, ${model.edges.length}/${totalEdges} edges`;
      if (!activeNodeId) {
        const loopCount = cycleEdges.size;
        out.textContent =
          `Mode: ${mode}. Scope: ${scope}. Focus seed: ${seed || 'none'}.\n` +
          (loopCount > 0
            ? `Detected ${loopCount} cycle edges. Select a node to inspect dependency chain.`
            : 'Select a node to inspect dependency chain.');
        return;
      }
      const node = (model.nodes || []).find((n2) => String(n2.id) === String(activeNodeId));
      const title = node ? String(node.label || activeNodeId) : activeNodeId;
      const up = Array.from(traversal.ancestors).sort();
      const down = Array.from(traversal.descendants).sort();
      const loopHits = [];
      cycleEdges.forEach((edge) => {
        const [s, t] = edge.split('->', 2);
        if (traversal.pathNodes.has(s) || traversal.pathNodes.has(t)) loopHits.push(edge);
      });
      out.textContent =
        `Mode: ${mode}. Scope: ${scope}.\n` +
        `Node: ${activeNodeId} (${title})\n` +
        `Upstream chain (${up.length}): ${up.slice(0, 12).join(', ') || 'none'}\n` +
        `Downstream chain (${down.length}): ${down.slice(0, 12).join(', ') || 'none'}\n` +
        `Cycle edges touching path: ${loopHits.length ? loopHits.slice(0, 12).join(', ') : 'none'}`;
    }

    function zoomGraph(multiplier) {
      graphView.scale = Math.min(3.6, Math.max(0.45, graphView.scale * multiplier));
      drawExpandedRepoGraph();
    }

    function resetGraphView() {
      resetGraphViewState();
      drawExpandedRepoGraph();
    }

    function render(data, source) {
      currentData = data;
      window.currentData = data;
      if (selectedRepo && !repoByName(selectedRepo)) selectedRepo = '';
      if (expandedRepo && !repoByName(expandedRepo)) expandedRepo = '';
      el('meta').textContent =
        'Generated: ' + (data.generated_at || 'n/a') + ' | repos: ' + (data.repo_count || 0) + ' | transport: ' + source;

      renderBriefing(data);
      renderRepoTable(data);
      drawRepoDependencyOverview(data);
      drawExpandedRepoGraph();
    }

    async function refreshHttp() {
      const res = await fetch('/api/status');
      const data = await res.json();
      render(data, 'http-poll');
    }

    function startPolling() {
      if (pollTimer) return;
      pollTimer = setInterval(() => refreshHttp().catch(() => {}), 10000);
    }

    function stopPolling() {
      if (!pollTimer) return;
      clearInterval(pollTimer);
      pollTimer = null;
    }

    function scheduleReconnect() {
      if (reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connectWebSocket();
      }, 2000);
    }

    function connectWebSocket() {
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${window.location.host}/ws/status`;
      try {
        ws = new WebSocket(url);
      } catch (_err) {
        startPolling();
        scheduleReconnect();
        return;
      }

      ws.onopen = () => stopPolling();
      ws.onmessage = (event) => {
        try {
          render(JSON.parse(event.data), 'websocket');
        } catch (_err) {}
      };
      ws.onerror = () => {
        try { ws.close(); } catch (_err) {}
      };
      ws.onclose = () => {
        startPolling();
        scheduleReconnect();
      };
    }

    document.addEventListener('click', (event) => {
      const target = event.target;
      const expander = target && target.closest ? target.closest('.briefing-expander') : null;
      if (expander) {
        var repoName = String(expander.getAttribute('data-repo') || '');
        var detailId = 'briefing-detail-' + repoName.replace(/[^a-zA-Z0-9_-]/g, '-');
        var detailEl = document.getElementById(detailId);
        if (detailEl) {
          detailEl.classList.toggle('open');
        }
        return;
      }
    });

    // Repo dependency graph zoom/pan state
    const repoDepView = { scale: 1, tx: 0, ty: 0, drag: false, dragStartX: 0, dragStartY: 0, dragBaseX: 0, dragBaseY: 0 };
    function applyRepoDepZoom() {
      const depSvg = el('repo-dep-graph');
      if (!depSvg) return;
      const baseW = Number(depSvg.dataset.baseWidth || 800);
      const baseH = Number(depSvg.dataset.baseHeight || 500);
      const w = baseW / repoDepView.scale;
      const h = baseH / repoDepView.scale;
      const ox = (baseW - w) / 2 - repoDepView.tx / repoDepView.scale;
      const oy = (baseH - h) / 2 - repoDepView.ty / repoDepView.scale;
      depSvg.setAttribute('viewBox', `${ox} ${oy} ${w} ${h}`);
    }
    el('dep-zoom-in').addEventListener('click', () => {
      repoDepView.scale = Math.min(4, repoDepView.scale * 1.25);
      applyRepoDepZoom();
    });
    el('dep-zoom-out').addEventListener('click', () => {
      repoDepView.scale = Math.max(0.3, repoDepView.scale / 1.25);
      applyRepoDepZoom();
    });
    el('dep-zoom-reset').addEventListener('click', () => {
      repoDepView.scale = 1; repoDepView.tx = 0; repoDepView.ty = 0;
      applyRepoDepZoom();
    });
    const repoDepSvg = el('repo-dep-graph');
    repoDepSvg.addEventListener('pointerdown', (event) => {
      const nodeEl = event.target && event.target.closest ? event.target.closest('[data-focus-repo]') : null;
      if (nodeEl) return; // Let click handler handle node clicks
      repoDepView.drag = true;
      repoDepView.dragStartX = event.clientX;
      repoDepView.dragStartY = event.clientY;
      repoDepView.dragBaseX = repoDepView.tx;
      repoDepView.dragBaseY = repoDepView.ty;
      repoDepSvg.classList.add('dragging');
      try { repoDepSvg.setPointerCapture(event.pointerId); } catch (_err) {}
    });
    repoDepSvg.addEventListener('pointermove', (event) => {
      if (!repoDepView.drag) return;
      repoDepView.tx = repoDepView.dragBaseX + (event.clientX - repoDepView.dragStartX);
      repoDepView.ty = repoDepView.dragBaseY + (event.clientY - repoDepView.dragStartY);
      applyRepoDepZoom();
    });
    function endRepoDepDrag(event) {
      if (!repoDepView.drag) return;
      repoDepView.drag = false;
      repoDepSvg.classList.remove('dragging');
      try { repoDepSvg.releasePointerCapture(event.pointerId); } catch (_err) {}
    }
    repoDepSvg.addEventListener('pointerup', endRepoDepDrag);
    repoDepSvg.addEventListener('pointercancel', endRepoDepDrag);
    repoDepSvg.addEventListener('click', (event) => {
      const nodeEl = event.target && event.target.closest ? event.target.closest('[data-focus-repo]') : null;
      if (!nodeEl) return;
      const name = String(nodeEl.getAttribute('data-focus-repo') || '');
      if (!name) return;
      selectRepo(name, { forceExpanded: true, scrollIntoView: true });
    });

    el('repo-search').addEventListener('input', function(e) {
      repoSearchText = String(e.target.value || '');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });
    el('repo-role-filter').addEventListener('change', function(e) {
      repoRoleFilter = String(e.target.value || 'all');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });
    el('repo-status-filter').addEventListener('change', function(e) {
      repoStatusFilter = String(e.target.value || 'all');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });
    el('repo-drift-filter').addEventListener('change', function(e) {
      repoDriftFilter = String(e.target.value || 'all');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });
    el('repo-health-filter').addEventListener('change', function(e) {
      repoHealthFilter = String(e.target.value || 'all');
      if (currentData) renderRepoTable(currentData);
      syncFiltersToUrl();
    });

    el('repo-table').querySelectorAll('th[data-sort]').forEach(function(th) {
      th.addEventListener('click', function() {
        var col = String(th.getAttribute('data-sort') || '');
        if (!col) return;
        if (repoSortCol === col) {
          repoSortAsc = !repoSortAsc;
        } else {
          repoSortCol = col;
          repoSortAsc = (col === 'name' || col === 'role' || col === 'activity');
        }
        if (currentData) renderRepoTable(currentData);
      });
    });

    el('repo-body').addEventListener('click', function(e) {
      var startBtn = e.target.closest('[data-start-repo]');
      if (startBtn) {
        e.stopPropagation();
        var repoName = String(startBtn.getAttribute('data-start-repo') || '');
        if (!repoName) return;
        startBtn.disabled = true;
        startBtn.textContent = 'Starting...';
        fetch('/api/repo/' + encodeURIComponent(repoName) + '/start', { method: 'POST' })
          .then(function(res) { return res.json(); })
          .then(function(data) {
            if (data.returncode === 0) {
              startBtn.textContent = 'Started';
              startBtn.style.background = 'var(--good)';
              startBtn.style.color = '#fff';
              startBtn.style.borderColor = 'var(--good)';
            } else {
              startBtn.textContent = 'Failed';
              startBtn.style.background = 'var(--bad)';
              startBtn.style.color = '#fff';
              startBtn.style.borderColor = 'var(--bad)';
              startBtn.disabled = false;
            }
          })
          .catch(function() {
            startBtn.textContent = 'Error';
            startBtn.disabled = false;
          });
        return;
      }
      var row = e.target.closest('.repo-row');
      if (!row) return;
      var name = String(row.getAttribute('data-repo-name') || '');
      if (!name) return;
      selectRepo(name, { toggleExpanded: true });
    });

    // Drawer graph controls
    el('drawer-graph-mode').addEventListener('change', function() {
      graphMode = String(el('drawer-graph-mode').value || 'full');
      selectedNodeId = '';
      resetGraphViewState();
      drawExpandedRepoGraph();
    });
    el('drawer-zoom-in').addEventListener('click', function() { zoomGraph(1.18); });
    el('drawer-zoom-out').addEventListener('click', function() { zoomGraph(1 / 1.18); });
    el('drawer-zoom-reset').addEventListener('click', function() { resetGraphView(); });

    // Drawer task graph pan/drag and node selection
    var drawerSvg = el('drawer-graph-svg');
    drawerSvg.addEventListener('pointerdown', function(event) {
      var nodeEl = event.target && event.target.closest ? event.target.closest('[data-node-id]') : null;
      if (nodeEl) {
        selectedNodeId = String(nodeEl.getAttribute('data-node-id') || '');
        drawExpandedRepoGraph();
        return;
      }
      graphView.drag = true;
      graphView.dragStartX = event.clientX;
      graphView.dragStartY = event.clientY;
      graphView.dragBaseX = graphView.tx;
      graphView.dragBaseY = graphView.ty;
      drawerSvg.classList.add('dragging');
      try { drawerSvg.setPointerCapture(event.pointerId); } catch (_err) {}
    });
    drawerSvg.addEventListener('pointermove', function(event) {
      if (!graphView.drag) return;
      graphView.tx = graphView.dragBaseX + (event.clientX - graphView.dragStartX);
      graphView.ty = graphView.dragBaseY + (event.clientY - graphView.dragStartY);
      drawExpandedRepoGraph();
    });
    function endGraphDrag(event) {
      if (!graphView.drag) return;
      graphView.drag = false;
      drawerSvg.classList.remove('dragging');
      try { drawerSvg.releasePointerCapture(event.pointerId); } catch (_err) {}
    }
    drawerSvg.addEventListener('pointerup', endGraphDrag);
    drawerSvg.addEventListener('pointercancel', endGraphDrag);

    loadFiltersFromUrl();
    refreshHttp().catch(() => {});
    startPolling();
    connectWebSocket();
  </script>
</body>
</html>
"""
