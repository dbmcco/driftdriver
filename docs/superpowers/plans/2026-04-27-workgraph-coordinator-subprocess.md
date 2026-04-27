# Workgraph Coordinator Subprocess Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the primary `workgraph` coordinator off the daemon-owned Claude stdio path and onto a real subprocess boundary using a coordinator-scoped `wg spawn-task` precursor plus `wg claude-handler`, while preserving the current single global chat store and honest local semantics.

**Architecture:** Keep the existing `.workgraph/chat/` storage and `.coordinator-0` identity, but split responsibilities cleanly. The daemon should supervise a coordinator subprocess, `spawn-task` should resolve the supported coordinator runtime and dispatch to a handler, and `claude-handler` should own inbox consumption, context injection, Claude stdio, and outbox/error writes. This is a coordinator-first precursor, not a fake import of upstream `chat_sessions`.

**Tech Stack:** Rust (`workgraph` CLI/service/runtime), Python (`driftdriver` upstream tracker), cargo test integration contracts, pytest tracker verification, git fork/upstream workflow.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `workgraph/src/commands/mod.rs` | Modify | Export the new coordinator runtime command modules |
| `workgraph/src/cli.rs` | Modify | Add hidden `spawn-task` and `claude-handler` command surfaces |
| `workgraph/src/main.rs` | Modify | Route the hidden commands to their handlers |
| `workgraph/src/commands/spawn_task.rs` | Create | Coordinator-first subprocess dispatch precursor |
| `workgraph/src/commands/claude_handler.rs` | Create | Claude CLI subprocess bridge over the current global chat files |
| `workgraph/src/commands/service/coordinator_agent.rs` | Modify | Convert from direct Claude protocol owner into supervisor/wakeup wrapper |
| `workgraph/src/commands/service/mod.rs` | Modify | Spawn the coordinator via `spawn-task` and stop owning inbox cursor advancement |
| `workgraph/tests/integration_coordinator_agent.rs` | Modify | Add subprocess-path, unsupported-runtime, and crash-recovery contracts |
| `driftdriver/.driftdriver/upstream-config.toml` | Modify | Add a compatibility gate for the coordinator subprocess contract |
| `driftdriver/.driftdriver/upstream-pins.toml` | Modify | Advance the adopted `workgraph` SHA after landing the tranche |
| `driftdriver/docs/superpowers/specs/2026-04-27-workgraph-coordinator-subprocess-design.md` | Modify | Update the design doc if the final landed behavior differs materially |
| `driftdriver/docs/superpowers/plans/2026-04-27-workgraph-coordinator-subprocess.md` | Create | This implementation plan |

---

### Task 1: Lock the failing coordinator subprocess contract

**Files:**
- Modify: `workgraph/tests/integration_coordinator_agent.rs`

- [ ] **Step 1: Add a dry-run contract for the coordinator `spawn-task` precursor**

Append these tests near the existing mock-based coordinator tests so they reuse `wg_cmd`, `wg_ok`, `MockClaude`, and `CoordinatorDaemonGuard`:

```rust
#[test]
fn coordinator_spawn_task_dry_run_primary() {
    let tmp = TempDir::new().unwrap();
    let wg_dir = init_workgraph(&tmp);

    let output = wg_cmd(
        &wg_dir,
        &["spawn-task", ".coordinator-0", "--role", "coordinator", "--dry-run"],
    );
    let stdout = String::from_utf8_lossy(&output.stdout);

    assert!(
        output.status.success(),
        "spawn-task dry-run should succeed once implemented.\nstdout: {}\nstderr: {}",
        stdout,
        String::from_utf8_lossy(&output.stderr),
    );
    assert!(
        stdout.contains("wg claude-handler"),
        "dry-run should preview claude-handler dispatch, got: {}",
        stdout,
    );
}

#[test]
fn coordinator_spawn_task_rejects_unsupported_executor() {
    let tmp = TempDir::new().unwrap();
    let wg_dir = init_workgraph(&tmp);
    fs::write(
        wg_dir.join("config.toml"),
        "[coordinator]\ncoordinator_agent = true\nexecutor = \"native\"\nmodel = \"openrouter:minimax/minimax-m1\"\n",
    )
    .unwrap();

    let output = wg_cmd(
        &wg_dir,
        &["spawn-task", ".coordinator-0", "--role", "coordinator", "--dry-run"],
    );
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert!(
        !output.status.success(),
        "unsupported coordinator executor should fail clearly"
    );
    assert!(
        stderr.contains("unsupported coordinator executor"),
        "stderr should explain the unsupported executor path, got: {}",
        stderr,
    );
}
```

- [ ] **Step 2: Add a subprocess-path round-trip contract**

Add a new integration test that proves the daemon is going through the handler subprocess rather than owning Claude stdio directly:

```rust
#[test]
fn coordinator_agent_uses_claude_handler_subprocess() {
    let tmp = TempDir::new().unwrap();
    let wg_dir = init_workgraph(&tmp);
    let mock = MockClaude::new();
    let guard = CoordinatorDaemonGuard::start(&wg_dir, &mock);

    let stdout = guard.chat_ok("subprocess path test", 15);
    let log = read_daemon_log(&wg_dir);

    assert!(stdout.contains("Mock coordinator response"));
    assert!(
        log.contains("spawn-task") || log.contains("claude-handler"),
        "daemon log should show subprocess-based coordinator execution.\n{}",
        log,
    );
}
```

- [ ] **Step 3: Add a subprocess crash-and-restart contract**

Rework the existing crash test so it proves handler-process restart and `system-error` surfacing, not just the old direct stdio recovery:

```rust
#[test]
fn coordinator_handler_crash_surfaces_error_and_recovers() {
    let tmp = TempDir::new().unwrap();
    let wg_dir = init_workgraph(&tmp);
    let mock = MockClaude::new_with_crash_trigger();
    let crash_file = wg_dir.join("service").join("mock_crash_trigger");
    fs::write(&crash_file, "crash").unwrap();

    let crash_file_str = crash_file.to_string_lossy().to_string();
    let guard = CoordinatorDaemonGuard::start_with_env(
        &wg_dir,
        &mock,
        &[("MOCK_CRASH_FILE", &crash_file_str)],
    );

    let first = guard.chat("trigger subprocess crash", 20);
    let first_stdout = String::from_utf8_lossy(&first.stdout);
    assert!(
        first_stdout.contains("crashed")
            || first_stdout.contains("system-error")
            || first_stdout.contains("error"),
        "crash should surface through the chat path, got: {}",
        first_stdout,
    );

    let second = guard.chat_ok("message after restart", 20);
    assert!(second.contains("Mock coordinator response"));
}
```

- [ ] **Step 4: Run the targeted coordinator integration tests and confirm they fail**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent coordinator_spawn_task_ -- --nocapture
cargo test --test integration_coordinator_agent coordinator_agent_uses_claude_handler_subprocess -- --nocapture
cargo test --test integration_coordinator_agent coordinator_handler_crash_surfaces_error_and_recovers -- --nocapture
```

Expected:
- `spawn-task` tests fail because the command does not exist yet
- subprocess-path test fails because the daemon still owns Claude stdio directly
- crash/restart assertions fail because the runtime has not been moved behind the handler boundary yet

- [ ] **Step 5: Commit the red test contract**

```bash
cd /Users/braydon/projects/experiments/workgraph
git add tests/integration_coordinator_agent.rs
git commit -m "test: add coordinator subprocess contract"
```

### Task 2: Add the hidden command surfaces and compile path

**Files:**
- Modify: `workgraph/src/commands/mod.rs`
- Modify: `workgraph/src/cli.rs`
- Modify: `workgraph/src/main.rs`
- Create: `workgraph/src/commands/spawn_task.rs`
- Create: `workgraph/src/commands/claude_handler.rs`

- [ ] **Step 1: Export the new command modules**

Add the new modules in `workgraph/src/commands/mod.rs`:

```rust
pub mod claude_handler;
pub mod spawn_task;
```

Place them with the other command exports; do not move unrelated modules.

- [ ] **Step 2: Add hidden CLI variants for the coordinator subprocess path**

In `workgraph/src/cli.rs`, add two hidden command variants:

```rust
    #[command(hide = true)]
    SpawnTask {
        /// Task or coordinator id to dispatch.
        task_id: String,

        /// Optional role override.
        #[arg(long)]
        role: Option<String>,

        /// Print the resolved handler command without executing it.
        #[arg(long)]
        dry_run: bool,
    },

    #[command(hide = true)]
    ClaudeHandler {
        /// Chat alias to service (`coordinator-0` for this tranche).
        #[arg(long)]
        chat: String,

        /// Optional model override from the daemon/runtime.
        #[arg(long, short = 'm')]
        model: Option<String>,
    },
```

- [ ] **Step 3: Route the hidden commands in `main.rs`**

Add the new match arms in `workgraph/src/main.rs`:

```rust
        Commands::SpawnTask {
            task_id,
            role,
            dry_run,
        } => commands::spawn_task::run(&workgraph_dir, &task_id, role.as_deref(), dry_run),

        Commands::ClaudeHandler { chat, model } => {
            commands::claude_handler::run(&workgraph_dir, &chat, model.as_deref())
        }
```

- [ ] **Step 4: Add compile-only stubs before real implementation**

Create minimal failing stubs so the command surfaces compile and produce explicit errors instead of silent no-ops:

```rust
// workgraph/src/commands/spawn_task.rs
use std::path::Path;
use anyhow::{bail, Result};

pub fn run(_workgraph_dir: &Path, _task_id: &str, _role_override: Option<&str>, _dry_run: bool) -> Result<()> {
    bail!("spawn-task not implemented yet")
}
```

```rust
// workgraph/src/commands/claude_handler.rs
use std::path::Path;
use anyhow::{bail, Result};

pub fn run(_workgraph_dir: &Path, _chat: &str, _model: Option<&str>) -> Result<()> {
    bail!("claude-handler not implemented yet")
}
```

- [ ] **Step 5: Re-run the dry-run coordinator tests and confirm the failure mode has tightened**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent coordinator_spawn_task_ -- --nocapture
```

Expected:
- tests now hit the new command surface
- failures should come from the explicit stub errors, not “unknown command”

- [ ] **Step 6: Commit the command-surface plumbing**

```bash
cd /Users/braydon/projects/experiments/workgraph
git add src/commands/mod.rs src/cli.rs src/main.rs src/commands/spawn_task.rs src/commands/claude_handler.rs
git commit -m "feat: add coordinator subprocess command surfaces"
```

### Task 3: Implement the coordinator-first `spawn-task` precursor

**Files:**
- Modify: `workgraph/src/commands/spawn_task.rs`

- [ ] **Step 1: Add the coordinator-only handler resolution model**

Replace the stub in `spawn_task.rs` with a real `HandlerSpec` and coordinator-aware resolution:

```rust
#[derive(Clone, Debug, PartialEq, Eq)]
enum HandlerSpec {
    Claude {
        chat_ref: String,
        model: Option<String>,
    },
}

fn is_primary_coordinator(task_id: &str) -> bool {
    task_id == ".coordinator-0" || task_id == "coordinator-0"
}

fn resolve_chat_ref(task_id: &str) -> String {
    if let Some(n) = task_id.strip_prefix(".coordinator-") {
        format!("coordinator-{}", n)
    } else {
        task_id.to_string()
    }
}
```

- [ ] **Step 2: Resolve runtime intent through the existing config path, then fail explicitly for unsupported executors**

Use the current coordinator config/effective executor logic instead of inventing a new mapping:

```rust
fn resolve_handler(workgraph_dir: &Path, task_id: &str, role_override: Option<&str>) -> Result<HandlerSpec> {
    if !is_primary_coordinator(task_id) {
        anyhow::bail!("this workgraph line supports only the primary coordinator for spawn-task");
    }

    let config = workgraph::config::Config::load_or_default(workgraph_dir);
    let mut coordinator_cfg = config.coordinator.clone();
    if role_override == Some("coordinator") {
        // keep current coordinator semantics; no extra mutation needed
    }

    let executor = coordinator_cfg.effective_executor();
    if executor != "claude" {
        anyhow::bail!(
            "unsupported coordinator executor for this tranche: {} (expected claude)",
            executor
        );
    }

    Ok(HandlerSpec::Claude {
        chat_ref: resolve_chat_ref(task_id),
        model: coordinator_cfg.model.clone(),
    })
}
```

- [ ] **Step 3: Implement `--dry-run` preview and real dispatch**

Use the current `wg` binary, not a hard-coded path:

```rust
fn current_wg_binary() -> Result<std::path::PathBuf> {
    std::env::current_exe().context("failed to resolve current wg binary")
}

pub fn run(workgraph_dir: &Path, task_id: &str, role_override: Option<&str>, dry_run: bool) -> Result<()> {
    let spec = resolve_handler(workgraph_dir, task_id, role_override)?;
    if dry_run {
        match spec {
            HandlerSpec::Claude { ref chat_ref, ref model } => {
                let mut preview = format!("wg claude-handler --chat {}", chat_ref);
                if let Some(model) = model {
                    preview.push_str(&format!(" -m {}", model));
                }
                println!("{}", preview);
            }
        }
        return Ok(());
    }

    dispatch(workgraph_dir, &spec)
}
```

- [ ] **Step 4: Dispatch to `claude-handler` as a real subprocess**

Keep the boundary real:

```rust
fn dispatch(workgraph_dir: &Path, spec: &HandlerSpec) -> Result<()> {
    let wg = current_wg_binary()?;
    let mut cmd = std::process::Command::new(wg);
    cmd.arg("--dir").arg(workgraph_dir);

    match spec {
        HandlerSpec::Claude { chat_ref, model } => {
            cmd.arg("claude-handler").arg("--chat").arg(chat_ref);
            if let Some(model) = model {
                cmd.arg("-m").arg(model);
            }
        }
    }

    let status = cmd.status().context("failed to run handler subprocess")?;
    if status.success() {
        Ok(())
    } else {
        anyhow::bail!("handler subprocess exited with status {}", status)
    }
}
```

- [ ] **Step 5: Run the dry-run tests until they pass**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent coordinator_spawn_task_ -- --nocapture
```

Expected:
- primary coordinator dry-run passes and previews `wg claude-handler`
- unsupported coordinator executor fails with the explicit error text

- [ ] **Step 6: Commit the `spawn-task` precursor**

```bash
cd /Users/braydon/projects/experiments/workgraph
git add src/commands/spawn_task.rs tests/integration_coordinator_agent.rs
git commit -m "feat: add coordinator spawn-task precursor"
```

### Task 4: Implement `claude-handler` on top of the current global chat store

**Files:**
- Modify: `workgraph/src/commands/claude_handler.rs`
- Modify: `workgraph/src/commands/service/coordinator_agent.rs`

- [ ] **Step 1: Factor the existing Claude session code out of `CoordinatorAgent` into handler-friendly helpers**

Lift only the Claude-session pieces that the handler truly needs:

```rust
pub(crate) fn spawn_claude_process(model: Option<&str>) -> Result<Child> { /* lifted from coordinator_agent.rs */ }

pub(crate) fn parse_assistant_text(line: &str) -> Option<String> { /* lifted JSON parser logic */ }
```

Keep `build_coordinator_context(...)` in `coordinator_agent.rs`; do not duplicate it.

- [ ] **Step 2: Implement a handler loop that owns inbox cursor advancement**

In `claude_handler.rs`, read from the existing global chat store and advance the coordinator cursor only after a request has been processed:

```rust
pub fn run(workgraph_dir: &Path, chat: &str, model: Option<&str>) -> Result<()> {
    anyhow::ensure!(chat == "coordinator-0", "this tranche supports only coordinator-0");

    let mut child = spawn_claude_process(model)?;
    let mut stdin = child.stdin.take().context("claude stdin unavailable")?;
    let stdout = child.stdout.take().context("claude stdout unavailable")?;
    let mut reader = std::io::BufReader::new(stdout);

    loop {
        let cursor = workgraph::chat::read_coordinator_cursor(workgraph_dir)?;
        let messages = workgraph::chat::read_inbox_since(workgraph_dir, cursor)?;
        if messages.is_empty() {
            std::thread::sleep(std::time::Duration::from_millis(100));
            continue;
        }

        for msg in messages {
            let context = crate::commands::service::coordinator_agent::build_coordinator_context(
                workgraph_dir,
                "1970-01-01T00:00:00Z",
                None,
            )?;
            write_turn(&mut stdin, &context, &msg.content)?;
            let response = read_response(&mut reader)?;
            workgraph::chat::append_outbox(workgraph_dir, &response, &msg.request_id)?;
            workgraph::chat::write_coordinator_cursor(workgraph_dir, msg.id)?;
        }
    }
}
```

- [ ] **Step 3: Surface handler-side failures as chat-visible `system-error`s**

When Claude startup, write, or read fails, append an error to the outbox before returning:

```rust
fn append_handler_error(workgraph_dir: &Path, request_id: &str, err: &anyhow::Error) {
    let _ = workgraph::chat::append_error(
        workgraph_dir,
        &format!("The coordinator handler failed.\n\nError:\n{:#}", err),
        request_id,
    );
}
```

- [ ] **Step 4: Run the basic conversation and cursor tests until they pass through the handler**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent coordinator_agent_basic_conversation -- --nocapture
cargo test --test integration_coordinator_agent coordinator_agent_multi_turn -- --nocapture
cargo test --test integration_coordinator_agent coordinator_agent_cursor_tracking -- --nocapture
cargo test --test integration_coordinator_agent coordinator_agent_storage_consistency -- --nocapture
```

Expected:
- responses still come back through the mock Claude CLI
- coordinator cursor now advances because the handler, not the daemon, owns it

- [ ] **Step 5: Commit the handler implementation**

```bash
cd /Users/braydon/projects/experiments/workgraph
git add src/commands/claude_handler.rs src/commands/service/coordinator_agent.rs tests/integration_coordinator_agent.rs
git commit -m "feat: add coordinator claude handler"
```

### Task 5: Convert `CoordinatorAgent` into a subprocess supervisor and stop daemon-side cursor ownership

**Files:**
- Modify: `workgraph/src/commands/service/coordinator_agent.rs`
- Modify: `workgraph/src/commands/service/mod.rs`

- [ ] **Step 1: Replace direct Claude stdio ownership with a supervisor loop**

Change `CoordinatorAgent::spawn(...)` so it launches `wg spawn-task .coordinator-0 --role coordinator` as a child process instead of opening Claude stdio directly:

```rust
pub fn spawn(
    dir: &Path,
    model: Option<&str>,
    logger: &DaemonLogger,
    event_log: SharedEventLog,
) -> Result<Self> {
    let (tx, rx) = mpsc::channel::<ChatRequest>();
    let alive = Arc::new(Mutex::new(false));
    let pid = Arc::new(Mutex::new(0u32));

    let dir = dir.to_path_buf();
    let model = model.map(String::from);
    let logger = logger.clone();

    let agent_thread = thread::Builder::new()
        .name("coordinator-agent".to_string())
        .spawn(move || {
            supervisor_thread_main(&dir, model.as_deref(), rx, alive, pid, &logger, &event_log);
        })?;

    Ok(Self { tx, _agent_thread: agent_thread, alive, pid, event_log })
}
```

- [ ] **Step 2: Stop `route_chat_to_agent(...)` from reading and advancing the inbox cursor**

The handler now owns cursor advancement. Update `route_chat_to_agent(...)` in `service/mod.rs` to wake the supervisor without consuming inbox messages:

```rust
fn route_chat_to_agent(
    dir: &Path,
    agent: &coordinator_agent::CoordinatorAgent,
    logger: &DaemonLogger,
) -> Result<usize> {
    let cursor = workgraph::chat::read_coordinator_cursor(dir)?;
    let messages = workgraph::chat::read_inbox_since(dir, cursor)?;
    if messages.is_empty() {
        return Ok(0);
    }

    for msg in &messages {
        if let Err(e) = agent.send_message(msg.request_id.clone(), msg.content.clone()) {
            logger.error(&format!("Failed to wake coordinator subprocess: {}", e));
            let _ = workgraph::chat::append_error(
                dir,
                &format!("The coordinator agent is not available.\n\nError:\n{:#}", e),
                &msg.request_id,
            );
        }
    }

    Ok(messages.len())
}
```

Important: do **not** call `write_coordinator_cursor(...)` here anymore.

- [ ] **Step 3: Make the supervisor restart the handler and preserve current rate limiting**

Keep the existing restart window logic, but apply it to the `wg spawn-task` child:

```rust
fn launch_spawn_task(dir: &Path, model: Option<&str>) -> Result<Child> {
    let wg = std::env::current_exe().context("failed to resolve wg binary")?;
    let mut cmd = Command::new(wg);
    cmd.arg("--dir")
        .arg(dir)
        .arg("spawn-task")
        .arg(".coordinator-0")
        .arg("--role")
        .arg("coordinator");
    if let Some(model) = model {
        cmd.env("WG_COORDINATOR_MODEL", model);
    }
    cmd.stdout(Stdio::null()).stderr(Stdio::piped()).stdin(Stdio::null());
    cmd.spawn().context("failed to spawn coordinator subprocess")
}
```

- [ ] **Step 4: Re-run the subprocess-path and instant-wakeup tests**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent coordinator_agent_uses_claude_handler_subprocess -- --nocapture
cargo test --test integration_coordinator_agent coordinator_agent_instant_wakeup -- --nocapture
```

Expected:
- daemon log shows handler/subprocess path
- urgent wake still delivers a response quickly even though the daemon no longer owns Claude stdin

- [ ] **Step 5: Commit the supervisor conversion**

```bash
cd /Users/braydon/projects/experiments/workgraph
git add src/commands/service/coordinator_agent.rs src/commands/service/mod.rs tests/integration_coordinator_agent.rs
git commit -m "feat: supervise coordinator via spawn-task"
```

### Task 6: Land crash semantics and tracker coverage

**Files:**
- Modify: `workgraph/tests/integration_coordinator_agent.rs`
- Modify: `driftdriver/.driftdriver/upstream-config.toml`
- Modify: `driftdriver/.driftdriver/upstream-pins.toml`
- Modify: `driftdriver/docs/superpowers/specs/2026-04-27-workgraph-coordinator-subprocess-design.md`

- [ ] **Step 1: Tighten the crash-recovery test around handler failure**

Make the crash test assert the new runtime boundary explicitly:

```rust
let log = read_daemon_log(&wg_dir);
assert!(
    log.contains("spawn-task") || log.contains("claude-handler"),
    "restart path should mention the subprocess boundary.\n{}",
    log,
);
assert!(
    log.contains("restarting") || log.contains("Coordinator agent"),
    "supervisor log should show a restart decision.\n{}",
    log,
);
```

- [ ] **Step 2: Add a dedicated `driftdriver` compatibility check**

Append a new check to `driftdriver/.driftdriver/upstream-config.toml`:

```toml
[[external_repos.compatibility_checks]]
name = "workgraph-coordinator-subprocess-contract"
command = "cd /Users/braydon/projects/experiments/workgraph && cargo test --test integration_coordinator_agent coordinator_spawn_task_ -- --nocapture && cargo test --test integration_coordinator_agent coordinator_agent_uses_claude_handler_subprocess -- --nocapture && cargo test --test integration_coordinator_agent coordinator_handler_crash_surfaces_error_and_recovers -- --nocapture"
```

- [ ] **Step 3: Advance the adopted Workgraph SHA**

Update `driftdriver/.driftdriver/upstream-pins.toml` so the adopted `workgraph` SHA matches the landed fork commit for this tranche.

- [ ] **Step 4: Run the targeted verification in both repos**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent -- --nocapture

cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_upstream_tracker.py -q
```

Expected:
- all coordinator integration tests pass on the subprocess path
- tracker tests pass with the new compatibility check included

- [ ] **Step 5: Commit and push the tracker/docs update**

```bash
cd /Users/braydon/projects/experiments/driftdriver
git add .driftdriver/upstream-config.toml .driftdriver/upstream-pins.toml docs/superpowers/specs/2026-04-27-workgraph-coordinator-subprocess-design.md docs/superpowers/plans/2026-04-27-workgraph-coordinator-subprocess.md
git commit -m "chore: track workgraph coordinator subprocess adoption"
```

### Task 7: Final verification and landing

**Files:**
- No new files; verify the changed ones above

- [ ] **Step 1: Run the final focused verification**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
cargo test --test integration_coordinator_agent -- --nocapture

cd /Users/braydon/projects/experiments/driftdriver
PYTHONPATH=$PWD pytest tests/test_upstream_tracker.py -q
```

Expected:
- `integration_coordinator_agent` passes end-to-end on the subprocess runtime
- `test_upstream_tracker.py` passes with the new contract

- [ ] **Step 2: Push both repos**

Run:

```bash
cd /Users/braydon/projects/experiments/workgraph
git pull --rebase
git push
git status -sb

cd /Users/braydon/projects/experiments/driftdriver
git pull --rebase
git push
git status -sb
```

Expected:
- `workgraph` shows `## main...fork/main`
- `driftdriver` shows `## main...origin/main`

- [ ] **Step 3: Hand off the next honest tranche**

Record the remaining follow-on boundary in the final notes:
- broader coordinator executor support beyond Claude
- moving more runtime selection into shared handler dispatch
- real session continuity/repair on top of the subprocess model
- only then considering upstream `chat_sessions`

---

## Self-Review

- Spec coverage: this plan covers the approved coordinator-first subprocess precursor, explicit unsupported-executor failure, handler-owned cursor advancement, crash/restart behavior, and the `driftdriver` compatibility contract.
- Placeholder scan: no `TODO`, `TBD`, or “similar to” placeholders remain; each task names exact files, commands, and concrete code slices.
- Type consistency: the plan consistently uses `spawn-task`, `claude-handler`, `CoordinatorAgent::spawn`, `build_coordinator_context(...)`, `read_coordinator_cursor(...)`, and `write_coordinator_cursor(...)` as the real local surfaces.
