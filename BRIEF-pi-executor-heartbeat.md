# BRIEF: pi executor never streams output → wg respawns it every ~5 minutes

You are fixing a Speedrift/Workgraph infrastructure defect. This brief is self-contained.
Coordinator pane id is `wP:p3` — you will report back to it at the end.

## Confirmed evidence (do not re-derive; verify only where marked VERIFY)

On 2026-07-25 a task was dispatched in `/Users/braydon/projects/experiments/paia-agent-runtime`
via the `pi` executor (`executor=pi, model=pi:zai:glm-5.2`). Observed:

- Agent `agent-5692` started 15:07:56. pi produced a correct 493-line implementation plus a
  558-line test file within roughly the first 5 minutes.
- pi was then restarted 5 times. Session files in
  `~/.pi/agent/sessions/--Users-braydon-projects-experiments-paia-agent-runtime-.wg-worktrees-agent-5692--/`
  are stamped 15:07:58, 15:13:07, 15:18:17, 15:23:33, 15:28:48 — intervals of 5m09s, 5m10s,
  5m16s, 5m15s. Each restart is a FRESH pi session with no memory of the previous one.
- Sessions 2-5 made zero edits; they re-read the codebase and thrashed on `bash` calls.
- `.workgraph/agents/agent-5692/raw_stream.jsonl` was **0 bytes** for the entire 20 minutes.
- `.workgraph/agents/agent-5692/output.log` contained only unrelated model-catalog warnings.
- The task was eventually released back to `ready` with an orphaned pi process still running.

## Root-cause hypothesis (VERIFY before fixing)

`.workgraph/executors/pi-run.sh` ends with:

```bash
"$PI_BIN" -p "${MODEL_ARGS[@]}" "${THINKING_ARGS[@]}" "$PROMPT"
```

There is no `--mode json`. `pi -p` defaults to **text mode**, which emits nothing to stdout
until the run completes. wg tees that stdout into a file named `raw_stream.jsonl` and appears
to use it as the agent liveness heartbeat. With `[agent] heartbeat_timeout = 5` in
`.workgraph/config.toml`, wg concludes the agent is dead and respawns it — forever.

Compare the sibling executor `.workgraph/executors/codex-run.sh`, whose header states it runs
`codex exec --json`. The pi executor never got the equivalent flag.

`pi --help` documents: `--mode <mode>   Output mode: text (default), json, or rpc`.

## Blast radius — this is NOT a paia problem

`driftdriver/templates/executors/pi-run.sh` is the source template. It is replicated by
identical md5 (`e6eb8ea65ed94200b14609b33e3cc35d`) across 20+ repos under
`/Users/braydon/projects/experiments/*/.workgraph/executors/pi-run.sh`.
`paia-agent-runtime` has a locally divergent copy (`1b9938ccb7edd70f193b9d31e8a88247`).

Every repo dispatching via the pi executor is affected. Any pi task exceeding ~5 minutes
cannot complete.

## YOUR ASSIGNMENT

### Step 1 — VERIFY the mechanism (do not skip; the fix depends on it)

Read the workgraph Rust source at `/Users/braydon/projects/experiments/workgraph-pr-staging`
and answer concretely:

1. What exactly does wg use to determine agent liveness / heartbeat? Find the code path that
   writes or watches `raw_stream.jsonl`. Is it byte arrival on stdout, parsed events, or a
   separate heartbeat file?
2. What is `heartbeat_timeout = 5` measured in — seconds or minutes? The observed 5-minute
   respawn cadence suggests minutes, but confirm in code.
3. Does the `pi` handler parse a specific JSON event schema? If so, what shape does it expect,
   and does `pi --mode json` actually emit that shape? Run
   `pi -p --mode json --provider zai --model glm-5.2 "say hi and stop"` in a scratch directory
   and capture the real output shape.
4. Is there a `reaper` that should have killed the orphaned pi process but didn't?
   (`reaper_grace_seconds = 30` is set.) Orphaned processes surviving task release is a second,
   separate bug — record it, do not fix it in this task.

Write your findings to `/Users/braydon/projects/experiments/driftdriver/docs/pi-executor-heartbeat-findings.md`.

### Step 2 — FIX the template

Based on Step 1, fix `driftdriver/templates/executors/pi-run.sh`.

- If `--mode json` produces the shape wg's pi handler expects: add it.
- If wg's handler expects a different shape: add a translation layer, or adjust the handler —
  whichever is smaller and better contained. State which you chose and why.
- If the real fix is elsewhere (e.g. wg should not treat stdout silence as death): say so
  explicitly and fix the right thing. Do not force the hypothesis.

Also handle the divergent `paia-agent-runtime` copy so it does not silently drift back.

### Step 3 — PROVE it with a real dispatch

This is the acceptance gate. A code change is not evidence.

Dispatch a real wg task via the pi executor that is **guaranteed to run longer than 8 minutes**
(e.g. a task whose steps include deliberate long-running work), and show that:

- pi is NOT respawned — exactly one session file appears for that agent
- `raw_stream.jsonl` is non-empty and grows during the run
- the task reaches `done` on its own

Use a scratch repo or a throwaway task. Do NOT run this against paia-agent-runtime's real
harness-parity tasks — those are paused deliberately and are not yours.

### Step 4 — STOP before mass redistribution

Do NOT push the fixed template to all 20+ repos. Report back first with the diff and the
Step 3 evidence. The coordinator gates redistribution.

## HARD RULES

- Do NOT create or modify `_session.md` or any workspace state file — those are owned by the coordinator.
- Do NOT touch `/Users/braydon/projects/experiments/paia-agent-runtime/.workgraph/` task state,
  and do NOT resume/unpause any task there. Four tasks are paused deliberately.
- Do NOT modify `src/` in paia-agent-runtime, and do not touch the branch
  `harness-parity/timeout-policy` — it holds salvaged work.
- If a step fails twice, STOP and report BLOCKED with the failing output.
- Commit your work on a branch in driftdriver; do not push to main.

## FINAL STEP — mandatory. After your last action:

```
herdr pane run wP:p3 "DONE pi-executor-heartbeat: <one-line status>"
```

If you cannot proceed:

```
herdr pane run wP:p3 "BLOCKED pi-executor-heartbeat: <one-line reason>"
```
