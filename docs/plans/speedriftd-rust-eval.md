# ABOUTME: Decision document evaluating whether speedriftd should be rewritten in Rust.
# ABOUTME: Based on measured performance, memory, LOC, and workgraph overlap analysis.

# speedriftd Rust Rewrite Evaluation

**Date:** 2026-03-07
**Recommendation:** YAGNI

## Measurements

### Timing

| Operation | Wall time | User | Sys |
|-----------|-----------|------|-----|
| `speedriftd status --refresh` (5-run avg) | **92ms** | 70ms | 20ms |
| `speedriftd once` (single cycle) | **99ms** | 70ms | 20ms |

Both operations are well under the 100ms YAGNI threshold. The variance across 5 runs was 88-102ms, indicating stable performance.

### Memory (RSS)

| Metric | Value |
|--------|-------|
| Maximum RSS | **34 MB** (34,291,712 bytes) |
| Peak memory footprint | 23 MB |
| Page reclaims | 5,373 |
| Page faults | 45 |

Well under the 50 MB YAGNI threshold. Most of the 34 MB is the Python 3.14 interpreter itself.

### Subprocess Calls Per Cycle

The `collect_runtime_snapshot` hot path makes:
- **1 subprocess call** to `wg ready` (always)
- **N subprocess calls** to `wg show <task-id>` (one per ready task)
- **0 subprocess calls** for workgraph loading (direct JSONL file read)
- **0 subprocess calls** for worker health checks (reads `/tmp/claude-workers/*.events.jsonl` directly)

In the current state (0 ready tasks, 10 in-progress, 0 active workers): **1 subprocess call per cycle**.

Worst case with, say, 5 ready tasks: **6 subprocess calls per cycle**. Each `wg` invocation is a precompiled Rust binary and completes in <10ms.

### Lines of Code

| Component | LOC |
|-----------|-----|
| `speedriftd.py` | 197 |
| `dispatch.py` | 192 |
| `speedriftd_state.py` | 350 |
| `worker_monitor.py` | 206 |
| **Total speedriftd-critical path** | **945** |
| Full driftdriver Python codebase | 14,245 |

A Rust rewrite of the 945-line critical path would expand to roughly 2,000-2,500 lines of Rust (2-2.5x typical expansion factor for Python-to-Rust with error handling and serde).

### Workgraph (Rust) Overlap Analysis

The `wg` binary (23,331 lines Rust) already has:
- `src/service/` module (2,961 lines): `AgentRegistry`, `ExecutorConfig`, process lifecycle management
- Agent spawning, heartbeat tracking, dead-agent detection, file-locked registry
- The `wg agent run --once` command for single-cycle agent work

**Overlap is partial but not convergent.** The workgraph service layer manages wg-native agents (spawn, heartbeat, kill). Speedriftd manages the *supervisor* layer above that: runtime snapshots, control-state machines (observe/supervise/autonomous), lease management, manual-claim detection, multi-runtime worker health normalization, and event journaling. These are complementary, not duplicative.

## Bottleneck Analysis

There are no meaningful bottlenecks:
- **CPU:** 70ms user time is almost entirely Python startup + module import
- **I/O:** All file reads are small JSON/JSONL (<50KB). Writes use atomic temp-rename pattern
- **Subprocess:** Single `wg ready` call; the Rust binary responds in <10ms
- **No network I/O** in the hot path
- **No LLM calls** in the hot path

The 30-second default poll interval means speedriftd spends 99.7% of its time sleeping.

## Recommendation: YAGNI

**Do not rewrite speedriftd in Rust.** The numbers are unambiguous:

1. **92ms per cycle** is 5x below the "consider porting" threshold (500ms)
2. **34 MB RSS** is below the 50 MB threshold, and most of that is Python itself
3. **1 subprocess call** per cycle in steady state is negligible
4. The 945-line Python codebase is readable, well-tested, and actively evolving
5. Workgraph's Rust service layer is complementary, not a merge target
6. A Rust port would cost ~2,000-2,500 new LOC plus FFI or IPC bridging for the drift lanes that remain in Python

The only scenario where Rust makes sense is if speedriftd grows into a long-running daemon with sub-second polling and hundreds of concurrent workers across dozens of repos. That is not the current trajectory. If it becomes one, the right approach would be to extend `wg service` with a `speedriftd` subcommand in Rust, not to rewrite the Python.

## If Circumstances Change

Re-evaluate if any of these become true:
- Cycle time exceeds 500ms (e.g., from scanning large JSONL event logs)
- RSS exceeds 100MB (e.g., from holding many worker snapshots in memory)
- Poll interval drops below 5 seconds
- speedriftd needs to run as a persistent daemon (not invoked per-cycle by launchd/cron)
