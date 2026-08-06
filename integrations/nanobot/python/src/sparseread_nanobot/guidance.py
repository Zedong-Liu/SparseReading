"""Model-visible SparseRead guidance injected by the NanoBot hook."""

SRO_GUIDANCE = """\
[SparseRead protocol]

Use SRO for large supported objects when a native read/list/grep would pull
too much content. SRO results are task evidence: do not replace resolved
evidence with broad raw reads.

Activation boundary:
- At the start of a likely long-document, cross-file evidence, or large
  multi-table analysis episode, call `sro_preview` once with `episode_hint`
  (goal/relation/coverage/summary). Also use SRO when a native tool returns an
  SRO handoff.
- Multi-file audit/diagnosis that reconciles three or more sources is a
  `cross_file_evidence` episode: preview the collection before reading its
  children.
- Analysis over three or more structured datasets is `structured_compute`:
  use one bounded schema/evidence plan, then compute natively.
- A request whose primary deliverable is source code, a query, configuration,
  or a single-table exact calculation is `edit_or_execute`: keep it native.

Episode hint is a boundary label, not a detailed plan:
- `relation`: `new` / `continue` / `switch`
- `goal`: `selective_read` / `cross_file_evidence` / `structured_compute` /
  `edit_or_execute` / `full_fidelity` / `unknown`
- `coverage`: `selective` / `exhaustive` / `unknown`
- `summary`: one short sentence.

The Gate, not the hint, remains authoritative. Unsupported/small sources,
editing/execution, full-fidelity reading, and single-table exact computation
stay native. When `sro_read` returns `overall_status: ready`, write the
deliverable immediately; do not re-read or dump resolved sources.
"""
