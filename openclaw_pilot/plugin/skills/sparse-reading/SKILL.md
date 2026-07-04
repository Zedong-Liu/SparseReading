---
name: sparse-reading
description: Use SparseRead for long documents, PDFs, and compact multi-file evidence closures without disrupting small native reads.
---

Use this skill only when a tool result, `sro_preview`, or the task shape indicates
SparseRead is useful. Keep ordinary native `read`, `grep`, and `exec` workflows
for small files, small config/code/data tasks, exact full-table computation, and
script-driven analysis.

SparseRead protocol:

1. Call `sro_preview(path)` on a large file, PDF/report, or compact evidence
   directory before broad native reading. The preview includes the FileCard and
   does not require a HintSpec.
2. Continue with `sro_read({ artifact_id }, mode, hint)`. For multi-question
   documents and evidence closures, prefer `mode: "collect"` with explicit
   `slots`.
3. If `slot_digest.overall_status` is `ready` or `next_action.allowed_next`
   allows writing, write the requested deliverable. Do not keep broad-reading
   or repeating `sro_read` after ready.
4. Native small reads are still allowed for setup files, templates, unsupported
   objects, and named unresolved slots.

OpenClaw gate profiles:

- `native`: do not push SparseRead.
- `advisory`: SparseRead may help, but native tools stay available.
- `enforce`: broad native read/search/exec-dump should use
  `sro_preview -> targeted sro_read` first.

Command-security closures are advisory, not hard replacement. Use one
`sro_read(mode="collect")`; once ready, write the report/JSON immediately. Read
small templates or named unresolved files natively when needed.

`sro_card` remains available only for benchmark and legacy compatibility.
