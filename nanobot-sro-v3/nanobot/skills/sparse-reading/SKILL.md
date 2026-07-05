---
name: sparse-reading
description: Use Sparse Reading Orchestrator for supported large structured files and long text objects.
always: true
metadata:
  nanobot:
    always: true
    requires:
      env:
        - SRO_ENABLED
---

# Sparse Reading

This branch is the paper/benchmark profile. When `SRO_ENABLED=1`, use the
original compact path:

```text
sro_card(path) -> sro_read(target={artifact_id}, mode, HintSpec) -> write_file
```

`sro_preview` is the production entrypoint on the single-repo integration
branch, but it is not the default trajectory for this nanobot benchmark branch.
If only `sro_card` and `sro_read` are registered, do not ask for preview or raw
fallback tools.

Use SRO after a tool recommends SRO or returns an SRO handoff. If no such
signal appears, keep the agent's native workflow bounded: read authoritative
inputs, make the smallest complete change, run focused verification, then stop.

**Terminal write rules (highest priority):**
- If `slot_digest.overall_status` is `"ready"`, write the requested output from
  its candidates immediately. Do not verify, search, or raw-read the source
  before writing.
- If a collection response allows `write_file`, write from its evidence
  immediately. Do not inspect covered source files or code unless the response
  names a specific unresolved fact.
- Write in the requested format. `one answer per line` means unnumbered answer
  values in the original question order.

## Protocol

1. If a tool result already contains an SRO handoff and `artifact_id`, follow
   its `next_action`.
2. Otherwise call `sro_card(path)` before reading a known large object.
3. Use `{"path": "/path"}` only for first discovery. Use
   `{"artifact_id": "sro_..."}` for every follow-up.
4. After an SRO read, follow its `next_action` or `allowed_next`. For the first
   read, route the explicit user need with the table below.

| Need | First useful `sro_read` |
| --- | --- |
| Multiple explicit questions about one PDF or long text | `collect` immediately, with `hint.slots` in the user's original question order |
| One fact or named section | `focus` with concrete `needles` |
| Goal is unclear | `scout`, then `focus` |
| Collection audit, diagnosis, rules, or cross-file facts | `collect` for source-keyed excerpts |
| Exact check of unresolved evidence | `verify` with the existing `artifact_id` |

Use `refine` only for unresolved evidence on an existing artifact.
For `hint.scope`, use only `"new"`, `"narrow"`, `"verify"`, or `"expand"`;
use `"verify"` for an exact follow-up check. Do not invent scope values.

## Stop Or Continue

| Returned signal | Action |
| --- | --- |
| `slot_digest.overall_status: "ready"` | Apply the terminal write rule above. |
| `slot_digest.overall_status: "needs_verify"` | Verify only `unresolved_slots` or low-confidence slots. |
| Non-empty `unresolved` | Refine or verify only those missing items. |
| `calc_ready` with no unresolved item | Run one short calculation from its TSV artifact(s); do not reread the source. |
| Collection `allowed_next` includes `write_file` | Apply the terminal write rule above. |
| `sro_guard` for a covered source | Use the existing digest; do not retry broad reads on that source. |

Do not dump a supported large source with `read_file`, `cat`, `grep`, or
`exec` after SRO has supplied evidence. For a small complete table request, use
`want: "table"` with `scope: "expand"` and state `all rows` in the goal.

## Multi-Question Text Template

```json
{
  "target": {"artifact_id": "sro_..."},
  "mode": "collect",
  "hint": {
    "goal": "Answer the requested report questions",
    "type_hint": "pdf",
    "slots": [
      {"id": "q1", "question": "copy the first user question", "expected": "number"},
      {"id": "q2", "question": "copy the second user question", "expected": "date"}
    ]
  }
}
```
