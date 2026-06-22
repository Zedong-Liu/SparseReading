# SR Auto + L0 Preview Implementation Plan

## Summary

SparseRead production behavior should converge on one default profile: `auto`.

- `sro_preview` is the production entrypoint for no-HintSpec default reading.
- `sro_card` is retained as a low-level inspect/bind primitive, but production
  prompts should not require a separate card step. `sro_preview` embeds the
  minimum card fields in its response.
- `sro_read` remains the goal-directed path and still requires a concrete
  HintSpec for `collect`, `focus`, `refine`, and `verify`.
- OpenCode and OpenClaw should share one server/session implementation so the
  two framework adapters do not fork state, preview, raw-ref, and ready-guard
  behavior.
- MCP is intentionally deferred for this pass.
- `bench_protocol` is retained as a non-production profile for historical
  benchmark comparability.

Repository layout is single-trunk and framework adapters are directories, not
long-lived branches:

```text
nanobot-sro-v3/          SparseRead core, public facade, shared bridge
integrations/openclaw/   OpenClaw plugin and local/API runners
integrations/opencode/   OpenCode plugin and offline/real runner
openclaw_pilot/          compatibility symlinks only
opencode_pilot/          compatibility symlinks only
```

New development should happen under `integrations/*`. The pilot paths remain as
temporary compatibility aliases for old commands and historical reports.

## Production API

New production tools:

```text
sro_preview(target: {path | artifact_id}) -> PreviewPack
sro_raw(raw_ref, range? | selector?) -> raw fallback
```

`PreviewPack` must include:

```json
{
  "artifact_id": "...",
  "card": {
    "type": "csv|json|yaml|xml|xlsx|log|text|pdf|collection",
    "size_bytes": 0,
    "sparse_recommended": true,
    "reason": "...",
    "recommended_next": "use_preview|sro_read_with_goal|native"
  },
  "summary": "...",
  "structure": {},
  "samples": [],
  "signals": [],
  "compression": {
    "recipe": "...",
    "input_bytes": 0,
    "visible_bytes": 0,
    "omitted": true
  },
  "raw_ref": "...",
  "next_action": {
    "allowed_next": ["use_preview", "sro_read_with_goal", "sro_raw"],
    "instruction": "Use preview if sufficient; call sro_read with artifact_id and a concrete HintSpec only for targeted evidence."
  }
}
```

Default L0 recipes:

- CSV/TSV/XLSX: schema, row/sheet counts, cheap column typing, first/last
  samples, rare/error-like values, and `script_native_ok`.
- JSON/YAML/XML: top-level structure, array/object counts, schema sketch, and
  rare status/error/outlier samples.
- Log/text: time range, level counts, repeated-line collapse, error/warn
  samples, and headings/skeleton.
- PDF/long text: section/page skeleton and representative anchors, not
  question answering.
- Collection: grouped file card, sizes, notable long/log/report files, with no
  benchmark-specific closure unless a later HintSpec asks for it.

## Production Flow

```text
small/native-fit source
  -> native pass-through

first broad read/list/search/dump of large supported source
  -> sro_preview(path)

preview sufficient
  -> continue/write/use native small tools

specific evidence needed
  -> sro_read({artifact_id}, mode=collect|focus|refine|verify, hint=HintSpec)

evidence ready
  -> write_file_now guard

raw needed
  -> sro_raw(raw_ref)
```

Production prompts and adapter block messages should point to `sro_preview`,
not to `sro_card -> sro_read`. `sro_card` remains callable for debug, adapter
internals, and benchmark compatibility.

## Unified Server And Adapters

Add shared server methods:

```text
preview
raw
card
read
decide
native_event
usage_event
trace
shutdown
```

The server owns:

- one SparseRead runtime per framework session;
- artifact and path/root mapping;
- preview cache;
- raw-ref registry;
- ready guard state;
- native, usage, and trace events;
- adapter policy decisions.

OpenCode and OpenClaw plugins should become thin wrappers:

- register `sro_preview`, `sro_raw`, `sro_card`, `sro_read`, and `sro_trace`;
- call the shared server methods;
- keep only framework-specific hook wiring;
- block broad native read/list/grep/exec dump only when the server says
  `sro_preview` is required;
- describe only production `auto` in prompts, not observe/nudge/enforce as user
  modes.

MCP is out of scope for this pass.

## Bench Profile

Keep a non-production profile:

```text
SR_PROFILE=bench_protocol
```

Behavior:

- preserves the original-style `sro_card -> sro_read` path for benchmark reruns;
- keeps existing runner modes and reports comparable;
- allows `diagnostic_hints` only when explicitly requested and labeled
  protocol-assisted;
- never appears in production quickstart or default adapter config.

Production default:

```text
SR_PROFILE=auto
```

Old `observe`, `nudge`, `enforce`, and `native` names may remain as trace labels
or runner compatibility aliases, but not as user-facing production modes.

## Test And Validation Requirements

Core tests:

- `sro_preview` works without HintSpec for CSV, JSON, YAML, log/text, PDF/text,
  and collection.
- `sro_preview` returns embedded minimal card data, stable `artifact_id`,
  `raw_ref`, and deterministic recipe metadata.
- `sro_read collect/focus/refine/verify` still requires a valid HintSpec.
- `sro_raw(raw_ref)` retrieves original content or returns a clear stale-ref
  error.
- Generated outputs, runtime artifacts, small configs/scripts, and full-table
  compute paths stay native.

Convergence tests:

- broad first read on a large supported source returns preview, not a HintSpec
  error.
- after ready evidence, repeated reads return `protocol_next=write_file_now`.
- explicit raw retrieval is the only full-content fallback after preview.
- no production path requires `sro_card` before `sro_preview`.

Bridge tests:

- OpenCode and OpenClaw both use the shared server for preview/read/raw/trace.
- Existing T12/T21/LooGLE/T86 tests pass with updated tool expectations.
- `SR_PROFILE=auto` exposes production auto behavior.
- `SR_PROFILE=bench_protocol` preserves enough of the original benchmark path
  for fair historical comparison.

Validation scenarios:

- L0 smoke: large CSV, JSON array with rare error, repeated log warnings, long
  markdown, mixed collection.
- Positive SR: T12, T21, LooGLE 3q retain quality and stop repeated reads.
- Boundary: T86 remains one-collect/write in bench and does not become hard
  replace in production auto.
- Native-fit: T36/T58/T59/T67/T94 do not get forced into SR beyond optional
  preview of genuinely large sources.

Current acceptance uses the local API/regression commands in `runbook.md` plus
the OpenClaw plugin TypeScript build. Remote OpenCode/OpenClaw benchmark
validation is deferred until a later production-hardening pass.
