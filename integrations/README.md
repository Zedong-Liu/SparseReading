# SparseRead Framework Integrations

This directory contains framework-specific SparseRead integrations. The
framework-neutral runtime lives in `packages/sparseread-core`; each framework
owns one thin Python adapter and, where needed, one JavaScript plugin package:

```bash
python -m sparseread_opencode.bridge
python -m sparseread_openclaw.bridge
```

## Layout

```text
integrations/
  openclaw/
    python/                 # sparseread-openclaw bridge adapter
    plugin/                 # @sparseread/openclaw package
    run_openclaw_*.py       # local validation runners
  opencode/
    python/                 # sparseread-opencode bridge adapter
    plugin/                 # @sparseread/opencode package
    run_pilot.py            # offline/live pilot runner
  nanobot/
    python/                 # sparseread-nanobot adapter entry point
```

The old root-level `openclaw_pilot/` and `opencode_pilot/` copies were removed.
They had drifted from the canonical implementations and are not release APIs.

## Default Install Shape

For users who already have OpenCode or OpenClaw installed, use the installer
from the repository root:

```bash
python3 scripts/install_sparseread.py --platform opencode --opencode-workspace /path/to/project --doctor
python3 scripts/install_sparseread.py --platform openclaw --doctor
```

The installer creates a managed Python runtime containing `sparseread`
and only the selected framework adapter. It installs packed JavaScript
artifacts rather than linking the source checkout.

## Boundary

- Shared behavior belongs in `packages/sparseread-core/src/sparseread/`.
- Platform policy belongs in `sparseread_openclaw.bridge` or
  `sparseread_opencode.bridge`.
- Plugin code should only register tools, wire lifecycle hooks, and present
  framework-specific prompts or block/nudge messages.
- Benchmark-only prompts, fixed targets, and diagnostic slots belong in runner
  code, not in product adapters or SR core.
- Core and plugins use bridge protocol `1.0`; plugins reject incompatible
  bridge runtimes before serving tool calls.
