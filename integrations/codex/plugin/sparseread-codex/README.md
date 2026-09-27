# SparseRead for Codex

This Codex plugin exposes SparseRead's six MCP tools and adds a conservative `PreToolUse` hook for a direct, unbounded `cat <path>` command. Bounded reads, search commands, compound shell commands, and hook or bridge failures continue through Codex's normal behavior. Advisory installs only add guidance.

The project installer copies this directory to:

```text
<workspace>/.sparseread/codex/sparseread-codex
```

It then writes `.sparseread-runtime.json` in the copied plugin root:

```json
{
  "python": "/absolute/path/to/managed-venv/bin/python",
  "workspace": "/absolute/path/to/workspace",
  "mode": "auto",
  "protocol": "1.0"
}
```

The Python executable points to the installed agent-tools and core wheels. The plugin therefore runs after the repository checkout is removed. Node.js must be available as `node`; Python does not need to be on `PATH`.

The source `.mcp.json` uses `${CLAUDE_PLUGIN_ROOT}` as a source-package placeholder. Current Codex app-server testing showed that legacy MCP arguments do not expand it, even though the direct Node launcher works. The project installer must concretize the launcher argument to the absolute `scripts/launcher.mjs` path inside this copied plugin before the plugin is enabled. Do not use the source `.mcp.json` unchanged for a direct marketplace install. This does not change hook paths: Codex documents plugin-root variables for hook commands, and bundled hooks still require separate user review and trust.

The installer registers the copied plugin through the workspace's `.agents/plugins/marketplace.json` and enables it in `.codex/config.toml`. The hook receives `PLUGIN_ROOT` and `PLUGIN_DATA` from Codex. Review and trust the bundled hook definitions in `/hooks` before expecting interception; the plugin never bypasses Codex hook trust.

The hook only denies a direct unbounded `cat` when the installed mode is `auto` and the Codex host gate requests enforcement. It gives a `sro_preview` path, records a single redirect per session and path under `PLUGIN_DATA`, then lets a retry follow Codex's normal permission flow. The lock and atomic state update make concurrent hook invocations share that one redirect safely.
