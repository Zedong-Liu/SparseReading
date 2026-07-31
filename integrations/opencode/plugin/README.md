# @sparseread/opencode

OpenCode plugin for SparseRead. It registers SparseRead tools and lifecycle
guards, then talks to the independently installed `sparseread-opencode` Python
bridge over JSONL protocol `1.0`.

Build and inspect the publishable package:

```bash
npm ci
npm run build
npm pack --dry-run
```
