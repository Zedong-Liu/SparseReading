# SparseRead installation

This guide describes the current single-repository source installation. The
repository ships one framework-neutral core and one adapter per supported agent
framework. It does not require a framework checkout at runtime.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+ and npm for OpenCode or OpenClaw
- Git
- An installed OpenCode, OpenClaw, Claude Code, or NanoBot host

Windows users should use PowerShell. The installer resolves `.cmd`, `.exe`, and
`.bat` host commands automatically.

## Verify a checkout

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading

PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --with pytest --with pytest-asyncio pytest tests/test_release_fixtures.py -q
```

For a full local regression:

```bash
PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --with pytest --with pytest-asyncio pytest -q
```

On PowerShell, replace the `:` separators in `PYTHONPATH` with `;`.

## Install an adapter

OpenCode:

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/your/project \
  --doctor
```

OpenClaw:

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --doctor
```

Claude Code:

```bash
python3 scripts/install_sparseread.py \
  --platform claude \
  --claude-workspace /path/to/your/project \
  --doctor
```

NanoBot uses ordinary Python packages:

```bash
uv pip install \
  packages/sparseread-core \
  integrations/nanobot/python
```

The NanoBot host loads `sparseread-nanobot` through its adapter entry point.

## Modes

The default `auto` mode routes high-confidence sparse-reading tasks through
SparseRead while leaving small or computation-heavy tasks native. Use
`advisory` when you want the tools and model guidance without intercepting
native reads.

Users normally do not call `sro_preview` or `sro_read` manually. Ask the agent
to use SparseRead for a large artifact and let the selected adapter expose the
protocol.

## Platform notes

- OpenCode uses a workspace plugin plus a managed Python bridge.
- OpenClaw uses a packed npm plugin plus a managed Python bridge.
- Claude Code uses an MCP stdio server, `PreToolUse`/`PostToolUse` hooks, and a
  generated `CLAUDE.md` when the workspace does not already have one.
- NanoBot uses `sparseread-core` and `sparseread-nanobot` as Python dependencies.

For architecture and package ownership, see
[`docs/release_architecture.md`](release_architecture.md). For the Chinese
installation guide and the detailed Windows matrix, see
[`docs/sparseread_installation.md`](sparseread_installation.md).
