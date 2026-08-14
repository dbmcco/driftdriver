# wg Binary Lineage Blocker — orphan-fix installation

Date: 2026-08-14 — **RESOLVED same day** (see Resolution below)

## Situation (historical)

The orphan-process fix (macOS descendant collection + process-group
signaling) is complete, tested, and committed on
`fix/orphan-process-cleanup` in `workgraph-pr-staging` (commit `8aaed6d6`,
branch head). It is **not installed**.

## Why

The production `wg` binary at `~/.cargo/bin/wg` (version
`0.1.0+79fb2ddf0525`, backed up as `wg.bak-79fb2ddf`) was built from a
source state that does not exist in any local branch of
`workgraph-pr-staging` or `workgraph-fork-archive`:

- Commit `79fb2ddf` is not present in either repo (2924+ commits searched,
  all refs).
- The running binary reads and writes an actor variant `finalizer` in
  graph.jsonl that no local source defines (main's actor enum: operator,
  dispatcher, worker, process-observer, wait-matcher, acceptance-controller,
  evaluation-runner, reconciler, importer).
- A binary built from current main + the orphan fix parses live graphs and
  then fails: `unknown variant 'finalizer'`. Installing it would break every
  repo whose graph contains those events.

## Verification performed before rollback

- `cargo build` green (after also fixing pre-existing macOS breakage:
  `libc::pipe2` Linux-only in `src/commands/service/signals.rs` — note the
  fork-archive already carries an equivalent fix, `10ed0773`).
- 3 new macOS tests green (grandchild discovery, tree kill, session-leader
  kill); suite delta vs main: +3 passed, same 12 pre-existing failures,
  zero regressions.
- Release binary built and smoke-tested in a scratch repo (init/add/list OK).
- Installed, hit the `finalizer` parse failure on the live driftdriver
  graph, rolled back via backup. Old binary re-verified healthy.

## Unblock options

1. Locate the source of the `79fb2ddf` build (likely a dirty working tree
   or a since-deleted checkout) and rebase the orphan fix onto it.
2. Cherry-pick `8aaed6d6` onto the true production lineage once found;
   re-run the verification ladder; install.
3. If the lineage is unrecoverable, add a parser compatibility shim
   (unknown actor variants deserialize as an opaque passthrough), but this
   changes parser semantics and needs deliberate review.

Until one of these lands, the orphan fix ships only as the verified branch.

## Resolution (2026-08-14, later same day)

The production binary's lineage was **Erik's upstream** (`graphwork/workgraph`).
Upstream `origin/main` advanced `b0892ea7 → 29459696` (2026-08-10), and the
`finalizer` actor variant landed upstream on 2026-07-28 (`0dd48b92`, "lazily
mint candidate evaluation evidence"). Local main in workgraph-pr-staging was
386 commits behind and contained no unique commits — the version string's
`79fb2ddf0525` is a build/service-identity hash, not a git commit.

Resolution steps:
1. Fast-forwarded `workgraph-pr-staging` main to `origin/main` (`29459696`).
2. Cherry-picked the orphan fix (`8aaed6d6` → `5cb7978f`), which also carries
   the macOS `pipe2` fix that upstream still lacks.
3. Verified: 3 orphan tests green; the 12 remaining lib-test failures are the
   same pre-existing worktree-cleanup/graph-watcher set present on upstream
   HEAD without the fix.
4. Built release, confirmed the binary parses every live graph (driftdriver,
   paia-os, paia-agent-runtime, founder-finance), installed (with ad-hoc
   codesign), restarted the driftdriver daemon. Other repos' daemons pick up
   the binary at their next natural restart.

Status: **installed and live.** Upstream push of the cherry-pick is blocked
(graphwork remote is 403); the fix rides on the local main and is also
preserved on branch `fix/orphan-process-cleanup`.
