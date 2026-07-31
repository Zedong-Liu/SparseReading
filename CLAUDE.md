# SparseReading Project

## SparseRead File Reading Protocol

This project uses SparseRead (SRO) to optimize reading of large files. SRO tools
are available as MCP tools (`sro_preview`, `sro_read`, `sro_card`, `sro_raw`,
`sro_decide`, `sro_trace`, `sro_preflight`, `sro_usage`).

### When to use SparseRead

| File Type | Action |
|-----------|--------|
| **PDF files** (any size ≥4KB) | **MUST** use `sro_preview()` first, then `sro_read()` for evidence |
| **Text/Markdown/log >12KB** | **MUST** use `sro_preview()` first |
| **Directories with 3+ text files** | **MUST** use `sro_preview()` first |
| **CSV/JSON/structured data** | **PREFER** `sro_preview()` for schema exploration |
| **Code files** (.py, .sh, .toml, .js, .ts, .go, .rs) | Direct `read_file()` is fine |
| **Small configs** (<4KB) | Direct `read_file()` is fine |

### SRO Workflow

1. `sro_preview(path="...")` — structure overview, content samples, key signals
2. If preview sufficient → answer directly
3. If need evidence → `sro_read(target={"artifact_id": "..."}, mode="collect", hint={...})`
4. When `sro_read` returns `"overall_status": "ready"` → **write deliverable immediately, do NOT re-read**
5. `sro_raw(raw_ref="...")` — only for unfiltered content access (last resort)

### Don't Do This

- Don't `read_file()` on PDFs or files >12KB (will be blocked by hook)
- Don't re-read after `sro_read` returns "ready"
- Don't use SRO for small code files (native read is cheaper)
- Don't use `cat`/`head`/`tail` on large files (will be blocked by hook)

### Debugging SRO

- `sro_decide(path="...")` — see gate decision for any path
- `sro_trace()` — session event log and summary
- `sro_usage()` — token consumption metrics and savings report
