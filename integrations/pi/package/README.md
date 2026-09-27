# @sparseread/pi

Pi extension for SparseRead. It forwards `sro_preview`, `sro_read`, `sro_raw`,
`sro_card`, `sro_decide`, and `sro_trace` to the shared Python bridge over
JSONL protocol `1.0`.

Available in the GitHub `v0.1.2` source installer bundle. The npm tarball alone
does not configure Python: use the project installer below.
If Pi rejects an untrusted project, review its files/extensions and explicitly
rerun the installer with `--pi-approve` (this install command only, not permanent
trust). SparseRead never adds that approval automatically.

Raw ranges are zero-based Unicode character offsets with an exclusive end,
not bytes/lines. Default raw reads return at most 50000 characters: check
`truncated`. Preview again after session reset; stale references are tool errors.

## Install for a workspace

Run the installer from a SparseRead checkout, with Pi available on `PATH`:

```bash
uv run --project /path/to/sparse-reading python /path/to/sparse-reading/scripts/install_sparseread.py \
  --platform pi --workspace /path/to/project
```

The installer stages this package at
`/path/to/project/.sparseread/pi/sparseread-pi`, writes
`.sparseread-runtime.json` in that package root, and registers the absolute
package path with Pi in project scope. The runtime file contains the managed
Python executable, workspace, mode (`auto` or `advisory`), and protocol.

If Pi reports that the runtime file is missing, install the workspace-managed
package with the command above and reload Pi. A package installed directly
from npm or git has no workspace Python runtime by itself.

Pi extensions run inside the Pi process with its operating-system permissions.
Review the package and grant project trust only when you trust the source.

## Read behavior

In `auto` mode, SparseRead checks unbounded Pi `read` calls. It redirects a
read only when the shared core returns `host_gate.block_native_read: true`;
bounded reads pass through. Bridge errors, timeouts, aborts, and targets outside
the configured workspace keep Pi's native read available. `advisory` mode never
blocks native reads.

## Development

```bash
npm ci
npm run typecheck
npm test
npm pack --dry-run
```

The extension is distributed as TypeScript source through `pi.extensions` and
Pi's loader. Its Pi SDK peer dependencies are supplied by Pi.
