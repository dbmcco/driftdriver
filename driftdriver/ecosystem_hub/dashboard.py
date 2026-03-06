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
    main {
      padding: 1rem 1.2rem 2rem;
      display: grid;
      gap: 0.95rem;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 0.9rem;
      box-shadow: 0 6px 12px rgba(24, 34, 28, 0.06);
    }
    .span-all {
      grid-column: 1 / -1;
    }
    h2 {
      margin: 0 0 0.65rem;
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #30443b;
    }
    .narrative {
      line-height: 1.45;
      font-size: 0.95rem;
      margin: 0;
      color: #1f2f28;
    }
    .cards {
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.55rem 0.6rem;
      background: linear-gradient(180deg, rgba(255,255,255,0.7), rgba(246,241,232,0.7));
    }
    .card .k {
      color: var(--muted);
      font-size: 0.74rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .card .v {
      margin-top: 0.2rem;
      font-size: 1.05rem;
      font-weight: 650;
      font-family: var(--mono);
    }
    .card .sub {
      margin-top: 0.24rem;
      font-size: 0.74rem;
      color: var(--muted);
      line-height: 1.25;
    }
    .spark {
      display: block;
      width: 100%;
      height: 34px;
      margin-top: 0.34rem;
    }
    .trend-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 0.7rem;
      margin-top: 0.8rem;
    }
    .trend-panel {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.65rem 0.75rem;
      background: linear-gradient(180deg, rgba(255,255,255,0.74), rgba(247,243,235,0.92));
    }
    .trend-panel h3 {
      margin: 0 0 0.45rem;
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #30443b;
    }
    .trend-panel ul {
      margin: 0;
      padding-left: 1rem;
    }
    .trend-panel li {
      margin: 0.32rem 0;
      line-height: 1.35;
      color: #30443b;
      font-size: 0.83rem;
    }
    .attention-list, ul {
      margin: 0;
      padding-left: 1rem;
    }
    li {
      margin: 0.23rem 0;
      font-size: 0.9rem;
    }
    .repo-grid {
      display: grid;
      gap: 0.7rem;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    }
    .repo-toolbar {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-bottom: 0.55rem;
    }
    #repo-summary {
      margin: 0 0 0.5rem;
      color: #31433a;
      font-size: 0.86rem;
    }
    .repo-card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.65rem;
      background: rgba(255,255,255,0.6);
      cursor: pointer;
      transition: border-color 120ms ease, box-shadow 120ms ease;
    }
    @keyframes repoCardPulse {
      0% {
        border-color: #0f6f7c;
        box-shadow: 0 0 0 0 rgba(15, 111, 124, 0.42);
      }
      72% {
        border-color: #5e95a0;
        box-shadow: 0 0 0 8px rgba(15, 111, 124, 0);
      }
      100% {
        border-color: #0f6f7c;
        box-shadow: 0 0 0 0 rgba(15, 111, 124, 0);
      }
    }
    .repo-card.active-running {
      border-color: #0f6f7c;
      animation: repoCardPulse 1.95s ease-out infinite;
    }
    .repo-card:hover {
      border-color: #9cb7bc;
      box-shadow: 0 3px 8px rgba(30, 58, 64, 0.12);
    }
    .repo-card.active-running:hover {
      border-color: #0f6f7c;
      box-shadow: 0 0 0 0 rgba(15, 111, 124, 0.42), 0 3px 10px rgba(30, 58, 64, 0.15);
    }
    .repo-card.stalled {
      border-color: #d8b596;
      background: rgba(255, 250, 243, 0.9);
    }
    .repo-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.6rem;
      font-size: 0.92rem;
      margin-bottom: 0.35rem;
    }
    .repo-name { font-weight: 700; }
    .pill {
      font-family: var(--mono);
      font-size: 0.72rem;
      border-radius: 999px;
      padding: 0.1rem 0.48rem;
      border: 1px solid var(--line);
      background: #f8f4eb;
      color: #47544d;
    }
    .pill.bad { color: var(--bad); border-color: #d9bcbc; background: #fbeeee; }
    .pill.warn { color: var(--warn); border-color: #e0c8b5; background: #fff3e8; }
    .pill.good { color: var(--good); border-color: #bfd8c4; background: #e8f5ea; }
    .repo-meta {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 0.24rem 0.4rem;
      font-size: 0.8rem;
      color: #324139;
      margin-bottom: 0.4rem;
    }
    .repo-note {
      margin: 0;
      font-size: 0.84rem;
      color: #2d3a33;
      line-height: 1.35;
    }
    .repo-note.stall {
      margin-top: 0.35rem;
      color: #7a4322;
    }
    .repo-note.signal {
      margin-top: 0.32rem;
      color: #5f3e22;
      font-style: italic;
    }
    .repo-note.warn { color: var(--warn); }
    .repo-actions { margin-top: 0.45rem; }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .good { color: var(--good); }
    code {
      font-family: var(--mono);
      font-size: 0.82rem;
    }
    .graph-toolbar {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      flex-wrap: wrap;
      margin-bottom: 0.55rem;
    }
    .graph-toolbar button {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.2rem 0.48rem;
      background: #fff;
      cursor: pointer;
    }
    .graph-toolbar button:hover {
      background: #f5efe2;
    }
    select {
      font: inherit;
      padding: 0.25rem 0.38rem;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #fff;
    }
    .graph-wrap {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fffdfa;
      overflow: auto;
      height: 560px;
    }
    #graph {
      display: block;
      width: 100%;
      height: 100%;
      touch-action: none;
      cursor: grab;
    }
    #graph.dragging {
      cursor: grabbing;
    }
    @keyframes taskPulseHalo {
      0% {
        opacity: 0.9;
        transform: scale(0.82);
      }
      70% {
        opacity: 0;
        transform: scale(1.72);
      }
      100% {
        opacity: 0;
        transform: scale(1.8);
      }
    }
    @keyframes repoPulseHalo {
      0% {
        opacity: 0.88;
        transform: scale(0.86);
      }
      72% {
        opacity: 0;
        transform: scale(1.62);
      }
      100% {
        opacity: 0;
        transform: scale(1.66);
      }
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
    .graph-path {
      margin-top: 0.55rem;
      padding: 0.45rem 0.55rem;
      border: 1px solid var(--line);
      border-radius: 9px;
      background: #fcf8ef;
      color: #2b3c34;
      font-size: 0.82rem;
      line-height: 1.35;
      min-height: 2.4rem;
      white-space: pre-wrap;
    }
    .repo-dep-wrap {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fffdfa;
      padding: 0.45rem;
      margin-bottom: 0.55rem;
    }
    .repo-dep-meta {
      color: #4a5b53;
      font-size: 0.8rem;
      margin-bottom: 0.35rem;
    }
    #repo-dep-graph {
      width: 100%;
      height: 280px;
      border: 1px solid #e2dacb;
      border-radius: 8px;
      background: #fffcf8;
      display: block;
      overflow: visible;
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
    #repo-dep-note {
      margin-top: 0.35rem;
      line-height: 1.28;
    }
    .action-toolbar {
      display: flex;
      align-items: center;
      gap: 0.55rem;
      flex-wrap: wrap;
      margin-bottom: 0.55rem;
    }
    #action-summary {
      margin: 0 0 0.6rem;
      color: #31433a;
      font-size: 0.86rem;
    }
    .action-grid {
      display: grid;
      gap: 0.7rem;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
    }
    .action-panel {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 0.6rem;
      background: rgba(255,255,255,0.7);
      min-height: 220px;
    }
    .action-head {
      margin: 0 0 0.45rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.45rem;
    }
    .action-panel h3 {
      margin: 0;
      font-size: 0.79rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #385148;
    }
    .action-count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.5rem;
      padding: 0.02rem 0.3rem;
      border-radius: 999px;
      border: 1px solid #c8beae;
      background: #f4eee2;
      font-family: var(--mono);
      font-size: 0.72rem;
      color: #495b52;
    }
    .action-list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: grid;
      gap: 0.35rem;
    }
    .action-item {
      border: 1px solid #d9d0c1;
      border-left-width: 4px;
      border-radius: 8px;
      padding: 0.4rem 0.5rem;
      background: #fffdf8;
      font-size: 0.82rem;
      line-height: 1.3;
    }
    .action-item.sev-high { border-left-color: #9c2525; }
    .action-item.sev-med { border-left-color: #a26c13; }
    .action-item.sev-low { border-left-color: #2f6e39; }
    .action-title {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 0.4rem;
      color: #25352f;
      margin-bottom: 0.15rem;
    }
    .action-why {
      color: #52645b;
      font-size: 0.77rem;
      margin-bottom: 0.15rem;
    }
    .action-prompt {
      margin-top: 0.2rem;
      color: #3f5148;
      font-size: 0.76rem;
      line-height: 1.28;
      background: #f7f2e8;
      border: 1px solid #dfd5c4;
      border-radius: 7px;
      padding: 0.28rem 0.32rem;
      word-break: break-word;
    }
    .action-empty {
      color: #5f6f66;
      border-style: dashed;
      border-left-width: 1px;
    }
    .action-link {
      font: inherit;
      font-size: 0.76rem;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 0.12rem 0.35rem;
      margin-left: 0.35rem;
      cursor: pointer;
    }
    .action-link:hover {
      background: #eef7f8;
      border-color: #9fbec5;
    }
    .cmd {
      margin-top: 0.18rem;
      color: #60726a;
      font-size: 0.76rem;
      line-height: 1.25;
    }
    .graph-all-grid {
      padding: 0.5rem;
      display: grid;
      gap: 0.6rem;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
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
    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      .repo-meta { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Speedrift Ecosystem Hub</h1>
    <div class="meta" id="meta">Loading ecosystem state…</div>
  </header>
  <main>
    <section class="span-all">
      <h2>Narrated Overview</h2>
      <p class="narrative" id="narrative">Waiting for first snapshot.</p>
    </section>

    <section class="span-all">
      <h2>North Star Scorecard</h2>
      <p class="narrative" id="northstar-summary">Loading north-star effectiveness…</p>
      <div class="cards" id="northstar-cards"></div>
      <div class="trend-grid">
        <article class="trend-panel">
          <h3>Trend Review</h3>
          <p class="narrative" id="northstar-trend-summary">Loading effectiveness history…</p>
        </article>
        <article class="trend-panel">
          <h3>Window Deltas</h3>
          <ul id="northstar-window-deltas"></ul>
        </article>
        <article class="trend-panel">
          <h3>Target Gaps</h3>
          <ul id="northstar-target-gaps"></ul>
        </article>
        <article class="trend-panel">
          <h3>Weekly Rollups</h3>
          <ul id="northstar-weekly-rollups"></ul>
        </article>
        <article class="trend-panel">
          <h3>Top Regressions</h3>
          <ul id="northstar-regressions"></ul>
        </article>
        <article class="trend-panel">
          <h3>Top Improvements</h3>
          <ul id="northstar-improvements"></ul>
        </article>
      </div>
    </section>

    <section class="span-all">
      <h2>Operational Overview</h2>
      <div class="cards" id="overview-cards"></div>
    </section>

    <section class="span-all">
      <h2>By Repo</h2>
      <p class="narrative" id="repo-summary">Loading repos…</p>
      <div class="repo-toolbar">
        <label for="repo-sort">Sort:</label>
        <select id="repo-sort">
          <option value="priority" selected>priority</option>
          <option value="dirty">dirty first</option>
          <option value="blocked">blocked first</option>
          <option value="behind">behind first</option>
          <option value="name">name</option>
        </select>
        <label for="repo-health-filter">Health:</label>
        <select id="repo-health-filter">
          <option value="all" selected>all</option>
          <option value="risk">risk</option>
          <option value="watch">watch</option>
          <option value="healthy">healthy</option>
        </select>
        <label for="repo-dirty-filter">Dirty:</label>
        <select id="repo-dirty-filter">
          <option value="all" selected>all</option>
          <option value="dirty">dirty only</option>
          <option value="clean">clean only</option>
        </select>
        <label for="repo-service-filter">Service:</label>
        <select id="repo-service-filter">
          <option value="all" selected>all</option>
          <option value="stopped">stopped</option>
          <option value="running">running</option>
        </select>
      </div>
      <div class="repo-grid" id="repo-grid"></div>
    </section>

    <section class="span-all" id="graph-section">
      <h2>Dependency Graph</h2>
      <div class="graph-toolbar">
        <label for="graph-repo">Repo:</label>
        <select id="graph-repo"></select>
        <label for="graph-mode">Mode:</label>
        <select id="graph-mode">
          <option value="focus">focus chain</option>
          <option value="active" selected>active + blocked</option>
          <option value="full">full graph</option>
        </select>
        <button id="graph-zoom-out" type="button">-</button>
        <button id="graph-zoom-in" type="button">+</button>
        <button id="graph-zoom-reset" type="button">reset</button>
        <span class="meta" id="graph-meta"></span>
      </div>
      <div class="repo-dep-wrap">
        <div class="repo-dep-meta" id="repo-dep-meta">Loading repo dependency overview…</div>
        <svg id="repo-dep-graph" viewBox="0 0 1200 280" preserveAspectRatio="xMidYMid meet"></svg>
        <div class="cmd" id="repo-dep-note">Edge A -> B means repo A has dependency signals pointing to repo B. Pulsing repo nodes have in-progress tasks. Click a repo to focus its task graph.</div>
      </div>
      <div class="graph-wrap">
        <svg id="graph" viewBox="0 0 1200 340" preserveAspectRatio="xMidYMin meet"></svg>
        <div id="graph-all" class="graph-all-grid" style="display:none;"></div>
      </div>
      <div class="graph-legend">
        <span><span class="dot" style="background:#2f6e39"></span>Done</span>
        <span><span class="dot" style="background:#0f6f7c"></span>In progress</span>
        <span><span class="dot" style="background:#a26c13"></span>Open/Ready</span>
        <span><span class="dot" style="background:#9c2525"></span>Blocked</span>
        <span><span class="dot" style="background:#8c2f2f"></span>Cycle edge</span>
        <span>Pulsing node = currently active work</span>
      </div>
      <div class="graph-path" id="graph-path">Select a node to inspect dependency chain.</div>
    </section>

    <section class="span-all">
      <h2>Action Center</h2>
      <p class="narrative" id="action-summary">Loading prioritized actions…</p>
      <div class="action-toolbar">
        <label for="action-repo-filter">Repo filter:</label>
        <select id="action-repo-filter">
          <option value="__all__">all repos</option>
        </select>
        <label for="action-sort">Sort:</label>
        <select id="action-sort">
          <option value="priority" selected>priority</option>
          <option value="dirtiness">dirty first</option>
          <option value="age">age</option>
          <option value="repo">repo</option>
        </select>
        <label for="action-priority-filter">Priority:</label>
        <select id="action-priority-filter">
          <option value="all" selected>all</option>
          <option value="high">high only</option>
          <option value="med">medium + high</option>
        </select>
        <label for="action-dirty-filter">Repo dirty:</label>
        <select id="action-dirty-filter">
          <option value="all" selected>all</option>
          <option value="dirty">dirty only</option>
          <option value="clean">clean only</option>
        </select>
      </div>
      <div class="action-grid">
        <article class="action-panel">
          <div class="action-head">
            <h3>Attention Queue</h3>
            <span class="action-count" id="attention-count">0</span>
          </div>
          <ul class="action-list" id="attention"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Aging, Gaps, Dependencies</h3>
            <span class="action-count" id="aging-count">0</span>
          </div>
          <ul class="action-list" id="aging"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Upstream Candidates</h3>
            <span class="action-count" id="upstream-count">0</span>
          </div>
          <ul class="action-list" id="upstream"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Planned Next Work</h3>
            <span class="action-count" id="next-count">0</span>
          </div>
          <ul class="action-list" id="next"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Security Reviews</h3>
            <span class="action-count" id="security-count">0</span>
          </div>
          <ul class="action-list" id="security"></ul>
        </article>
        <article class="action-panel">
          <div class="action-head">
            <h3>Quality Reviews</h3>
            <span class="action-count" id="quality-count">0</span>
          </div>
          <ul class="action-list" id="quality"></ul>
        </article>
      </div>
    </section>

    <section class="span-all">
      <h2>Updates</h2>
      <div id="updates"></div>
    </section>
  </main>
  <script>
    let ws = null;
    let pollTimer = null;
    let reconnectTimer = null;
    let selectedRepo = '';
    let graphMode = 'active';
    let currentData = null;
    let selectedNodeId = '';
    let actionRepoFilter = '__all__';
    let actionSortMode = 'priority';
    let actionPriorityFilter = 'all';
    let actionDirtyFilter = 'all';
    let repoSortMode = 'priority';
    let repoHealthFilter = 'all';
    let repoDirtyFilter = 'all';
    let repoServiceFilter = 'all';
    let graphModel = { repo: '', nodes: [], edges: [], pos: {} };
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

    function el(id) { return document.getElementById(id); }
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
    function repoPath(name) {
      const repo = repoByName(name);
      return repo ? String(repo.path || '') : '';
    }
    function focusRepoInGraph(name, scrollToGraph = false) {
      const value = String(name || '');
      const select = el('graph-repo');
      const exists = Array.from(select.options).some((opt) => String(opt.value) === value);
      if (!exists) return;
      selectedRepo = value;
      selectedNodeId = '';
      select.value = value;
      if (currentData) drawGraph(currentData);
      if (scrollToGraph) {
        el('graph-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
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

    function repoPriorityScore(repo) {
      const north = repo.northstar || {};
      const northPriority = Number(north.priority_score || 0);
      const errorWeight = n((repo.errors || []).length) * 10;
      const missingWeight = n(repo.missing_dependencies) * 6;
      const blockedWeight = n(repo.blocked_open) * 4;
      const staleWeight = n((repo.stale_open || []).length) * 2 + n((repo.stale_in_progress || []).length) * 3;
      const serviceWeight = repo.workgraph_exists && !repo.service_running ? 8 : 0;
      const stalledWeight = repo.stalled ? 10 : 0;
      const behindWeight = Math.min(10, n(repo.behind));
      const dirtyWeight = repo.git_dirty ? 2 : 0;
      const sec = repo.security || {};
      const qa = repo.quality || {};
      const securityWeight = (n(sec.critical) * 14) + (n(sec.high) * 7) + (n(sec.medium) * 3);
      const qualityWeight = (n(qa.critical) * 10) + (n(qa.high) * 6) + Math.max(0, Math.floor((80 - n(qa.quality_score || 100)) / 2));
      return Math.max(
        northPriority,
        errorWeight + missingWeight + blockedWeight + staleWeight + serviceWeight + stalledWeight + behindWeight + dirtyWeight + securityWeight + qualityWeight,
      );
    }

    function riskWatchSentence(repo, label) {
      if (label !== 'risk' && label !== 'watch') return '';
      const north = repo.northstar || {};
      if (north.reason) {
        const intro = label === 'risk' ? 'At risk because' : 'Watch because';
        return `${intro} ${String(north.reason)}.`;
      }
      const reasons = [];
      if ((repo.errors || []).length) reasons.push(`errors=${(repo.errors || []).slice(0, 2).join(',')}`);
      if (repo.stalled) {
        const stallTop = Array.isArray(repo.stall_reasons) && repo.stall_reasons.length ? String(repo.stall_reasons[0]) : 'no active execution';
        reasons.push(`stalled (${stallTop})`);
      }
      if (n(repo.missing_dependencies) > 0) reasons.push(`${n(repo.missing_dependencies)} missing dependencies`);
      if (n(repo.blocked_open) > 0) reasons.push(`${n(repo.blocked_open)} blocked open tasks`);
      if (n((repo.stale_open || []).length) > 0) reasons.push(`${n((repo.stale_open || []).length)} aging open`);
      if (n((repo.stale_in_progress || []).length) > 0) reasons.push(`${n((repo.stale_in_progress || []).length)} aging active`);
      if (repo.workgraph_exists && !repo.service_running) reasons.push('workgraph service stopped');
      if (n(repo.behind) > 0) reasons.push(`behind upstream by ${n(repo.behind)}`);
      if (repo.git_dirty) reasons.push('dirty working tree');
      const sec = repo.security || {};
      const qa = repo.quality || {};
      if (n(sec.critical) > 0) reasons.push(`security critical=${n(sec.critical)}`);
      if (n(sec.high) > 0) reasons.push(`security high=${n(sec.high)}`);
      if (n(qa.critical) > 0) reasons.push(`quality critical=${n(qa.critical)}`);
      if (n(qa.high) > 0) reasons.push(`quality high=${n(qa.high)}`);
      if (n(qa.quality_score || 100) < 90) reasons.push(`quality score=${n(qa.quality_score || 100)}`);
      if (!reasons.length) return '';
      const intro = label === 'risk' ? 'At risk because' : 'Watch because';
      return `${intro} ${reasons.slice(0, 3).join('; ')}.`;
    }

    function repoDirtyAllowed(repo) {
      if (repoDirtyFilter === 'dirty') return !!repo.git_dirty;
      if (repoDirtyFilter === 'clean') return !repo.git_dirty;
      return true;
    }

    function repoHealthAllowed(repo) {
      if (repoHealthFilter === 'all') return true;
      const [label] = qualityPill(repo);
      return String(label || '') === repoHealthFilter;
    }

    function repoServiceAllowed(repo) {
      if (repoServiceFilter === 'all') return true;
      const state = repo.service_running ? 'running' : (repo.workgraph_exists ? 'stopped' : 'none');
      if (repoServiceFilter === 'stopped') return state === 'stopped';
      if (repoServiceFilter === 'running') return state === 'running';
      return true;
    }

    function compareRepos(a, b) {
      if (repoSortMode === 'name') {
        return String(a.name || '').localeCompare(String(b.name || ''));
      }
      if (repoSortMode === 'dirty') {
        return Number(!!b.git_dirty) - Number(!!a.git_dirty) ||
          repoPriorityScore(b) - repoPriorityScore(a) ||
          String(a.name || '').localeCompare(String(b.name || ''));
      }
      if (repoSortMode === 'blocked') {
        return n(b.blocked_open) - n(a.blocked_open) ||
          n(b.missing_dependencies) - n(a.missing_dependencies) ||
          repoPriorityScore(b) - repoPriorityScore(a) ||
          String(a.name || '').localeCompare(String(b.name || ''));
      }
      if (repoSortMode === 'behind') {
        return n(b.behind) - n(a.behind) ||
          repoPriorityScore(b) - repoPriorityScore(a) ||
          String(a.name || '').localeCompare(String(b.name || ''));
      }
      return repoPriorityScore(b) - repoPriorityScore(a) || String(a.name || '').localeCompare(String(b.name || ''));
    }

    function refreshRepoSummary(total, shown) {
      const detail = `sort=${repoSortMode}, health=${repoHealthFilter}, dirty=${repoDirtyFilter}, service=${repoServiceFilter}`;
      el('repo-summary').textContent = `Showing ${shown} of ${total} repos (${detail}).`;
    }

    function refreshActionRepoFilter(data) {
      const select = el('action-repo-filter');
      if (!select) return;
      const repos = (data.repos || [])
        .map((repo) => String(repo.name || ''))
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b));
      const existing = new Set(Array.from(select.options).map((opt) => String(opt.value || '')));
      const expected = ['__all__', ...repos];
      const needsReset = expected.length !== existing.size || expected.some((name) => !existing.has(name));
      if (needsReset) {
        select.innerHTML = ['<option value="__all__">all repos</option>', ...repos.map((name) => `<option value="${escAttr(name)}">${esc(name)}</option>`)].join('');
      }
      if (actionRepoFilter !== '__all__' && !repos.includes(actionRepoFilter)) {
        actionRepoFilter = '__all__';
      }
      select.value = actionRepoFilter;
    }

    function actionRepoAllowed(repoName) {
      const value = String(repoName || '');
      return actionRepoFilter === '__all__' || value === actionRepoFilter;
    }

    function actionRepoDirtyAllowed(repoName) {
      const repo = repoByName(repoName) || {};
      if (actionDirtyFilter === 'dirty') return !!repo.git_dirty;
      if (actionDirtyFilter === 'clean') return !repo.git_dirty;
      return true;
    }

    function actionPriorityAllowed(severity) {
      const level = Number(severity || 0);
      if (actionPriorityFilter === 'high') return level >= 3;
      if (actionPriorityFilter === 'med') return level >= 2;
      return true;
    }

    function actionRowAllowed(row) {
      const repoName = String(row.repo || '');
      return actionRepoAllowed(repoName) &&
        actionRepoDirtyAllowed(repoName) &&
        actionPriorityAllowed(row.severity);
    }

    function compareActionRows(a, b) {
      if (actionSortMode === 'repo') {
        return String(a.repo || '').localeCompare(String(b.repo || '')) ||
          Number(b.priority || 0) - Number(a.priority || 0);
      }
      if (actionSortMode === 'age') {
        return Number(b.age_days || 0) - Number(a.age_days || 0) ||
          Number(b.severity || 0) - Number(a.severity || 0) ||
          String(a.repo || '').localeCompare(String(b.repo || ''));
      }
      if (actionSortMode === 'dirtiness') {
        const dirtyDelta = Number((repoByName(b.repo) || {}).git_dirty) -
          Number((repoByName(a.repo) || {}).git_dirty);
        if (dirtyDelta !== 0) return dirtyDelta;
      }
      return Number(b.severity || 0) - Number(a.severity || 0) ||
        Number(b.priority || 0) - Number(a.priority || 0) ||
        String(a.repo || '').localeCompare(String(b.repo || ''));
    }

    function buildAgentPrompt(kind, payload) {
      const repoName = String(payload.repo || '');
      if (kind === 'attention') {
        const reasons = String(payload.reasons || '').trim();
        return `In repo ${repoName}, review attention signals (${reasons || 'no reasons supplied'}). Decide the highest-priority blocker, update Workgraph task/dependency state, and return the next concrete execution step with follow-up tasks.`;
      }
      if (kind === 'aging') {
        const taskId = String(payload.task_id || '');
        const title = String(payload.title || '');
        const label = String(payload.label || '');
        const ageDays = Number(payload.age_days || 0);
        const ageText = ageDays > 0 ? ` (age ${ageDays}d)` : '';
        if (label === 'stalled repo') {
          return `In repo ${repoName}, diagnose and unblock stalled execution (${title || 'no reason supplied'}). Start one ready task or create missing dependency tasks, update Workgraph statuses/dependencies, and report the exact unblock step taken.`;
        }
        return `In repo ${repoName}, resolve ${label} for task ${taskId || 'unknown'} ${title}${ageText}. Decide execute vs unblock vs close, fix dependency state in Workgraph, and summarize what changed.`;
      }
      if (kind === 'next') {
        const taskId = String(payload.task_id || '');
        const title = String(payload.title || '');
        const status = String(payload.status || 'open');
        return `In repo ${repoName}, take task ${taskId} (${title}) currently ${status}. Execute one meaningful step, keep Workgraph dependencies/status accurate, and provide a concise progress update plus any follow-up tasks.`;
      }
      if (kind === 'upstream') {
        const category = String(payload.category || 'candidate');
        const ahead = Number(payload.ahead || 0);
        const files = Number(payload.file_count || 0);
        return `In repo ${repoName}, prepare an upstream contribution plan for ${category} changes (${files} files, ahead ${ahead}). Propose smallest safe PR scope, draft title/body, and call out splits or risks before opening a PR.`;
      }
      if (kind === 'security') {
        const category = String(payload.category || 'security-finding');
        const severity = String(payload.severity || 'medium');
        const evidence = String(payload.evidence || '');
        return `In repo ${repoName}, triage security finding (${severity}/${category}). Evidence: ${evidence}. Identify root cause, pick smallest safe remediation, define verification, and update Workgraph with exact review tasks/dependencies.`;
      }
      if (kind === 'quality') {
        const category = String(payload.category || 'quality-finding');
        const severity = String(payload.severity || 'medium');
        const evidence = String(payload.evidence || '');
        return `In repo ${repoName}, triage quality finding (${severity}/${category}). Evidence: ${evidence}. Preserve active work, define minimal corrective scope, verification plan, and exact Workgraph updates.`;
      }
      return `In repo ${repoName}, determine the highest-priority next action, update Workgraph state, and provide a concise execution plan.`;
    }

    function renderActionItemHtml(item) {
      const repoName = String(item.repo || '');
      const prompt = String(item.prompt || '');
      const why = String(item.why || '');
      const title = String(item.title || '');
      const focusButton = `<button class="action-link" data-focus-repo="${escAttr(repoName)}">focus graph</button>`;
      const copyButton = prompt ? `<button class="action-link" data-copy-prompt="${escAttr(prompt)}">copy prompt</button>` : '';
      return `
        <li class="action-item ${actionSeverityClass(item.severity)}">
          <div class="action-title">
            <span>${title}</span>
            <span>${focusButton}${copyButton}</span>
          </div>
          <div class="action-why">${why}</div>
          ${prompt ? `<div class="action-prompt"><strong>Prompt:</strong> ${esc(prompt)}</div>` : ''}
        </li>
      `;
    }

    function actionSeverityClass(level) {
      const value = Number(level || 0);
      if (value >= 3) return 'sev-high';
      if (value >= 2) return 'sev-med';
      return 'sev-low';
    }

    function setActionCount(id, count) {
      const target = el(id);
      if (target) target.textContent = String(Math.max(0, Number(count || 0)));
    }

    function renderActionSummary(counts) {
      const total = n(counts.attention) + n(counts.aging) + n(counts.upstream) + n(counts.next) + n(counts.security) + n(counts.quality);
      const repoText = actionRepoFilter === '__all__' ? 'all repos' : actionRepoFilter;
      const text =
        `Showing ${total} actionable items for ${repoText} (sort=${actionSortMode}, priority=${actionPriorityFilter}, dirty=${actionDirtyFilter}). ` +
        `Attention=${n(counts.attention)}, Aging/Gaps=${n(counts.aging)}, Upstream=${n(counts.upstream)}, Next Work=${n(counts.next)}, Security=${n(counts.security)}, Quality=${n(counts.quality)}.`;
      el('action-summary').textContent = text;
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
        svg.setAttribute('viewBox', '0 0 1200 280');
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
      const width = Math.max(1000, 260 + rankedNodes.length * 54);
      const height = 320;
      const centerX = width / 2;
      const centerY = height / 2;
      const radius = rankedNodes.length <= 1 ? 0 : Math.max(78, Math.min(width, height) * 0.34);
      const pos = {};
      rankedNodes.forEach((node, idx) => {
        const id = String(node.id || '');
        if (!id) return;
        const theta = (Math.PI * 2 * idx) / Math.max(1, rankedNodes.length);
        pos[id] = {
          x: centerX + radius * Math.cos(theta),
          y: centerY + radius * Math.sin(theta),
          node,
        };
      });

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
          const cx = mx + (centerX - mx) * 0.35;
          const cy = my + (centerY - my) * 0.35;
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

    function renderNorthstar(data) {
      const ns = data.northstardrift || {};
      const summary = ns.summary || {};
      const axes = ns.axes || {};
      const targets = ns.targets || {};
      const historyBlock = ns.history || {};
      const history = Array.isArray(historyBlock.points) ? historyBlock.points : [];
      const weekly = Array.isArray(historyBlock.weekly_points) ? historyBlock.weekly_points : [];
      const windows = (historyBlock.windows && typeof historyBlock.windows === 'object') ? historyBlock.windows : {};
      const historySummary = (historyBlock.summary && typeof historyBlock.summary === 'object') ? historyBlock.summary : {};
      const targetSummary = (targets.summary && typeof targets.summary === 'object') ? targets.summary : {};
      const scoreText = (value) => {
        if (value == null || value === '') return 'n/a';
        const num = Number(value);
        return Number.isFinite(num) ? num.toFixed(1) : String(value);
      };
      const signedScoreText = (value) => {
        if (value == null || value === '') return 'n/a';
        const num = Number(value);
        if (!Number.isFinite(num)) return String(value);
        return `${num >= 0 ? '+' : ''}${num.toFixed(1)}`;
      };
      const targetFor = (key) => {
        if (key === 'overall') return targets.overall || {};
        return ((targets.axes || {})[key]) || {};
      };
      const seriesFor = (key) => history
        .map((point) => {
          if (key === 'overall') return Number(point.overall_score || 0);
          return Number((((point.axes || {})[key]) || {}).score || 0);
        })
        .filter((value) => Number.isFinite(value));
      const sparkline = (values, color) => {
        if (!values.length) return '';
        const width = 120;
        const height = 30;
        const min = Math.min(...values);
        const max = Math.max(...values);
        const span = Math.max(1, max - min);
        const pts = values.map((value, idx) => {
          const x = values.length <= 1 ? 0 : (idx / (values.length - 1)) * width;
          const y = height - (((value - min) / span) * (height - 4)) - 2;
          return `${x.toFixed(1)},${y.toFixed(1)}`;
        }).join(' ');
        return `<svg class="spark" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="2.2" points="${pts}" /></svg>`;
      };
      const cardSub = (key, axis) => {
        const target = targetFor(key);
        const bits = [`${axis.tier || 'n/a'}`, `${axis.trend || 'flat'}`];
        if (target.target != null) {
          bits.push(`target ${scoreText(target.target)}`);
        }
        if (target.gap != null) {
          bits.push(`${target.status || 'gap'} ${signedScoreText(target.gap)}`);
        }
        return bits.join(' | ');
      };
      const cards = [
        ['Dark Factory', summary.overall_score, cardSub('overall', { tier: summary.overall_tier, trend: summary.overall_trend }), sparkline(seriesFor('overall'), '#0f6f7c')],
        ['Continuity', (axes.continuity || {}).score, cardSub('continuity', axes.continuity || {}), sparkline(seriesFor('continuity'), '#2f6e39')],
        ['Autonomy', (axes.autonomy || {}).score, cardSub('autonomy', axes.autonomy || {}), sparkline(seriesFor('autonomy'), '#0f6f7c')],
        ['Quality', (axes.quality || {}).score, cardSub('quality', axes.quality || {}), sparkline(seriesFor('quality'), '#934e1c')],
        ['Coordination', (axes.coordination || {}).score, cardSub('coordination', axes.coordination || {}), sparkline(seriesFor('coordination'), '#7b5a1c')],
        ['Self Improve', (axes.self_improvement || {}).score, cardSub('self_improvement', axes.self_improvement || {}), sparkline(seriesFor('self_improvement'), '#6e4d8f')],
      ];
      el('northstar-summary').textContent = summary.narrative || 'No north-star narrative generated yet.';
      el('northstar-cards').innerHTML = cards
        .map(([k, v, sub, svg]) => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(scoreText(v))}</div><div class="sub">${esc(sub || '')}</div>${svg || ''}</div>`)
        .join('');
      const pointsCount = Number(historySummary.count || history.length || 0);
      const taskEmit = ns.task_emit || {};
      const calibration = ns.calibration || {};
      el('northstar-trend-summary').textContent =
        `History recent=${pointsCount}, daily=${n(historySummary.daily_count)}, weekly=${n(historySummary.weekly_count)}. Participating repos=${n((ns.counts || {}).participating_repos)}. Latent repos=${n((ns.counts || {}).latent_repos)}. Missing repo North Stars=${n((ns.counts || {}).repos_missing_north_star)}. Targets met=${n(targetSummary.met)} watch=${n(targetSummary.watch_gap)} critical=${n(targetSummary.critical_gap)}. Review tasks created=${n(taskEmit.created)} existing=${n(taskEmit.existing)} skipped=${n(taskEmit.skipped)}. Dirty policy=${esc(String((ns.config || {}).dirty_repo_review_task_mode || 'n/a'))}. Calibration=${Array.isArray(calibration.notes) ? calibration.notes.join(' | ') : 'n/a'}.`;
      const windowRows = Object.values(windows || {});
      el('northstar-window-deltas').innerHTML = windowRows.length
        ? windowRows
          .map((row) => `<li><strong>${esc(String(row.label || 'window'))}</strong>: ${esc(String(row.trend || 'flat'))} ${esc(signedScoreText(row.delta))} from ${esc(scoreText(row.baseline_score))} to ${esc(scoreText(row.latest_score))} (${esc(String(row.coverage || 'partial'))}, ${esc(String(row.point_count || 0))} pts)</li>`)
          .join('')
        : '<li>No window deltas available yet.</li>';
      const priorityGaps = Array.isArray(targets.priority_gaps) ? targets.priority_gaps : [];
      el('northstar-target-gaps').innerHTML = priorityGaps.length
        ? priorityGaps
          .map((row) => `<li><strong>${esc(String(row.name || 'metric'))}</strong>: ${esc(scoreText(row.score))} vs target ${esc(scoreText(row.target))} (${esc(String(row.status || 'gap'))}, ${esc(signedScoreText(row.gap))})</li>`)
          .join('')
        : '<li>All north-star targets are currently met.</li>';
      el('northstar-weekly-rollups').innerHTML = weekly.length
        ? weekly.slice(-4).reverse()
          .map((row) => `<li><strong>${esc(String(row.week || 'week'))}</strong>: overall ${esc(scoreText(row.overall_score))} (${esc(String(row.trend || 'flat'))} ${esc(signedScoreText(row.delta))}) | samples=${esc(String(row.sample_count || 0))} | range=${esc(String(row.start_date || ''))} to ${esc(String(row.end_date || ''))}</li>`)
          .join('')
        : '<li>No weekly rollups available yet.</li>';
      const regressions = Array.isArray(ns.regressions) ? ns.regressions : [];
      const improvements = Array.isArray(ns.improvements) ? ns.improvements : [];
      el('northstar-regressions').innerHTML = regressions.length
        ? regressions.slice(0, 5).map((row) => `<li>${esc(String(row.summary || ''))}</li>`).join('')
        : '<li>No regression trend recorded.</li>';
      el('northstar-improvements').innerHTML = improvements.length
        ? improvements.slice(0, 5).map((row) => `<li>${esc(String(row.summary || ''))}</li>`).join('')
        : '<li>No improvement trend recorded.</li>';
    }

    function renderOverviewCards(data) {
      const ov = data.overview || {};
      const supervisor = data.supervisor || {};
      const cards = [
        ['Repos', ov.repos_total],
        ['In Progress', ov.tasks_in_progress],
        ['Stalled Repos', ov.repos_stalled],
        ['Ready', ov.tasks_ready],
        ['Blocked', ov.blocked_open],
        ['Aging Open', ov.stale_open],
        ['Aging Active', ov.stale_in_progress],
        ['Missing Deps', ov.missing_dependencies],
        ['Orch Gaps', ov.repos_with_inactive_service],
        ['Dirty Repos', ov.repos_dirty],
        ['North Stars', ov.repos_with_north_star],
        ['North Star Gaps', ov.repos_missing_north_star],
        ['Sec Risk Repos', ov.repos_security_risk],
        ['Sec Critical', ov.security_critical],
        ['Sec High', ov.security_high],
        ['QA Risk Repos', ov.repos_quality_risk],
        ['QA Critical', ov.quality_critical],
        ['QA High', ov.quality_high],
        ['Svc Restarts', supervisor.started],
        ['Svc Restart Fail', supervisor.failed],
        ['Upstream PRs', ov.upstream_candidates],
      ];
      el('overview-cards').innerHTML = cards
        .map(([k, v]) => `<div class="card"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`)
        .join('');
    }

    function renderAttention(data) {
      const ov = data.overview || {};
      const rows = Array.isArray(ov.attention_repos) ? ov.attention_repos : [];
      const out = el('attention');
      out.innerHTML = '';
      if (!rows.length) {
        out.innerHTML = '<li class="action-item action-empty">No high-pressure repo at this moment.</li>';
        setActionCount('attention-count', 0);
        return 0;
      }
      const shaped = rows.map((row) => {
        const repoName = String(row.repo || '');
        const score = n(row.score);
        const reasons = Array.isArray(row.reasons) ? row.reasons.join('; ') : '';
        const severity = score >= 22 ? 3 : (score >= 10 ? 2 : 1);
        const repo = repoByName(repoName) || {};
        const prompt = String((((repo.northstar || {}).prompts) || {}).claude || '') || buildAgentPrompt('attention', { repo: repoName, reasons });
        return {
          repo: repoName,
          severity,
          priority: score,
          age_days: 0,
          title: `<code>${esc(repoName)}</code> attention score=<code>${esc(score)}</code>`,
          why: esc(reasons || 'no reason attached'),
          prompt,
        };
      });
      const filtered = shaped.filter(actionRowAllowed).sort(compareActionRows).slice(0, 12);
      if (!filtered.length) {
        out.innerHTML = '<li class="action-item action-empty">No matching attention items for current repo filter.</li>';
        setActionCount('attention-count', 0);
        return 0;
      }
      out.innerHTML = filtered.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('attention-count', filtered.length);
      return filtered.length;
    }

    function renderAging(data) {
      const out = el('aging');
      out.innerHTML = '';
      const issues = [];
      (data.repos || []).forEach((repo) => {
        (repo.stale_in_progress || []).forEach((task) => {
          issues.push({
            severity: 2,
            repo: repo.name,
            label: 'aging in-progress',
            task,
            priority: 14 + n(task.age_days),
            age_days: n(task.age_days),
          });
        });
        (repo.stale_open || []).forEach((task) => {
          issues.push({
            severity: 1,
            repo: repo.name,
            label: 'aging open',
            task,
            priority: 6 + n(task.age_days),
            age_days: n(task.age_days),
          });
        });
        (repo.dependency_issues || []).forEach((item) => {
          issues.push({
            severity: 3,
            repo: repo.name,
            label: item.kind || 'dependency issue',
            task: {id: item.task_id || '', title: item.task_title || '', age_days: ''},
            priority: 24,
            age_days: 0,
          });
        });
        if (repo.stalled) {
          const reasons = Array.isArray(repo.stall_reasons) ? repo.stall_reasons : [];
          issues.push({
            severity: 3,
            repo: repo.name,
            label: 'stalled repo',
            task: {id: '', title: reasons.slice(0, 2).join('; '), age_days: 0},
            priority: 28 + n(repo.blocked_open) + n(repo.missing_dependencies),
            age_days: 0,
          });
        }
      });
      const shaped = issues.map((item) => {
        const repoName = String(item.repo || '');
        const taskId = String(item.task.id || '');
        const taskTitle = String(item.task.title || '');
        const age = Number(item.age_days || 0);
        const prompt = buildAgentPrompt('aging', {
          repo: repoName,
          task_id: taskId,
          title: taskTitle,
          label: item.label,
          age_days: age,
        });
        return {
          repo: repoName,
          severity: n(item.severity),
          priority: n(item.priority),
          age_days: age,
          title: `<code>${esc(repoName)}</code> ${esc(item.label)}`,
          why: `<code>${esc(taskId || 'n/a')}</code> ${esc(taskTitle)}${age > 0 ? ` age=${esc(age)}d` : ''}`,
          prompt,
        };
      });
      const sorted = shaped.filter(actionRowAllowed).sort(compareActionRows);
      const shown = sorted.slice(0, 18);
      if (!issues.length) {
        out.innerHTML = '<li class="action-item action-empty">No stale tasks or dependency gaps detected.</li>';
        setActionCount('aging-count', 0);
        return 0;
      }
      if (!shown.length) {
        out.innerHTML = '<li class="action-item action-empty">No matching aging/gap items for current filters.</li>';
        setActionCount('aging-count', 0);
        return 0;
      }
      out.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('aging-count', shown.length);
      return shown.length;
    }

    function renderRepoCards(data) {
      const allRows = (data.repos || []).slice();
      const rows = allRows
        .filter((repo) => repoHealthAllowed(repo) && repoDirtyAllowed(repo) && repoServiceAllowed(repo))
        .sort(compareRepos);
      const container = el('repo-grid');
      container.innerHTML = '';
      refreshRepoSummary(allRows.length, rows.length);
      if (!rows.length) {
        container.innerHTML = '<article class="repo-card"><p class="repo-note">No repos match the active filters.</p></article>';
        return;
      }
      rows.forEach((repo) => {
        const repoName = String(repo.name || '');
        const card = document.createElement('article');
        const activeCount = Array.isArray(repo.in_progress) ? repo.in_progress.length : 0;
        const isActiveRunning = String(repo.activity_state || '').toLowerCase() === 'active';
        const isStalled = !!repo.stalled;
        const north = repo.northstar || {};
        const repoNorthStar = repo.repo_north_star || {};
        card.className = `repo-card${isActiveRunning ? ' active-running' : ''}${isStalled ? ' stalled' : ''}`;
        card.setAttribute('data-repo-name', repoName);
        const [pillLabel, pillKind] = qualityPill(repo);
        const priorityScore = repoPriorityScore(repo);
        const errs = (repo.errors || []).length ? `<div class="warn">errors=${esc((repo.errors || []).join(','))}</div>` : '';
        const stallReasons = Array.isArray(repo.stall_reasons) ? repo.stall_reasons : [];
        const stallNote = isStalled
          ? `<p class="repo-note stall"><strong>stalled:</strong> ${esc(stallReasons.slice(0, 3).join('; ') || 'no active execution reason captured')}</p>`
          : '';
        const signalSentence = riskWatchSentence(repo, pillLabel);
        const signalNote = signalSentence ? `<p class="repo-note signal">${esc(signalSentence)}</p>` : '';
        card.innerHTML = `
          <div class="repo-head">
            <span class="repo-name"><code>${esc(repoName)}</code></span>
            <span class="pill ${pillKind}">${esc(pillLabel)}</span>
          </div>
          <div class="repo-meta">
            <span>northstar=<code>${esc(north.score != null ? Number(north.score).toFixed(1) : 'n/a')}</code> ${esc(north.tier || 'n/a')}</span>
            <span>trend=${esc(north.trend || 'flat')} delta=${esc(north.delta != null ? north.delta : 0)}</span>
            <span>priority=<code>${esc(priorityScore)}</code></span>
            <span>activity=${esc(repo.activity_state || 'unknown')}</span>
            <span>branch=<code>${esc(repo.git_branch || 'n/a')}</code></span>
            <span>dirty=${repo.git_dirty ? 'yes' : 'no'}</span>
            <span>ahead=${esc(repo.ahead || 0)} behind=${esc(repo.behind || 0)}</span>
            <span>service=${repo.service_running ? 'running' : (repo.workgraph_exists ? 'stopped' : 'n/a')}</span>
            <span>reporting=${repo.reporting ? 'yes' : 'no'} heartbeat=${esc(repo.heartbeat_age_seconds != null ? repo.heartbeat_age_seconds : 'n/a')}</span>
            <span>in-progress=${esc((repo.in_progress || []).length)} ready=${esc((repo.ready || []).length)}</span>
            <span>blocked=${esc(repo.blocked_open || 0)} missing-deps=${esc(repo.missing_dependencies || 0)}</span>
            <span>sec c/h=${esc((repo.security || {}).critical || 0)}/${esc((repo.security || {}).high || 0)}</span>
            <span>qa score=${esc((repo.quality || {}).quality_score || 100)} high=${esc((repo.quality || {}).high || 0)}</span>
            <span>repo-north-star=${esc(repoNorthStar.present ? (repoNorthStar.status || 'present') : 'missing')}</span>
            <span>source=${esc(repo.source || 'n/a')}</span>
          </div>
          ${errs}
          <p class="repo-note ${pillKind === 'bad' ? 'warn' : ''}">${esc(repo.narrative || '')}</p>
          ${signalNote}
          ${stallNote}
          <div class="repo-actions">
            <button class="action-link" data-focus-repo="${escAttr(repoName)}" data-scroll-graph="1">open graph</button>
          </div>
        `;
        card.addEventListener('click', (event) => {
          const target = event.target;
          if (target && target.closest && target.closest('button')) return;
          focusRepoInGraph(repoName, true);
        });
        container.appendChild(card);
      });
    }

    function refreshGraphSelector(data) {
      const select = el('graph-repo');
      const graphRepos = (data.repos || [])
        .map((repo) => ({
          name: repo.name,
          nodes: (repo.task_graph_nodes || []).length,
          edges: (repo.task_graph_edges || []).length,
        }))
        .sort((a, b) => (
          b.edges - a.edges ||
          b.nodes - a.nodes ||
          String(a.name || '').localeCompare(String(b.name || ''))
        ));
      const repos = graphRepos.map((row) => String(row.name || ''));
      const existing = new Set(Array.from(select.options).map((opt) => opt.value));
      const expected = ["__all__", ...repos];
      const needsReset = expected.length !== existing.size || expected.some((name) => !existing.has(name));
      if (needsReset) {
        const opts = ['<option value="__all__">all repos</option>'];
        graphRepos.forEach((row) => {
          const label = `${row.name} (${row.nodes}n/${row.edges}e)`;
          opts.push(`<option value="${escAttr(row.name)}">${esc(label)}</option>`);
        });
        select.innerHTML = opts.join('');
      }
      if (!selectedRepo || (!repos.includes(selectedRepo) && selectedRepo !== "__all__")) {
        const preferred = graphRepos.find((row) => row.edges > 0) || graphRepos.find((row) => row.nodes > 0);
        selectedRepo = preferred ? String(preferred.name) : "__all__";
      }
      select.value = selectedRepo;
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

    function setGraphPathText(model, activeNodeId, traversal, cycleEdges, mode, seed) {
      const out = el('graph-path');
      if ((model.edges || []).length === 0) {
        if (!activeNodeId) {
          out.textContent = `Mode: ${mode}. No dependency edges found for this repo yet (tasks may not define "after" links).`;
          return;
        }
      }
      if (!activeNodeId) {
        const loopCount = cycleEdges.size;
        out.textContent =
          `Mode: ${mode}. Focus seed: ${seed || 'none'}.\n` +
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
        `Node: ${activeNodeId} (${title})\n` +
        `Upstream chain (${up.length}): ${up.slice(0, 12).join(', ') || 'none'}\n` +
        `Downstream chain (${down.length}): ${down.slice(0, 12).join(', ') || 'none'}\n` +
        `Cycle edges touching path: ${loopHits.length ? loopHits.slice(0, 12).join(', ') : 'none'}`;
    }

    function zoomGraph(multiplier) {
      graphView.scale = Math.min(3.6, Math.max(0.45, graphView.scale * multiplier));
      if (currentData) drawGraph(currentData);
    }

    function resetGraphView() {
      graphView.scale = 1;
      graphView.tx = 0;
      graphView.ty = 0;
      if (currentData) drawGraph(currentData);
    }

    function renderMiniGraphSvg(repo) {
      const base = normalizeGraph(repo);
      const shaped = subgraphForMode(base, 'focus', '');
      const model = layoutGraph(shaped);
      const width = 360;
      const height = 150;
      const sx = width / Math.max(1, model.width);
      const sy = height / Math.max(1, model.height);
      const scale = Math.max(0.25, Math.min(sx, sy));
      const tx = 8;
      const ty = 14;
      const edgeSvg = (model.edges || [])
        .filter((edge) => model.pos[edge.source] && model.pos[edge.target])
        .map((edge) => {
          const a = model.pos[edge.source];
          const b = model.pos[edge.target];
          return `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="#c6bdaf" stroke-width="1" opacity="0.8" />`;
        })
        .join('');
      const nodeSvg = Object.values(model.pos).map((entry) =>
        `<circle cx="${entry.x}" cy="${entry.y}" r="4.2" fill="${colorFor(entry.node)}" />`
      ).join('');
      return `<svg viewBox="0 0 ${width} ${height}"><rect x="0" y="0" width="${width}" height="${height}" fill="#fffcf8" /><g transform="translate(${tx} ${ty}) scale(${scale})">${edgeSvg}${nodeSvg}</g></svg>`;
    }

    function drawAllGraphs(data) {
      const svg = el('graph');
      const all = el('graph-all');
      const graphMeta = el('graph-meta');
      svg.style.display = 'none';
      all.style.display = 'grid';
      const repos = (data.repos || [])
        .slice()
        .sort((a, b) => (
          Number((b.task_graph_edges || []).length) - Number((a.task_graph_edges || []).length) ||
          Number((b.task_graph_nodes || []).length) - Number((a.task_graph_nodes || []).length) ||
          String(a.name || '').localeCompare(String(b.name || ''))
        ));
      graphMeta.textContent = `all repos | ${repos.length} listed`;
      const blocks = repos.map((repo) => {
        const nodes = (repo.task_graph_nodes || []).length;
        const edges = (repo.task_graph_edges || []).length;
        const mini = nodes > 0 ? renderMiniGraphSvg(repo) : '<div class="cmd">No graph nodes available.</div>';
        return `
          <article class="graph-mini">
            <h4>
              <span><code>${esc(repo.name)}</code> ${esc(nodes)}n/${esc(edges)}e</span>
              <button class="action-link" data-focus-repo="${escAttr(repo.name)}" data-scroll-graph="1">open</button>
            </h4>
            ${mini}
          </article>
        `;
      });
      all.innerHTML = `<div style="grid-column:1 / -1;" class="cmd">All repo graph previews. Click <code>open</code> to focus a repo graph.</div>${blocks.join('')}`;
      el('graph-path').textContent = 'All graph previews shown. Choose a repo for interactive path tracing.';
    }

    function drawGraph(data) {
      drawRepoDependencyOverview(data);
      const svg = el('graph');
      const all = el('graph-all');
      const graphMeta = el('graph-meta');
      if (selectedRepo === "__all__") {
        drawAllGraphs(data);
        graphModel = { repo: "__all__", nodes: [], edges: [], pos: {} };
        return;
      }
      svg.style.display = 'block';
      all.style.display = 'none';
      const repo = (data.repos || []).find((r) => r.name === selectedRepo);
      if (!repo || !Array.isArray(repo.task_graph_nodes) || repo.task_graph_nodes.length === 0) {
        svg.setAttribute('viewBox', '0 0 1200 340');
        svg.innerHTML = '<text x="40" y="60" fill="#5f6f66" font-size="18">No task graph for selected repo.</text>';
        graphMeta.textContent = `${selectedRepo} | 0 nodes`;
        el('graph-path').textContent = 'No graph data for selected repo. This usually means tasks have not been written to .workgraph/graph.jsonl yet.';
        graphModel = { repo: selectedRepo, nodes: [], edges: [], pos: {} };
        return;
      }

      const baseModel = normalizeGraph(repo);
      const shaped = subgraphForMode(baseModel, graphMode, selectedNodeId);
      const model = layoutGraph(shaped);
      graphModel = { repo: repo.name, nodes: model.nodes, edges: model.edges, pos: model.pos };
      if (selectedNodeId && !model.pos[selectedNodeId]) {
        selectedNodeId = '';
      }
      const activeNodeId = selectedNodeId || shaped.seed || '';
      const traversal = activeNodeId ? traverseSelection(model, activeNodeId) : null;
      const cycleEdges = detectCycleEdges(baseModel.edges);

      const edgeSvg = model.edges
        .filter((edge) => model.pos[edge.source] && model.pos[edge.target])
        .map((edge) => {
          const a = model.pos[edge.source];
          const b = model.pos[edge.target];
          const cx1 = a.x + Math.max(24, Math.abs(b.x - a.x) * 0.35);
          const cx2 = b.x - Math.max(24, Math.abs(b.x - a.x) * 0.35);
          const edgeKey = `${edge.source}->${edge.target}`;
          const inPath = traversal ? traversal.pathEdges.has(edgeKey) : false;
          const isCycle = cycleEdges.has(edgeKey);
          const stroke = inPath ? '#0f6f7c' : (isCycle ? '#8c2f2f' : '#b8b0a3');
          const opacity = inPath ? 1.0 : (traversal ? 0.2 : 0.82);
          const dash = isCycle ? ' stroke-dasharray="6 4"' : '';
          const width = inPath ? 2.1 : 1.4;
          return `<path d="M ${a.x} ${a.y} C ${cx1} ${a.y}, ${cx2} ${b.y}, ${b.x} ${b.y}" stroke="${stroke}" stroke-width="${width}" fill="none" opacity="${opacity}"${dash} />`;
        })
        .join('');

      const selectedRuntime = selectedRepo && selectedRepo.runtime && typeof selectedRepo.runtime === 'object' ? selectedRepo.runtime : {};
      const selectedActiveTaskIds = Array.isArray(selectedRuntime.active_task_ids) && selectedRuntime.active_task_ids.length
        ? new Set(selectedRuntime.active_task_ids.map((value) => String(value)))
        : null;
      const selectedRepoActive = String((selectedRepo && selectedRepo.activity_state) || '').toLowerCase() === 'active';
      const nodeSvg = Object.values(model.pos).map((entry) => {
        const label = String(entry.node.label || entry.node.id || '').slice(0, 28);
        const status = String(entry.node.status || '').toLowerCase();
        const statusClass = status.replace(/[^a-z0-9]+/g, '-');
        const isInProgress = status === 'in-progress';
        const isRuntimeActive = selectedActiveTaskIds ? selectedActiveTaskIds.has(String(entry.node.id || '')) : false;
        const shouldPulse = selectedActiveTaskIds ? isRuntimeActive : (isInProgress && selectedRepoActive);
        const age = Number.isFinite(Number(entry.node.age_days)) ? `${entry.node.age_days}d` : '';
        const isSelected = activeNodeId && String(entry.node.id) === String(activeNodeId);
        const inPath = traversal ? traversal.pathNodes.has(String(entry.node.id)) : false;
        const stroke = isSelected ? '#0f6f7c' : (inPath ? '#1b5f69' : '#fff');
        const strokeW = isSelected ? 3 : (inPath ? 2 : 1);
        const opacity = traversal ? (inPath ? 1 : 0.34) : 1;
        return `
          <g class="graph-node status-${statusClass}" data-node-id="${esc(entry.node.id)}" style="opacity:${opacity}; cursor:pointer;">
            ${shouldPulse ? `<circle class="pulse-halo" cx="${entry.x}" cy="${entry.y}" r="14" fill="none" stroke="#0f6f7c" stroke-width="2.3" />` : ''}
            <circle class="base-node" cx="${entry.x}" cy="${entry.y}" r="10" fill="${colorFor(entry.node)}" stroke="${stroke}" stroke-width="${strokeW}" />
            <text x="${entry.x + 16}" y="${entry.y + 5}" fill="#2b3932" font-size="12">${esc(entry.node.id)}</text>
            <text x="${entry.x + 16}" y="${entry.y + 20}" fill="#6b776f" font-size="10">${esc(label)} ${esc(age)}</text>
          </g>
        `;
      }).join('');

      const depthLabels = Array.from({ length: Math.max(1, model.maxDepth + 1) }, (_v, idx) => idx)
        .map((depth) => `<text x="${120 + depth * 230 - 16}" y="32" fill="#6b776f" font-size="12">D${depth}</text>`)
        .join('');

      svg.setAttribute('viewBox', `0 0 ${model.width} ${model.height}`);
      svg.innerHTML =
        `<rect x="0" y="0" width="${model.width}" height="${model.height}" fill="#fffdfa" pointer-events="none" />` +
        `<g id="graph-content" transform="translate(${graphView.tx} ${graphView.ty}) scale(${graphView.scale})">${depthLabels}${edgeSvg}${nodeSvg}</g>`;
      const loopCount = cycleEdges.size;
      const totalNodes = Number((baseModel.nodes || []).length);
      const totalEdges = Number((baseModel.edges || []).length);
      const scope = graphMode === 'full'
        ? `${model.nodes.length} nodes, ${model.edges.length} edges`
        : `${model.nodes.length}/${totalNodes} nodes, ${model.edges.length}/${totalEdges} edges`;
      graphMeta.textContent =
        `${repo.name} | mode=${graphMode} | ${scope}, loops=${loopCount} | zoom=${graphView.scale.toFixed(2)}x`;
      setGraphPathText(
        model,
        activeNodeId,
        traversal || { ancestors: new Set(), descendants: new Set(), pathNodes: new Set(), pathEdges: new Set() },
        cycleEdges,
        graphMode,
        shaped.seed,
      );
    }

    function renderNext(data) {
      const list = el('next');
      list.innerHTML = '';
      const rows = (data.next_work || []).map((item) => {
        const repoName = String(item.repo || '');
        const taskId = String(item.task_id || '');
        const status = String(item.status || 'unknown');
        const severity = status === 'in-progress' ? 2 : 1;
        const prompt = buildAgentPrompt('next', {
          repo: repoName,
          task_id: taskId,
          title: String(item.title || ''),
          status,
        });
        return {
          repo: repoName,
          severity,
          priority: n(item.priority) + (severity * 3),
          age_days: 0,
          title: `<code>${esc(repoName)}</code> <code>${esc(taskId)}</code> ${esc(item.title || '')}`,
          why: `status=<code>${esc(status)}</code>`,
          prompt,
        };
      });
      const shown = rows.filter(actionRowAllowed).sort(compareActionRows).slice(0, 20);
      if (!shown.length) {
        list.innerHTML = '<li class="action-item action-empty">No next-work tasks for current repo filter.</li>';
        setActionCount('next-count', 0);
        return 0;
      }
      list.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('next-count', shown.length);
      return shown.length;
    }

    function renderUpstream(data) {
      const list = el('upstream');
      list.innerHTML = '';
      const rows = (data.upstream_candidates || []).map((item) => {
        const repoName = String(item.repo || '');
        const ahead = n(item.ahead);
        const files = n((item.changed_files || []).length);
        const prompt = buildAgentPrompt('upstream', {
          repo: repoName,
          category: String(item.category || ''),
          ahead,
          file_count: files,
        });
        return {
          repo: repoName,
          severity: 1,
          priority: ahead + files,
          age_days: 0,
          title: `<code>${esc(repoName)}</code> ${esc(item.category || 'candidate')} ahead=<code>${esc(ahead)}</code> files=<code>${esc(files)}</code>`,
          why: esc(String(item.summary || '')),
          prompt,
        };
      });
      const shown = rows.filter(actionRowAllowed).sort(compareActionRows).slice(0, 20);
      if (!shown.length) {
        list.innerHTML = '<li class="action-item action-empty">No upstream candidates for current repo filter.</li>';
        setActionCount('upstream-count', 0);
        return 0;
      }
      list.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('upstream-count', shown.length);
      return shown.length;
    }

    function renderSecurity(data) {
      const list = el('security');
      list.innerHTML = '';
      const rows = [];
      (data.repos || []).forEach((repo) => {
        const repoName = String(repo.name || '');
        (repo.security_findings || []).forEach((finding) => {
          const severityRaw = String(finding.severity || 'medium').toLowerCase();
          const severity = severityRaw === 'critical' ? 3 : (severityRaw === 'high' ? 3 : (severityRaw === 'medium' ? 2 : 1));
          const priority = (severityRaw === 'critical' ? 30 : (severityRaw === 'high' ? 18 : (severityRaw === 'medium' ? 10 : 4))) + n((repo.security || {}).risk_score || 0);
          const prompt = String(finding.model_prompt || '') || buildAgentPrompt('security', {
            repo: repoName,
            category: String(finding.category || ''),
            severity: severityRaw,
            evidence: String(finding.evidence || ''),
          });
          rows.push({
            repo: repoName,
            severity,
            priority,
            age_days: 0,
            title: `<code>${esc(repoName)}</code> security ${esc(severityRaw)} <code>${esc(String(finding.category || 'finding'))}</code>`,
            why: esc(String(finding.evidence || finding.title || '')),
            prompt,
          });
        });
      });
      const shown = rows.filter(actionRowAllowed).sort(compareActionRows).slice(0, 20);
      if (!shown.length) {
        list.innerHTML = '<li class="action-item action-empty">No security review items for current filters.</li>';
        setActionCount('security-count', 0);
        return 0;
      }
      list.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('security-count', shown.length);
      return shown.length;
    }

    function renderQuality(data) {
      const list = el('quality');
      list.innerHTML = '';
      const rows = [];
      (data.repos || []).forEach((repo) => {
        const repoName = String(repo.name || '');
        (repo.quality_findings || []).forEach((finding) => {
          const severityRaw = String(finding.severity || 'medium').toLowerCase();
          const severity = severityRaw === 'critical' ? 3 : (severityRaw === 'high' ? 3 : (severityRaw === 'medium' ? 2 : 1));
          const qualityScore = n((repo.quality || {}).quality_score || 100);
          const priority = (severityRaw === 'critical' ? 24 : (severityRaw === 'high' ? 16 : (severityRaw === 'medium' ? 10 : 4))) + Math.max(0, Math.floor((95 - qualityScore) / 2));
          const prompt = String(finding.model_prompt || '') || buildAgentPrompt('quality', {
            repo: repoName,
            category: String(finding.category || ''),
            severity: severityRaw,
            evidence: String(finding.evidence || ''),
          });
          rows.push({
            repo: repoName,
            severity,
            priority,
            age_days: 0,
            title: `<code>${esc(repoName)}</code> quality ${esc(severityRaw)} <code>${esc(String(finding.category || 'finding'))}</code>`,
            why: esc(String(finding.evidence || finding.title || '')),
            prompt,
          });
        });
      });
      const shown = rows.filter(actionRowAllowed).sort(compareActionRows).slice(0, 20);
      if (!shown.length) {
        list.innerHTML = '<li class="action-item action-empty">No quality review items for current filters.</li>';
        setActionCount('quality-count', 0);
        return 0;
      }
      list.innerHTML = shown.map((item) => renderActionItemHtml(item)).join('');
      setActionCount('quality-count', shown.length);
      return shown.length;
    }

    function render(data, source) {
      currentData = data;
      window.currentData = data;
      el('meta').textContent =
        `Generated: ${data.generated_at || 'n/a'} | repos: ${data.repo_count || 0} | transport: ${source}`;
      el('narrative').textContent = data.narrative || 'No narrative generated yet.';
      renderNorthstar(data);
      el('updates').textContent = (data.updates && data.updates.summary) ? data.updates.summary : 'No update summary';
      renderOverviewCards(data);
      refreshActionRepoFilter(data);
      const attentionCount = renderAttention(data);
      const agingCount = renderAging(data);
      renderRepoCards(data);
      const nextCount = renderNext(data);
      const upstreamCount = renderUpstream(data);
      const securityCount = renderSecurity(data);
      const qualityCount = renderQuality(data);
      renderActionSummary({
        attention: attentionCount,
        aging: agingCount,
        upstream: upstreamCount,
        next: nextCount,
        security: securityCount,
        quality: qualityCount,
      });
      refreshGraphSelector(data);
      drawGraph(data);
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

    el('graph-repo').addEventListener('change', (event) => {
      selectedRepo = String(event.target.value || '');
      selectedNodeId = '';
      if (currentData) drawGraph(currentData);
    });
    el('action-repo-filter').addEventListener('change', (event) => {
      actionRepoFilter = String(event.target.value || '__all__');
      if (currentData) render(currentData, 'filtered');
    });
    el('action-sort').addEventListener('change', (event) => {
      actionSortMode = String(event.target.value || 'priority');
      if (currentData) render(currentData, 'filtered');
    });
    el('action-priority-filter').addEventListener('change', (event) => {
      actionPriorityFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('action-dirty-filter').addEventListener('change', (event) => {
      actionDirtyFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-sort').addEventListener('change', (event) => {
      repoSortMode = String(event.target.value || 'priority');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-health-filter').addEventListener('change', (event) => {
      repoHealthFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-dirty-filter').addEventListener('change', (event) => {
      repoDirtyFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('repo-service-filter').addEventListener('change', (event) => {
      repoServiceFilter = String(event.target.value || 'all');
      if (currentData) render(currentData, 'filtered');
    });
    el('graph-mode').addEventListener('change', (event) => {
      graphMode = String(event.target.value || 'active');
      selectedNodeId = '';
      if (currentData) drawGraph(currentData);
    });

    document.addEventListener('click', (event) => {
      const target = event.target;
      const copy = target && target.closest ? target.closest('[data-copy-prompt]') : null;
      if (copy) {
        const promptText = String(copy.getAttribute('data-copy-prompt') || '');
        if (promptText && navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(promptText).then(() => {
            copy.textContent = 'copied';
            setTimeout(() => { copy.textContent = 'copy prompt'; }, 900);
          }).catch(() => {});
        }
        return;
      }
      const btn = target && target.closest ? target.closest('[data-focus-repo]') : null;
      if (!btn) return;
      const repo = String(btn.getAttribute('data-focus-repo') || '');
      if (!repo) return;
      const scroll = String(btn.getAttribute('data-scroll-graph') || '') === '1';
      focusRepoInGraph(repo, scroll);
    });

    el('graph-zoom-in').addEventListener('click', () => zoomGraph(1.18));
    el('graph-zoom-out').addEventListener('click', () => zoomGraph(1 / 1.18));
    el('graph-zoom-reset').addEventListener('click', () => resetGraphView());

    const svg = el('graph');
    svg.addEventListener('wheel', (event) => {
      event.preventDefault();
      const delta = event.deltaY < 0 ? 1.08 : 0.92;
      zoomGraph(delta);
    }, { passive: false });

    svg.addEventListener('pointerdown', (event) => {
      const nodeEl = event.target && event.target.closest ? event.target.closest('[data-node-id]') : null;
      if (nodeEl) {
        selectedNodeId = String(nodeEl.getAttribute('data-node-id') || '');
        if (currentData) drawGraph(currentData);
        return;
      }
      graphView.drag = true;
      graphView.dragStartX = event.clientX;
      graphView.dragStartY = event.clientY;
      graphView.dragBaseX = graphView.tx;
      graphView.dragBaseY = graphView.ty;
      svg.classList.add('dragging');
      try { svg.setPointerCapture(event.pointerId); } catch (_err) {}
    });

    svg.addEventListener('pointermove', (event) => {
      if (!graphView.drag) return;
      graphView.tx = graphView.dragBaseX + (event.clientX - graphView.dragStartX);
      graphView.ty = graphView.dragBaseY + (event.clientY - graphView.dragStartY);
      if (currentData) drawGraph(currentData);
    });

    function endGraphDrag(event) {
      if (!graphView.drag) return;
      graphView.drag = false;
      svg.classList.remove('dragging');
      try { svg.releasePointerCapture(event.pointerId); } catch (_err) {}
    }
    svg.addEventListener('pointerup', endGraphDrag);
    svg.addEventListener('pointercancel', endGraphDrag);

    refreshHttp().catch(() => {});
    startPolling();
    connectWebSocket();
  </script>
</body>
</html>
"""
