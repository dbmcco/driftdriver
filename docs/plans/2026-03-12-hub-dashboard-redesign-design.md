# Ecosystem Hub Dashboard Redesign — Design

## Approach

**"A to C"** — Simplify in place (Approach A) with structure that enables a command center with chat (Approach C) later.

**Goal:** Compress the dashboard from 7 sections / 15,000+ px to 4 zones in ~2 viewports, prioritizing triage over information density.

**Audience:** Braydon (sole human consumer). Agents use the API directly.

**Current state:** Single-page HTML at http://127.0.0.1:8777/ rendering snapshot data for 39 repos. 7 sections: Narrated Overview, North Star Scorecard, Operational Overview, By Repo (39 cards), Dependency Graph, Action Center (6 columns, 110 items), Updates.

---

## Layout

```
┌─────────────────────────────────────────────────┐
│  BRIEFING BAR                                    │
├─────────────────────────────────────────────────┤
│  ATTENTION QUEUE                                 │
├───────────────────────┬─────────────────────────┤
│  REPO TABLE           │  GRAPHS PANEL            │
│                       │  (chat-ready flex slot)   │
└───────────────────────┴─────────────────────────┘
```

Four zones, top to bottom. Repo Table and Graphs Panel sit side by side in a flex row. The flex container includes a `<aside id="chat-panel" hidden>` stub for Approach C.

---

## Zone 1: Briefing Bar

2-3 sentence overview generated from snapshot data. Replaces the current Narrated Overview wall of text.

**Template:** "{count} repos need attention — {top issues}. Ecosystem trend: {direction} over last 7 days."

**Expandable detail:** Each repo name mentioned in the briefing is a clickable expander. Clicking reveals a 3-5 line inline detail block (what's wrong, when it started, recommended action). Click again to collapse.

**Rules:**
- Max 3 repos mentioned by name (most urgent)
- One trend sentence
- No metrics beyond what's needed to orient
- Collapsed state stays scannable; expanded state gives enough to decide without scrolling

---

## Zone 2: Attention Queue

Sortable table of items needing human decision. Everything the factory can handle is suppressed.

| Column | Content |
|--------|---------|
| Repo | Repo name |
| Issue | Brief description |
| Severity | high / medium / low |
| Age | Time since first detected |
| Action | Stub label (Restart, View, Dismiss) |

**Filtering:** Pulls from the same data as the current Action Center's 6 columns, but only the "needs human" subset.

**Sorting:** Severity (default), age, or repo name.

**Dismiss:** Removes item from queue for this snapshot cycle.

**Empty state:** "Nothing needs your attention right now." (one line)

**Approach C upgrade:** Action stubs become one-click dispatch buttons that send commands to agents.

---

## Zone 3: Repo Table

Dense, filterable table replacing 39 equal-weight cards.

**Filter bar:** Dropdown pills for role, status, has-drift. Multi-select. Persists via URL params. Text search field.

**Columns:**
- **Repo** — name, clickable to expand inline detail panel
- **Role** — from ecosystem.toml (orchestrator, baseline, lane, product)
- **Status** — active/idle/missing with dot indicator
- **Drift** — count of open findings, colored by severity
- **Tasks** — completed/total from workgraph
- **Trend** — inline sparkline (7-day drift trend, migrated from North Star Scorecard)
- **Last Activity** — relative timestamp

**Expanded row:** Shows task breakdown, recent drift findings, dependency links, and task-loop visualization (break-fix cycles rendered as visible loops in a mini DAG).

---

## Zone 4: Graphs Panel

Right column alongside repo table. Flex container shared with future chat panel.

**Cross-Repo Dependency Map:**
- Force-directed layout showing repo-to-repo dependencies
- Hover a node to highlight its dependents/dependencies
- Impact zones: repos that could break if a dependency is being modified light up
- Compact — not the current full-width SVG

**Task Structure View (per-repo):**
- Shown inside expanded repo rows, not in the graphs panel
- Mini DAG of the repo's workgraph tasks
- Break-fix loops render as visible cycles
- Color-coded: green (done), blue (in progress), gray (pending), red (blocked)

**Chat-ready:** Panel sits in flex container. Approach C activates the hidden `<aside id="chat-panel">` to share this space.

---

## What's Removed

| Current Section | Disposition |
|-----------------|-------------|
| Narrated Overview | Replaced by Briefing Bar (2-3 sentences + expanders) |
| North Star Scorecard (KPI cards) | Removed. Sparklines migrate to Repo Table Trend column |
| Operational Overview | Folded into Briefing Bar or Repo Table column headers |
| By Repo (39 cards) | Replaced by Repo Table |
| Dependency Graph (full-page) | Replaced by compact cross-repo map + per-repo task DAGs |
| Action Center (6 columns) | Replaced by Attention Queue (human-decision items only) |
| Updates | Removed (redundant with Attention Queue + Repo Table) |
| Copy-prompt buttons | Removed (replaced by dispatch stubs for Approach C) |

---

## Approach C Upgrade Path

When ready to add the command center:
1. Unhide `<aside id="chat-panel">` in the flex container
2. Wire chat to an agent endpoint that can query snapshot data
3. Convert Attention Queue action stubs to one-click dispatch buttons
4. Chat can reference any repo/issue visible on the dashboard
