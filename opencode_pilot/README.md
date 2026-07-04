# OpenCode SparseRead Pilot

This directory contains the local OpenCode plugin, bridge harness, and pilot
runner for comparing OpenCode native truncation against SparseRead on the three
positive tasks selected for the first OpenCode trial.

The plugin keeps SparseRead core behavior in `nanobot-sro-v3` unchanged. It
calls `python -m sparseread.bridge.opencode` through a stdio JSONL bridge.
On this workspace, launch the bridge through `uv run --project nanobot-sro-v3
python` so Python 3.11+ and project dependencies are used.

Production entrypoint is `sro_preview(path)`. It returns the L0 default preview
and embeds the FileCard. `sro_read` is for targeted follow-up evidence.
`sro_card` remains available for benchmark and legacy compatibility.

## Install

```bash
cd opencode_pilot/plugin
npm install
npm run build
cd ../..

mkdir -p .opencode/plugins
cp opencode_pilot/plugin/sparseread.ts .opencode/plugins/sparseread.ts

export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
```

Run OpenCode from the workspace after setting those environment variables.

## Modes

- `native_truncation`: no plugin; OpenCode default `read` / `grep` / `bash`.
- `plugin_observe`: SR tools are available and native tool calls are traced.
- `plugin_nudge`: compatibility name for the OpenCode `advisory` policy.
  Native truncation / runtime-gated large reads append a short SR hint.
- `plugin_replace_truncation_experimental`: compatibility name for the
  OpenCode `enforce` policy. Only OpenCode high-confidence broad reads are
  blocked and redirected to `sro_preview` / targeted `sro_read`.

The product gate is runtime-feature based. It does not branch on benchmark task
ids: long text/PDF and compact audit closures can be enforced; medium or
uncertain collections are advisory; small code/data/computation work stays
native. Command-security bundles stay SR-assisted, but use a stricter
one-collect-then-write advisory trajectory to avoid repeated SR ready-after
reads while preserving native reads for small templates.

## Runner

```bash
uv run --project nanobot-sro-v3 python opencode_pilot/run_pilot.py --offline
```

Use `npx -y opencode-ai` or `--opencode-cmd opencode` after installing
OpenCode to run real agent trajectories.
