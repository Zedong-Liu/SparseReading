# SparseRead Installation Guide

This guide describes the current production integration shape for nanobot,
OpenCode, and OpenClaw.

Production entrypoint:

```text
sro_preview(path) -> deterministic L0 preview with embedded FileCard
sro_read(target, mode, hint) -> targeted evidence only when needed
sro_raw(raw_ref) -> explicit original-content fallback
```

`sro_card` is still shipped for benchmark and legacy compatibility. New
integrations should not teach users to start there.

## Prerequisites

- Python 3.11+
- `uv`
- Node.js 22.22.2+ for TypeScript plugin build checks. Node 24 should be
  24.15.0+; Node 24.14.x can build locally but may emit upstream npm engine
  warnings from OpenCode dependencies.
- A checkout of this repository

Use the repo-backed bridge command for OpenCode/OpenClaw:

```bash
export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
```

## Shared Local Verification

Run the Python core, public API, and bridge tests:

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

## nanobot

Enable SparseRead in nanobot with `SRO_ENABLED=1`. The nanobot integration
registers `sro_preview`, `sro_raw`, `sro_read`, and legacy `sro_card`, then
attaches guards to `read_file`, `list_dir`, `grep`, and `exec`.

Adapter install for an existing nanobot-style agent:

```python
from sparseread.adapters.nanobot import install

runtime = install(agent, workspace=".")
```

Smoke check:

```bash
uv run --project nanobot-sro-v3 python - <<'PY'
from pathlib import Path
from sparseread import SparseRead

root = Path(".sro_smoke")
root.mkdir(exist_ok=True)
target = root / "report.md"
target.write_text("# Report\n\nThe gateway exposes a typed WebSocket API.\n" * 80)

sr = SparseRead(workspace=root, mode="force")
preview = sr.orchestrator.preview({"path": str(target)})
print(preview.artifact_id, preview.card["type"], preview.raw_ref)
PY
```

## OpenCode

OpenCode loads the local TypeScript plugin from a workspace plugin directory.

Build/typecheck the plugin:

```bash
cd integrations/opencode/plugin
npm install
npm run build
cd ../../..
```

Install into a workspace:

```bash
mkdir -p .opencode/plugins
cp integrations/opencode/plugin/sparseread.ts .opencode/plugins/sparseread.ts
```

Start OpenCode from the workspace with the shared bridge environment above.
The plugin exposes `sro_preview`, `sro_raw`, `sro_read`, legacy `sro_card`,
and `sro_trace`. In `enforce` policy it blocks only high-confidence broad
native reads and points the model to `sro_preview`.

Bridge subprocess smoke:

```bash
uv run --project nanobot-sro-v3 python -m sparseread.bridge.opencode \
  --workspace . --mode force
```

Send JSONL:

```json
{"id":"1","method":"preview","params":{"path":"README.md"}}
{"id":"2","method":"trace","params":{}}
{"id":"3","method":"shutdown","params":{}}
```

## OpenClaw

Build the plugin:

```bash
cd integrations/openclaw/plugin
npm install
npm run build
cd ../../..
```

Install into OpenClaw:

```bash
openclaw plugins install --link integrations/openclaw/plugin
openclaw plugins enable sparseread-openclaw
openclaw plugins inspect sparseread-openclaw --runtime --json
```

Use the shared bridge environment above when launching OpenClaw. The plugin
declares `sro_preview`, `sro_raw`, `sro_read`, `sro_decide`, `sro_trace`, and
legacy `sro_card`. `openclaw` is an optional peer dependency for build-time
safety; the running OpenClaw host provides `openclaw/plugin-sdk/plugin-entry`.

Bridge subprocess smoke:

```bash
uv run --project nanobot-sro-v3 python -m sparseread.bridge.openclaw \
  --workspace . --mode force
```

Send JSONL:

```json
{"id":"1","method":"preview","params":{"path":"README.md"}}
{"id":"2","method":"trace","params":{}}
{"id":"3","method":"shutdown","params":{}}
```

## Benchmark Compatibility

Existing benchmark scripts may still count `sro_card` and `sro_read` calls.
That path remains available through `SPARSEREAD_MODE=bench_protocol` and
`SparseReadConfig(mode="bench_protocol")`. Product prompts and user-facing docs
should prefer `sro_preview` so default L0 reading does not require a HintSpec.
