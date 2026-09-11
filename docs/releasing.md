# Releasing SparseRead to PyPI and npm

This runbook is for maintainers. Registry versions are immutable, so every
upload is built and inspected before a tag is allowed to publish anything.

## Release surfaces

One version is shared by all registry artifacts:

| Registry | Package |
|---|---|
| PyPI | `sparseread` |
| PyPI | `sparseread-nanobot` |
| PyPI | `sparseread-opencode` |
| PyPI | `sparseread-openclaw` |
| PyPI | `sparseread-claude` |
| npm | `@sparseread/opencode` |
| npm | `@sparseread/openclaw` |

The source path `packages/sparseread-core` intentionally stays unchanged. Its
public PyPI distribution is named `sparseread` for a simpler installation.

## One-time registry setup

### GitHub environments

Create protected environments named `pypi` and `npm` in repository settings.
Add required reviewers so a pushed release tag cannot publish without a human
approval. Do not place long-lived PyPI credentials in GitHub.

### PyPI trusted publishers

Create a PyPI account with two-factor authentication. For each of the five
Python package names above, add a pending GitHub publisher with these exact
values:

```text
Owner: Zedong-Liu
Repository: SparseReading
Workflow: release.yml
Environment: pypi
```

Pending publishers allow the first release to use GitHub OIDC; an API token is
not needed. Confirm that all five names are still available before creating
the release tag. See the official
[PyPI trusted publisher guide](https://docs.pypi.org/trusted-publishers/adding-a-publisher/).

### npm scope and first publication

Create or gain publish access to the `@sparseread` npm organization/scope and
enable two-factor authentication. npm requires a package to exist before its
trusted publisher can be configured, so the first release needs one temporary
granular access token:

1. Create a granular npm token limited to the two package names, with bypass
   2FA enabled only if npm requires it for CI publishing.
2. Add it as the `NPM_TOKEN` secret in the protected `npm` GitHub environment.
3. Push the first release tag and approve the `npm` environment deployment.
4. In each npm package's Trusted Publisher settings, select GitHub Actions and
   configure repository `Zedong-Liu/SparseReading`, workflow `release.yml`, and
   environment `npm`.
5. Delete `NPM_TOKEN` from GitHub and revoke the npm token. Later releases use
   OIDC only.

The workflow deliberately supports both the one-time token bootstrap and the
normal tokenless trusted-publisher path. npm publishes with provenance; see the
official [npm trusted publisher guide](https://docs.npmjs.com/trusted-publishers/).

## Build without publishing

Run the complete local build with Python 3.12, uv, Node.js 24, and npm 11.12.1:

```bash
release_dir=$(mktemp -d)
uv run --python 3.12 python scripts/build_release.py --output "$release_dir"
uvx twine check "$release_dir"/python/*
```

This produces five wheels, five source distributions, and two npm tarballs.
It verifies package names and versions, bundled licenses, project links,
typing markers, compiled JavaScript, OpenClaw plugin resources, and production
npm dependencies. It does not upload anything.

The `Release registries` workflow can also be started manually. A
`workflow_dispatch` run builds, smoke-tests, and stores the artifacts but never
publishes them. Only a `v*` tag can enter publishing jobs.

## Release checklist

1. Update all package versions, module `__version__` values, the OpenClaw
   manifest, and `CHANGELOG.md`.
2. Run `python scripts/check_release.py` under Python 3.11+ and the full test
   suite.
3. Run the local build above, inspect the generated artifacts, and run a manual
   build-only GitHub workflow.
4. Confirm the PyPI and npm environments have the expected reviewers and the
   trusted-publisher settings point to `release.yml`.
5. Create and push an annotated tag matching the package version:

   ```bash
   git tag -a v0.1.1 -m "SparseRead v0.1.1"
   git push origin v0.1.1
   ```

6. Approve the protected environments after the build and smoke tests pass.
7. Verify installability in clean environments and then publish the GitHub
   release notes.

If one registry job fails after another succeeds, do not retag or overwrite a
version. Fix only the external configuration and rerun the failed job. PyPI
uploads skip an already-existing file, and npm jobs confirm an already-existing
version before attempting a publish.

## Post-release checks

```bash
python -m venv /tmp/sparseread-check
/tmp/sparseread-check/bin/pip install "sparseread[all]==0.1.1"
/tmp/sparseread-check/bin/python -c "import sparseread; print(sparseread.__version__)"

npm view @sparseread/opencode@0.1.1 dist.integrity
npm view @sparseread/openclaw@0.1.1 dist.integrity
```

Use a platform-appropriate temporary directory on Windows instead of `/tmp`.
