# sparseread-claude

Claude Code adapter for SparseRead. This distribution owns the Claude-specific
bridge surface (JSONL bridge, MCP server, PreToolUse/PostToolUse hook, token
tracker) and depends on the framework-neutral `sparseread-core` runtime.

The core BenefitGate, episode controller, readers, and denoise layer are shared
unchanged with the NanoBot/OpenCode/OpenClaw adapters; only the bridge surface
is Claude-specific.

```bash
python -m sparseread_claude.bridge --workspace .          # JSONL bridge
python -m sparseread_claude.claude_mcp --workspace .      # MCP stdio server
python -m sparseread_claude.hook --workspace .            # session hook
```

Install via `scripts/install_sparseread.py --platform claude`, which creates a
managed Python runtime, writes `.mcp.json` and `.claude/settings.local.json`,
and optionally creates `CLAUDE.md` guidance.
