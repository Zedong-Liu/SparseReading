# @sparseread/opencode

OpenCode plugin for SparseRead. It registers SparseRead tools and lifecycle
guards, then talks to the independently installed `sparseread-opencode` Python
bridge over JSONL protocol `1.0`.

```bash
npm install @sparseread/opencode
pip install sparseread-opencode
```

Both components are required: npm provides the OpenCode host plugin and PyPI
provides its Python bridge. The repository installer can configure both into a
workspace automatically.

Build and inspect the publishable package:

```bash
npm ci
npm run build
npm pack --dry-run
```
