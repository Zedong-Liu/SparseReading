# SparseRead for Claude Code

Claude Code integration follows the same shape as the OpenCode/OpenClaw
adapters: one framework-neutral core (`packages/sparseread-core`) plus a
Claude-specific bridge surface. Claude Code has no npm plugin system, so the
"plugin" here is:

- `.mcp.json` — starts the MCP server exposing 8 SRO tools;
- `.claude/settings.local.json` — session hooks that enforce/advisory-gate
  native `Read`/`Bash` calls and nudge after large outputs;
- `CLAUDE.md` — static usage protocol for the model.

## Install

```bash
python3 scripts/install_sparseread.py --platform claude \
  --claude-workspace /path/to/your/project --doctor
```

The installer builds `sparseread-core` and `sparseread-claude` wheels, creates
a managed Python runtime under `~/.sparseread/claude`, writes `.mcp.json` and
`.claude/settings.local.json`, and creates `CLAUDE.md` when the workspace does
not already have one.

## Design notes

- Routing comes from the shared production BenefitGate (modes
  `force_sro/native/advisory`); Claude-specific fields
  (`hook_can_block_read/bash/inject_context`, `trajectory`) only describe how
  Claude Code can execute the decision.
- Multi-file audit and structured-compute episodes should open with
  `sro_preview(..., episode_hint={goal, relation, coverage, summary})` so the
  episode controller can route the whole episode.
- Native reads without a hint use shape-based defaults; the one-time-block
  rule downgrades a previously blocked path to advisory so the model can
  always proceed after SRO context has been injected.
