# OpenClaw SparseRead Pilot

This directory contains the OpenClaw SparseRead integration.  It is based
on OpenClaw's real extension surface: plugin-registered tools,
plugin-provided skills, and per-session bridge state.

The implementation keeps SparseRead core behavior in `nanobot-sro-v3`
unchanged.  OpenClaw calls the existing core through:

```bash
python -m sparseread.bridge.openclaw
```

## Design

- `sro_preview(path)`: production entrypoint; returns L0 structure, samples,
  signals, embedded minimal card metadata, raw_ref, and next-step guidance.
- `sro_raw(raw_ref)`: explicit full-content fallback after preview.
- `sro_card(path)`: compatibility/debug FileCard plus an OpenClaw adapter gate.
- `sro_read(target, mode, hint)`: returns an EvidencePack from the existing SR
  readers and collection closures.
- `sro_decide(path)`: exposes the runtime-feature gate without creating a card.
- `sro_trace()`: reports SR events, native tool events, truncation counts, usage
  events, ready-after-read counts, and adapter-gate reasons.

OpenClaw-specific gate profiles:

- `native`: SR does not intervene.  Small files, small config/code/data tasks,
  structured full-table computation, and script-heavy work stay native.
- `advisory`: SR is available and native tools remain available.  This is the
  default for boundary collections, command-security closures, and ambiguous
  diagnosis bundles.
- `enforce`: broad native read/search/exec-dump is blocked and redirected to
  `sro_preview`, followed by `sro_read` only when targeted evidence is needed.
  This is only for high-confidence long documents/PDFs and compact audit closures.

Command-security bundles use `advisory + one_collect_then_write`: preview first,
then exactly one collection `collect` only when slots are explicit, write once
ready, and allow small template or named unresolved-slot native reads.

Production installs keep `hookMode=off`: the plugin registers SparseRead tools
and the SparseRead skill, but it does not register native tool lifecycle hooks.
This is the stable path for OpenClaw 2026.6.11 and Windows.  `hookMode=trace`
records after-call/usage events, and `hookMode=enforce` additionally registers
`before_tool_call` for controlled benchmark runs where native read/search/dump
blocking is desired.

## Install

Use the source installer from the repository root when OpenClaw is already
installed:

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --openclaw-hook-mode off \
  --doctor
```

The installer builds this plugin, links it into OpenClaw, enables it, and
patches the plugin config with the repo-backed SparseRead bridge command.

## Local Development

Install or link the plugin with an OpenClaw CLI that supports native plugins:

```bash
REPO=/path/to/sparse-reading
cd "$REPO/integrations/openclaw/plugin"
npm install
npm run build
openclaw plugins install --link .
openclaw plugins enable sparseread-openclaw
openclaw plugins inspect sparseread-openclaw --runtime --json
openclaw gateway restart
cd "$REPO"
```

Useful environment overrides:

```bash
export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_PYTHON="uv --project $PWD/nanobot-sro-v3 run --with pymupdf python"
export SPARSEREAD_POLICY=advisory
export SPARSEREAD_OPENCLAW_HOOK_MODE=off
```

Use `SPARSEREAD_POLICY=enforce` only for controlled tests of high-confidence
long document/PDF or compact audit-closure cases. Use
`SPARSEREAD_OPENCLAW_HOOK_MODE=enforce` only for controlled lifecycle-hook
compatibility tests.

Legacy path compatibility remains available through `openclaw_pilot/` symlinks,
but new development should use `integrations/openclaw/`.
