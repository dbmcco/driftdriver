# ABOUTME: Standalone HTML page for the redacted model registry dashboard.
# ABOUTME: Fetches /api/model-registry and renders route, repo, and key coverage.
from __future__ import annotations


def render_model_registry_dashboard_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Model Registry</title>
  <style>
    :root {
      --bg: #f7f7f4;
      --panel: #ffffff;
      --ink: #1f2528;
      --muted: #667076;
      --line: #d9dddf;
      --accent: #176b6f;
      --accent-soft: #e5f2f2;
      --good: #1f7a43;
      --warn: #9a6415;
      --bad: #a83232;
      --mono: "IBM Plex Mono", "SFMono-Regular", Menlo, monospace;
      --sans: "IBM Plex Sans", "Avenir Next", "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: var(--sans);
      font-size: 14px;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      background: rgba(255, 255, 255, 0.94);
      border-bottom: 1px solid var(--line);
      padding: 0.85rem 1rem;
    }
    .header-row {
      max-width: 1500px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
    }
    h1 {
      margin: 0;
      font-size: 1.02rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    a { color: var(--accent); text-decoration: none; }
    main {
      max-width: 1500px;
      margin: 0 auto;
      padding: 1rem;
      display: grid;
      gap: 1rem;
    }
    .meta {
      color: var(--muted);
      font-size: 0.82rem;
      margin-top: 0.22rem;
      font-family: var(--mono);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
      gap: 0.75rem;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0.85rem;
    }
    .metric {
      display: flex;
      flex-direction: column;
      gap: 0.18rem;
      min-height: 72px;
      justify-content: center;
    }
    .metric .value {
      font-size: 1.45rem;
      font-weight: 700;
      line-height: 1;
    }
    .metric .label {
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    h2 {
      margin: 0 0 0.7rem;
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
      margin-bottom: 0.65rem;
      flex-wrap: wrap;
    }
    input, select {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 0.42rem 0.52rem;
      font: inherit;
      background: #fff;
      min-width: 190px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      border-bottom: 1px solid #edf0f1;
      text-align: left;
      vertical-align: top;
      padding: 0.48rem 0.42rem;
      overflow-wrap: anywhere;
    }
    th {
      color: #465157;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      background: #fafafa;
      position: sticky;
      top: 58px;
      z-index: 1;
    }
    code, .mono {
      font-family: var(--mono);
      font-size: 0.78rem;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0.08rem 0.42rem;
      margin: 0.05rem 0.15rem 0.05rem 0;
      font-family: var(--mono);
      font-size: 0.72rem;
      color: #334148;
      background: #fff;
    }
    .ok { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .pill.ok { border-color: #b9dfc8; background: #eef9f2; color: var(--good); }
    .pill.warn { border-color: #edd5ab; background: #fff6e6; color: var(--warn); }
    .pill.bad { border-color: #efc3c3; background: #fff1f1; color: var(--bad); }
    .split {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 1rem;
    }
    @media (max-width: 980px) {
      .split { grid-template-columns: 1fr; }
      th { position: static; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-row">
      <div>
        <h1>Model Registry</h1>
        <div class="meta" id="meta">Loading registry...</div>
      </div>
      <a href="/">Ecosystem Hub</a>
    </div>
  </header>
  <main>
    <section class="grid" id="summary"></section>

    <section class="card">
      <div class="toolbar">
        <h2>Repo Coverage</h2>
        <input id="repo-filter" placeholder="Filter repos, presets, credentials" />
      </div>
      <div id="repo-table"></div>
    </section>

    <section class="card">
      <div class="toolbar">
        <h2>Credentials</h2>
        <select id="credential-filter">
          <option value="all">All credentials</option>
          <option value="missing">Missing only</option>
          <option value="present">Present only</option>
        </select>
      </div>
      <div id="credential-table"></div>
    </section>

    <section class="split">
      <div class="card">
        <h2>Route Assignments</h2>
        <div id="assignment-table"></div>
      </div>
      <div class="card">
        <h2>Presets</h2>
        <div id="preset-table"></div>
      </div>
    </section>

    <section class="card">
      <div class="toolbar">
        <h2>Model Routes</h2>
        <input id="route-filter" placeholder="Filter routes, surfaces, models" />
      </div>
      <div id="route-table"></div>
    </section>
  </main>
  <script>
    let payload = null;
    const el = (id) => document.getElementById(id);
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const pills = (values, cls = '') => (values || []).map(v => `<span class="pill ${cls}">${esc(v)}</span>`).join('');
    const statusPill = (value, severity = '') => {
      const ok = ['centralized', 'centralized-api', 'centralized-cli', 'centralized-local', 'present', 'verified'];
      const warn = ['needs-secret-source', 'local-env-pending-migration', 'partial', 'unresolved', 'waived'];
      const bad = ['hardcoded-route-found', 'hardcoded-secret-found', 'missing'];
      const cls = severity === 'muted' ? '' : severity || (ok.includes(value) || value === true ? 'ok' : warn.includes(value) ? 'warn' : bad.includes(value) ? 'bad' : '');
      return `<span class="pill ${cls}">${esc(value)}</span>`;
    };
    const probeDetails = (row) => `
      ${statusPill(row.probe_status || 'unsupported')}
      <div class="meta">${esc(row.last_verified_at || '')}</div>
      <div class="meta">${esc(row.exception_reason || '')}</div>
      <div class="meta">${esc(row.owner_next_step || '')}</div>
    `;
    const table = (headers, rows) => `
      <table>
        <thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
        <tbody>${rows.join('') || `<tr><td colspan="${headers.length}" class="mono">No rows</td></tr>`}</tbody>
      </table>`;

    function renderSummary(data) {
      const s = data.summary || {};
      const probes = s.probe_status_counts || {};
      const metrics = [
        ['Credentials', `${s.credentials_present || 0}/${s.credentials_total || 0}`],
        ['Missing Keys', s.credentials_missing || 0],
        ['Model Routes', s.model_routes_total || 0],
        ['Provider Surfaces', s.provider_surfaces_total || 0],
        ['Strict Presets', `${s.strict_presets_total || 0}/${s.presets_total || 0}`],
        ['Centralized Repos', `${s.repo_rows_centralized || 0}/${s.repo_rows_total || 0}`],
        ['Verified Probes', probes.verified || 0],
        ['Waived Probes', probes.waived || 0],
        ['Attention Rows', s.repo_rows_attention || 0],
      ];
      el('summary').innerHTML = metrics.map(([label, value]) => `
        <div class="card metric"><div class="value">${esc(value)}</div><div class="label">${esc(label)}</div></div>
      `).join('');
    }

    function renderRepos() {
      const q = el('repo-filter').value.toLowerCase();
      const rows = (payload.repo_coverage || []).filter(r => JSON.stringify(r).toLowerCase().includes(q)).map(r => `
        <tr>
          <td><code>${esc(r.repo)}</code><div class="meta">${esc(r.role)} ${esc(r.lifecycle)}</div></td>
          <td>${statusPill(r.status, r.severity)}<div class="meta">${esc(r.next_action || '')}</div></td>
          <td>${probeDetails(r)}</td>
          <td><code>${esc(r.route_preset || '')}</code><div>${pills(r.transports || [])}${pills(r.providers || [])}</div></td>
          <td>${(r.resolved_credentials || []).map(c => `<span class="pill ${c.present ? 'ok' : 'warn'}">${esc(c.provider)} → ${esc(c.id)}</span>`).join('')}${pills(r.matching_credentials || [])}</td>
          <td><span class="pill">models ${esc((r.signals || {}).model_literal_files || 0)}</span><span class="pill">keys ${esc((r.signals || {}).credential_reference_files || 0)}</span><span class="pill ${((r.signals || {}).hardcoded_secret_files || 0) ? 'bad' : ''}">secrets ${esc((r.signals || {}).hardcoded_secret_files || 0)}</span></td>
        </tr>
      `);
      el('repo-table').innerHTML = table(['Repo', 'Status / Next Action', 'Probe', 'Route / Transport', 'Credentials', 'Code Signals'], rows);
    }

    function renderCredentials() {
      const filter = el('credential-filter').value;
      const rows = (payload.credentials || []).filter(c => {
        if (filter === 'missing') return !c.present;
        if (filter === 'present') return c.present;
        return true;
      }).map(c => `
        <tr>
          <td><code>${esc(c.id)}</code><div class="meta">${esc(c.provider)} / ${esc(c.source)}</div></td>
          <td><code>${esc(c.env_var)}</code></td>
          <td>${statusPill(c.present ? 'present' : 'missing')}</td>
          <td>${c.present_in_environment ? statusPill('process') : ''}${c.present_in_env_files ? statusPill('env file') : ''}</td>
          <td>${pills(c.owners || [])}</td>
        </tr>
      `);
      el('credential-table').innerHTML = table(['Credential', 'Env Var', 'Presence', 'Detected In', 'Owners'], rows);
    }

    function renderAssignments() {
      const agentRows = (payload.agent_assignments || []).map(a => `
        <tr><td><code>agent:${esc(a.id)}</code></td><td><code>${esc(a.preset)}</code></td><td>${pills(a.route_ids || [])}</td></tr>
      `);
      const serviceRows = (payload.service_assignments || []).map(a => `
        <tr><td><code>service:${esc(a.id)}</code></td><td><code>${esc(a.preset)}</code></td><td>${pills(a.route_ids || [])}</td></tr>
      `);
      el('assignment-table').innerHTML = table(['Owner', 'Preset', 'Routes'], agentRows.concat(serviceRows));
    }

    function renderPresets() {
      const rows = (payload.presets || []).map(p => `
        <tr>
          <td><code>${esc(p.id)}</code></td>
          <td>${(p.profiles || []).map(profile => `<div><span class="pill ${profile.fallback_enabled ? 'warn' : 'ok'}">${esc(profile.profile)} ${profile.fallback_enabled ? 'fallback' : 'strict'}</span>${pills(profile.routes || [])}</div>`).join('')}</td>
        </tr>
      `);
      el('preset-table').innerHTML = table(['Preset', 'Profiles'], rows);
    }

    function renderRoutes() {
      const q = el('route-filter').value.toLowerCase();
      const rows = (payload.model_routes || []).filter(r => JSON.stringify(r).toLowerCase().includes(q)).map(r => `
        <tr>
          <td><code>${esc(r.id)}</code><div class="meta">${esc(r.owner)}</div></td>
          <td><code>${esc(r.surface)}</code><div class="meta">${esc(r.provider)}</div></td>
          <td><code>${esc(r.model)}</code></td>
          <td>${pills([r.transport, r.quality_tier, r.cost_tier].filter(Boolean))}</td>
          <td>${probeDetails(r)}</td>
          <td>${pills(r.owners || [])}</td>
        </tr>
      `);
      el('route-table').innerHTML = table(['Route', 'Surface', 'Model', 'Tiers', 'Probe', 'Used By'], rows);
    }

    function render(data) {
      payload = data;
      el('meta').textContent = `${data.active_preset || 'no active preset'} • ${data.registry_path || ''} • ${data.generated_at || ''}`;
      renderSummary(data);
      renderRepos();
      renderCredentials();
      renderAssignments();
      renderPresets();
      renderRoutes();
    }

    async function load() {
      const response = await fetch('/api/model-registry', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    }
    el('repo-filter').addEventListener('input', renderRepos);
    el('credential-filter').addEventListener('change', renderCredentials);
    el('route-filter').addEventListener('input', renderRoutes);
    load().catch(err => {
      el('meta').textContent = `Failed to load registry: ${err.message}`;
      el('summary').innerHTML = '';
    });
  </script>
</body>
</html>"""
