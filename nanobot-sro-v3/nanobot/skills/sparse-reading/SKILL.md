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

When `sro_preview` is available, use the production path:

```text
sro_preview(path) -> optional sro_read(target={artifact_id}, mode, HintSpec) -> write_file
```

If only `sro_card` and `sro_read` are registered by `bench_protocol`, use the
original compact card/read path and do not ask for preview or raw fallback tools.

Use SRO after a tool recommends SRO or returns an SRO handoff. If no such
signal appears, keep the agent's native workflow bounded: read authoritative
inputs, make the smallest complete change, run focused verification, then stop.

For a known large CSV, TSV, XLSX, JSON, YAML, or XML object, choose the path
from the requested operation before opening it:

- If the request names one record, row, key, entity, or other concrete target
  and asks for only a few fields, call the available SRO discovery tool and use
  at most one focused `sro_read` if the preview does not already contain it.
- If the request gives a workbook formula and exact cell/range, call
  `sro_preview` once. The preview is formula-first and should expose the target
  neighborhood plus small source-sheet samples. If that is sufficient, run one
  bounded edit script and one focused verification; otherwise make at most one
  focused `sro_read`. Never dump whole worksheets or repeat discovery.
  When writing Excel formulas with `$` absolute references, create the edit
  script with `write_file` and execute it with `python3` (not `python`). Never embed such formulas in
  a double-quoted `python -c` shell command: shell expansion corrupts `$A$1`
  ranges and can silently produce an invalid workbook. After the edit exits 0,
  make one compact formula/format check against the generated workbook and stop;
  do not reopen the source workbook or add a separate existence/listing check.
- If the request needs all rows, aggregation, a join, regression, formula
  recalculation, or another full-table computation, use SRO to select the
  authoritative source files and expose their schema/calculation contract, then
  run local code over every selected row. Full computation does not require
  full table contents in the model context. The script should write the requested
  deliverable directly and print only a compact aggregate/validation result; do
  not print row tables or read persisted stdout back into the conversation.

**Terminal write rules (highest priority):**
- If `slot_digest.overall_status` is `"ready"`, write the requested output from
  its candidates immediately. Do not verify, search, or raw-read the source
  before writing.
- If a collection response allows `write_file`, write from its evidence
  immediately. Do not inspect covered source files or code unless the response
  names a specific unresolved fact.
- If a collection response says `overall_status: ready_for_compute`, accept its
  selected/excluded sources and calculation contract. Write one short script,
  run it once, and stop after the requested deliverables exist and the run exits
  successfully. Do not call `sro_read` on child files or rerun discovery.
- Write in the requested format. `one answer per line` means unnumbered answer
  values in the original question order.

## Protocol

1. If a tool result already contains an SRO handoff and `artifact_id`, follow
   its `next_action`.
2. Otherwise call the registered discovery tool (`sro_preview` in auto mode,
   `sro_card` in bench protocol) before reading a known large object.
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
| Structured multi-file calculation/regression | `collect` once for a `ready_for_compute` source/schema plan, then one local script over the selected paths |
| Collection returns `candidate_targets` | Choose only the needed child `artifact_id`, then call `sro_read` on it; do not rediscover files with shell or `sro_card` |
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
| Collection `overall_status: "ready_for_compute"` | Run one local script over selected sources; keep stdout compact and do not reread it. |
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
