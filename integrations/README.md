# SparseRead Framework Integrations

This directory contains framework-specific SparseRead integrations.  The
SparseRead runtime, readers, benefit gate, shared bridge, and public facade live
under `nanobot-sro-v3/`; framework code should stay thin and delegate to:

```bash
python -m sparseread.bridge.<framework>
```

## Layout

```text
integrations/
  openclaw/
    plugin/                 # OpenClaw plugin package
    run_openclaw_*.py       # local validation runners
  opencode/
    plugin/                 # OpenCode plugin source
    run_pilot.py            # offline/live pilot runner
```

Compatibility symlinks remain at `openclaw_pilot/` and `opencode_pilot/` so
older runbook commands and local tooling continue to work.  New documentation
and development should use `integrations/openclaw` and `integrations/opencode`.

## Boundary

- Shared behavior belongs in `nanobot-sro-v3/sparseread/bridge/server.py`.
- Platform policy belongs in `sparseread.bridge.openclaw` or
  `sparseread.bridge.opencode`.
- Plugin code should only register tools, wire lifecycle hooks, and present
  framework-specific prompts or block/nudge messages.
- Benchmark-only prompts, fixed targets, and diagnostic slots belong in runner
  code, not in product adapters or SR core.
