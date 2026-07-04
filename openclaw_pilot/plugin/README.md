# SparseRead for OpenClaw

OpenClaw plugin pilot for exposing SparseRead as tools plus runtime gate hooks.

The plugin starts `python -m sparseread.bridge.openclaw` through stdio JSONL and
keeps one bridge per OpenClaw session key.  The Python bridge owns artifact ids,
ready state, and trace aggregation while delegating all reading logic to the
existing SparseRead core.

Default policy is `advisory`.  Use `enforce` only for controlled high-confidence
long-document/PDF or compact audit-closure runs.

Production entrypoint is `sro_preview(path)`. It returns the L0 default preview
and embeds the FileCard. `sro_read` is for targeted follow-up evidence.
`sro_card` remains available for benchmark and legacy compatibility.

## Install

```bash
cd openclaw_pilot/plugin
npm install
npm run build
cd ../..

openclaw plugins install --link openclaw_pilot/plugin

export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
```

The plugin keeps `openclaw` as an optional peer dependency. The build does not
install the host CLI; the running OpenClaw process provides the plugin SDK at
runtime.
