# OpenCode SparseRead Pilot

This directory contains the local OpenCode plugin, bridge harness, and pilot
runner for comparing OpenCode native truncation against SparseRead on the three
positive tasks selected for the first OpenCode trial.

The plugin keeps SparseRead core behavior in `nanobot-sro-v3` unchanged. It
calls `python -m sparseread.bridge.opencode` through a stdio JSONL bridge.
On this workspace, launch the bridge through `uv run --project nanobot-sro-v3
python` so Python 3.11+ and project dependencies are used.

## Modes

- `native_truncation`: no plugin; OpenCode default `read` / `grep` / `bash`.
- `plugin_observe`: SR tools are available and native tool calls are traced.
- `plugin_nudge`: compatibility name for the OpenCode `advisory` policy.
  Native truncation / runtime-gated large reads append a short SR hint.
- `plugin_replace_truncation_experimental`: compatibility name for the
  OpenCode `enforce` policy. Only OpenCode high-confidence broad reads are
  blocked and redirected to `sro_preview`, with `sro_read` used only when the
  preview is insufficient and targeted evidence is needed.

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

## Runner

```bash
uv run --project nanobot-sro-v3 python opencode_pilot/run_pilot.py --offline
```

Use `npx -y opencode-ai` or `--opencode-cmd opencode` after installing
OpenCode to run real agent trajectories.
