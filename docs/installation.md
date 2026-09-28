# SparseRead installation

This guide describes the current single-repository source installation. The
repository ships one framework-neutral core and one adapter per supported agent
framework. It does not require a framework checkout at runtime.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Node.js 22+ for OpenCode/OpenClaw/Codex/Pi; npm for OpenCode/OpenClaw builds
- Git
- An installed OpenCode, OpenClaw, Claude Code, NanoBot, Codex, or Pi host

Windows users should use PowerShell. The installer resolves `.cmd`, `.exe`, and
`.bat` host commands automatically.

The installer validates Python before changing a workspace. The `uv run` form
below supplies a compatible interpreter; when invoking the script directly,
pass `--python /path/to/python3.12` if the default `python3` is older than 3.11.
PDF/XLSX reader dependencies are installed by default; use
`--reader-extras none` for a text-only runtime.

## Verify a checkout

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading

PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --project . --extra test pytest tests/test_release_fixtures.py -q
```

For a full local regression:

```bash
PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --project . --extra test pytest -q
```

On PowerShell, replace the `:` separators in `PYTHONPATH` with `;`.

## Install an adapter

Codex or Pi (project-scoped):

Download the `v0.1.3` source-installer ZIP/tar.gz from GitHub Releases and extract
it; the `v0.1.1` installer does not include these adapters.

```bash
uv run --python 3.12 python scripts/install_sparseread.py \
  --platform codex --workspace /path/to/your/project --doctor
uv run --python 3.12 python scripts/install_sparseread.py \
  --platform pi --workspace /path/to/your/project --doctor
```

Restart/reload in the project and review its trust prompt. Codex hook trust is
separate from plugin installation; Pi project extensions also require approval.
Use `--sparseread-mode advisory` for non-blocking guidance. Read the
[Codex/Pi design and recovery notes](codex-pi-adapters.md) for tested host versions,
cache-safe reinstalls, native fallback, and source-package limitations.

OpenCode:

```bash
uv run --project . python scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/your/project \
  --doctor
```

OpenClaw:

```bash
uv run --project . python scripts/install_sparseread.py \
  --platform openclaw \
  --doctor
```

Claude Code:

```bash
uv run --project . python scripts/install_sparseread.py \
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
