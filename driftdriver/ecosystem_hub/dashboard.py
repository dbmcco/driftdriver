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

    /* Zone 2: Attention Queue */
    .attention-queue {
      /* wrapper inherits .card */
    }
    .attention-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.88rem;
    }
    .attention-table th,
    .attention-table td {
      padding: 0.45rem 0.55rem;
      text-align: left;
      border-bottom: 1px solid var(--line);
    }
    .attention-table th {
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--muted);
      cursor: pointer;
      user-select: none;
    }
    .attention-table th:hover {
      color: var(--accent);
    }
    .severity-high { color: var(--bad); font-weight: 600; }
    .severity-medium { color: var(--warn); font-weight: 600; }
    .severity-low { color: var(--good); }
    .action-stub {
      font-size: 0.78rem;
      color: var(--muted);
      font-style: italic;
    }
    .attention-empty {
      color: var(--muted);
      font-size: 0.88rem;
      font-style: italic;
      margin: 0.5rem 0 0;
    }

    /* Zone 3+4: Split panel */
    .split-panel {
      display: flex;
      gap: 0.95rem;
      min-height: 500px;
    }
    .repo-panel {
      flex: 3;
    }
    .graphs-panel {
      flex: 2;
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
    .task-dag {
      margin-top: 0.35rem;
    }
    .task-dag-svg {
      width: 100%;
      max-height: 200px;
      display: block;
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
    .graph-controls button:hover {
      background: #f5efe2;
    }

    /* Chat panel stub */
    #chat-panel[hidden] {
      display: none;
    }

    /* Responsive */
    @media (max-width: 900px) {
      .split-panel {
        flex-direction: column;
      }
      .repo-panel,
      .graphs-panel {
        flex: none;
      }
      .graphs-panel {
        order: -1;
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
  <script>
    let ws = null;
    let pollTimer = null;
    let reconnectTimer = null;
    let currentData = null;
    let selectedRepo = '';

    let attentionSortCol = 'severity';
    let attentionSortAsc = false;

    let repoSearchText = '';
    let repoRoleFilter = 'all';
    let repoStatusFilter = 'all';
    let repoDriftFilter = 'all';
    let repoSortCol = 'name';
    let repoSortAsc = true;
    let expandedRepo = null;

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
    function repoByName(name) {
      return (currentData && currentData.repos || []).find((repo) => String(repo.name || '') === String(name || '')) || null;
    }

    function syncFiltersToUrl() {
      var params = new URLSearchParams();
      if (repoSearchText) params.set('q', repoSearchText);
      if (repoRoleFilter !== 'all') params.set('role', repoRoleFilter);
      if (repoStatusFilter !== 'all') params.set('status', repoStatusFilter);
      if (repoDriftFilter !== 'all') params.set('drift', repoDriftFilter);
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
      el('repo-search').value = repoSearchText;
      el('repo-role-filter').value = repoRoleFilter;
      el('repo-status-filter').value = repoStatusFilter;
      el('repo-drift-filter').value = repoDriftFilter;
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

      meta.textContent =
        `repo dependencies | repos=${n(summary.repo_count || nodes.length)} | links=${n(summary.edge_count || edges.length)} | isolated=${n(summary.isolated_repos || 0)}`;

      const topOut = Array.isArray(summary.top_outbound) && summary.top_outbound.length ? summary.top_outbound[0] : null;
      const topIn = Array.isArray(summary.top_inbound) && summary.top_inbound.length ? summary.top_inbound[0] : null;
      const outText = topOut && topOut.repo ? `${topOut.repo} (${n(topOut.weight)})` : 'n/a';
      const inText = topIn && topIn.repo ? `${topIn.repo} (${n(topIn.weight)})` : 'n/a';
      note.innerHTML =
        `Edge A -> B means repo A has dependency signals pointing to repo B. Pulsing repo nodes have in-progress tasks. Top outbound: <code>${esc(outText)}</code>. Top inbound: <code>${esc(inText)}</code>.`;
    }

    function drawTaskDag(repo) {
      var repoName = String(repo.name || '');
      var container = document.getElementById('task-dag-' + repoName);
      if (!container) return;
      var allNodes = Array.isArray(repo.task_graph_nodes) ? repo.task_graph_nodes : [];
      var allEdges = Array.isArray(repo.task_graph_edges) ? repo.task_graph_edges : [];
      if (!allNodes.length) return;

      // For large graphs, filter to non-done nodes to keep it readable
      var nodes, edges;
      if (allNodes.length > 60) {
        var activeIds = new Set();
        allNodes.forEach(function(nd) {
          var st = String(nd.status || '').toLowerCase();
          if (st !== 'done') activeIds.add(String(nd.id || ''));
        });
        // If all done, show last 20
        if (!activeIds.size) {
          nodes = allNodes.slice(-20);
          var showIds = new Set(nodes.map(function(nd) { return String(nd.id || ''); }));
          edges = allEdges.filter(function(e) {
            return showIds.has(String(e.from || e.source || '')) && showIds.has(String(e.to || e.target || ''));
          });
        } else {
          nodes = allNodes.filter(function(nd) { return activeIds.has(String(nd.id || '')); });
          edges = allEdges.filter(function(e) {
            return activeIds.has(String(e.from || e.source || '')) || activeIds.has(String(e.to || e.target || ''));
          });
          // Also include done nodes that are direct parents of active nodes
          var extraIds = new Set();
          edges.forEach(function(e) {
            var from = String(e.from || e.source || '');
            if (!activeIds.has(from)) extraIds.add(from);
          });
          if (extraIds.size) {
            allNodes.forEach(function(nd) {
              if (extraIds.has(String(nd.id || ''))) nodes.push(nd);
            });
          }
        }
        container.setAttribute('data-filtered', 'Showing ' + nodes.length + ' of ' + allNodes.length + ' tasks (non-done)');
      } else {
        nodes = allNodes;
        edges = allEdges;
      }

      // Status color mapping
      var statusColor = function(nd) {
        var st = String(nd.status || '').toLowerCase();
        if (st === 'done') return '#2f6e39';
        if (st === 'in-progress' || st === 'in_progress') return '#0f6f7c';
        if (st === 'blocked') return '#9c2525';
        return '#a26c13'; // open/ready/other
      };

      // Build adjacency and detect incoming edges
      var adj = {};
      var incoming = {};
      nodes.forEach(function(nd) {
        var id = String(nd.id || '');
        adj[id] = [];
        incoming[id] = 0;
      });
      var nodeIds = new Set(nodes.map(function(nd) { return String(nd.id || ''); }));
      edges.forEach(function(e) {
        var from = String(e.from || e.source || '');
        var to = String(e.to || e.target || '');
        if (!from || !to || !nodeIds.has(from) || !nodeIds.has(to)) return;
        if (!adj[from]) adj[from] = [];
        adj[from].push(to);
        if (incoming[to] == null) incoming[to] = 0;
        incoming[to]++;
      });

      // Find root nodes (no incoming edges)
      var roots = [];
      nodes.forEach(function(nd) {
        var id = String(nd.id || '');
        if ((incoming[id] || 0) === 0) roots.push(id);
      });
      if (!roots.length) {
        // All nodes have incoming; pick first as fallback
        roots = [String(nodes[0].id || '')];
      }

      // BFS to assign columns (layer = distance from root)
      var col = {};
      var visited = {};
      var queue = [];
      roots.forEach(function(id) {
        col[id] = 0;
        visited[id] = true;
        queue.push(id);
      });
      while (queue.length) {
        var cur = queue.shift();
        var children = adj[cur] || [];
        children.forEach(function(child) {
          var nextCol = (col[cur] || 0) + 1;
          if (!visited[child] || nextCol > (col[child] || 0)) {
            col[child] = nextCol;
          }
          if (!visited[child]) {
            visited[child] = true;
            queue.push(child);
          }
        });
      }
      // Assign column 0 to unvisited nodes
      nodes.forEach(function(nd) {
        var id = String(nd.id || '');
        if (col[id] == null) col[id] = 0;
      });

      // Group nodes by column
      var columns = {};
      var maxCol = 0;
      nodes.forEach(function(nd) {
        var id = String(nd.id || '');
        var c = col[id] || 0;
        if (c > maxCol) maxCol = c;
        if (!columns[c]) columns[c] = [];
        columns[c].push(nd);
      });

      // Layout constants
      var colSpacing = 120;
      var rowSpacing = 36;
      var nodeRadius = 7;
      var startX = 40;
      var startY = 28;

      // Calculate positions
      var pos = {};
      var maxRowCount = 0;
      for (var c = 0; c <= maxCol; c++) {
        var colNodes = columns[c] || [];
        if (colNodes.length > maxRowCount) maxRowCount = colNodes.length;
        colNodes.forEach(function(nd, idx) {
          var id = String(nd.id || '');
          pos[id] = {
            x: startX + c * colSpacing,
            y: startY + idx * rowSpacing,
            node: nd
          };
        });
      }

      var svgWidth = 560;
      var svgHeight = Math.max(60, startY + maxRowCount * rowSpacing + 16);

      // Detect back-edges: edges where target is in same or earlier column
      var backEdgeCount = 0;
      var edgeSvg = '';
      edges.forEach(function(e) {
        var from = String(e.from || e.source || '');
        var to = String(e.to || e.target || '');
        if (!pos[from] || !pos[to]) return;
        var isBackEdge = (col[to] || 0) <= (col[from] || 0);
        if (isBackEdge) backEdgeCount++;
        var a = pos[from];
        var b = pos[to];
        var stroke = isBackEdge ? '#9c2525' : '#b8b0a3';
        var dash = isBackEdge ? ' stroke-dasharray="4,3"' : '';
        edgeSvg += '<line x1="' + a.x + '" y1="' + a.y + '" x2="' + b.x + '" y2="' + b.y + '" stroke="' + stroke + '" stroke-width="1.2"' + dash + ' />';
      });

      // Render nodes
      var nodeSvg = '';
      nodes.forEach(function(nd) {
        var id = String(nd.id || '');
        if (!pos[id]) return;
        var p = pos[id];
        var fill = statusColor(nd);
        var title = String(nd.title || nd.id || '');
        var label = title.length > 20 ? title.slice(0, 19) + '\u2026' : title;
        nodeSvg += '<circle cx="' + p.x + '" cy="' + p.y + '" r="' + nodeRadius + '" fill="' + fill + '" stroke="#fff" stroke-width="1" />';
        nodeSvg += '<text x="' + (p.x + nodeRadius + 4) + '" y="' + (p.y + 3) + '" fill="#2e3d35" font-size="9">' + esc(label) + '</text>';
      });

      var html = '<svg class="task-dag-svg" viewBox="0 0 ' + svgWidth + ' ' + svgHeight + '" preserveAspectRatio="xMinYMin meet">'
        + '<rect x="0" y="0" width="' + svgWidth + '" height="' + svgHeight + '" fill="none" />'
        + edgeSvg + nodeSvg + '</svg>';

      if (backEdgeCount > 0) {
        html += '<div class="loop-indicator">' + esc(String(backEdgeCount)) + ' break-fix loop' + (backEdgeCount !== 1 ? 's' : '') + ' detected</div>';
      }

      var filterNote = container.getAttribute('data-filtered');
      if (filterNote) {
        html += '<div style="font-size:0.75rem;color:var(--muted);margin-top:0.25rem">' + esc(filterNote) + '</div>';
      }

      container.innerHTML = html;
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

    function renderAttentionQueue(data) {
      var attentionRepos = (data.overview && Array.isArray(data.overview.attention_repos))
        ? data.overview.attention_repos : [];
      var repos = Array.isArray(data.repos) ? data.repos : [];
      var items = [];
      var seenRepos = {};

      // 1. Attention repos
      attentionRepos.forEach(function(ar) {
        var repoName = String(ar.repo || '');
        seenRepos[repoName] = true;
        var score = n(ar.score);
        var severity = score >= 22 ? 'high' : (score >= 10 ? 'medium' : 'low');
        var severityNum = score >= 22 ? 3 : (score >= 10 ? 2 : 1);
        var reasons = Array.isArray(ar.reasons) ? ar.reasons : [];
        var issueText = reasons.length
          ? reasons.slice(0, 2).join('; ')
          : 'elevated attention score';
        items.push({
          repo: repoName,
          issue: issueText,
          severity: severity,
          severityNum: severityNum,
          ageNum: score,
          ageText: 'score ' + String(score),
          action: 'Review'
        });
      });

      // 2. Stalled repos not already in attention
      repos.forEach(function(r) {
        var repoName = String(r.name || '');
        if (seenRepos[repoName]) return;
        if (!r.stalled) return;
        seenRepos[repoName] = true;
        var stallReasons = Array.isArray(r.stall_reasons) ? r.stall_reasons : [];
        var issueText = stallReasons.length
          ? 'Stalled: ' + stallReasons.slice(0, 2).join('; ')
          : 'Stalled: no active execution';
        items.push({
          repo: repoName,
          issue: issueText,
          severity: 'high',
          severityNum: 3,
          ageNum: 99,
          ageText: 'stalled',
          action: 'Unblock'
        });
      });

      // 3. Aging in-progress tasks from repos not in attention
      var now = Date.now();
      repos.forEach(function(r) {
        var repoName = String(r.name || '');
        if (seenRepos[repoName]) return;
        var tasks = Array.isArray(r.in_progress) ? r.in_progress : [];
        tasks.forEach(function(task) {
          var created = task.created || task.started || '';
          if (!created) return;
          var createdMs = new Date(created).getTime();
          if (isNaN(createdMs)) return;
          var ageDays = Math.floor((now - createdMs) / 86400000);
          if (ageDays < 3) return;
          var severity = ageDays >= 7 ? 'high' : 'medium';
          var severityNum = ageDays >= 7 ? 3 : 2;
          var title = String(task.title || task.id || 'unknown task');
          items.push({
            repo: repoName,
            issue: title + ' (' + String(ageDays) + 'd old)',
            severity: severity,
            severityNum: severityNum,
            ageNum: ageDays,
            ageText: String(ageDays) + 'd',
            action: 'Check'
          });
        });
      });

      // Sort
      items.sort(function(a, b) {
        var col = attentionSortCol;
        var dir = attentionSortAsc ? 1 : -1;
        if (col === 'severity') {
          var d = a.severityNum - b.severityNum;
          return d !== 0 ? d * dir : String(a.repo || '').localeCompare(String(b.repo || ''));
        }
        if (col === 'age') {
          var d2 = a.ageNum - b.ageNum;
          return d2 !== 0 ? d2 * dir : String(a.repo || '').localeCompare(String(b.repo || ''));
        }
        if (col === 'repo') {
          return String(a.repo || '').localeCompare(String(b.repo || '')) * dir;
        }
        if (col === 'issue') {
          return String(a.issue || '').localeCompare(String(b.issue || '')) * dir;
        }
        return (b.severityNum - a.severityNum);
      });

      var shown = items.slice(0, 15);

      var table = el('attention-table');
      var body = el('attention-body');
      var countBadge = el('attention-count');
      var emptyEl = el('attention-empty');

      if (!shown.length) {
        table.style.display = 'none';
        emptyEl.style.display = '';
        countBadge.textContent = '0';
        return;
      }
      table.style.display = '';
      emptyEl.style.display = 'none';
      countBadge.textContent = String(shown.length);

      body.innerHTML = shown.map(function(item) {
        var sevClass = 'severity-' + item.severity;
        return '<tr>'
          + '<td><code>' + esc(item.repo) + '</code></td>'
          + '<td>' + esc(item.issue) + '</td>'
          + '<td><span class="' + sevClass + '">' + esc(item.severity) + '</span></td>'
          + '<td>' + esc(item.ageText) + '</td>'
          + '<td><span class="action-stub">' + esc(item.action) + '</span></td>'
          + '</tr>';
      }).join('');
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

        rows.push(
          '<tr class="repo-row" data-repo-name="' + escAttr(repoName) + '">'
          + '<td><strong>' + esc(repoName) + '</strong></td>'
          + '<td>' + esc(role || '\u2014') + '</td>'
          + '<td><span class="status-dot ' + status + '"></span></td>'
          + '<td>' + driftHtml + '</td>'
          + '<td>' + esc(String(tc.done)) + '/' + esc(String(tc.total)) + '</td>'
          + '<td>' + sparkSvg + '</td>'
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
      var nodes = Array.isArray(repo.task_graph_nodes) ? repo.task_graph_nodes : [];
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
        stalledText = '<div style="margin-top:0.25rem;color:var(--bad);font-weight:600">Stalled'
          + (stallReasons.length ? ': ' + stallReasons.map(function(r) { return esc(String(r)); }).join('; ') : '')
          + '</div>';
      }

      var dagDiv = '';
      if (nodes.length > 0 && nodes.length <= 200) {
        dagDiv = '<div class="task-dag" id="task-dag-' + escAttr(repoName) + '"></div>';
      }

      return '<tr class="repo-expanded-row" data-repo-expanded="' + escAttr(repoName) + '">'
        + '<td colspan="7">'
        + '<div class="repo-expanded">'
        + '<div><strong>Tasks:</strong> ' + taskSummary + '</div>'
        + '<div><strong>Git:</strong> ' + gitState + '</div>'
        + stalledText
        + dagDiv
        + '</div>'
        + '</td>'
        + '</tr>';
    }

    function render(data, source) {
      currentData = data;
      window.currentData = data;
      el('meta').textContent =
        'Generated: ' + (data.generated_at || 'n/a') + ' | repos: ' + (data.repo_count || 0) + ' | transport: ' + source;

      renderBriefing(data);
      renderAttentionQueue(data);
      renderRepoTable(data);
      drawRepoDependencyOverview(data);

      if (expandedRepo) {
        var repo = repoByName(expandedRepo);
        if (repo) drawTaskDag(repo);
      }
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
      const baseW = 800, baseH = 500;
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

    el('attention-table').querySelectorAll('th[data-sort]').forEach(function(th) {
      th.addEventListener('click', function() {
        var col = String(th.getAttribute('data-sort') || '');
        if (!col) return;
        if (attentionSortCol === col) {
          attentionSortAsc = !attentionSortAsc;
        } else {
          attentionSortCol = col;
          attentionSortAsc = false;
        }
        if (currentData) renderAttentionQueue(currentData);
      });
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
      var row = e.target.closest('.repo-row');
      if (!row) return;
      var name = String(row.getAttribute('data-repo-name') || '');
      if (!name) return;
      expandedRepo = (expandedRepo === name) ? null : name;
      if (currentData) {
        renderRepoTable(currentData);
        if (expandedRepo) {
          var repo = repoByName(expandedRepo);
          if (repo) drawTaskDag(repo);
        }
      }
    });

    loadFiltersFromUrl();
    refreshHttp().catch(() => {});
    startPolling();
    connectWebSocket();
  </script>
</body>
</html>
"""
