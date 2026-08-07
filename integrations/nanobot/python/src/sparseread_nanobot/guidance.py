"""Model-visible SparseRead guidance injected by the NanoBot hook.

This is the exact body of the vendored NanoBot host SKILL.md so the new
hook-based integration exposes the same model guidance as the old host.
"""

SRO_GUIDANCE = """# Sparse Reading

Use SRO for a large supported object when `sro_preview`, `read_file`, or
`list_dir` recommends it. SRO results are task evidence: do not replace
resolved evidence with broad raw reads.

**Activation boundary:** At the start of a likely long-document, cross-file
evidence, or large multi-table analysis episode, call `sro_preview` once with
the lightweight `episode_hint` below. Also use this protocol when a native tool
returns an SRO handoff. Otherwise continue with native tools.
An audit or diagnosis that reconciles three or more source files is a
`cross_file_evidence` episode: preview the collection before listing or reading
its children. Analysis over three or more structured datasets is
`structured_compute` even when the deliverables include a script and report;
use one bounded schema/evidence plan, then compute natively. A request whose
primary deliverable is source code, a query, configuration, or a single-table
exact calculation is `edit_or_execute` even when you must read supporting
documentation; keep that workflow native.
For ordinary native code/config/data tasks, keep the workflow bounded: read the
authoritative inputs, make the smallest complete change, run focused
verification, then stop when the requested deliverables pass.

`episode_hint` is a boundary label, not a detailed plan:

- `relation`: `new` for a new task, `continue` for a follow-up on the same
  evidence goal and resource scope, `switch` when moving to unrelated work.
- `goal`: `selective_read`, `cross_file_evidence`, `structured_compute`,
  `edit_or_execute`, `full_fidelity`, or `unknown`.
- `coverage`: `selective`, `exhaustive`, or `unknown`.
- `summary`: one short sentence. Paraphrasing it does not start a new episode.

Put this hint on the first `list_dir` or `read_file` call when that is the
natural first action. Those native tools pass the label into the same Gate, so
you do not need an extra classification request or a speculative preview.

The Gate, not the hint, remains authoritative. Unsupported/small sources,
editing/execution, full-fidelity reading, and single-table exact computation
stay native. A large multi-table analysis may use one bounded SparseRead
evidence/schema plan and then return to native computation.
Do not call `sro_read` after a preview/episode decision says `native`.

**Terminal write rules (highest priority):**
- If `slot_digest.overall_status` is `"ready"`, write the requested output
  from its candidates immediately. It overrides individual slot confidence:
  do not verify, search, or raw-read the source before writing.
- If a collection response allows `write_file`, write from its evidence
  immediately. Do not inspect covered source files or code with `read_file`,
  `grep`, or `exec` unless the response names a specific unresolved fact.
- Write in the requested format. `one answer per line` means unnumbered
  answer values in the original question order.

## Protocol

1. If a tool result already contains an SRO handoff and `artifact_id`, follow
   its `next_action`; do not call `sro_preview` again.
2. Otherwise call `sro_preview(path, episode_hint=...)` before reading a known
   large object or collection at an episode boundary. The preview contains the
   FileCard plus a deterministic default view and does not require a HintSpec.
3. Use `{"path": "/path"}` only for first discovery. Use
   `{"artifact_id": "sro_..."}` for every follow-up.
4. After an SRO read, follow its `next_action` or `allowed_next`. For the
   first read, route the explicit user need with the table below.

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

`sro_card` remains available for benchmark and legacy compatibility, but it is
not the production entrypoint.

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
`exec` after SRO has supplied evidence. For a small complete table request,
use `want: "table"` with `scope: "expand"` and state `all rows` in the goal.

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
"""
