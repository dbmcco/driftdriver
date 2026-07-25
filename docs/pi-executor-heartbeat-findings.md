# Pi Executor Heartbeat Findings

Date: 2026-07-25

## Conclusion

The respawn loop is caused by the pi executor running `pi -p` in the default text mode. The generated Workgraph wrapper already captures the `pi` executor's stdout into `raw_stream.jsonl`, and its code explicitly assumes `pi --mode json`. Text mode emits no stdout until the run completes, so `raw_stream.jsonl` remains empty during a long task. Workgraph's zero-output sweep kills the current agent after five minutes and the dispatcher respawns the task.

This is severe degradation, not a guaranteed hard failure: the affected task eventually completed and squash-merged to main as commit `de57bae` after approximately 35 minutes and five restarts. The defect causes repeated restarts, total context loss per restart, approximately five times the wasted tokens, and non-deterministic completion.

The contained fix is to add `--mode json` to the pi executor invocation. The existing wrapper and Workgraph pi parsing already support that stream format. The orphaned-child-process behavior is a separate issue and is not fixed here.

## 1. What determines liveness and what writes `raw_stream.jsonl`?

There are two related liveness paths in the Workgraph source.

### Registry heartbeat and dead-agent reaper

`src/commands/heartbeat.rs` implements `wg heartbeat watch`. The generated wrapper starts this watcher with a guard pipe. On each interval, `refresh_supervised_heartbeat` verifies the registered wrapper PID and process identity, then updates the registry's `last_heartbeat`. The watcher is tied to wrapper lifetime through the guard pipe; it is not driven by stdout byte arrival.

`src/commands/service/triage.rs` contains `detect_dead_reason` and `cleanup_dead_agents`. The checks are:

1. The registered PID is alive.
2. The PID still identifies the original process (PID-reuse protection).
3. If the registry heartbeat is stale, a recent stream event can be used as a positive liveness signal. `check_stream_liveness` reads `stream.jsonl` first and `raw_stream.jsonl` second. A stream event newer than the heartbeat and within the timeout window prevents `HeartbeatTimeout`.

The generated wrapper is built in `src/commands/spawn/execution.rs`. For executor type `"pi"`, lines around 2178-2194 construct:

```bash
{timed_command} > >(tee -a {raw_stream_path} >> "$OUTPUT_FILE") 2>> "$OUTPUT_FILE"
```

The wrapper therefore tees pi stdout incrementally to `raw_stream.jsonl` and to `output.log`. After pi exits, it runs `wg pi-stream-bridge`, which reads the raw NDJSON and creates the canonical `stream.jsonl` plus the session summary. The wrapper comment explicitly says: `Pi (pi --mode json) emits NDJSON on stdout.`

### Zero-output sweep

The respawn cadence in the incident is explained by `src/commands/service/zero_output.rs`, not by raw-stream byte arrival being the only heartbeat mechanism. `sweep_zero_output_agents` calls `check_zero_output` during the coordinator tick. `check_zero_output` considers an agent to have output only when `raw_stream.jsonl` or `stream.jsonl` exists and has more than zero bytes (`file_has_content`). If both are empty/missing, the agent has been alive for at least `ZERO_OUTPUT_KILL_THRESHOLD`, and it has no active child processes, the sweep kills it and the task is respawned.

`ZERO_OUTPUT_KILL_THRESHOLD` is:

```rust
const ZERO_OUTPUT_KILL_THRESHOLD: Duration = Duration::from_secs(5 * 60);
```

Thus the observed approximately 5-minute restart cadence is the zero-output circuit breaker crossing its threshold, plus the coordinator tick delay. In default text mode, pi is silent on stdout until completion, so the wrapper's raw stream remains empty and this sweep classifies the long-running task as a zero-output zombie.

## 2. What unit is `heartbeat_timeout = 5`?

Minutes.

Evidence:

- `src/config.rs` documents `heartbeat_timeout` as `Heartbeat timeout in minutes (for detecting dead agents)`.
- The TUI configuration surface labels it `Heartbeat timeout (min)`.
- `AgentConfig::heartbeat_timeout_secs()` converts the configured minute value to seconds.
- `src/commands/heartbeat.rs::run_check_agents` receives `threshold_minutes` and multiplies it by 60.

Therefore `.workgraph/config.toml` value `heartbeat_timeout = 5` means 5 minutes (300 seconds). The separate zero-output kill threshold is also 300 seconds.

## 3. Pi JSON schema and probe

The pi handler/parser expects pi's native NDJSON stream, not a Claude `result` object. `src/graph.rs::extract_pi_token_usage` scans for one `type: "turn_end"` event per turn, then reads `message.usage`. It expects pi's fields:

```json
{
  "input": 25098,
  "output": 17,
  "cacheRead": 22400,
  "cacheWrite": 0,
  "totalTokens": 47515,
  "cost": {
    "input": 0,
    "output": 0,
    "cacheRead": 0,
    "cacheWrite": 0,
    "total": 0
  }
}
```

The actual probe was run in `/tmp/pi-heartbeat-probe`:

```bash
pi -p --mode json --provider zai --model glm-5.2 "say hi and stop"
```

Result: exit code 0, 28 NDJSON lines, 21,764 bytes. The stream included:

- `session`
- `agent_start`
- `turn_start`
- `message_start`
- incremental `message_update` events (`thinking_delta`, `text_delta`, `text_start`, `text_end`, `thinking_end`)
- `message_end`
- `turn_end` with `message.usage`
- `agent_end`
- `agent_settled`

The probe's final `turn_end` had the expected `message.usage` shape, and output was streamed as NDJSON rather than held until completion. This matches the Workgraph parser and the wrapper's intended capture path. Adding `--mode json` is therefore sufficient; no translation layer or Workgraph handler change is needed.

## 4. Reaper and orphaned pi processes

`reaper_grace_seconds = 30` is the grace period used by `detect_dead_reason` before acting on a missing PID. It prevents startup races; it is not an orphan-process cleanup timeout.

The dead-agent cleanup path marks the registry agent dead and unclaims or triages its task. The zero-output path separately kills the registered agent PID and resets/respawns the task. The evidence that a pi child survived task release indicates a separate process-group/orphan cleanup defect: killing or releasing the registered wrapper does not reliably terminate a surviving pi descendant. The reaper grace setting does not solve that problem. This assignment records the issue but does not modify reaper behavior.

## Broader Speedrift ecosystem and backup check

The GitHub repository is `https://github.com/dbmcco/speedrift-ecosystem`. Its archived checkout at `/Volumes/backup/projects/experiments/speedrift-ecosystem` documents PI as an optional Workgraph/Speedrift integration and requires fake plus credentialed smoke evidence before promotion. Its `.workgraph/executors/pi-run.sh` is an older text-mode copy without `--mode json` (md5 `8916cef4ac7091a4565631de5e995295`). The archived Driftdriver template at `/Volumes/backup/projects/experiments/driftdriver/driftdriver/templates/executors/pi-run.sh` is the same older copy. Those backup files were inspected but not modified.

The active Driftdriver template is `/Users/braydon/projects/experiments/driftdriver/driftdriver/templates/executors/pi-run.sh` (md5 before fix `e6eb8ea65ed94200b14609b33e3cc35d`). The required divergent active copy is `/Users/braydon/projects/experiments/paia-agent-runtime/.workgraph/executors/pi-run.sh`. It is gitignored and untracked in that repository, so the mode fix there is not a version-controlled deliverable; its task state, source, and harness-parity branch were not touched. The coordinator reports three pre-existing failures in `tests/test_wg_resolve_pi_model.py` at commit `b843af2` (`test_pi_run_sh_bridges_gpt55_to_openai`, `test_pi_run_sh_openai_codex_alias_resolves`, and `test_pi_run_sh_keeps_zai_working`). Those failures are not regressions from this assignment and were not changed or used as a validation gate.

## Step 3 acceptance evidence

The successful real dispatch ran in the isolated scratch repository `/tmp/pi-heartbeat-wg-smoke3.cgG7Dq` with the fixed template copied to `.wg/executors/pi-run.sh`, production liveness settings (`heartbeat_timeout = 5`, `reaper_grace_seconds = 30`), and automatic assignment/evaluation disabled so the target task went directly to the pi executor.

Task: `pi-executor-heartbeat-smoke-20260725`

- One Workgraph agent, `agent-1`, started at `16:05:42Z` and completed at `16:18:14Z`.
- The task created `smoke-started.txt`, then performed a real `sleep 510` from `16:07:22Z` through `16:15:52Z`, created `smoke-complete.txt` with exact bytes `done`, and marked itself `done`.
- At 5m, 6m, 7m, 8m, 9m, and 10m there was still exactly one active agent and no replacement agent.
- `raw_stream.jsonl` was 49,914 bytes shortly after startup, 4,854,040 bytes at the 5-minute checkpoint, and 9,984,297 bytes at completion.
- Exactly one Pi session file existed for the agent: `~/.pi/agent/sessions/--private-tmp-pi-heartbeat-wg-smoke3.cgG7Dq--/2026-07-25T16-05-43-548Z_019f9a06-79fc-7698-88a8-1d14fe6e3b97.jsonl`.
- Final Workgraph status was `done`, with zero active agents and one completed task. The scratch daemon was stopped after evidence capture.

A first scratch attempt did not reach the target executor because its copied production config had `agency.auto_assign = true` but no work-agent definitions; Workgraph repeatedly respawned the assignment scaffold and blocked the task. That isolated configuration failure was corrected before the successful acceptance run and did not involve the Driftdriver change.
