# codexdriver MVP Design

## Goal

Make Codex a first-class Speedrift runtime with functional parity to the existing Claude worker path.

The MVP should satisfy the worker-driver contract without depending on tmux.

## Local CLI Surface Available

Current local Codex CLI supports:

- `codex exec`
- `codex exec --json`
- `codex exec -o <file>`
- `codex resume`

This is enough to build a bounded worker runtime.

## MVP Strategy

Use `codex exec` as the primary execution transport.

Why:

- non-interactive and bounded
- straightforward subprocess supervision
- JSON event output available
- works without a foreground interactive pane

## Worker Launch Model

`codexdriver` launches each worker as a detached subprocess:

```text
codex exec \
  --json \
  -C <repo> \
  -o <last_message_file> \
  <prompt>
```

Daemon-side wrapper responsibilities:

- generate `worker_id`
- allocate output files
- record pid and launch metadata
- start stdout/stderr capture
- append `worker_launched` event

## Prompt Delivery

For MVP, prompt delivery happens at launch time.

`send_prompt(...)` on an existing worker is only required for continuation-capable workers.

## Output Capture

Primary sources:

- stdout JSONL stream from `codex exec --json`
- final assistant text via `-o <file>`

`codexdriver` should normalize both into the shared output-event schema.

## Heartbeat Model

Codex does not need a bespoke heartbeat API if the wrapper can provide:

- subprocess alive state
- stdout/log growth
- last output timestamp

Heartbeat is therefore synthesized from:

- process liveness
- file mtimes
- latest parsed JSON event

## Continuation Plan

MVP should not assume that `codex resume` is reliable for every `exec`-created worker session.

Preferred MVP behavior:

- `resume_worker(...)` launches a fresh Codex worker
- daemon injects a continuation context pack containing:
  - task id
  - prior result summary
  - relevant evidence paths
  - explicit next-step instruction

Future optimization:

- switch to native `codex resume` where proven stable

## Result Classification

`collect_result(...)` should combine:

- process exit code
- final output file
- parsed JSON event tail

Classification rules:

- successful execution with completion language and green verification intent -> `done`
- explicit blocker / human-needed language -> `needs_human`
- timeout without fresh output -> `timed_out`
- live process with stale output beyond threshold -> `stalled`
- non-zero exit or explicit failure language -> `failed`

Final state selection must remain deterministic and auditable.

## Repo-Local Artifacts

Suggested worker layout:

- `.workgraph/service/runtime/codex/<worker_id>/launch.json`
- `.workgraph/service/runtime/codex/<worker_id>/stdout.jsonl`
- `.workgraph/service/runtime/codex/<worker_id>/last_message.txt`
- `.workgraph/service/runtime/codex/<worker_id>/result.json`

## Safety Posture

MVP uses:

- one task per worker
- bounded timeout
- bounded retry count
- no hidden interactive tmux dependency
- no assumption that a human-facing Codex chat stays open

## Explicit Non-Goals For MVP

- multi-task conversational workers
- pane-based orchestration
- native long-lived conversational resume as a hard dependency
- model-specific heuristics in the daemon

## Acceptance Tests

`codexdriver` MVP is acceptable when it can:

1. launch a task worker unattended
2. stream output into runtime events
3. surface heartbeat freshness in the dashboard
4. return normalized terminal state
5. relaunch with a continuation context pack
6. stop cleanly on timeout or policy cancel

## Phase 2 Upgrades

After MVP:

- validate whether `codex resume` can replace relaunch-based continuation
- add richer tool-call event normalization if needed
- add session reuse for faster multi-step continuation paths
