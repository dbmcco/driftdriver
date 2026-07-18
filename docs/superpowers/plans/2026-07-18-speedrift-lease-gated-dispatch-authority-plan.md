# Speedrift Lease-Gated Dispatch Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Driftdriver-owned dispatch and Workgraph service-start path fail closed without an active elevated lease, and stop an already-running coordinator exactly once when that lease expires.

**Architecture:** Add one pure `dispatch_authority` decision in `speedriftd_state.py`, then make autopilot, factory, ecosystem-hub service starts, and runtime expiry handling consume it. Keep the authority decision side-effect-free; keep coordinator stop/revocation in the runtime layer; preserve the already-shipped Pi compatibility and runtime-gate behavior.

**Tech Stack:** Python 3.12, pytest, existing Driftdriver control-state JSON, `ExecutorShim`, Workgraph CLI, Speedrift drift checks.

## Global Constraints

- Use current Driftdriver `main` at `4232313`; do not revive or rebase the historical `speedrift-maintenance-20260716` worktree.
- Do not modify State System, `paia-contracts`, `paia-agent-runtime`, `paia-os`, PlanForge, Workgraph upstream, or Pi persona repositories.
- Workgraph task/state is authoritative; create/use task `speedrift.lease-gated-dispatch-authority` for drift checks and progress.
- Run the pre-task and post-task drift checks exactly as specified in the design spec.
- Keep one implementation writer in the Driftdriver repository; reviewers are read-only.
- Preserve dry-run behavior, current Pi provider-qualified model propagation, fallback receipts, observe-first defaults, and lease-owner/reason requirements.
- Do not arm autonomous or supervise mode, send external messages, use live credentials, or operate an external Workgraph branch for validation.

---

## Repository map and interfaces

The current implementation surfaces are:

- `driftdriver/speedriftd_state.py`: `_normalize_control_state`, `_apply_runtime_gate`, `load_control_state`, `write_control_state`, and runtime JSON paths.
- `driftdriver/project_autopilot.py`: `dispatch_task(task, project_dir, scripts_dir, run)` and `run_autopilot_loop(run)`.
- `driftdriver/factorydrift.py`: `_dispatch_ready_workers(repo_path, cfg)`.
- `driftdriver/ecosystem_hub/snapshot.py`: `supervise_repo_services(...)`, including both `ExecutorShim` and direct `wg service start` paths.
- `driftdriver/ecosystem_hub/api.py`: `POST /api/repo/<name>/start` and `POST /api/repo/<name>/service/workgraph/start` routes.
- `driftdriver/speedriftd.py`: `collect_runtime_snapshot`, `run_runtime_cycle`, and `run_runtime_loop`.

The phase task must not introduce a second authority policy in any caller.

---

### Task 1: Define the pure dispatch-authority decision

**Files:**
- Modify: `driftdriver/speedriftd_state.py` near `_apply_runtime_gate`
- Test: `tests/test_speedriftd_state.py` in the existing control-state test classes

**Interfaces:**
- Consumes: the normalized control mapping containing `mode`, `lease_owner`, `lease_active`, and `dispatch_enabled`.
- Produces: `dispatch_authority(control: Mapping[str, Any]) -> dict[str, Any]` with keys `enabled: bool`, `mode: str`, `lease_active: bool`, and `reason: str`.

- [ ] **Step 1: Add failing tests for all authority states.**

Add tests with the existing `_default_control`/normalization helpers. Add `from unittest.mock import patch` to the test module. Use the exact expected result shape:

```python
def test_dispatch_authority_denies_observe() -> None:
    control = {"mode": "observe", "lease_owner": "", "lease_active": False}
    result = dispatch_authority(control)
    assert result == {
        "enabled": False,
        "mode": "observe",
        "lease_active": False,
        "reason": "mode does not permit dispatch",
    }


def test_dispatch_authority_allows_active_supervise_lease() -> None:
    control = {"mode": "supervise", "lease_owner": "agent-a", "lease_active": True}
    result = dispatch_authority(control)
    assert result["enabled"] is True
    assert result["reason"] == "active lease permits dispatch"


def test_dispatch_authority_denies_expired_lease() -> None:
    control = {"mode": "autonomous", "lease_owner": "agent-a", "lease_active": False}
    result = dispatch_authority(control)
    assert result["enabled"] is False
    assert result["reason"] == "lease is not active"


def test_dispatch_authority_denies_missing_owner_and_malformed_mode() -> None:
    assert dispatch_authority({"mode": "supervise", "lease_owner": "", "lease_active": True})["reason"] == "lease owner is missing"
    assert dispatch_authority({"mode": "broken", "lease_owner": "agent-a", "lease_active": True})["reason"] == "mode does not permit dispatch"
```

- [ ] **Step 2: Run the focused tests and confirm failure.**

Run:

```bash
cd /Users/braydon/projects/experiments/driftdriver
uv run pytest -q tests/test_speedriftd_state.py -k dispatch_authority
```

Expected: collection or assertion failure because `dispatch_authority` does not yet exist.

- [ ] **Step 3: Implement the pure decision and route runtime normalization through it.**

Add `Mapping` to the typing imports and implement the smallest side-effect-free helper:

```python
def dispatch_authority(control: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(control.get("mode") or "observe").strip().lower()
    lease_owner = str(control.get("lease_owner") or "").strip()
    lease_active = bool(control.get("lease_active"))
    if mode not in {"supervise", "autonomous"}:
        reason = "mode does not permit dispatch"
        enabled = False
    elif not lease_owner:
        reason = "lease owner is missing"
        enabled = False
    elif not lease_active:
        reason = "lease is not active"
        enabled = False
    else:
        reason = "active lease permits dispatch"
        enabled = True
    return {
        "enabled": enabled,
        "mode": mode,
        "lease_active": lease_active,
        "reason": reason,
    }
```

Change `_apply_runtime_gate` to set `dispatch_enabled` and `interactive_service_start` from `dispatch_authority(control)["enabled"]`. Do not mutate lease fields or acquire/release a lease in the helper.

- [ ] **Step 4: Run the state tests and the existing runtime-gate tests.**

Run:

```bash
uv run pytest -q tests/test_speedriftd_state.py tests/test_speedriftd_runtime_gate.py
```

Expected: all tests pass, including existing mode/lease normalization behavior.

- [ ] **Step 5: Commit the pure authority boundary.**

```bash
git add driftdriver/speedriftd_state.py tests/test_speedriftd_state.py
git commit -m "feat: define lease-aware dispatch authority"
```

---

### Task 2: Gate autopilot and factory admission before side effects

**Files:**
- Modify: `driftdriver/project_autopilot.py` in `dispatch_task` and `run_autopilot_loop`
- Modify: `driftdriver/factorydrift.py` in `_dispatch_ready_workers`
- Test: `tests/test_project_autopilot.py`
- Test: `tests/test_autopilot_federation.py` where peer dispatch is covered
- Test: `tests/test_factorydrift.py`

**Interfaces:**
- Consumes: `load_control_state(project_dir)` and `dispatch_authority(control)` from Task 1.
- Produces: a deterministic denied `WorkerContext` from `dispatch_task`, and a deterministic `{ok: False, status: "blocked", reason: ...}` result from `_dispatch_ready_workers`; denied paths must not call `ExecutorShim.execute`, `launch_worker`, `claude`, or `wg claim`.

- [ ] **Step 1: Add failing no-side-effect tests.**

Patch the existing command/worker mocks and add tests equivalent to:

```python
def test_dispatch_task_denies_without_active_lease(tmp_path, monkeypatch):
    run = AutopilotRun(config=AutopilotConfig(project_dir=tmp_path))
    task = {"id": "task-1", "title": "Example"}
    monkeypatch.setattr("driftdriver.project_autopilot.load_control_state", lambda _: {
        "mode": "observe", "lease_owner": "", "lease_active": False,
    })
    with patch("driftdriver.project_autopilot.DirectiveLog.append") as claim:
        result = dispatch_task(task, tmp_path, None, run)
    assert result.status == "blocked"
    assert result.response == "mode does not permit dispatch"
    claim.assert_not_called()


def test_factory_dispatch_denies_before_ready_tasks_or_worker_launch(tmp_path, monkeypatch):
    monkeypatch.setattr("driftdriver.factorydrift.load_control_state", lambda _: {
        "mode": "autonomous", "lease_owner": "agent-a", "lease_active": False,
    })
    with patch("driftdriver.project_autopilot.get_ready_tasks") as ready:
        result = _dispatch_ready_workers(repo_path=tmp_path, cfg={"max_dispatch_per_repo": 2})
    assert result["ok"] is False
    assert result["status"] == "blocked"
    assert result["reason"] == "lease is not active"
    ready.assert_not_called()
```

Also cover active leases preserving the existing claim/worker path and dry-run preserving its current no-side-effect behavior.

- [ ] **Step 2: Run the new tests and confirm failure.**

```bash
uv run pytest -q tests/test_project_autopilot.py tests/test_autopilot_federation.py tests/test_factorydrift.py -k 'lease or dispatch or dry_run'
```

Expected: failures because current paths do not use the shared authority result.

- [ ] **Step 3: Gate direct task dispatch before constructing or executing the claim directive.**

At the beginning of `dispatch_task`, load the current control state and evaluate `dispatch_authority`. If denied, return a `WorkerContext` with `status="blocked"`, `response` set to the stable reason, and do not construct `DirectiveLog`, `ExecutorShim`, or launch a worker. In `run_autopilot_loop`, evaluate the same authority before reading actionable work and again immediately before each local batch; if denied, stop the loop without adding a task to `completed_tasks`.

Keep the existing `dry_run` branch unchanged except that it must not be treated as evidence of an authorized dispatch.

- [ ] **Step 4: Gate factory dispatch before ready-task selection.**

At the beginning of `_dispatch_ready_workers`, load the control state and evaluate `dispatch_authority`. Return the exact blocked result before discovering the session driver or calling `get_ready_tasks`:

```python
{
    "ok": False,
    "status": "blocked",
    "reason": authority["reason"],
    "using_session_driver": False,
    "attempted": 0,
    "ready_seen": 0,
    "dispatched": [],
    "failed": [],
    "escalated": [],
}
```

- [ ] **Step 5: Run focused admission tests.**

```bash
uv run pytest -q tests/test_project_autopilot.py tests/test_autopilot_federation.py tests/test_factorydrift.py
```

Expected: PASS with no claim, worker launch, or completion mutation in denied cases.

- [ ] **Step 6: Commit the admission gates.**

```bash
git add driftdriver/project_autopilot.py driftdriver/factorydrift.py tests/test_project_autopilot.py tests/test_autopilot_federation.py tests/test_factorydrift.py
git commit -m "feat: fail closed before autopilot dispatch"
```

---

### Task 3: Gate ecosystem-hub service starts

**Files:**
- Modify: `driftdriver/ecosystem_hub/snapshot.py` in `supervise_repo_services`
- Modify: `driftdriver/ecosystem_hub/api.py` in both Workgraph start routes
- Test: `tests/test_ecosystem_hub.py`

**Interfaces:**
- Consumes: `load_control_state(repo_path)` and `dispatch_authority(control)`.
- Produces: no service-start side effect for denied authority; API responses use HTTP 409 with a stable `{error: "dispatch_not_authorized", reason: ...}` payload; supervisor rows record a deterministic skip reason.

- [ ] **Step 1: Add failing service-start denial tests.**

Cover both API aliases and the snapshot supervisor with mocks around `subprocess.run` and `ExecutorShim.execute`. Add `from unittest.mock import patch` to the test module:

```python
def test_supervise_repo_services_skips_expired_lease(tmp_path, monkeypatch):
    row = {
        "name": "demo", "path": str(tmp_path), "exists": True,
        "workgraph_exists": True, "service_running": False,
        "ready": [{"id": "task-1"}], "in_progress": [],
    }
    monkeypatch.setattr("driftdriver.speedriftd_state.load_control_state", lambda _: {
        "mode": "supervise", "lease_owner": "agent-a", "lease_active": False,
    })
    with patch("driftdriver.ecosystem_hub.snapshot._run_cmd") as run:
        result = supervise_repo_services(repos_payload=[row], cooldown_seconds=0, max_starts=1)
    assert result["attempted"] == 0
    assert result["restart_candidates"] == 0
    run.assert_not_called()
```

Add route tests using the existing handler/server fixture; assert HTTP 409, stable reason, and no subprocess call for observe/expired control state. Add an active-lease test that preserves the current successful start behavior.

- [ ] **Step 2: Run the tests and confirm failure.**

```bash
uv run pytest -q tests/test_ecosystem_hub.py -k 'service or start or lease'
```

Expected: current supervisor/API code attempts service start based on mode or route alone.

- [ ] **Step 3: Apply the shared authority to `supervise_repo_services`.**

Replace the mode-only check with `dispatch_authority(load_control_state(repo_path))`. If denied, do not increment attempt counters, update cooldown state, construct directives, or call `wg service start`. Preserve existing behavior for active leases.

- [ ] **Step 4: Apply the shared authority to both API start aliases.**

After resolving and validating `repo_path`/`.workgraph`, load control state and return before `subprocess.run` when authority is denied:

```python
self._send_json(
    {"error": "dispatch_not_authorized", "repo": repo_name, "reason": authority["reason"]},
    status=HTTPStatus.CONFLICT,
)
return
```

Do not gate the explicit stop route in this task; stop is the safety action used by expiry handling.

- [ ] **Step 5: Run ecosystem-hub tests.**

```bash
uv run pytest -q tests/test_ecosystem_hub.py tests/test_repo_detail_api.py tests/test_repo_detail_smoke.py
```

Expected: PASS, with denied start paths producing no subprocess/directive side effect.

- [ ] **Step 6: Commit the service-start gates.**

```bash
git add driftdriver/ecosystem_hub/api.py driftdriver/ecosystem_hub/snapshot.py tests/test_ecosystem_hub.py
git commit -m "feat: gate ecosystem service starts by lease"
```

---

### Task 4: Stop the coordinator on lease-expiry transition

**Files:**
- Modify: `driftdriver/speedriftd.py` in `run_runtime_cycle` and a new private stop helper
- Test: `tests/test_speedriftd.py`
- Test: `tests/test_speedriftd_runtime_gate.py`

**Interfaces:**
- Consumes: the previous runtime snapshot, the newly collected control state, and the repository `.workgraph` path.
- Produces: an idempotent terminal expiry event and exactly one `wg service stop` attempt when the previous state was active and the current state is denied because the lease expired.

- [ ] **Step 1: Add failing transition tests.**

Use the existing temporary-repository helpers and patch `_run_cmd` or the new stop helper. Add tests for one stop, repeated cycles, non-expiry observe/manual transitions, and stop failure being recorded rather than hidden:

```python
def test_run_runtime_cycle_stops_coordinator_once_when_lease_expires(tmp_path, monkeypatch):
    _write_control(tmp_path, mode="supervise", lease_owner="agent-a", lease_ttl_seconds=0)
    write_runtime_snapshot(tmp_path, {"repo": tmp_path.name, "control": {
        "mode": "supervise", "lease_owner": "agent-a", "lease_active": True,
    }, "active_workers": []})
    calls = []
    monkeypatch.setattr(speedriftd, "_stop_workgraph_service", lambda repo: calls.append(repo) or {"ok": True})
    _expire_control(tmp_path)
    run_runtime_cycle(tmp_path)
    run_runtime_cycle(tmp_path)
    assert calls == [tmp_path]
```

The exact fixture helper should use the repository's existing JSON writers rather than sleeping in real time; control the timestamps directly.

- [ ] **Step 2: Run the tests and confirm failure.**

```bash
uv run pytest -q tests/test_speedriftd.py tests/test_speedriftd_runtime_gate.py -k 'expiry or stop or revoke'
```

Expected: the current runtime cycle writes snapshots but never invokes a coordinator stop.

- [ ] **Step 3: Implement an idempotent stop helper.**

Add a private helper in `speedriftd.py` that runs the local Workgraph stop command through the existing command execution convention, captures return code/stdout/stderr, and returns a redacted result. Do not start a service, acquire a lease, or alter mode. The helper must be patchable in tests.

In `run_runtime_cycle`, load the previous snapshot before collecting the new snapshot. Detect only this transition:

```python
previous_control.get("lease_active") is True
and current_control.get("lease_active") is False
and current_control.get("mode") in {"supervise", "autonomous"}
```

Call the helper once, record a terminal expiry-stop event in the runtime result/ledger, and persist a marker that prevents duplicate stops on later cycles. Preserve normal snapshot writing even when stop fails; include the failure as explicit evidence.

- [ ] **Step 4: Run runtime tests.**

```bash
uv run pytest -q tests/test_speedriftd.py tests/test_speedriftd_runtime_gate.py
```

Expected: PASS; one stop attempt on expiry, no duplicate stop, no stop for non-expiry transitions, and explicit failure evidence.

- [ ] **Step 5: Commit expiry handling.**

```bash
git add driftdriver/speedriftd.py tests/test_speedriftd.py tests/test_speedriftd_runtime_gate.py
git commit -m "feat: stop coordinator when lease expires"
```

---

### Task 5: Audit all dispatch bypasses and run the phase gate

**Files:**
- Modify only if a discovered call site is inside the approved touch set.
- Test: existing affected test files from Tasks 1–4.
- Documentation: add a bounded follow-up task/log for every out-of-scope bypass; do not silently expand this phase.

- [ ] **Step 1: Search every raw admission side effect.**

Run:

```bash
cd /Users/braydon/projects/experiments/driftdriver
rg -n 'wg.*(service start|spawn|claim)|ExecutorShim|launch_worker|subprocess\.run|run_command' driftdriver tests
```

Classify each match as:

- gated by `dispatch_authority` before the side effect;
- a safety stop/diagnostic path intentionally allowed without an active lease; or
- an unhandled bypass requiring a traceable follow-up.

Do not modify State System/schema paths discovered by the search.

- [ ] **Step 2: Run the required focused suite.**

```bash
uv run pytest -q \
  tests/test_speedriftd_runtime_gate.py \
  tests/test_speedriftd_state.py \
  tests/test_project_autopilot.py \
  tests/test_autopilot_federation.py \
  tests/test_speedriftd.py \
  tests/test_ecosystem_hub.py \
  tests/test_factorydrift.py
```

Expected: all relevant tests pass. If a named file or fixture does not exist, record the exact discrepancy and run the current equivalent; do not invent a green result.

- [ ] **Step 3: Run the full supported suite.**

```bash
uv run pytest
```

Expected: full collection and pass. If collection fails on the known undeclared `anthropic` dependency, record the exact failure as a baseline exception; do not call focused green tests a full-suite pass.

- [ ] **Step 4: Run pre-handoff drift and review gates.**

```bash
./.workgraph/drifts check --task speedrift.lease-gated-dispatch-authority --write-log --create-followups
```

Request one fresh read-only adversarial review and RoboRev/review covering expiry races, duplicate stop events, denial status, raw bypass paths, and accidental State System/schema changes. Record findings as follow-up tasks rather than expanding the implementation silently.

- [ ] **Step 5: Verify the final tree and repository boundaries.**

```bash
git status --short
git diff HEAD~4 --name-only
```

Expected: only the approved Driftdriver/test files and the committed design/plan docs appear. The historical maintenance worktree, `.driftdriver/upstream-pins.toml`, adopted Workgraph checkout, State System, and schema repositories remain untouched.

- [ ] **Step 6: Commit only any bounded review/documentation updates.**

```bash
git add docs/superpowers/plans/2026-07-18-speedrift-lease-gated-dispatch-authority-plan.md
git commit -m "docs: add lease authority implementation plan"
```

Do not commit source changes from this gate task. Source changes must already be committed by Tasks 1–4 with their own focused test evidence.

---

## Review checkpoints

- After Task 1, confirm the predicate is pure and all existing runtime-gate tests remain green.
- After Tasks 2–3, perform a read-only call-site review before implementing expiry handling.
- After Task 4, inspect the expiry marker and stop failure evidence before running the full suite.
- Before completion, verify no State System/schema files changed and obtain RoboRev/review evidence.

## Rollback boundaries

Each source task is a separate commit. If expiry-stop behavior proves unsafe, revert only Task 4 while retaining the pure predicate and fail-closed admission gates. Never reset the historical maintenance worktree or alter the adopted Workgraph checkout as part of rollback.

## Deferred work

Handler-first Pi semantics, Agency default policy, the next Workgraph SHA, canonical `.workgraph`/`.wg` resolution, validation-failure completion semantics, external directive interception, and new drift-lane design remain separate decision tasks.
