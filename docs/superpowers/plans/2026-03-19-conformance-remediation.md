# Conformance Remediation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve all 23 conformance findings surfaced by governancedrift — stop lifecycle-violation daemons, clear process debt, and write north star documents for 17 active repos.

**Architecture:** Three parallel workstreams: (A) mechanical daemon stops, (B) process debt triage, (C) 17 independent north star documents. All tasks can run in parallel except C tasks that share a repo.

**Tech Stack:** wg CLI, bash, markdown (NORTH_STAR.md per repo)

---

## Workstream A — Lifecycle Violations (stop daemons)

**Repos:** news-briefing, speedrift-ecosystem-v2-run3, speedrift-ecosystem-v2-run5
**Finding:** Classified `retired`/`experimental` + daemon_posture=`never` but daemon sockets live.

- [ ] Stop each daemon:
```bash
wg service stop --dir /Users/braydon/projects/experiments/news-briefing
wg service stop --dir /Users/braydon/projects/experiments/speedrift-ecosystem-v2-run3
wg service stop --dir /Users/braydon/projects/experiments/speedrift-ecosystem-v2-run5
```
- [ ] Verify sockets gone:
```bash
for repo in news-briefing speedrift-ecosystem-v2-run3 speedrift-ecosystem-v2-run5; do
  sock="/Users/braydon/projects/experiments/$repo/.workgraph/service/daemon.sock"
  echo "$repo: $([ -S $sock ] && lsof $sock 2>/dev/null | grep -q . && echo STILL_LIVE || echo stopped)"
done
```
- [ ] Verify conformance finding clears on next hub tick (allow 2 min):
```bash
curl -s http://127.0.0.1:8777/api/conformance | python3 -c "import json,sys; d=json.load(sys.stdin); lv=[f for f in d['findings'] if f['category']=='lifecycle-violation']; print(f'lifecycle-violations remaining: {len(lv)}')"
```
- [ ] Commit nothing — no code changes, daemon state only.

---

## Workstream B — Process Debt (zombie agent triage)

**Repos:** paia-identity, paia-meetings, paia-work
**Finding:** Live agents with tasks_ready=0 — agents alive but no work to do.

For each repo:
- [ ] Check live agents:
```bash
for repo in paia-identity paia-meetings paia-work; do
  count=$(pgrep -f "claude.*$repo" 2>/dev/null | wc -l | tr -d ' ')
  echo "$repo: $count live agents"
done
```
- [ ] Stop daemon (stops coordinator, lets agents finish naturally):
```bash
wg service stop --dir /Users/braydon/projects/experiments/paia-identity
wg service stop --dir /Users/braydon/projects/experiments/paia-meetings
wg service stop --dir /Users/braydon/projects/experiments/paia-work
```
- [ ] Wait 60s, verify agents cleared:
```bash
sleep 60
for repo in paia-identity paia-meetings paia-work; do
  count=$(pgrep -f "claude.*$repo" 2>/dev/null | wc -l | tr -d ' ')
  echo "$repo: $count agents remaining"
done
```
- [ ] Archive open failed/abandoned tasks in each repo to clean the graph:
```bash
for repo in paia-identity paia-meetings paia-work; do
  wg archive --status failed --dir /Users/braydon/projects/experiments/$repo 2>/dev/null || echo "$repo: no failed tasks to archive"
  wg archive --status abandoned --dir /Users/braydon/projects/experiments/$repo 2>/dev/null || echo "$repo: no abandoned tasks to archive"
done
```
- [ ] Verify process-debt findings clear on next hub tick.

---

## Workstream C — North Stars (17 repos)

Each task is independent. Worker should:
1. Read the repo's key source files to understand what it does
2. Write `NORTH_STAR.md` in the repo root
3. Commit

**Format** (from existing north stars in the ecosystem):
```markdown
# North Star — <repo-name>

One paragraph: what this repo is and what it exists to do.

## Outcome target

One concrete, measurable statement of what "done well" looks like.

## Current phase

`<phase>` (e.g., onboarded → production-ready, or stable)

Brief description of current active work or state.

## Dependencies (if applicable)

| Service | Port | Role |
|---------|------|------|
```

---

### C1 — paia-os
**Port:** 3500 (backend), 3501 (Next.js frontend)
**Role:** Core paia monolith — the central AI operating system service. Houses agent coordination, integrations, media pipeline, and the primary API surface.
**Key dirs to read:** `src/` or main app entry, `README.md`

### C2 — paia-shell
**Port:** 3540
**Role:** Unified communication shell — conversations, workrooms, channel abstraction (Telegram, etc.), persona surfaces. The "chat interface" of the paia ecosystem.

### C3 — paia-program
**Port:** N/A (program graph, not a service)
**Role:** The paia program graph — all milestones, epics, and coordination tasks across the entire paia platform live here. 2800+ tasks done. The workgraph IS the product for this repo.

### C4 — samantha
**Port:** 3530
**Role:** Scheduling and orchestration agent. Accepts tasks from paia event bus, handles calendar/scheduling domain, delegates to other agents.

### C5 — derek
**Port:** 3531
**Role:** Dev agent and working architect. Handles development tasks, code generation, architecture decisions. Has dual-memory: ecosystem hub (live ops) + arch facets (persistent knowledge).

### C6 — ingrid
**Port:** 3532
**Role:** Back-office ops and finance agent. Handles deadlines, payments, financial tracking, operational communications.

### C7 — assistant-system
**Role:** n8n-based assistant integration system. Houses n8n workflows, webhook handlers, integration glue between external services and paia. Infrastructure-layer, not a user-facing service.

### C8 — folio (formerly paia-vault)
**Port:** 3520
**Role:** Standalone knowledge management app. Semantic search, annotations, dual namespace (PAIA/SYNTH), 96 tests. Independent from paia-os.

### C9 — lfw-ai-graph-crm
**Ports:** LFW 3200, Synthyra 3201 (same codebase, CRM_PROFILE env)
**Role:** AI-powered relationship intelligence CRM. North Star: understands relationships the way you would with perfect memory — not a pipeline/funnel, but a living graph of where you stand with each person. 30K LOC production, 548 tasks.

### C10 — third-layer-news
**Railway:** Live (web + API on Railway)
**Role:** Model-mediated editorial news pipeline. 11 RSS sources, LLM classification/curation, magazine-style frontend. V2.3 shipped.

### C11 — lodestar
**Port:** 3417
**Role:** Situation intelligence platform for decision-making. Multi-situation tracking, scenario engine, signal enhancement, decision engine. Target user: Noah.

### C12 — grok-aurora-cli
**Port:** 8787
**Role:** Media generation broker CLI. Routes to 20+ providers (Grok, Venice, Fal, Gemini, Kling). Upstream of paia media pipeline via broker at port 7070.

### C13 — paia-events
**Port:** 3511
**Role:** Event bus infrastructure. paia-events push → paia-triage → agent intake. Foundation of the agent autonomy pipeline.

### C14 — paia-n8n
**Port:** 5678
**Role:** n8n integration configuration and workflow management for paia. Telegram integration, Gmail/GCal adapters, webhook coordination.

### C15 — paia-agent-runtime
**Role:** Shared agent runtime library. The base classes, health endpoints, task intake patterns used by samantha, derek, ingrid, caroline, and other paia agents.

### C16 — paia-os-wt-evidence-ledger-monitoring
**Role:** Worktree for evidence ledger monitoring work. Tracks and monitors the evidence quality pipeline within paia-os.

### C17 — driftdriver
**Role:** The speedrift ecosystem orchestrator. Houses the ecosystem hub (port 8777), all drift lanes, northstardrift, governancedrift, factorydrift, and the workgraph coordinator/executor infrastructure. Meta: this repo's north star IS the speedrift north star.
