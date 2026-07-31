# NanoBot 0.2.0 SparseRead Adapter

This branch updates the single-repository SparseRead integration from
NanoBot `0.1.5.post2` to NanoBot `0.2.0`.

## Rollback checkpoint

- Worktree: `/Users/captainliu/sparse-reading-sr-single-repo-integrations`
- Branch: `codex/sr-single-repo-integrations`
- Commit: `1b0c57c10162a18340af2073b8d5016ed56bfd12`

The rollback worktree is unchanged. The NanoBot 0.2.0 port lives in the
separate `codex/sr-nanobot-v020-adapter` branch.

## Adapter changes

- Pass one `SparseReadingOrchestrator` through NanoBot 0.2.0's `ToolContext`
  and `ToolLoader`.
- Register SparseRead macro tools after NanoBot's native tools.
- Restore read/list/grep/exec handoffs and write/exec state tracking.
- Restore DeepSeek plain-text DSML tool-call parsing.
- Preserve context-window output clipping and historical tool-argument
  compaction required by the benchmark runner.
- Update the PinchBench shim to use `AgentLoop.from_config`.
- Resolve benchmark fixture paths independently from the result worktree and
  propagate background job failures.
- Record NanoBot version, source revision, and dirty state in every run
  manifest.

## Verification

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

The SparseRead suite passes with 170 tests.

NanoBot 0.2.0's official suite passes with one macOS-only MCP socket teardown
test excluded because `server.wait_closed()` does not return:

```bash
cd nanobot-sro-v3
uv run --with pytest --with pytest-asyncio \
  pytest /tmp/sr-nanobot-v020.R6aC5o/new/tests -q \
  -k 'not test_probe_returns_true_for_open_port'
```

Result: `3147 passed, 5 skipped, 1 deselected`.
