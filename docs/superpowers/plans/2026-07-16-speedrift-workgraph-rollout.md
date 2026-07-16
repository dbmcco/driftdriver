# Workgraph Update and Pi Lunaroute Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for fixture changes and superpowers:executing-plans for the ordered rollout commands. Speedrift owns the dependency gate; use fresh Pi Lunaroute reviewers for the final diff and live evidence. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the tested Workgraph integration from commit `58380883` to reviewed upstream `e0b0a047`, preserve the patched-Pi installer fix, repair known stale smoke fixtures, and prove real Pi Lunaroute execution through Workgraph and driftdriver.

**Architecture:** The Workgraph update is a separate branch and rollback unit. No Workgraph update begins until driftdriver and PlanForge are green against `58380883`. The new branch starts at exact upstream `e0b0a047`, cherry-picks installer fix `9cda81cb`, applies fixture-only corrections in a separate commit, then runs credential-free and Lunaroute credentialed validation.

**Tech Stack:** Rust 1.96.0, Cargo, Bash, Node/npm, patched Pi 0.80.6, Workgraph smoke harness, driftdriver, Pi `lunaroute/glm-5.2-nvfp4`.

## Global Constraints

- Do not modify the existing dirty `workgraph-pr-staging` working tree.
- Use exact base `e0b0a047`, not a moving `origin/main` during validation.
- Preserve installer fix `9cda81cb` as its own commit.
- Keep smoke-fixture corrections separate from product code.
- Do not change Workgraph's handler-first execution contract.
- Do not add a Speedrift fallback engine.
- Direct Pi uses `lunaroute/glm-5.2-nvfp4`; Workgraph uses `pi:lunaroute/glm-5.2-nvfp4`.
- Real live tests must verify terminal Workgraph task state, not only process exit status.
- Roll back the Workgraph branch independently if any post-update live smoke fails.

---

### Task 1: Create the exact upstream branch and reapply the Pi installer fix

**Files:**
- Modify through cherry-pick: `scripts/install-patched-pi.sh`
- No other source files.

**Interfaces:**
- Base commit: `e0b0a047`.
- Reapplied fix: `9cda81cb`.
- Produces branch: `speedrift-workgraph-e0b0a047-20260716`.

- [ ] **Step 1: Confirm prerequisite branches are green against the old base**

Require recorded passing output from:

```bash
cd /Users/braydon/projects/experiments/driftdriver/.worktrees/speedrift-maintenance-20260716
uv run python -m unittest discover -s tests
```

and from `/Users/braydon/projects/experiments/braydon-workspace-skills/.worktrees/speedrift-maintenance-20260716`:

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: both commands exit `0` while the installed Workgraph binary still corresponds to the tested `58380883` integration base.

- [ ] **Step 2: Fetch and verify exact commits**

From `/Users/braydon/projects/experiments/workgraph-pr-staging`:

```bash
git fetch origin main
git cat-file -e e0b0a047^{commit}
git cat-file -e 9cda81cb^{commit}
```

Expected: all commands exit `0`.

- [ ] **Step 3: Create a clean worktree**

```bash
git worktree add \
  -b speedrift-workgraph-e0b0a047-20260716 \
  .worktrees/speedrift-workgraph-e0b0a047-20260716 \
  e0b0a047
cd .worktrees/speedrift-workgraph-e0b0a047-20260716
git status --short
```

Expected: empty status.

- [ ] **Step 4: Cherry-pick the installer fix**

```bash
git cherry-pick 9cda81cb
git show --stat --oneline HEAD
git diff e0b0a047..HEAD -- scripts/install-patched-pi.sh
```

Expected: the only functional change installs Pi monorepo dependencies from the monorepo root:

```bash
(cd "$WORK/pi" && npm install --ignore-scripts)
```

- [ ] **Step 5: Run the installer test path**

```bash
make install-patched-pi
pi --version
wg doctor
```

Expected:

- patched Pi builds and installs;
- Pi reports version `0.80.6`;
- Workgraph doctor reports the Pi output guard as healthy.

- [ ] **Step 6: Confirm commit isolation**

```bash
git diff --name-only e0b0a047..HEAD
```

Expected output:

```text
scripts/install-patched-pi.sh
```

---

### Task 2: Repair the two known stale smoke fixtures

**Files:**
- Modify: `tests/smoke/scenarios/agency_pi_weak_tier_routes_to_pi_handler.sh`
- Modify: `tests/smoke/scenarios/pi_handler_plugin_transports.sh`

**Interfaces:**
- Preserves: handler-first Agency Pi primary and same-handler fallback assertions.
- Preserves: RPC and Node-host transport assertions.
- Removes: GNU-only `sed -i` dependence.
- Adds: the Node-host fixture's required `node_modules` directory shape.

- [ ] **Step 1: Run both fixtures and capture the known failures**

```bash
bash tests/smoke/scenarios/agency_pi_weak_tier_routes_to_pi_handler.sh
bash tests/smoke/scenarios/pi_handler_plugin_transports.sh
```

Expected before repair:

- the Agency fixture fails on macOS at GNU-style `sed -i` or on stale initialized-config assumptions;
- the transport fixture fails its fabricated Node-host topology because the fake plugin lacks the dependency directory shape expected by current plugin validation.

- [ ] **Step 2: Replace GNU-only in-place editing**

Replace:

```bash
sed -i 's|^fast = .*|fast = "pi:openai-codex:gpt-5.6-terra"|' .wg/config.toml
```

with a portable temporary-file rewrite:

```bash
python3 - <<'PY'
from pathlib import Path
path = Path('.wg/config.toml')
text = path.read_text(encoding='utf-8')
lines = []
for line in text.splitlines():
    if line.startswith('fast = '):
        lines.append('fast = "pi:openai-codex:gpt-5.6-terra"')
    else:
        lines.append(line)
path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
PY
```

Keep the existing `awk` role-model rewrite and handler/fallback assertions. If `e0b0a047` no longer emits one of the old role sections, update the fixture to assert and rewrite only sections that exist; do not fabricate deprecated configuration.

- [ ] **Step 3: Give the fake Node plugin the expected topology**

Change the fixture setup from:

```bash
mkdir -p "$plugin_dir/host" "$plugin_dir/dist"
```

into:

```bash
mkdir -p "$plugin_dir/host" "$plugin_dir/dist" "$plugin_dir/node_modules"
```

Do not run network installation in the smoke fixture. The fake host has no external imports; the directory exists only to satisfy current topology validation.

- [ ] **Step 4: Run both fixtures**

```bash
bash tests/smoke/scenarios/agency_pi_weak_tier_routes_to_pi_handler.sh
bash tests/smoke/scenarios/pi_handler_plugin_transports.sh
```

Expected:

```text
PASS: ...
PASS: wg pi-handler loads plugin transports in RPC and Node-host modes and writes replies
```

- [ ] **Step 5: Commit fixture repairs separately**

```bash
git add \
  tests/smoke/scenarios/agency_pi_weak_tier_routes_to_pi_handler.sh \
  tests/smoke/scenarios/pi_handler_plugin_transports.sh
git commit -m "test(pi): refresh handler-first smoke fixtures"
```

---

### Task 3: Build, install, and run the real Pi Lunaroute chain

**Files:**
- No product changes unless an existing upstream defect is reproduced by a named test and fixed in a separate commit.

**Interfaces:**
- Direct Pi model: `lunaroute/glm-5.2-nvfp4`.
- Workgraph execution spec: `pi:lunaroute/glm-5.2-nvfp4`.
- Success requires Workgraph task state `done`.

- [ ] **Step 1: Run the Rust and embedded-plugin gates**

```bash
cargo fmt --check
cargo test --locked
make embed-pi-plugin-check
bash tests/smoke/scenarios/pi_worker_one_shot_prompt_and_cred_error.sh
bash tests/smoke/scenarios/pi_json_closed_consumer.sh
bash tests/smoke/scenarios/pi_plugin_install_hermetic.sh
bash tests/smoke/scenarios/pi_config_lint_rejects_unsatisfied_route.sh
bash tests/smoke/scenarios/pi_runtime_delivery.sh
bash tests/smoke/scenarios/pi_handler_plugin_transports.sh
bash tests/smoke/scenarios/agency_pi_weak_tier_routes_to_pi_handler.sh
```

Expected: all commands exit `0`.

- [ ] **Step 2: Install the exact Workgraph branch**

```bash
cargo install --path . --locked
git rev-parse --short HEAD
wg doctor
```

Expected: `wg doctor` reports Pi compatibility and output guard healthy.

- [ ] **Step 3: Run a direct Pi Lunaroute marker**

```bash
pi -p \
  --model lunaroute/glm-5.2-nvfp4 \
  'Reply with exactly SPEEDRIFT_LUNAROUTE_OK'
```

Expected response contains exactly:

```text
SPEEDRIFT_LUNAROUTE_OK
```

- [ ] **Step 4: Run a real Workgraph Pi Lunaroute task**

Use a temporary repository:

```bash
export SMOKE_REPO="$(mktemp -d)"
wg --dir "$SMOKE_REPO/.workgraph" init
export TASK_ID="speedrift-lunaroute-live"
wg --dir "$SMOKE_REPO/.workgraph" add \
  --id "$TASK_ID" \
  "Return the live marker" \
  --description $'Reply with exactly WG_LUNAROUTE_OK\n\n## Validation\n- Final output contains WG_LUNAROUTE_OK' \
  --model 'pi:lunaroute/glm-5.2-nvfp4'
wg --dir "$SMOKE_REPO/.workgraph" spawn "$TASK_ID"
wg --dir "$SMOKE_REPO/.workgraph" --json show "$TASK_ID"
```

Expected JSON has task status `done`, and task output contains `WG_LUNAROUTE_OK`.

- [ ] **Step 5: Run real driftdriver Pi dispatch**

From `/Users/braydon/projects/experiments/driftdriver/.worktrees/speedrift-maintenance-20260716`, create a second task and dispatch it through the public `dispatch_task` entrypoint:

```bash
export DD_TASK_ID="speedrift-driftdriver-live"
wg --dir "$SMOKE_REPO/.workgraph" add \
  --id "$DD_TASK_ID" \
  "Return the driftdriver marker" \
  --description $'Reply with exactly DRIFTDRIVER_LUNAROUTE_OK\n\n## Validation\n- Final output contains DRIFTDRIVER_LUNAROUTE_OK' \
  --model 'pi:lunaroute/glm-5.2-nvfp4'

SMOKE_REPO="$SMOKE_REPO" DD_TASK_ID="$DD_TASK_ID" uv run python - <<'PY'
from dataclasses import asdict
import json
import os
from pathlib import Path
from driftdriver.task_router import DispatchResult, ExecutorConfig, dispatch_task

repo = Path(os.environ["SMOKE_REPO"])
task_id = os.environ["DD_TASK_ID"]
executor = ExecutorConfig(
    name="pi-live",
    type="pi",
    endpoint="",
    tag_match="",
)
result: DispatchResult = dispatch_task(
    {"id": task_id, "model": "pi:lunaroute/glm-5.2-nvfp4"},
    executor,
    repo,
)
print(json.dumps(asdict(result), sort_keys=True))
if not result.dispatched:
    raise SystemExit(result.error or "driftdriver dispatch failed")
PY

for attempt in $(seq 1 60); do
  STATUS="$(wg --dir "$SMOKE_REPO/.workgraph" --json show "$DD_TASK_ID" | python3 -c 'import json,sys; p=json.load(sys.stdin); print(p.get("status") or p.get("task",{}).get("status") or "")')"
  [ "$STATUS" = "done" ] && break
  sleep 2
done
[ "$STATUS" = "done" ]
wg --dir "$SMOKE_REPO/.workgraph" show "$DD_TASK_ID"
```

Expected:

- dispatch succeeds;
- the task model remains `pi:lunaroute/glm-5.2-nvfp4`;
- the Workgraph task reaches `done`;
- output contains `DRIFTDRIVER_LUNAROUTE_OK`.

- [ ] **Step 6: Re-run Speedrift maintenance suites against the installed update**

```bash
cd /Users/braydon/projects/experiments/driftdriver/.worktrees/speedrift-maintenance-20260716
uv run python -m unittest \
  tests.test_workgraph \
  tests.test_graph_dir_cli \
  tests.test_task_router \
  tests.test_install_ordering \
  tests.test_planforge_agency \
  tests.test_speedriftd \
  tests.test_speedriftd_state \
  tests.test_install_templates -v
```

Then run the PlanForge test file from `/Users/braydon/projects/experiments/braydon-workspace-skills/.worktrees/speedrift-maintenance-20260716`:

```bash
uv run --with jsonschema python skills/planforge/tests/test_planforge_v2.py
```

Expected: PASS.

- [ ] **Step 7: Request Pi Lunaroute final reviews**

One reviewer checks exact spec compliance and rollback separation. A different reviewer checks fixture portability, build evidence, and live task-state evidence.

- [ ] **Step 8: Push the Workgraph branch**

Push the exact branch to the existing writable `fork` remote:

```bash
git push -u fork speedrift-workgraph-e0b0a047-20260716
```

Expected: the branch containing the installer fix and fixture commit is present in `/Users/braydon/projects/experiments/workgraph-fork-archive`, and the original dirty Workgraph working tree remains unchanged.

## Rollback

If a post-update smoke fails:

1. Reinstall the last known-good Workgraph build based on `58380883`.
2. Leave the driftdriver and PlanForge maintenance commits intact; they were proven against the old base.
3. Record the exact failing command, exit status, Workgraph task state, and log path.
4. Open a traceable follow-up for the Workgraph-specific defect.
