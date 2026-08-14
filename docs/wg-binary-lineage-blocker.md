# wg Binary Lineage Blocker — orphan-fix installation

Date: 2026-08-14

## Situation

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
