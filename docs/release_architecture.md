# SparseRead release architecture

SparseRead ships as one framework-neutral core plus independently versioned
framework adapters. No release package imports source from another framework.

```text
packages/sparseread-core/             Python: sparseread-core
  src/sparseread/                     readers, gate, orchestrator, public API,
                                      generic JSONL bridge protocol

integrations/nanobot/python/          Python: sparseread-nanobot
  src/sparseread_nanobot/             NanoBot adapter entry point

integrations/opencode/python/         Python: sparseread-opencode
integrations/opencode/plugin/         npm: @sparseread/opencode

integrations/openclaw/python/         Python: sparseread-openclaw
integrations/openclaw/plugin/         npm: @sparseread/openclaw

nanobot-sro-v3/nanobot/sparse_reading/ compatibility imports for existing
                                      NanoBot callers; no core implementation
```

## Ownership rules

- Core owns data models, readers, the benefit gate, orchestration, generic
  tools, public wrappers, and the transport-independent bridge server.
- Python adapters own framework decision fields and bridge entry points.
- JavaScript plugins own framework tool registration, hooks, prompts, and
  process lifecycle.
- Benchmark runners are validation infrastructure and are not imported by any
  release package.
- `.sparseread/` is the only new runtime-artifact namespace. The core still
  recognizes legacy `.nanobot/sro-calc/` artifacts for compatibility.

## Compatibility contract

Core and JavaScript plugins negotiate bridge protocol `1.0`. The first plugin
request performs a `version` call and rejects an incompatible bridge before a
tool call can be served. Package versions may advance independently while this
protocol remains compatible.

## Installation shape

The source installer builds package artifacts, creates a managed Python
environment, installs `sparseread-core` plus exactly one Python adapter, and
installs the packed JavaScript plugin. Installed runtimes do not use editable
packages, `uv --project`, plugin links, or paths back into the checkout.

NanoBot consumes `sparseread-core` and `sparseread-nanobot` as ordinary Python
dependencies. Its old `nanobot.sparse_reading` modules are forwarding shims for
downstream compatibility and can be deprecated separately.
