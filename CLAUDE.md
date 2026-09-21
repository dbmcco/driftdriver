# CLAUDE.md

<!-- driftdriver-claude:start -->
## Speedrift Ecosystem

**Speedrift** is the development quality system across this workspace. It combines
[Workgraph](https://github.com/graphwork/workgraph) (task spine) with
[Driftdriver](https://github.com/dbmcco/driftdriver) (drift orchestrator) to keep
code, specs, and intent in sync without hard-blocking work.

Use `/speedrift` (or `/rifts`) to invoke the full protocol skill.

### Quick Reference

```bash
# Drift-check a task (run at start + before completion)
./.workgraph/drifts check --task <id> --write-log --create-followups

# Ecosystem dashboard (40+ repos, pressure scores, action queue)
# Local:     http://127.0.0.1:8777/
# Tailscale: http://100.77.214.44:8777/

# Create tasks with current wg flags (add creates a draft; publish activates it)
wg add "Title" --after <dep-id> -d "## Validation
- [ ] test command passes"
wg publish <task-id> --only

# Attractor loop — check convergence status or run convergence
driftdriver attractor status --json
driftdriver attractor run --json
```

### Runtime Authority
- Workgraph is the task/dependency source of truth. `speedriftd` is the repo-local supervisor.
- Sessions default to `observe`. Do not use `wg service start` as a generic kickoff.
- Refresh state: `driftdriver --dir "$PWD" --json speedriftd status --refresh`
- Arm repo: `driftdriver --dir "$PWD" speedriftd status --set-mode supervise --lease-owner <agent> --reason "reason"`
- Disarm: `driftdriver --dir "$PWD" speedriftd status --set-mode observe --release-lease --reason "done"`

### Attractor Loop (Convergence Engine)
- Each repo declares a target attractor in `drift-policy.toml`: `onboarded` → `production-ready` → `hardened`
- The loop runs diagnose → plan → execute → re-diagnose until convergence or circuit breaker
- Circuit breakers: max 3 passes, plateau detection (2 consecutive no-improvement), task budget cap (30)
- Bundles (reusable fix templates) are matched to findings automatically; unmatched findings escalate
- Check status: `driftdriver attractor status --json`
- Run convergence: `driftdriver attractor run --json`

### What Happens Automatically
- **Drift task guard**: follow-up tasks are deduped + capped at 3 per lane per repo
- **Attractor convergence**: repos are driven toward their declared target state via the attractor loop
- **Notifications**: significant findings alert via terminal/webhook/wg-notify
- **Prompt evolution**: recurring drift patterns trigger `wg evolve` to teach agents
- **Outcome learning**: resolution rates feed back into notification significance scoring
<!-- driftdriver-claude:end -->

## Planner Consolidation Rules

The planner surfaces were consolidated onto `driftdriver/planner_core.py` in
August 2026 (one canonical node schema, prompt builder, parser, materializer,
and route policy; `driftdriver plan` is the live CLI surface; PlanForge at the
skill layer materializes through the same core). Three rules keep that
consolidation from leaking value again:

1. **Content inventory before deletion.** Before retiring any planner or
   prompt surface, enumerate its behavioral elements line by line (prompt
   sections, guidance, defaults, post-steps) and mark each migrated,
   deliberately dropped with a reason, or missed. The missed bucket must be
   empty before the delete commit lands. Scanning code callers is not enough —
   prompt text carries behavior too.
2. **Adoption proof after consolidation.** A consolidation is done when the
   canonical artifact has at least one production caller AND the old surface
   delegates or is deleted. An orphaned canonical module is an unfinished
   consolidation, not a finished one.
3. **Prompt changes are eval-gated.** Any edit to planner prompt text
   (`planner_core.build_decompose_prompt`, `quality_planner`, the PlanForge
   persona) must rerun `uv run python scripts/eval_planner_prompts.py` and
   compare against `scripts/eval_baseline_*.json` (structural metrics:
   parse rate, dangling edges, verify/wg-contract/touch/route coverage,
   pattern adoption, premium discipline, run-to-run similarity). Include the
   delta in the commit message or PR notes.
