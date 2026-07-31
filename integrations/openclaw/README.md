# OpenClaw SparseRead Pilot

This directory contains the OpenClaw SparseRead integration.  It is based
on OpenClaw's real extension surface: plugin-registered tools,
plugin-provided skills, and per-session bridge state.

The implementation keeps SparseRead core behavior in `nanobot-sro-v3`
unchanged.  OpenClaw calls the existing core through:

```bash
python -m sparseread_openclaw.bridge
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

Production installs expose one user-facing mode. The default
`--sparseread-mode auto` registers SparseRead tools, the SparseRead skill,
prompt preflight, and gate-controlled native tool interception. High-confidence
long documents/PDFs/logs and compact audit closures are routed to
`sro_preview`, while low-benefit cases keep OpenClaw tools. Use
`--sparseread-mode advisory` when you want guidance only and no native tool
interception.

## Install

Use the source installer from the repository root when OpenClaw is already
installed:

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --doctor
```

The installer packs this plugin, installs it into OpenClaw, enables it, and
patches the plugin config with a managed SparseRead bridge runtime.

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
export SPARSEREAD_PYTHON="$PWD/.venv/bin/python"
export SPARSEREAD_POLICY=auto
export SPARSEREAD_OPENCLAW_HOOK_MODE=enforce
```

Use the source installer for normal installs. These environment variables are
developer overrides only; production users should choose `--sparseread-mode
auto` or `--sparseread-mode advisory`.

This directory is the only OpenClaw implementation source. The former
root-level `openclaw_pilot/` copy was removed to prevent release drift.
