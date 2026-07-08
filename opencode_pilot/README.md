# OpenCode SparseRead Integration

This directory contains the OpenCode plugin, bridge harness, and local runner
for comparing OpenCode native truncation against SparseRead.

The plugin keeps SparseRead core behavior in `nanobot-sro-v3` unchanged. It
calls `python -m sparseread.bridge.opencode` through a stdio JSONL bridge.
On this workspace, launch the bridge through `uv run --project nanobot-sro-v3
python` so Python 3.11+ and project dependencies are used.

## Modes

- `native_truncation`: no plugin; OpenCode default `read` / `grep` / `bash`.
- `plugin_observe`: SR tools are available and native tool calls are traced.
- `plugin_auto`: production-equivalent row. It sends `SPARSEREAD_POLICY=auto`
  to the plugin, so long text/PDF and compact high-confidence closures can be
  redirected to `sro_preview`, boundary collections can stay advisory, and
  small code/data/computation work stays native.
- `plugin_nudge`: compatibility name for the OpenCode `advisory` policy.
  Native truncation / runtime-gated large reads append a short SR hint.
- `plugin_replace_truncation_experimental`: compatibility name for the
  OpenCode `enforce` policy. Only OpenCode high-confidence broad reads are
  blocked and redirected to `sro_preview`, with `sro_read` used only when the
  preview is insufficient and targeted evidence is needed.

Only `plugin_auto` matches the recommended source-install production shape.
`plugin_nudge` and `plugin_replace_truncation_experimental` remain
compatibility/debug rows for runner comparisons.

Production tool path:

```text
sro_preview(path) -> use preview | sro_read({artifact_id}, mode, HintSpec) | sro_raw(raw_ref)
```

`sro_card -> sro_read` remains available under `SR_PROFILE=bench_protocol` and
for compatibility/debugging, but it is no longer the production first step.

The product gate is runtime-feature based. It does not branch on benchmark task
ids: long text/PDF and compact audit closures can be enforced; medium or
uncertain collections are advisory; small code/data/computation work stays
native. Command-security bundles stay SR-assisted, but use a stricter
one-collect-then-write advisory trajectory to avoid repeated SR ready-after
reads while preserving native reads for small templates.

## Install

Use the source installer from the repository root when OpenCode is already
installed:

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/project \
  --doctor
```

Then launch OpenCode from that workspace:

```bash
cd /path/to/project
opencode run "Use SparseRead to inspect the large report"
```

The installer writes `.opencode/sparseread.json` as the persistent workspace
config, so production launch no longer depends on shell-specific `source`
activation. On Windows, use PowerShell and run `py scripts/install_sparseread.py ...`.

## Runner

```bash
uv run --project nanobot-sro-v3 python integrations/opencode/run_pilot.py --offline
```

Use the installed `opencode` CLI to run real agent trajectories. If it is not on
PATH, set `OPENCODE_PATH` or pass `--opencode-cmd` with the executable path (or
a JSON argv array).

Legacy path compatibility remains available through `opencode_pilot/` symlinks,
but new development should use `integrations/opencode/`.
