# SparseRead File Reading Protocol

This project uses SparseRead (SRO) to optimize reading of large files. SRO
tools are available as MCP tools (`sro_preview`, `sro_read`, `sro_card`,
`sro_raw`, `sro_decide`, `sro_trace`, `sro_preflight`, `sro_usage`).

## When to use SparseRead

| File Type | Action |
|---|---|
| PDF files (any size ≥4KB) | MUST use `sro_preview()` first, then `sro_read()` for evidence |
| Text/Markdown/log >12KB | MUST use `sro_preview()` first |
| Directories with 3+ text files | MUST use `sro_preview()` first |
| CSV/JSON/structured data | PREFER `sro_preview()` for schema exploration |
| Code files (.py, .sh, .toml, .js, .ts) | Direct `read_file()` is fine |
| Small configs (<4KB) | Direct `read_file()` is fine |

## Episode hint

At the start of a multi-file audit or structured-compute episode, pass
`episode_hint` on the first `sro_preview`:

```json
{
  "episode_hint": {
    "goal": "cross_file_evidence",
    "relation": "new",
    "coverage": "selective",
    "summary": "one sentence describing the episode"
  }
}
```

`goal` is one of `selective_read`, `cross_file_evidence`,
`structured_compute`, `edit_or_execute`, `full_fidelity`, `unknown`.
`relation` is `new`, `continue`, `switch`, or `unknown`.
The Gate, not the hint, remains authoritative.

## SRO workflow

1. `sro_preview(path="...")` — structure overview, content samples, signals.
2. If preview is sufficient → answer directly.
3. If evidence is needed → `sro_read(target={"artifact_id": "..."},
   mode="collect", hint={goal, slots/needles})`.
4. When `sro_read` returns `overall_status: ready` → write the deliverable
   immediately; do NOT re-read.
5. `sro_raw(raw_ref="...")` only for unfiltered content (last resort).

## Don't do this

- Don't `read_file()` on PDFs or files >12KB (the hook may block it).
- Don't re-read after `sro_read` returns `ready`.
- Don't use SRO for small code/config files (native read is cheaper).
- Don't `cat`/`head`/`tail` large files (the hook may block it).

## Debugging

- `sro_decide(path="...")` — gate decision for any path.
- `sro_trace()` — session event log and summary.
- `sro_usage()` — token consumption metrics.
