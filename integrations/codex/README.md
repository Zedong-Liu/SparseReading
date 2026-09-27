# Codex integration

`plugin/sparseread-codex/` is the installable Codex package. Use the repository installer for project-scoped setup so its managed Python environment, runtime manifest, local marketplace entry, and project enablement all point at the same workspace.

The plugin directory contains the Codex compatibility manifest, `.mcp.json`, auto-discovered `hooks/hooks.json`, a small Node launcher, and a SparseRead skill. The installer-generated `.sparseread-runtime.json` records the absolute wheel-installed Python executable, target workspace, mode (`auto` or `advisory`), and protocol `1.0`.

The source `.mcp.json` keeps `${CLAUDE_PLUGIN_ROOT}` as a plugin-root placeholder. In current Codex app-server testing, the legacy MCP manifest did not expand that token (the MCP process failed initialization), although launching the Node MCP launcher directly worked. The project installer must replace the source argument with the absolute path to the copied plugin's `scripts/launcher.mjs` before enabling the plugin. This MCP launch-path requirement is separate from hook commands, for which Codex documents `PLUGIN_ROOT` and `CLAUDE_PLUGIN_ROOT`.

Codex requires a user to review and trust plugin hooks before they run. The `PreToolUse` hook only considers a plain unbounded `cat` command; it leaves bounded reads, searches, shell compounds, advisory mode, and bridge failures on the native path.
