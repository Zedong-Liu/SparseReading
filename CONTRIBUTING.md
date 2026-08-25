# Contributing to SparseRead

Thanks for helping make agent reading more efficient and more trustworthy.

## Before you start

Please open an issue for a large behavioral change or a new framework adapter
before implementation. Small bug fixes, documentation improvements, tests, and
reproducible benchmark fixes can go directly into a pull request.

Do not include API keys, private workspaces, generated transcripts, local model
weights, or unreviewed benchmark dumps in a commit.

## Development setup

Use Python 3.11+ and `uv`. The core and each adapter are independently
packaged:

```bash
uv run --project packages/sparseread-core --with pytest --with pytest-asyncio \
  pytest packages/sparseread-core/tests -q

PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --with pytest --with pytest-asyncio pytest -q
```

If you change a JavaScript plugin, also run its build:

```bash
npm --prefix integrations/opencode/plugin ci
npm --prefix integrations/opencode/plugin run build
npm --prefix integrations/openclaw/plugin ci
npm --prefix integrations/openclaw/plugin run build
```

## Pull requests

- Keep changes scoped and explain the user-visible behavior.
- Add or update a regression test for behavior changes.
- Keep the framework-neutral protocol in `packages/sparseread-core`.
- Keep host-specific lifecycle and bridge logic in `integrations/<framework>`.
- Update the relevant README or docs when installation or support changes.
- Report benchmark results with the model, framework, task set, mode, and
  whether the run was local or remote.

CI runs core tests, the full Python suite, package builds, plugin builds, and
Windows installer checks.
