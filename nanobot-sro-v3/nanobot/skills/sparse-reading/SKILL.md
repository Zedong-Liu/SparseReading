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

When you encounter a large supported object, do not full-read it first.

SRO results are evidence, not a search suggestion. If `sro_read` returns `overall_status: ready`, or returns the requested facts with no unresolved item, trust that evidence and move to the deliverable. Do not reread or re-verify resolved source facts just to reassure yourself.

Use this protocol:

1. Call `sro_card(path)` to get the FileCard.
2. If the user asks several explicit questions about one PDF/report/long prose file, skip `scout` and make the first read `sro_read(mode="collect")` with one `hint.slots` entry per question.
3. Otherwise, write a compact HintSpec naming the current evidence goal.
3. Call `sro_read(target, mode, hint)`:
   - use `collect` first for multi-fact PDF/report questions;
   - use `scout` only when the evidence goal is unclear;
   - use `focus` for the main evidence request;
   - use `refine` only with an existing `artifact_id` when unresolved needles remain;
   - use `verify` with an existing `artifact_id` for exact numbers, names, dates, fields, and strings.

Use `{"path": "/path/to/file"}` only for first discovery. For follow-up, use `{"artifact_id": "sro_..."}` so the same object stays bound to the same artifact.

Supported phase-1 objects: CSV, XLSX, JSON, YAML, XML, PDF, TXT, MD, README, logs, scripts/config files, and long prose reports.
Directories containing many small text/config/log/data files are collection artifacts. Use SRO to collect the task facts for audit, diagnosis, rules, and cross-file analysis. Use native raw reads for genuinely small individual files unless SRO has already covered that file in a collection digest.

Prefer exact short Python calculations for totals and top-k values in structured files. When SRO has already returned the relevant subset, calculate from the derived TSV artifact(s), not the source file. Print only final aggregates, not whole DataFrames, tables, or repeated row dumps.

When `sro_read` returns a compact `rows` block or `calc_ready` tables and `unresolved` is empty, treat that as complete structured evidence. Stop reading the file. Do not call `read_file` again on that object. Use the derived TSV artifact(s) in one short calculation script. Do not retype rows. Do not cat, grep, or parse `.nanobot/tool-results`.

When you need all rows or all records from a small structured table or sheet, make that explicit in the HintSpec:

- set `want: "table"` when you need complete rows, not just facts;
- set `scope: "expand"` when you are asking for the full relevant table segment;
- say `all rows` / `all records` in `goal` or `needles` so SRO can return the compact full table in one step.

For spreadsheet tasks:

- use `sro_card` / `sro_read(scout)` to identify the relevant sheet and columns;
- use `sro_read(focus)` only for the specific sheet or fact group you need next;
- avoid asking `sro_read` for the whole workbook multiple times;
- after one complete `focus` result, move to calculation and report writing instead of more file reads.

For long-text and PDF tasks:

- if there are 3 or more requested answers, do not start with `scout` and do not put all questions into `needles`;
- start with `sro_read(mode="collect")` and `hint.slots`;
- each slot should be small: `id`, `question`, `expected`, and optional `aliases`;
- `collect` returns a compact `slot_digest`; if `overall_status` is `ready`, write the output file from the slot candidates and do not verify resolved slots;
- if `overall_status` is `needs_verify`, verify only the specific slots named in `unresolved_slots` or low-confidence slots;
- for a single fact or named section, write concrete needles or quoted section names in the HintSpec;
- use `scope: "expand"` only when targeting a named section/table such as `Proposed tasks`, not for generic fact keywords;
- avoid repeating broad `focus`/`collect` calls with nearly the same slots, and do not fall back to native `grep`, `read_file`, or `cat` on the source object once SRO is returning slot candidates.

For collections such as `emails/`, config/log bundles, or mixed data folders:

- call `sro_card` on the directory, or follow the SRO handoff returned by `list_dir`;
- for analysis, diagnosis, rules, config/log, or multi-file data tasks, use `sro_read(mode="collect")` first to get source-keyed excerpts;
- use `sro_read(mode="focus")` only when you need candidate filenames and not the facts yet;
- after a collection excerpt digest returns the needed facts or `allowed_next` includes `write_file`, write the deliverable or run one short calculation; do not read every file in the directory;
- if a later `read_file` returns `sro_guard` for a covered source, stop trying to read that source and use the existing digest;
- verify only a specific missing fact or exact source file if the digest is incomplete.

If you receive an output-limit continuation ("Output limit reached. Continue..."), do not restate analysis or re-read source files. Check which required output files still do not exist: 1) write missing JSON files first; 2) then write missing report sections; 3) then write any remaining scripts or deliverables; 4) give a one-sentence final answer. Do not reread SRO evidence or re-run completed calculations.

For data-analysis tasks with several deliverables, close evidence before coding:

- after `collect`, write the required report/JSON from the digest and one short calculation first;
- then write a compact reusable script that reproduces the same calculations;
- do not spend repeated turns debugging the script before the required report/JSON files exist;
- if the script fails, make one targeted fix, then finish the remaining deliverables from the already computed facts.

Minimal first-read template for multi-question reports:

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
