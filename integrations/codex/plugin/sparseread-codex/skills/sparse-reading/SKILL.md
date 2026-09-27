---
name: sparse-reading
description: Use SparseRead tools to inspect large, structured, or multi-file project inputs with targeted evidence.
---

# SparseRead in Codex

Use SparseRead when an answer depends on a large document, structured file, or a collection of project files.

- Call `sro_decide` to inspect the recommended reading route for a path.
- Call `sro_preview` before exploring a large or structured input. Use the returned card and preview to choose the evidence needed.
- Call `sro_read` with a focused goal and suitable mode such as `scout`, `focus`, `collect`, or `verify`.
- Call `sro_raw` only with a `raw_ref` returned by SparseRead and a narrow range or selector.
- Call `sro_card` for file metadata and `sro_trace` to explain recent SparseRead routing.

Small and bounded reads can use Codex's normal tools. If a hook redirects a broad shell read, preview the named path and use the evidence tools before retrying the native command.

Codex does not run the bundled hooks until you review and trust their definitions in `/hooks`. Trust is a user decision; the plugin does not bypass it.
