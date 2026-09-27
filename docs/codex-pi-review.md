# Independent Codex / Pi adapter review

Date: 2026-09-27  
Branch: `codex/codex-pi-adapters`  
Base: `e1e0f3653e5c0ea882c1bf77c6b993b927e37d77`

## Finding

### [P2] Installer follows a symlink out of the workspace — RESOLVED (2026-09-27 follow-up)

[`scripts/install_sparseread.py:601`](../scripts/install_sparseread.py#L601) treats any destination symlink whose target contains `.sparseread-runtime.json` as a managed installation. [`scripts/install_sparseread.py:621`](../scripts/install_sparseread.py#L621) then calls `shutil.copytree(..., dirs_exist_ok=True)`, which follows that symlink; the subsequent runtime write at line 623 follows it as well.

Reproduction: in a temporary workspace, I made `.sparseread/pi/sparseread-pi` a directory symlink to a directory outside the workspace containing a runtime marker and a pre-existing `package.json`. Destination validation accepted it; the install path copied the Pi package outside the workspace and replaced that `package.json`. I stubbed only wheel creation and the Pi CLI call; the installer’s destination validation, copy, and runtime-file writes were exercised.

Minimal fix: before creating the runtime or copying files, reject symlinks in the managed destination chain, or resolve each destination and require it to remain beneath the canonical workspace. Apply the same check to both Codex and Pi destinations.

Resolution verified in the follow-up below: `validate_host_destination` now rejects symlinks in the destination ancestor chain and within the owned plugin tree, and `install_host` calls it before creating a runtime or building wheels. The check intentionally does not scan the venv tree.

No P0 or P1 findings remain from this review.

## Resolved issues verified

- Pi JSONL UTF-8 decoding: the bytewise Chinese/emoji regression passes with a per-child `StringDecoder`.
- Pi stdin `EPIPE`: the regression passes; the child stream error is handled and pending work rejects without an uncaught host error.
- Pi native-read relative paths: current code resolves from Pi's `context.cwd` and passes an absolute path to `decide`; the subdirectory regression passes. The shared SparseRead tool path contract remains workspace-relative.
- Codex MCP plugin-root expansion and same-version cache reuse were corrected in the implementation validation. The supplied isolated Codex home now loads the plugin's six MCP tools after the mode reinstall.

## Concurrent implementation compatibility check (not an independent finding)

The implementation owner reports that Node 22.23.3 exposed seven cancelled tests when unreferenced request/shutdown timers allowed fake-child promises to leave the event loop before settlement. Production timers now remain referenced until settlement or cleanup. The owner reports the rerun passed all 18 tests, including the directed-offset regression; I did not independently rerun Node 22.23.3.

## Verification evidence

- `uv run --extra test pytest -q`: **217 passed**.
- On Node **v24.15.0**, `SPARSEREAD_PI_RUNTIME_FILE=/tmp/sparseread-host-smoke.1UK42s/project/.sparseread/pi/sparseread-pi/.sparseread-runtime.json npm test`: **16 passed, 0 skipped**, including the installed-runtime preview/raw smoke.
- `CODEX_HOME=/tmp/sparseread-host-smoke.1UK42s/codex-home uv run python scripts/check_codex_plugin.py /tmp/sparseread-host-smoke.1UK42s/project`: passed against Codex CLI 0.158.0-alpha.2; discovered the plugin, skill, and hooks, loaded all six MCP tools, and successfully called `sro_trace`. No model turn was made.
- `packages/sparseread-core` has no diff from the stated base.
- The actual Pi 0.87.1 package loader and actual Codex app-server were exercised. Official contracts consulted: [Codex plugin packaging](https://developers.openai.com/plugins/build/plugins), [Codex hooks](https://learn.chatgpt.com/docs/hooks), [Pi extensions](https://pi.dev/docs/latest/extensions), and [Pi packages](https://pi.dev/docs/latest/packages).

## Limits

This review did not rerun the complete installer from a pristine checkout into a new workspace; the Pi installed-source/runtime smoke and Codex app-server smoke used the supplied installed fixture. Remote CI has not run, and no live host hook-trust UI, Windows host, or model-based quality/token/latency benchmark was exercised. The implementation validation record documents these limits in [`docs/codex-pi-validation.md`](codex-pi-validation.md).

## Final follow-up — 2026-09-27

- `uv run --extra test pytest -q tests/test_host_install.py`: **17 passed**.
- `uv run --extra test pytest -q`: **225 passed**.
- The eight parameterized symlink cases ran without skips across Codex and Pi, covering parent, plugin, nested-plugin, and config links. They assert installation aborts before runtime build and the outside sentinel remains unchanged.
- The destination guard is invoked by `install_host` before runtime creation/build and by preflight. The owned plugin subtree is checked for child links; venv symlinks are left alone.
- Based on these checks, the original P2 finding is **resolved**. No P0/P1 findings were found. The Node 22.23.3 all-18-passed result is implementation-owner-reported rather than independently rerun by this reviewer.
