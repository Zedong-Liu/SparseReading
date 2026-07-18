# SRO v3 Development Outcomes

## 2026-07-18: Structured convergence, Kimi repair, and five-model refresh

Objective:

- Diagnose Kimi-K2.5's low-score, high-token structured trajectories without
  adding task ids, filenames, fixed cells, answer values, or model-specific
  routing.
- Re-run the four structured positives (T58, T73, SpreadsheetBench Verified
  49333, and 11276) on DeepSeek-V4-Pro and GLM-5.1 after the generic repair.
- Replace the structured rows for all five main-experiment models, omit one
  confirmed provider-stall time pair, and regenerate the canonical figures.

Root causes and generic repairs:

- Structured children under a `collect_then_native_compute` collection were
  previewed independently, so Kimi missed the parent compute closure and
  repeatedly generated and repaired large scripts. Child previews now bind to
  the parent artifact and return one family-specific bounded compute handoff.
- Panel-data closure is triggered by input shape rather than model hint wording.
  Its contract preserves panel columns during metadata merge, prevents
  duplicate identifiers/suffixes, requires native scalar serialization, and
  specifies one fixed-effects/clustered-SE execution plus compact reporting.
- P&L closure now uses exact source columns and a deterministic compact profile
  for date coverage, totals, losses, fee-subsidized profitable deals, and
  historical comparisons. Loss classification is based only on total P&L.
- Formula preview detects horizontal formula-fill ranges and returns a terminal
  `ready_for_edit` handoff. This fixed Kimi 11276 without workbook/task-specific
  constants.

Final four-task structured aggregate (`score sum / mean`; costs are Native to
SR):

| Model | Native score | SR score | Tokens | Requests | Seconds |
| --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Flash | 3.969 / 0.992 | 4.000 / 1.000 | 1,839,320 -> 1,084,458 (-41.0%) | 77 -> 45 (-41.6%) | 868.0 -> 257.4 (-70.3%) |
| DeepSeek-V4-Pro | 3.520 / 0.880 | 3.969 / 0.992 | 1,845,623 -> 795,374 (-56.9%) | 70 -> 37 (-47.1%) | 909.1 -> 371.2 (-59.2%) |
| Qwen3.6-Plus | 2.677 / 0.669 | 4.000 / 1.000 | 1,038,806 -> 515,464 (-50.4%) | 64 -> 37 (-42.2%) | 935.3 -> 298.5 (-68.1%; 3 valid pairs) |
| GLM-5.1 | 3.428 / 0.857 | 4.000 / 1.000 | 2,344,300 -> 842,450 (-64.1%) | 112 -> 41 (-63.4%) | 2,075.0 -> 585.9 (-71.8%) |
| Kimi-K2.5 | 3.538 / 0.884 | 3.813 / 0.953 | 492,715 -> 415,344 (-15.7%) | 32 -> 24 (-25.0%) | 535.0 -> 313.2 (-41.5%) |

Protocol notes:

- Qwen T58 score/tokens/requests are retained, but both wall-clock values are
  omitted because the SR run had a confirmed provider-side stall. Time
  aggregation uses only paired valid measurements.
- Pro Native T73 passed all six automated checks; the original judge returned
  empty, and a same-deliverable rejudge scored 1.00. Original cost metrics are
  retained.
- Kimi-K2.6 is now API-accessible but rejected for the paper result: its paired
  smoke was unstable, including three Native 50-request caps and an SR T58
  loop. Kimi-K2.5 remains the approved model.
- GLM 49333 remains a task-level variance boundary (quality held, tokens and
  requests increased), while the four-task GLM aggregate is strongly positive.

Artifacts and validation:

- Final Pro/GLM runsets: `structured_postfix_dsv4pro_20260718` and
  `structured_postfix_glm51_20260718`.
- Final Flash/Qwen/Kimi runsets are recorded per row in
  `figures/sro_experiment_data.csv`.
- Full Sparse Reading suite: `174 passed in 1.01s`.

The 2026-07-17 structured table below is retained as historical pre-fix
evidence and is superseded by this section.

## 2026-07-17: Five-model structured main-experiment refresh

Objective:

- Make T58, T73, SpreadsheetBench Verified 49333, and 11276 the complete
  structured scenario for every main-experiment model.
- Replace the older pre-convergence structured rows with clean paired Native/SR
  runs while leaving the other 13 task rows unchanged.
- Record score, tokens, requests, and wall-clock seconds in the canonical CSV
  and both detailed and scenario-level figures.

Execution:

- Retained final post-fix pairs for DeepSeek-V4-Flash and Qwen3.6-Plus.
- Added 24 new executions (four tasks x two modes x three models):
  `structured_main4_dsv4pro_20260717`,
  `structured_main4_glm51_20260717`, and
  `structured_main4_kimik25_20260717`.
- All 24 new cases produced `result.json`; no task was omitted or replaced by
  an older favorable result.
- The three new model runsets used four parallel workers within one model and
  ran the models sequentially. Native and SR for a model therefore share the
  same concurrency envelope; timing remains a single-run smoke metric.

Structured aggregate (`score sum / mean`; costs are Native to SR):

| Model | Native score | SR score | Tokens | Requests | Seconds |
| --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Flash | 3.969 / 0.992 | 4.000 / 1.000 | 1,839,320 -> 1,230,655 (-33.1%) | 77 -> 51 (-33.8%) | 868.0 -> 516.3 (-40.5%) |
| DeepSeek-V4-Pro | 4.000 / 1.000 | 3.920 / 0.980 | 1,595,249 -> 1,036,397 (-35.0%) | 65 -> 46 (-29.2%) | 982.0 -> 479.2 (-51.2%) |
| Qwen3.6-Plus | 2.677 / 0.669 | 4.000 / 1.000 | 1,038,806 -> 561,276 (-46.0%) | 64 -> 41 (-35.9%) | 1,512.6 -> 602.7 (-60.2%) |
| GLM-5.1 | 3.843 / 0.961 | 3.933 / 0.983 | 919,008 -> 740,090 (-19.5%) | 52 -> 42 (-19.2%) | 1,468.4 -> 826.4 (-43.7%) |
| Kimi-K2.5 | 3.538 / 0.884 | 2.719 / 0.680 | 492,715 -> 1,344,057 (+172.8%) | 32 -> 54 (+68.8%) | 535.0 -> 633.4 (+18.4%) |

Interpretation:

- Flash, Qwen, and GLM improve mean structured score and combined efficiency.
- Pro reduces cost substantially with a small mean-score decrease of 0.02.
- Kimi is retained as the negative model-family boundary: T58/T73 accumulate
  protocol cost and 11276 fails correctness, despite a strong 49333 result.
- Across all five models and 20 structured pairs, mean score changes from
  0.901 to 0.929; combined tokens fall 16.5%, requests 19.3%, and time 43.0%.

Data and figure changes:

- `figures/sro_experiment_data.csv` now has 85 complete paired rows and adds
  `baseline_seconds` / `sro_seconds`; all 20 structured rows have both values.
- `figures/sro_main_scenario_summary.csv` records per-scenario score sums,
  means, combined tokens, requests, seconds, and their reductions.
- The detailed trajectory figure adds a wall-clock panel. The main scenario
  matrix adds a time-reduction row and uses the four-task structured aggregate.
- Builders and plotters compile successfully; PNG/SVG/PDF outputs were
  regenerated and visually inspected.
- Full Sparse Reading suite: `172 passed in 1.90s`; `git diff --check` passes.

## 2026-07-17: Structured sparse-plan convergence

Objective:

- Re-audit T58, T73, SpreadsheetBench Verified 49333, and 11276 without
  equating "full computation" with "no sparse-reading opportunity".
- Use SR to identify the authoritative inputs, schema/formula contract, and
  bounded work plan; then perform one complete local computation or local edit.
- Remove recent protocol loops without adding task ids, workbook names, fixed
  cells, answer values, or benchmark-specific hints.

Generic repairs:

- Native/advisory routing keeps the compact `sro_preview` and `sro_read`
  surface available. It no longer removes all SR tools, policy, and skill just
  because the final operation must use a native computation tool.
- Large structured collections produce a compact `ready_for_compute` plan:
  selected authoritative inputs, required schema/metrics, calculation
  invariants, and a one-script handoff. Raw tables are not copied into the
  model context.
- T58-shaped panel bundles preserve string/categorical entity ids and specify
  valid entity/time fixed-effects and clustered-standard-error semantics before
  the model runs the full regression locally.
- T73-shaped transaction bundles select current, historical, and configuration
  inputs, exclude duplicate or qualitative distractors, and require a compact
  metric contract including observed date coverage.
- XLSX preview is formula-first, detects informative headers and surrounding
  whitespace, and can terminate at `ready_for_edit`. After the edit succeeds,
  only one compact generated-output verification is allowed.
- Policy blocks read-only post-preview rescans and unsafe double-quoted
  `python -c` commands whose Excel `$A$1` references would be expanded by the
  shell, while still allowing a bounded local edit script.

Task-shape finding:

- T58 and T73 do require full local computation, but their reading remains
  sparse. The useful SR boundary is *plan then compute*: select the schema,
  model, source files, and metric closure sparsely, then calculate over all
  required rows outside the model context.
- 49333 and 11276 use *diagnose then edit*: preview the relevant formula and
  sheet shape, then make one bounded workbook edit. Their sparse value is not
  avoiding Excel execution; it is preventing broad worksheet inspection and
  repair loops.
- Final traces contain real SR calls and terminal closures:
  `collection_panel_did_closure` for T58,
  `collection_structured_compute_plan` for T73, and formula-first
  `sro_preview`/`ready_for_edit` paths for the workbook tasks.

Paired smoke results (`score / tokens / requests / seconds`):

| Model | Task | Native | Final SR | Result |
| --- | --- | --- | --- | --- |
| DeepSeek-V4-Flash | T58 | 1.00 / 901,143 / 38 / 325.2 | 1.00 / 704,548 / 28 / 217.0 | tokens -21.8%, requests -26.3% |
| DeepSeek-V4-Flash | T73 | 0.969 / 380,655 / 14 / 221.2 | 1.00 / 191,122 / 9 / 109.6 | score +0.031, tokens -49.8% |
| DeepSeek-V4-Flash | 11276 | 1.00 / 160,193 / 8 / 105.6 | 1.00 / 126,648 / 6 / 71.6 | tokens -20.9%, requests -25.0% |
| DeepSeek-V4-Flash | 49333 | 1.00 / 397,329 / 17 / 216.0 | 1.00 / 208,337 / 8 / 118.1 | tokens -47.6%, requests -52.9% |
| Qwen3.6-Plus | T58 | 1.00 / 859,012 / 47 / 577.2 | 1.00 / 304,789 / 19 / 283.1 | tokens -64.5%, requests -59.6% |
| Qwen3.6-Plus | T73 | 0.677 / 79,173 / 7 / 188.6 | 1.00 / 81,995 / 7 / 162.4 | score +0.323; tokens +3.6% |
| Qwen3.6-Plus | 11276 | 0.00 / 11,945 / 2 / 599.2 (timeout) | 1.00 / 81,430 / 7 / 91.0 | Native timeout; SR completes correctly |
| Qwen3.6-Plus | 49333 | 1.00 / 88,676 / 8 / 147.5 | 1.00 / 93,062 / 8 / 66.3 | tokens +4.9%, same requests, time -55.0% |

Interpretation:

- DeepSeek reproduces a positive efficiency result on all four structured
  positives while preserving or improving quality.
- Qwen confirms the mechanism but not a universal token win: T58 is a strong
  efficiency positive; T73 and 11276 are quality/convergence positives; 49333
  is a near-neutral token boundary with substantially lower latency.
- These are single-run paired smokes. They repair the earlier all-native
  conclusion, but task-level variance still requires repeated runs.

Validation and artifacts:

- DeepSeek baselines:
  `SRO_test/qwenclawbench/structured_sparse_compute_dsv4flash_20260716/baseline`.
  Final gates are the corresponding task results in
  `structured_sparse_compute_fix2_dsv4flash_20260716`,
  `structured_sparse_compute_fix3_dsv4flash_20260716`, and
  `structured_sparse_compute_fix4_dsv4flash_20260716`.
- Qwen T58/T73/11276 paired runset:
  `structured_sparse_compute_qwen36plus_20260716`; final 49333 gate:
  `structured_sparse_compute_fix2_qwen36plus_20260717`.
- Full Sparse Reading suite: `172 passed in 2.19s`.

## 2026-07-15: SpreadsheetBench Verified structured additions

Objective:

- Replace constructed public-dataset questions with native structured tasks
  from a computer/agent benchmark.
- Screen five official SpreadsheetBench Verified candidates and retain only
  evidence with explicit provenance and trace-level boundaries.

Accepted tasks:

| Task | Native score/tokens/req/s | Current evidence | Classification |
|---|---|---|---|
| 49333 trimmed VLOOKUP | 1.00 / 575,430 / 23 / 216.5 | Post-fix gate 1.00 / 352,705 / 12 / 132.5 | Strong positive |
| 11276 weekday row fix | 1.00 / 210,042 / 9 / 140.3 | Initial gate 1.00 / 165,861 / 7 / 106.3; post-fix repeat timed out | Weak, non-reproduced positive |

Structured-path audit:

- Multi-sheet full-value reads no longer report `ready` when only a small
  sheet was materialized and a relevant large sheet remains unread.
- XLSX preview now detects a dense header below leading banner rows and records
  `header_row`; 11276 changes from an empty row-1 header to the real row-4
  header.
- No task ids, filenames, cells, formulas, or answers were added to product
  code.

Verification:

- Targeted reader/preview regressions: `3 passed`.
- Full Sparse Reading suite: `163 passed in 1.65s`.
- Formula scores were recalculated because `openpyxl(data_only=True)` does not
  calculate newly written workbook formulas.

Boundary:

- 49333 remains a strong efficiency positive after the correctness fix, though
  its savings shrink to tokens -38.7%, requests -47.8%, and time -38.8%.
- 11276 remains recorded only because its first smoke was an equal-quality
  efficiency signal. Its prompt is nearly self-sufficient, it never used
  `sro_read`, and the post-fix timeout prevents a stable-benefit claim.
- NASA/USGS/NOAA/NYC/SEC derived local-fact pilots are integration fixtures,
  not native benchmark additions.

## 2026-07-15: Generic PDF typed-child convergence

Objective:

- Make PDF children follow the same bounded `FileCard -> HintSpec -> Typed Reader -> EvidencePack` loop as other SparseRead types.
- Do not add task ids, company names, report sections, fixed targets, or benchmark-only hints.

Implementation:

- Collections enumerate PDF children, register child artifacts, preserve ordinary text evidence in mixed directories, and expose complete typed-child manifests.
- A single selected PDF with no slots enters `focus`; multi-slot reads stay in `collect`.
- PDF/text slot extraction accepts Unicode labels and can resolve generic labeled total amounts.
- Cover pagination, weakly matched candidates, and structured-shape mismatches no longer become ready solely because they are non-empty.
- One concrete focus fallback is allowed after a weak slot digest. An unchanged verify result becomes terminal `stalled` evidence instead of false `ready` or a reread loop.

Verification:

- `uv run --with pytest --with pytest-asyncio --with pymupdf python -m pytest -q tests/sparse_reading`
- Result: `159 passed in 0.93s`.
- Direct real-PDF check resolved `87,122,954.71` at `p11:L91-L123` in the first collect call.

Benchmark protocol:

- Provider: Paratera proxy, `https://llmapi.paratera.com/v1`.
- Model id: `DeepSeek-V4-Flash`, verified from the provider model list before launch.
- Baseline runset: `SRO_test/qwenclawbench/pdf_typed_convergence_dsv4f_20260715`.
- Final gate runset: `SRO_test/qwenclawbench/pdf_typed_convergence_dsv4f_r2_20260715`.

Results:

| Task | Mode | Score | Total tokens | Requests | Seconds |
|---|---|---:|---:|---:|---:|
| T21 single long PDF, multi-question | baseline | 1.00 | 1,048,055 | 47 | 298.8 |
| T21 single long PDF, multi-question | gate | 1.00 | 42,632 | 4 | 24.8 |
| Multi-PDF collection, one local fact | baseline | 1.00 | 183,980 | 12 | 91.1 |
| Multi-PDF collection, one local fact | gate | 1.00 | 64,407 | 5 | 26.1 |

Trace checks:

- T21 gate: `sro_card -> sro_read(collect) -> write_file`.
- Multi-PDF local fact gate: `list_dir -> sro_preview(selected PDF) -> sro_read(collect) -> write_file`.
- No shell PDF extraction, package installation, raw fallback, repeated verify, or rediscovery loop remained in the final gate traces.

Boundary:

- These are single-run smoke comparisons. Repeat runs are still required for variance estimates.
- Broad multi-PDF synthesis remains a separate stress regime; this change establishes trustworthy routing and bounded evidence convergence rather than a task-specific full-report comparison strategy.

## 2026-07-15: External structured local-fact pilot

Objective:

- Find five structured-file candidates outside QwenClawBench using a task-shape criterion before dataset search.
- Add at least two attributable positive tasks without task IDs, target values, source filenames, or answer-specific routing in product code.

Selection criterion:

- Accept candidates where one natural unique identifier/entity selects one row or record and the answer needs only a few fields.
- Reject full-table aggregation, joins, regression, formula recalculation, small files, and artificial distractor concatenation.

Generic repairs:

- Numeric values no longer make an otherwise unmatched row a candidate.
- Row selection prioritizes non-header needles, so requested output column names cannot fill the 40-row cap before a deep target row.
- A sole structured collection child now dispatches through CSV, TSV, XLSX, JSON, YAML, or XML typed readers; multi-file structured audit/diagnosis bundles retain collection closure.
- Advisory structured workspaces keep optional SRO macros available; explicit native workspaces remain disabled and large structured `read_file` remains native by default.
- Always-on guidance chooses focused SRO for a unique target plus a few fields, and native local code for all-row computation, joins, regression, and formulas.

Verification:

- Candidate reader preflight resolved USGS row 6002, NYC row 3502, and NOAA row 35002 with `unresolved=[]`.
- Targeted regression tests cover deep numeric CSV rows and single structured-child dispatch.
- Full suite command: `uv run --with pytest --with pytest-asyncio python -m pytest -q tests/sparse_reading`.
- Full suite result: `161 passed in 0.94s`.

Benchmark protocol:

- Provider/model: Paratera `https://llmapi.paratera.com/v1`, `DeepSeek-V4-Flash`.
- Native baseline: `SRO_test/qwenclawbench/structured_external_dsv4flash_20260715/baseline`.
- Final gate: `SRO_test/qwenclawbench/structured_external_dsv4flash_fix4_20260715/gate`.
- External snapshot provenance and SHA-256: `SRO_test/structured_external_pilot/README.md`.

Final single-run results:

| Candidate | Native score/tokens/req/s | Gate score/tokens/req/s | Classification |
|---|---|---|---|
| NASA exoplanets | 0.50 / 64,274 / 5 / 32.4 | 1.00 / 69,557 / 5 / 33.0 | Quality-positive only |
| USGS earthquakes | 1.00 / 103,146 / 8 / 59.9 | 1.00 / 86,833 / 6 / 39.5 | Accepted positive |
| NOAA storms | 1.00 / 53,626 / 5 / 19.6 | 1.00 / 94,480 / 6 / 43.3 | Rejected |
| NYC 311 | 1.00 / 78,111 / 7 / 51.0 | 1.00 / 76,614 / 5 / 26.6 | Weak accepted positive; repeat for token variance |
| SEC EDGAR | 1.00 / 77,266 / 7 / 37.9 | 1.00 / 93,366 / 6 / 35.1 | Rejected |

Boundary:

- These are derived exact local-fact tasks over official external snapshots, not official benchmark scores from the source organizations.
- All metrics are one-run smoke values. USGS is the stronger new efficiency positive; NYC requires repetition because its token delta is only -1.9% even though requests and time improve materially.

## 2026-07-15: Five-model paper main experiment

Objective:

- Run the same 17 paired Native/SR tasks across DeepSeek-V4-Flash,
  DeepSeek-V4-Pro, Qwen3.6-Plus, GLM-5.1, and Kimi-K2.5.
- Evaluate four task shapes instead of optimizing for WB334 or any individual
  benchmark: long-context/PDF reading, multi-file audit/diagnosis, structured
  analysis, and native-fit controls.

Protocol:

- Paratera OpenAI-compatible endpoint, `SPARSEREAD_MODE=auto`, eight parallel
  workers, one run per pair.
- Runsets: `SRO_test/qwenclawbench/main17_<model>_20260715`, plus the approved
  Kimi replacement runset `main17_kimik25_20260716`.
- The runner now propagates parallel child failures, uses copied fixture
  symlinks, includes spreadsheet dependencies, and records its mode manifest.

Completed-model aggregate:

| Model | Native score / tokens / req | SR score / tokens / req |
|---|---|---|
| DeepSeek-V4-Flash | 0.804 / 7,492,200 / 321 | 0.879 / 5,873,290 / 218 |
| DeepSeek-V4-Pro | 0.843 / 4,879,357 / 241 | 0.908 / 3,212,266 / 157 |
| Qwen3.6-Plus | 0.789 / 3,157,839 / 211 | 0.871 / 2,433,644 / 172 |
| GLM-5.1 | 0.835 / 4,646,736 / 243 | 0.810 / 4,407,233 / 216 |
| Kimi-K2.5 | 0.443 / 7,124,950 / 347 | 0.750 / 2,463,512 / 132 |

Across all 85 pairs, mean score changes from 0.743 to 0.844, tokens fall from
27,301,082 to 18,389,945 (-32.6%), and requests from 1,363 to 895 (-34.3%). The
long-context/PDF scenario is consistent across all five models: 25/25 SR task
runs score 1.00; combined tokens fall from 9,659,035 to 1,295,257 (-86.6%) and
requests from 524 to 104 (-80.2%). GLM's weaker 17-task aggregate and the
structured-analysis cost increase remain useful boundaries rather than
contradicting the PDF result.

Generic PDF repair found during the matrix:

- GLM selected the correct PDF in the Kaima multi-PDF task but supplied one
  slot, which kept the child in `collect` and produced a wrong page-9 amount.
- A uniquely selected PDF child now treats zero or one slot as one focused fact;
  only two or more slots retain `collect`.
- GLM Kaima changed from `0.00 / 68,370 / 5` to
  `1.00 / 53,612 / 4`. The original failure and post-fix rerun are both kept.
- Full SparseReading regression: `164 passed in 1.40s`.

Evaluation corrections:

- Flash 49333 is score-only regraded to 1.00 after semantic validation of its
  `INDEX/MATCH/TRIM` formula; original cost is retained.
- Pro T12 passed all automated checks but received an empty LLM-judge response;
  a low-concurrency judge recheck scored 1.00 and original cost is retained.

Kimi model decision and result:

- `Kimi-K2.6` was present in the Paratera model list but repeatedly returned
  `local_rate_limitedError` (HTTP 429). After explicit approval, the fifth
  model was changed to `Kimi-K2.5`, which completed all 34 runs.
- Kimi-K2.5 changes from `0.443 / 7,124,950 / 347` Native to
  `0.750 / 2,463,512 / 132` SR. Its four Native long-reading failures are
  retained: two 300-second timeouts and two 50-tool-call caps; all four paired
  SR runs scored 1.00 in four requests.

## 2026-07-18: Multi-file audit convergence repair

Checkpoint before this work: `bacb43b` (`Converge typed and structured
SparseRead paths`).

Trajectory diagnosis:

- T55 and T98 had task-shaped Native exclusions, while T86 commonly entered
  SR after broad reads, so the gate missed the actual sparse space.
- A ready collection closure blocked one read but then escaped to Native.
- Large closure payloads were persisted and reread; repeated ready calls also
  duplicated evidence in the conversation.
- T98 could repeatedly execute near-identical generated shell verification
  commands after the diagnostic evidence was already closed.

Generic implementation:

- Route configuration/log/code diagnoses and scheduled code-plus-config audits
  to a compact collection closure without task IDs or fixture-specific values.
- Close common config/state/log/source/output contradictions in the collection
  reader, keep ready artifacts closed, and make repeated ready responses short.
- Allow at most two generated-shell checks after a collection becomes ready.
- Preserve required-output handling and direct writing of requested fixes.

Final paired runsets:

- `audit_final_paired_dsv4flash_20260718`
- `audit_final_paired_qwen36plus_20260718`

Five-task aggregate (`T12`, `T55`, `T86`, `T94`, `T98`):

| Model | Native mean score / tokens / req / s | Final SR mean score / tokens / req / s | Delta |
| --- | --- | --- | --- |
| DeepSeek-V4-Flash | 0.572 / 3,334,747 / 124 / 828.3 | 0.917 / 1,485,942 / 66 / 400.9 | Score +0.345; tokens -55.4%; req -46.8%; time -51.6% |
| Qwen3.6-Plus | 0.798 / 1,107,624 / 75 / 949.9 | 0.976 / 794,019 / 57 / 603.8 | Score +0.178; tokens -28.3%; req -24.0%; time -36.4% |

Boundary: T98 is not a strict Pareto positive in either model on this single
run. Flash saves cost with a 0.069 score loss; Qwen gains 0.148 score but uses
roughly twice the tokens. This is recorded as an executable-repair boundary,
not hidden in the aggregate.

Verification:

- Full SparseReading suite: `177 passed in 1.07s` after the implementation was
  finalized.
- Canonical data and all main/v2 figures were regenerated from the mixed
  provenance builder, with the two gold-model audit rows sourced from the final
  paired runsets.
