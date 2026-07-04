# SparseRead Fresh-Machine Installation

This is the default source-install shape for community users who already have
OpenCode or OpenClaw installed locally. SparseRead is installed from this
repository checkout; PyPI/npm marketplace publishing is a later release step.

Production entrypoint:

```text
sro_preview(path) -> deterministic L0 preview with embedded FileCard
sro_read(target, mode, hint) -> targeted evidence only when needed
sro_raw(raw_ref) -> explicit original-content fallback
```

`sro_card` is still shipped for benchmark and legacy compatibility. Product
docs and prompts should start from `sro_preview`.

## Supported Versions

Fresh install validation on 2026-07-04 used:

- OpenCode `1.17.13`
- OpenClaw `2026.6.11`
- Python `3.11+`
- Node.js `22.22.2+`; Node 24 should be `24.15.0+`
- `uv`
- `npm`

OpenClaw plugin metadata declares `openclaw >= 2026.5.17`. Older framework
versions may work only if they expose the same plugin/tool APIs.

## Install From Source

Clone the repository:

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading
```

Run the installer for the framework you use.

### OpenCode

Install SparseRead into an OpenCode workspace:

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/your/project \
  --policy auto \
  --mode auto \
  --doctor
```

The installer writes:

```text
/path/to/your/project/.opencode/plugins/sparseread.ts
/path/to/your/project/.opencode/sparseread.env
```

Launch OpenCode from that workspace with the generated environment:

```bash
cd /path/to/your/project
source .opencode/sparseread.env
opencode run "Use SparseRead to inspect the large report and answer the question"
```

If your binary is named differently, pass it explicitly:

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-cmd opencode-ai \
  --opencode-workspace /path/to/your/project
```

### OpenClaw

Install SparseRead into an existing OpenClaw profile:

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --policy auto \
  --mode auto \
  --doctor
```

The installer:

- builds `integrations/openclaw/plugin`
- runs `openclaw plugins install --link integrations/openclaw/plugin`
- enables `sparseread-openclaw`
- patches the plugin config with a repo-backed SparseRead bridge command

Inspect the loaded plugin:

```bash
openclaw plugins inspect sparseread-openclaw --runtime --json
```

Expected tool surface:

```text
sro_preview, sro_raw, sro_card, sro_read, sro_decide, sro_trace
```

OpenClaw provider/model credentials are still configured by OpenClaw itself.
SparseRead does not install or manage model keys.

For a named OpenClaw profile, pass it explicitly:

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --openclaw-profile work \
  --doctor

openclaw --profile work plugins inspect sparseread-openclaw --runtime --json
```

### Both Frameworks

If both CLIs are already installed:

```bash
python3 scripts/install_sparseread.py \
  --platform both \
  --opencode-workspace /path/to/your/project \
  --policy auto \
  --mode auto \
  --doctor
```

## Doctor Checks

Run checks without changing framework config:

```bash
python3 scripts/install_sparseread.py --platform opencode --doctor-only
python3 scripts/install_sparseread.py --platform openclaw --doctor-only
```

The doctor validates local commands and starts each Python bridge against a
small temporary markdown fixture.

## Release Fixture Suite

Every release should run the fixed six-fixture local suite:

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_release_fixtures.py -q
```

The six fixtures are:

1. long markdown key-value fields
2. log level preview plus raw selector
3. CSV schema/sample/signals
4. JSON schema/sample/signals
5. YAML schema/sample/signals
6. XML root/schema/sample preview

Each fixture runs through both `OpenCodeBridge` and `OpenClawBridge`, so the
suite checks shared bridge parity in addition to reader behavior.

For full local regression:

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

## Benchmark Compatibility

Existing benchmark scripts may still count `sro_card` and `sro_read` calls.
That path remains available through `SPARSEREAD_MODE=bench_protocol` and
`SparseReadConfig(mode="bench_protocol")`. Product installs should use
`mode=auto` and start from `sro_preview`.
