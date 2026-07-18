# SRO Supplementary Experiments

## Purpose

This document proposes the minimum supplementary experiments needed to make the SRO paper claims defensible. It is based on `v3_plan.md`, `v3_dev.md`, `SRO_report.md`, and the current canonical result table in `figures/sro_experiment_data.csv`.

The current evidence supports a narrow claim:

> SRO is a selective sparse-reading protocol. It helps when a task has high reading sparsity and a compact evidence-to-deliverable closure. A task may still need a full local computation: SR should sparsely select its authoritative inputs and calculation contract, then hand off one bounded computation rather than put all rows in model context.

The supplementary experiments should not try to show that SRO always wins. They should test the three paper risks directly:

1. Are collection closures task-specific benchmark patches?
2. Does the Benefit Gate provide real value beyond forced SRO or native baselines?
3. Are typed readers replaceable executors, or is the system dependent on one reader implementation?

## Current Evidence Snapshot

The paper main experiment now uses 17 paired tasks in four task-shape
scenarios. Every pair uses the same task fixture, model, endpoint, and runner;
only `SRO_ENABLED` differs. `SPARSEREAD_MODE=auto` is explicit. The original
main run used eight parallel workers; the structured replacement runs use four
workers within one model and run model families sequentially.

| Scenario | Tasks |
| --- | --- |
| Long-context and PDF reading | LooGLE 10Q/5Q/3Q, T21 long PDF QA, Kaima multi-PDF local fact |
| Multi-file audit and diagnosis | T12, T55, T86, T94, T98 |
| Structured analysis | T58, T73, SpreadsheetBench Verified 49333 and 11276 |
| Native-fit controls | T36, T59, T67 |

The first four main runsets are under
`SRO_test/qwenclawbench/main17_<model>_20260715`; the approved Kimi replacement
runset is `main17_kimik25_20260716`. All five models have completed. Kimi-K2.6
is now reachable, but its paired structured run was unstable: three Native
runs reached the 50-request cap and SR still looped on T58. It is therefore not
used in the paper table; the stable Kimi-K2.5 run remains the approved result.

The four structured rows and five audit rows for every model are replaced by
clean paired post-convergence runs. Other rows remain on their original main
runsets. The canonical CSV records this mixed provenance per row and includes
Native/SR wall-clock seconds in addition to score, tokens, and requests.

| Model | Native mean score / tokens / req / s | SR mean score / tokens / req / s | Interpretation |
| --- | --- | --- | --- |
| DeepSeek-V4-Flash | 0.734 / 8,060,557 / 334 / 2,594.3 | 0.896 / 3,506,749 / 163 / 1,084.3 | Score +0.162; tokens -56.5%; requests -51.2%; time -58.2% |
| DeepSeek-V4-Pro | 0.800 / 5,485,735 / 258 / 2,921.6 | 0.918 / 2,281,879 / 119 / 1,247.0 | Score +0.118; tokens -58.4%; requests -53.9%; time -57.3% |
| Qwen3.6-Plus | 0.800 / 3,656,606 / 243 / 3,178.4* | 0.917 / 1,869,255 / 142 / 1,629.4* | Score +0.117; tokens -48.9%; requests -41.6%; paired time -48.7%* |
| GLM-5.1 | 0.849 / 6,684,543 / 341 / 5,458.7 | 0.906 / 2,600,316 / 142 / 2,243.2 | Score +0.057; tokens -61.1%; requests -58.4%; time -58.9% |
| Kimi-K2.5 | 0.565 / 6,105,243 / 317 / 3,051.8 | 0.873 / 1,782,004 / 104 / 1,231.2 | Score +0.308; tokens -70.8%; requests -67.2%; time -59.7% |

`*` Qwen time aggregates contain 16 valid pairs. T58 is omitted from both
Native and SR wall-clock sums because the SR execution had a confirmed
provider-side stall; its score, token, and request measurements remain valid.

The aggregate result is deliberately not the primary claim. Across all 85
paired runs, mean score changes from 0.750 to 0.902, combined tokens fall from
29,992,684 to 12,040,203 (-59.9%), and requests fall from 1,493 to 670
(-55.1%). Across the 84 valid timed pairs, wall-clock time falls from 17,204.8
to 7,435.0 seconds (-56.8%). The
long-context/PDF scenario is the consistent signal: all 25 SR
task runs score 1.00, compared with a 0.798 Native mean; combined tokens fall
from 9,659,035 to 1,295,257 (-86.6%) and requests from 524 to 104 (-80.2%).
Per-model long-context/PDF token reductions are 86.6% (Flash), 81.8% (Pro),
76.9% (Qwen3.6-Plus), 78.9% (GLM-5.1), and 92.9% (Kimi-K2.5).

The other scenarios preserve the intended boundary. After all five audit
models were rerun, multi-file audit and diagnosis improves mean score by 0.223
while reducing tokens by 49.7%, requests by 44.4%, and time by 49.3%.
After the structured convergence reruns, the fixed four-task structured
scenario improves mean score by 0.133 while reducing combined tokens by 51.7%
and requests by 48.2%. Across its 19 valid timed pairs, time falls by 65.7%.
Native-fit controls reduce tokens by
26.0% but lose 0.023 mean score, so they remain gate/native-first tasks rather
than a broad SR claim.

One score-only evaluation repair remains in the canonical table. Pro Native
T73 passed all six automated checks; its original judge response was empty,
while a same-deliverable rejudge scored 1.00. The repair retains the original
run's tokens, requests, and seconds. Pro T12 now uses its clean paired audit
rerun and needs no score correction. The earlier Flash 49333 semantic regrade
is also no longer needed because its replacement structured run receives 1.00
directly from the current grader.

Kimi's four Native long-reading failures are also retained rather than
regraded. T21 and LooGLE 10Q timed out at about 300 seconds after 43 and 46
requests; LooGLE 3Q and 5Q reached the 50-tool-call cap without an answer. All
four paired SR runs scored 1.00 in four requests. These statuses are annotated
in the canonical CSV because they are convergence failures, not grader errors.

Key risks from the logs remain:

- Audit and command-security closures can be criticized as task-shaped and need held-out closure-family validation.
- Structured tasks remain model-dependent: sparse planning removes large
  context and repair loops, but a very efficient Native run can still make the
  fixed preview/protocol overhead slightly token-negative.
- Ready-closure compliance varies by model and concurrent run; a correct closure does not guarantee that the model stops reading.
- These are single runs. The scenario result is strong across models, but task-level variance still needs repeated trials.

## Executed Multi-file Audit Convergence Check

The original audit result was weak because the gate did not consistently use
the sparse opportunity. T55 and T98 were explicitly forced to Native; T86
often entered SR only after broad parallel reads. Even when collection closure
was ready, the orchestrator blocked once and then escaped back to Native.
Large closure responses were persisted as temporary files, which caused some
models to decompose and reread the generated evidence, while repeated closure
calls duplicated evidence. On T98, models could also loop through many shell
verification attempts after already finding the diagnosis.

The repair is task-agnostic. It recognizes compact cross-file diagnostic
contracts over configuration, state, logs, source, and generated output;
keeps a ready collection closed; returns a short completion response on repeat
calls; and caps generated-shell verification after closure. The code contains
no task IDs, fixture paths, expected answers, or benchmark-specific values.

Final paired single-run results are shown as
`score / tokens / requests / seconds`:

| Model | Task | Native | Final SR | Classification |
| --- | --- | --- | --- | --- |
| DeepSeek-V4-Flash | T12 | 0.546 / 292,387 / 16 / 107.2 | 1.000 / 107,118 / 6 / 38.0 | Strong positive |
| DeepSeek-V4-Flash | T55 | 0.433 / 1,409,983 / 44 / 257.5 | 0.947 / 667,136 / 25 / 138.1 | Strong positive |
| DeepSeek-V4-Flash | T86 | 0.046 / 525,185 / 17 / 162.2 | 0.896 / 213,744 / 11 / 96.3 | Strong positive; Native quality was unusually low |
| DeepSeek-V4-Flash | T94 | 1.000 / 515,914 / 24 / 139.8 | 0.976 / 76,220 / 5 / 31.1 | Strong efficiency positive; small score delta |
| DeepSeek-V4-Flash | T98 | 0.833 / 591,278 / 23 / 161.7 | 0.764 / 421,724 / 19 / 97.4 | Boundary: efficiency positive, score -0.069 |
| DeepSeek-V4-Pro | T12 | 0.333 / 224,626 / 13 / 158.8 | 1.000 / 72,061 / 5 / 43.9 | Strong positive; Native judge response was empty |
| DeepSeek-V4-Pro | T55 | 0.703 / 339,562 / 17 / 156.7 | 0.927 / 409,782 / 16 / 142.5 | Quality/time positive; tokens +20.7% |
| DeepSeek-V4-Pro | T86 | 0.363 / 461,037 / 19 / 311.0 | 0.988 / 163,412 / 7 / 148.2 | Strong positive |
| DeepSeek-V4-Pro | T94 | 0.950 / 116,566 / 7 / 95.4 | 0.990 / 64,522 / 4 / 42.7 | Positive |
| DeepSeek-V4-Pro | T98 | 0.832 / 400,072 / 19 / 149.4 | 0.644 / 142,908 / 8 / 64.0 | Boundary: efficiency positive, score -0.188 |
| Qwen3.6-Plus | T12 | 0.802 / 164,348 / 12 / 142.1 | 0.969 / 58,720 / 5 / 70.9 | Strong positive |
| Qwen3.6-Plus | T55 | 0.673 / 388,399 / 28 / 223.7 | 0.969 / 258,275 / 17 / 189.2 | Positive |
| Qwen3.6-Plus | T86 | 0.683 / 229,047 / 11 / 322.8 | 0.988 / 116,430 / 8 / 120.6 | Strong positive |
| Qwen3.6-Plus | T94 | 1.000 / 170,534 / 13 / 128.2 | 0.975 / 51,354 / 5 / 48.7 | Strong efficiency positive; small score delta |
| Qwen3.6-Plus | T98 | 0.832 / 155,296 / 11 / 133.2 | 0.979 / 309,240 / 22 / 174.4 | Quality positive, efficiency boundary |
| GLM-5.1 | T12 | 0.905 / 287,603 / 15 / 313.1 | 0.988 / 61,402 / 4 / 77.8 | Strong positive |
| GLM-5.1 | T55 | 0.661 / 1,152,004 / 50 / 581.5 | 0.939 / 389,745 / 19 / 248.7 | Strong positive |
| GLM-5.1 | T86 | 0.873 / 391,237 / 17 / 385.5 | 0.988 / 187,394 / 9 / 347.5 | Positive |
| GLM-5.1 | T94 | 1.000 / 207,421 / 13 / 277.6 | 0.984 / 51,778 / 4 / 60.6 | Strong efficiency positive |
| GLM-5.1 | T98 | 0.836 / 788,706 / 38 / 722.3 | 0.935 / 346,932 / 17 / 196.5 | Strong positive |
| Kimi-K2.5 | T12 | 0.667 / 130,260 / 8 / 92.0 | 0.938 / 56,759 / 4 / 52.9 | Strong positive |
| Kimi-K2.5 | T55 | 0.637 / 158,248 / 10 / 126.6 | 0.843 / 470,035 / 22 / 224.1 | Quality positive, efficiency boundary |
| Kimi-K2.5 | T86 | 0.508 / 151,399 / 6 / 208.6 | 0.900 / 81,880 / 4 / 135.8 | Strong positive |
| Kimi-K2.5 | T94 | 0.750 / 498,704 / 26 / 250.2 | 0.900 / 58,983 / 4 / 56.6 | Strong positive |
| Kimi-K2.5 | T98 | 0.792 / 205,389 / 11 / 144.3 | 0.814 / 169,406 / 10 / 69.9 | Weak positive; evidence-attribution issue remains |

Five-model audit aggregates:

| Model | Native mean score / tokens / req / s | Final SR mean score / tokens / req / s | Change |
| --- | --- | --- | --- |
| DeepSeek-V4-Flash | 0.572 / 3,334,747 / 124 / 828.3 | 0.917 / 1,485,942 / 66 / 400.9 | Score +0.345; tokens -55.4%; requests -46.8%; time -51.6% |
| DeepSeek-V4-Pro | 0.636 / 1,541,863 / 75 / 871.4 | 0.910 / 852,685 / 40 / 441.4 | Score +0.274; tokens -44.7%; requests -46.7%; time -49.3% |
| Qwen3.6-Plus | 0.798 / 1,107,624 / 75 / 949.9 | 0.976 / 794,019 / 57 / 603.8 | Score +0.178; tokens -28.3%; requests -24.0%; time -36.4% |
| GLM-5.1 | 0.855 / 2,826,971 / 133 / 2,280.0 | 0.967 / 1,037,251 / 53 / 931.1 | Score +0.112; tokens -63.3%; requests -60.2%; time -59.2% |
| Kimi-K2.5 | 0.671 / 1,144,000 / 61 / 821.7 | 0.879 / 837,063 / 44 / 539.3 | Score +0.208; tokens -26.8%; requests -27.9%; time -34.4% |

T98 remains the important boundary: its diagnosis is sparse, but producing and
validating the requested executable repair can dominate the reading savings.
Flash and Pro lose task-level score, while Qwen spends more tokens to improve
quality. GLM is a strong positive and Kimi is a weak positive, although Kimi's
trace partially attributes retry facts that were not visible in the retained
log excerpt. Therefore the evidence supports the audit scenario in aggregate,
not a claim that every diagnostic task is a strict Pareto improvement. Repeats
are still needed to estimate task-level variance, especially for T98.

## Executed Structured Sparse-Plan Convergence Check

The post-main audit corrects an overly aggressive interpretation of structured
tasks. T58 and T73 require calculations over all relevant rows, but they do not
require the model to read every row. Their sparse opportunity is to select the
authoritative sources, schema, estimator, and metric contract before one local
script performs the full computation. SpreadsheetBench Verified 49333 and
11276 similarly use SR for formula/sheet diagnosis before a bounded local edit.

No task ids, workbook names, fixed cells, formulas, answer values, or runner
hints were added to product routing. The retained paths are generic:

- panel-data bundles: sparse schema/model closure, then full local regression;
- transaction bundles: sparse source/metric closure, then full local aggregation;
- formula workbooks: formula-first preview, then one bounded edit and one
  compact output verification.

Paired single-run results are shown as `score / tokens / requests / seconds`:

| Model | Task | Native | Final SR | Classification |
| --- | --- | --- | --- | --- |
| DeepSeek-V4-Flash | T58 | 1.00 / 901,143 / 38 / 325.2 | 1.00 / 121,827 / 7 / 51.0 | Strong efficiency positive |
| DeepSeek-V4-Flash | T73 | 0.969 / 380,655 / 14 / 221.2 | 1.00 / 405,645 / 16 / 85.9 | Quality/time positive; tokens +6.6% |
| DeepSeek-V4-Flash | 11276 | 1.00 / 160,193 / 8 / 105.6 | 1.00 / 126,555 / 7 / 31.0 | Positive |
| DeepSeek-V4-Flash | 49333 | 1.00 / 397,329 / 17 / 216.0 | 1.00 / 430,431 / 15 / 89.5 | Quality/time held; tokens +8.3% |
| Qwen3.6-Plus | T58 | 1.00 / 859,012 / 47 / n/a | 1.00 / 331,072 / 21 / n/a | Strong efficiency positive; provider-stall time omitted |
| Qwen3.6-Plus | T73 | 0.677 / 79,173 / 7 / 188.6 | 1.00 / 77,815 / 6 / 160.6 | Quality and efficiency positive |
| Qwen3.6-Plus | 11276 | 0.00 / 11,945 / 2 / 599.2 (timeout) | 1.00 / 42,882 / 4 / 38.5 | Convergence rescue; token comparison invalid |
| Qwen3.6-Plus | 49333 | 1.00 / 88,676 / 8 / 147.5 | 1.00 / 63,695 / 6 / 99.3 | Positive |
| DeepSeek-V4-Pro | T58 | 0.520 / 1,076,034 / 37 / 440.8 | 0.969 / 490,627 / 20 / 162.5 | Quality and efficiency positive |
| DeepSeek-V4-Pro | T73 | 1.00 / 285,861 / 11 / 191.2 | 1.00 / 112,137 / 6 / 100.6 | Strong efficiency positive; Native score from rejudge |
| DeepSeek-V4-Pro | 11276 | 1.00 / 233,270 / 11 / 159.4 | 1.00 / 104,808 / 6 / 47.5 | Positive |
| DeepSeek-V4-Pro | 49333 | 1.00 / 250,458 / 11 / 117.8 | 1.00 / 87,802 / 5 / 60.7 | Strong positive |
| GLM-5.1 | T58 | 1.00 / 808,401 / 41 / 414.5 | 1.00 / 92,699 / 7 / 96.6 | Strong efficiency positive |
| GLM-5.1 | T73 | 0.428 / 1,139,030 / 50 / 1,164.0 | 1.00 / 166,681 / 9 / 244.2 | Convergence rescue and strong efficiency positive |
| GLM-5.1 | 11276 | 1.00 / 103,352 / 8 / 171.6 | 1.00 / 51,091 / 4 / 35.7 | Positive |
| GLM-5.1 | 49333 | 1.00 / 293,517 / 13 / 324.9 | 1.00 / 531,979 / 21 / 209.4 | Quality/time held; token/request variance boundary |
| Kimi-K2.5 | T58 | 0.875 / 87,946 / 6 / 189.9 | 0.875 / 110,538 / 6 / 95.7 | Quality/time held; tokens +25.7% |
| Kimi-K2.5 | T73 | 0.663 / 51,858 / 4 / 172.9 | 0.938 / 105,666 / 6 / 126.9 | Quality/time positive; token boundary |
| Kimi-K2.5 | 11276 | 1.00 / 113,997 / 9 / 71.5 | 1.00 / 62,359 / 4 / 28.1 | Positive |
| Kimi-K2.5 | 49333 | 1.00 / 238,914 / 13 / 100.6 | 1.00 / 136,781 / 8 / 62.5 | Strong positive |

The main structured score is the aggregate over exactly these four tasks:

| Model | Native score sum / mean | SR score sum / mean | Native → SR tokens | requests | seconds |
| --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Flash | 3.969 / 0.992 | 4.000 / 1.000 | 1,839,320 → 1,084,458 (-41.0%) | 77 → 45 (-41.6%) | 868.0 → 257.4 (-70.3%) |
| DeepSeek-V4-Pro | 3.520 / 0.880 | 3.969 / 0.992 | 1,845,623 → 795,374 (-56.9%) | 70 → 37 (-47.1%) | 909.1 → 371.2 (-59.2%) |
| Qwen3.6-Plus | 2.677 / 0.669 | 4.000 / 1.000 | 1,038,806 → 515,464 (-50.4%) | 64 → 37 (-42.2%) | 935.3 → 298.5 (-68.1%; 3 valid pairs) |
| GLM-5.1 | 3.428 / 0.857 | 4.000 / 1.000 | 2,344,300 → 842,450 (-64.1%) | 112 → 41 (-63.4%) | 2,075.0 → 585.9 (-71.8%) |
| Kimi-K2.5 | 3.538 / 0.884 | 3.813 / 0.953 | 492,715 → 415,344 (-15.7%) | 32 → 24 (-25.0%) | 535.0 → 313.2 (-41.5%) |

All five model families now show a positive four-task aggregate: mean score is
preserved or improved while tokens, requests, and valid paired time decline.
This does not imply every task-model cell is positive: GLM 49333 and Kimi T58
remain task-level token boundaries. The appropriate claim is robust aggregate
structured convergence with visible per-task variance, not universal per-cell
benefit. These are single-run paired smokes and need repetition for variance.

Final artifacts include `structured_no_regress_dsv4flash_20260718`, the three
Qwen no-regression runsets recorded per row in the canonical CSV,
`structured_postfix_dsv4pro_20260718`, `structured_postfix_glm51_20260718`,
`structured_kimi_final_k25_20260718`, and
`structured_kimi_convergence_k25_20260718`. The Pro/GLM paired runsets used
four workers within one model and ran the models sequentially. Native and SR
share the same per-model concurrency envelope, but wall-clock results remain
single-run smoke measurements.

## Executed PDF Integration Check: Multi-PDF Local Fact

`task_workspacebench_lite_334_kaima_rd` is a derived integration task over the
same four official annual-report PDFs used by Workspace-Bench-Lite 334. It is
not an official Workspace-Bench task and its result must not be included in the
official Workspace-Bench aggregate score.

The workspace contains the 2021 annual reports for Huili B (900939), Yitai B
Share (900948), Kaima B (900953), and Lingyun B Share (900957). The derived
prompt asks for only one source-local fact:

> Find the total R&D investment reported by Kaima B (900953) for 2021. Write
> only the amount in yuan, with exactly two decimal places and no currency
> symbol, to `answer.txt`.

The automated check removes commas and requires the exact value
`87122954.71`. This isolates two generic integration capabilities without
requiring broad synthesis across all four reports:

1. Select the named PDF from a collection of several large PDF children.
2. Use the typed PDF reader to retrieve one anchored fact and stop after the
   requested output is written.

The task contains no hidden target artifact, fixed page number, diagnostic
HintSpec, or answer-bearing benchmark hint. The target file is discoverable
from the ordinary workspace listing and filename, while the answer must still
be extracted from the PDF body.

### DeepSeek-V4-Flash Result

The validated run used the Paratera endpoint
`https://llmapi.paratera.com/v1` and model id `DeepSeek-V4-Flash`.

| Condition | Score | Total tokens | Requests | Time (s) |
| --- | ---: | ---: | ---: | ---: |
| Native baseline | 1.00 | 183,980 | 12 | 91.1 |
| Current SRO gate | 1.00 | 64,407 | 5 | 26.1 |

At equal score, SRO reduced total tokens by 65.0%, requests by 58.3%, and wall
time by 71.4%. The final SRO trajectory was:

```text
list_dir -> sro_preview(selected PDF) -> sro_read(collect) -> write_file
```

The PDF reader returned `87,122,954.71` from `p11:L91-L123` in the first
targeted collect call. The final trace contained no shell PDF extraction,
package installation, `sro_raw` fallback, repeated verification, or file
rediscovery loop.

Artifacts:

- Task definition: `SRO_test/qwenclawbench/sro_v3/task_workspacebench_lite_334_kaima_rd`
- Native result: `SRO_test/qwenclawbench/pdf_typed_convergence_dsv4f_20260715/baseline/task_workspacebench_lite_334_kaima_rd/result.json`
- Final SRO result: `SRO_test/qwenclawbench/pdf_typed_convergence_dsv4f_r2_20260715/gate/task_workspacebench_lite_334_kaima_rd/result.json`
- Final SRO transcript: `SRO_test/qwenclawbench/pdf_typed_convergence_dsv4f_r2_20260715/gate/task_workspacebench_lite_334_kaima_rd/task_transcript.jsonl`

Interpretation boundary: this task is a same-source integration proof for
multi-PDF selection plus local PDF fact extraction. It does not establish that
SRO is advantageous for the original WB-Lite 334 broad comparison task, which
requires multiple fact classes and cross-report synthesis. The current numbers
are single-run smoke results and should be repeated before reporting variance.

### Cross-Model Single-Slot Dispatch Repair

The GLM-5.1 main run exposed a generic PDF routing edge case. The model selected
the correct 900953 PDF but supplied one `hint.slots` item for the single fact.
Typed collection dispatch preserved `collect`, so the PDF slot extractor chose
an unrelated amount (`42,127,356.98`) from page 9 and the task scored 0.00.

The dispatch rule now treats zero or one slot on a uniquely selected PDF child
as a single-fact request: it removes the slot wrapper, preserves the concrete
needles and slot question, and sends the child through PDF `focus`. Only two or
more explicit slots retain PDF `collect`. This rule does not contain a task id,
company name, filename, page, field label, or answer value.

The GLM-5.1 post-fix rerun scored `1.00 / 53,612 tokens / 4 requests`, compared
with Native `1.00 / 54,663 / 5`. The original failed run remains under
`main17_glm51_20260715`; the corrected result is under
`main17_glm51_kaima_single_slot_fix_20260715` and is annotated as a post-fix
result in the canonical CSV. The full SparseReading suite passes with
`164 passed`.

## Executed Structured Benchmark Screening: SpreadsheetBench Verified

The structured-file supplement now uses native tasks from the official
[SpreadsheetBench](https://github.com/RUCKBReasoning/SpreadsheetBench)
Verified split. The prompt, input workbook, and official answer location are
preserved. The only runner wrapper asks the agent to save the completed
workbook as `answer.xlsx`. The task directories live below `qwenclawbench`
solely to reuse the trusted local runner; they are not QwenClawBench tasks.

Candidate selection happened before execution. A candidate needed a relatively
large workbook, a small official answer region, and a local edit or lookup that
did not require full-table aggregation. Five candidates were then run with the
Paratera endpoint and `DeepSeek-V4-Flash`:

| Task | Workbook / official target | Task shape |
| --- | --- | --- |
| 57033 | 146,864-byte XLSX / `Sheet4!K2:K7` | Three-way local match |
| 49333 | 79,491-byte XLSX / `Sheet1!G2:I7` | Repair a trimmed VLOOKUP across sheets |
| 52964 | 56,370-byte XLSX / `C2` | Local lookup and date difference |
| 50051 | 164,915-byte XLSX / `CC2:CC33` | Score-to-line lookup formula |
| 11276 | 409,849-byte XLSX / `F3:AJ3` | Repair and fill a weekday formula row |

### Scoring Protocol

SpreadsheetBench compares recalculated cell values. `openpyxl(data_only=True)`
does not calculate formulas written by an agent, so the raw local grader reports
zero for otherwise correct formula workbooks whose cached values are absent.
Formula tasks were therefore checked again with a local formula evaluator
against the official target cells. Raw `result.json` files remain unchanged;
the table below reports the post-recalculation score where applicable.

### Five-Candidate Result

Native and initial gate runs are under
`spreadsheetbench_verified_candidates_dsv4f_20260715`.

| Task | Native score / tokens / req / s | Initial gate score / tokens / req / s | Classification |
| --- | --- | --- | --- |
| 49333 | 1.00 / 575,430 / 23 / 216.5 | 1.00 / 208,641 / 9 / 73.8 | Strong positive; actual `sro_preview -> sro_read` path |
| 11276 | 1.00 / 210,042 / 9 / 140.3 | 1.00 / 165,861 / 7 / 106.3 | Weak positive; preview-assisted, no `sro_read` |
| 57033 | 1.00 / 87,471 / 6 / 82.6 | 1.00 / 218,177 / 11 / 66.9 | Rejected: equal quality but tokens +149.4% and requests +83.3% |
| 52964 | 0.00 / 412,319 / 17 / 332.3 | 1.00 / 383,230 / 13 / 186.6 | Rejected after gate repeat returned 0.00; unstable formula direction and fill range |
| 50051 | 0.00 / 599,658 / 19 / 273.6 | 0.00 / 589,538 / 17 / 262.9 | Rejected: neither path produced the required workbook |

### Accepted Additions

`task_spreadsheetbench_verified_49333_trimmed_vlookup` is the strong addition.
Trace review exposed one generic reader bug: its `sro_read` requested all NAME
values from Sheet1 and Sheet3, but only the small Sheet1 table was materialized
while the pack incorrectly declared the subset complete. The structured reader
now keeps a large unmaterialized sheet in `unresolved` and withholds the
immediate calculation action. After this fix, the current gate still scored
1.00 after recalculation with 352,705 tokens, 12 requests, and 132.5 seconds.
Against Native, the corrected path reduces tokens by 38.7%, requests by 47.8%,
and time by 38.8%.

`task_spreadsheetbench_verified_11276_weekday_row_fix` is retained as a weak
addition. The original candidate audit found a non-reproduced post-fix timeout,
but the 2026-07-17 sparse-plan audit subsequently reproduced an equal-quality
DeepSeek efficiency win: tokens -20.9%, requests -25.0%, and time -32.2%.
Qwen3.6-Plus also completed the SR path correctly in 91.0 seconds while its
Native pair timed out. It remains weaker than 49333 because the prompt already
reveals most of the repair and the benefit is model-dependent.

The two generic structured-reader fixes add no task ids, workbook names, target
cells, formulas, or answer values. Together with the PDF single-slot dispatch
regression, the Sparse Reading suite passes with `164 passed`.

Artifacts:

- Task definitions: `SRO_test/qwenclawbench/{baseline,sro_v3}/task_spreadsheetbench_verified_*`
- Five-candidate run: `SRO_test/qwenclawbench/spreadsheetbench_verified_candidates_dsv4f_20260715`
- 52964 repeat: `SRO_test/qwenclawbench/spreadsheetbench_verified_52964_repeat_dsv4f_20260715`
- Post-fix positive rerun: `SRO_test/qwenclawbench/spreadsheetbench_verified_positive_postfix_dsv4f_20260715`

Earlier NASA, USGS, NOAA, NYC 311, and SEC local-fact pilots are constructed
integration tasks over public data snapshots. They are useful for reader-path
debugging, but they are not native tasks from a computer or agent benchmark and
are not counted as new benchmark additions.

## Experiment 1: Closure Generalization

### Question

Do SRO closures generalize by task shape, or are they task-specific rules written for known benchmark instances?

### Hypothesis

A closure is defensible if the same closure family improves or preserves quality and reduces reading cost on held-out tasks with the same abstract shape, without using task IDs, exact filenames, or answer hardcoding.

### Closure Families

Evaluate three closure families separately:

| Closure family | Current positive anchor | Shape definition |
| --- | --- | --- |
| Long-document slot closure | `task_21` | One long report/PDF, multiple independent factual questions, answers must be anchored in the document. |
| Audit/integrity closure | `task_00012` | Small collection with state/output/config/script evidence; deliverable asks for bug, inconsistency, integrity, audit, or important findings. |
| Command/security closure | `task_00086` | Collection with executable command source, security policy/spec, test commands, and required security classification outputs. |

### Controls

For each task, run:

1. `Native baseline`: SRO disabled.
2. `SRO no closure`: SRO tools enabled, but the target closure family disabled while generic FileCard/HintSpec/EvidencePack remains.
3. `SRO closure`: current SRO with the relevant closure family enabled.
4. `Native plus guard only` if cheap to run: SRO guard enabled but no closure EvidencePack, to separate guard benefit from closure content.

### Experiment Switches

The current implementation exposes closure ablation switches for trusted runs:

```bash
SRO_COLLECTION_CLOSURES_ENABLED=0
SRO_DISABLED_CLOSURE_FAMILIES=audit
SRO_DISABLED_CLOSURE_FAMILIES=command_security
SRO_DISABLED_CLOSURE_FAMILIES=diagnosis,panel_did,rule_table_script
```

`local_agent_comp/run_qcb_trusted_batch.sh` also supports these modes:

```bash
--modes baseline,gate,force_sro_without_gate,no_collection_closures,no_audit_closure,no_command_security_closure
```

Use `no_audit_closure` on `task_00012` and held-out audit bundles. Use `no_command_security_closure` on `task_00086` and perturbed command-security bundles. Use `no_collection_closures` only as a broad sanity check because it disables unrelated closure families too.

### Task Selection

Minimum held-out set:

| Family | Include current anchor? | Held-out tasks to add | Selection rule |
| --- | --- | --- | --- |
| Long-document slot closure | Yes, `task_21` | 2 long PDF/report QA tasks from PinchBench or manually converted QwenClawBench assets | Must require at least 5 independent facts; not all answers appear in one nearby paragraph. |
| Audit/integrity closure | Yes, `task_00012` | 2 audit/state-consistency bundles from QwenClawBench | Must contain at least one state/config/output/script cross-check; no task-specific filenames in closure trigger. |
| Command/security closure | Yes, `task_00086` | 1-2 command/policy/security-analysis bundles | Must include policy conflict or classification logic plus required deliverable files. |

If no natural held-out task exists for command-security, use a synthetic variant generated by perturbing `task_00086` assets: rename files, change command strings, change counts, and change required output filenames. The synthetic variant is valid only if the closure code sees no task ID or exact old filename dependency.

Concrete QwenClawBench held-out candidates from local inspection:

| Family | Candidate | Why it is useful |
| --- | --- | --- |
| Audit/general diagnosis | `task_00029_openclaw_runtime_diagnostics_skill_and_health_audit` | Multi-file logs/config/session audit; broad enough to test whether closure finds cross-file runtime facts rather than stock-fetcher fields. |
| Audit/general diagnosis | `task_00031_server_workspace_audit_skill_and_config_change_analysis` | Config-change and workspace audit with `.new`/current diffs, SSL/credential checks, and multiple source types. |
| Audit/general diagnosis | `task_00063_second_pass_quality_audit_of_question_169663` | Quality audit with answer-key/SVG/reference cross-checks; tests whether audit shape generalizes beyond operations. |
| Audit/general diagnosis | `task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site` | Compact script/config/site-inventory audit; useful for off-by-one and sync bug validation. |
| Security/policy assessment | `task_00087_telegram_bot_credential_management_security_review` | Credential/config/log review; tests policy-vs-runtime security reasoning without command-prefix structure. |
| Security/policy assessment | `task_00088_personal_assistant_security_policy_assessment` | Trust-policy and audit-log reconciliation; tests policy conflict handling. |
| Security/policy assessment | `task_00091_security_policy_assessment_for_llm_assistant_input_trust_model` | Two policy versions plus incidents/compliance artifacts; strong held-out policy-resolution task. |
| Security/policy assessment | `task_00095_prompt_injection_defense_framework_with_skill_creation` | Security-framework task; useful but weaker because it also asks for skill creation. |

Negative controls:

| Candidate | Expected behavior |
| --- | --- |
| `task_00008_heartbeat_joke_scheduler_with_conditional_dispatch` | No audit or command-security closure should fire. |
| `task_00036_find_largest_file_in_downloads_directory` | Native/pass; closure should not fire. |
| `task_00067_write_sparql_query_for_product_reviews_containing_iphone` | Native/pass; query/spec task should not become closure-driven. |
| `task_00058_did_regression_on_simulated_panel_data` | Native/pass; full-table computation should not be forced into closure. |

QwenClawBench is sufficient for the first closure-generalization pass because it has multiple held-out audit/security tasks across domains. The remaining limitation is benchmark-style commonality: all tasks share QwenClawBench prompt/rubric conventions. For paper-strength evidence, add at least two synthetic perturbations per risky closure family.

Recommended perturbations:

| Perturbation | Applied to | Purpose |
| --- | --- | --- |
| Rename files/dirs/fields | `task_00012`, `task_00086` | Prove closure triggers by shape, not exact filenames or benchmark vocabulary. |
| Format migration | `task_00012` config YAML to JSON; `task_00086` policy YAML to TOML/JSON | Test format robustness. |
| Cross-domain audit transfer | Synthetic log-monitoring or scraper pipeline using state-list/output-record shape | Test audit closure beyond finance/stock announcements. |
| Policy multiplication | `task_00086` with extra conflicting policy/bulletin files and new pattern IDs | Test dynamic ID extraction and conflict priority. |
| Negative insertion | Add misleading `seen_ids`/`records` files to non-audit task | Measure false-positive closure rate. |

Implementation status as of 2026-05-20:

- Audit closure now detects state/output shape through generic state-list and record-list fields instead of requiring only `seen_ids`, `announcementId`, or `announcements_*.json`.
- Command-security closure now detects renamed policy/prefix/test/conflict bundles and extracts rule IDs dynamically instead of hardcoding `KI-007`, `INJ-004`, `LEGACY-R003`, or `SAB-2025-001`.
- Unit coverage includes renamed audit and renamed command-security fixtures. This is not a replacement for benchmark-level validation, but it removes the most obvious task-ID/filename dependency before running held-out tasks.

### Metrics

Primary:

- Score delta versus native baseline.
- Total tokens and request count.
- SRO calls by mode.
- Closure-use rate: whether a `collect` EvidencePack with the closure anchor was produced.
- Stop-after-ready rate: whether the agent writes the deliverable within 2 assistant turns after a ready closure.

Secondary:

- Raw source rereads after ready closure.
- Missing required output files.
- Judge failure reason category: missing evidence, wrong reasoning, incomplete deliverable, repeated verification, or tool failure.

### Support Criteria

Closure generalization is supported if:

- Held-out tasks show no average score regression greater than 1 percentage point versus native.
- At least two held-out tasks reduce total tokens or request count by 25% or more.
- The closure fires from shape-level signals, not task IDs or exact benchmark names.
- Stop-after-ready rate is higher with closure than without closure.

### Refutation Criteria

The closure claim is weakened if:

- Gains appear only on the original anchor tasks.
- Disabling the closure has no measurable effect.
- Held-out tasks trigger closure but miss key facts that native reads find.
- The closure requires exact filenames or benchmark-specific strings to work.

## Experiment 2: Benefit Gate Ablation

### Question

Does Benefit Gate actually improve the system, or are the results explainable by either always using SRO or never using SRO?

### Hypothesis

The gate is useful if it preserves SRO wins on high-sparse tasks while avoiding protocol tax on low-benefit tasks.

### Conditions

Run the same task set under:

1. `Native`: SRO disabled.
2. `Force SRO`: all supported objects route through SRO where possible.
3. `Current Gate`: current `force_sro/native/advisory` policy.
4. `Oracle Gate`: post-hoc hand-labeled upper bound based on task taxonomy, used only for analysis, not as a deployed method.

### Task Selection

Use a balanced taxonomy rather than a convenience set:

| Taxonomy | Positive or negative expectation | Current examples |
| --- | --- | --- |
| Long-document multi-fact QA | SRO positive | `task_21` |
| Structured full-table computation | Native or boundary | `task_18`, `task_00058` |
| Multi-file audit/integrity | SRO positive | `task_00012` |
| Query/spec generation | Native positive | `task_00067` |
| Security/rules closure | Model-dependent | `task_00086` |
| Mixed diagnosis | Boundary/advisory | `task_00098` |
| Large financial/report analysis | Native/advisory | `task_00073` |
| Simple file property task | Native/pass | `task_00036` |

Minimum executable set:

- Qwen: `task_21`, `task_00012`, `task_00067`, `task_00086`, `task_00098`
- DeepSeek: `task_21`, `task_00012`, `task_00067`, `task_00086`, `task_00098`

This is enough to cover positive, negative, and model-compliance cases without rerunning the full suite.

### Metrics

Primary:

- Score.
- Total tokens.
- Request count.
- SRO activation decision: `force_sro`, `native`, or `advisory`.
- Regret versus oracle: extra tokens and score loss caused by the current gate decision.

Secondary:

- Number of broad-read blocks.
- Number of ready-closure rereads.
- Native tool fallback count.
- Catastrophic failure flag: no deliverable, timeout, zero score, or invalid output.

### Support Criteria

Benefit Gate is supported if:

- `Current Gate` is close to `Oracle Gate` on average token cost while preserving score.
- `Current Gate` beats `Force SRO` on known negative shapes such as `task_00067` and computation-heavy tasks.
- `Current Gate` keeps SRO active on known positive shapes such as `task_21` and `task_00012`.
- Model-profile gating is needed only for closure-compliance shapes, not as a broad per-model switch.

### Refutation Criteria

The Benefit Gate claim is weakened if:

- `Force SRO` matches or beats current gate on both tokens and score.
- `Native` matches current gate on positives, making SRO unnecessary.
- The current gate requires many task-specific exceptions.
- Gate decisions are unstable across harmless file renames or directory layout changes.

## Experiment 3: Typed Reader Replaceability

### Question

Are typed readers replaceable executors behind the same protocol, or are paper results tied to one reader implementation?

### Hypothesis

If FileCard, HintSpec routing, artifact continuity, and EvidencePack feedback are the system core, then swapping a reader implementation should preserve the agent-facing protocol and most task behavior, with predictable tradeoffs in evidence quality or token cost.

### Reader Swap Targets

Run replaceability at the reader-family level:

| Reader family | Current implementation | Replacement candidate |
| --- | --- | --- |
| Text/PDF | Current `TextReader` with extracted text, heading-aware chunks, `collect+slots` | A simpler lexical reader: fixed-size chunks, BM25/keyword scoring, same EvidencePack schema. |
| Structured CSV/XLSX | Current `StructuredReader` with schema/sample/full-table and `calc_ready` TSV | Native script reader: bounded Python/csv/openpyxl extraction that returns the same `calc_ready` and EvidencePack fields. |
| Collection | Current `CollectionReader` with audit/diagnosis/security closures | Closure-disabled generic collection reader that returns source-keyed excerpts only. |

Do not add new agent-facing tools. The replacement must implement the same `sro_card` / `sro_read` contract.

### Controls

For each reader family:

1. `Current reader`.
2. `Replacement reader`.
3. `Reader removed/native fallback`.

### Task Selection

| Reader family | Tasks |
| --- | --- |
| Text/PDF | `task_21` plus at least one held-out long PDF/report QA task. |
| Structured | `task_18`, `task_00059`, and one full-table negative task such as `task_00058`. |
| Collection | `task_00012`, `task_00086`, `task_00098`. |

### Metrics

Primary:

- Protocol compatibility: same tool names, valid FileCard, valid HintSpec, valid EvidencePack.
- Score delta versus current reader.
- Token delta versus current reader.
- Artifact continuity errors: path rediscovery, missing artifact id, invalid refine/verify.
- Evidence sufficiency: unresolved slots, missing `calc_ready`, missing required closure fields.

Secondary:

- Reader implementation complexity: lines of code touched and added dependencies.
- Failure localization: whether failures are reader evidence errors or downstream deliverable errors.

### Support Criteria

Reader replaceability is supported if:

- Replacement readers can run without changing prompts, tools, or benchmark harness.
- At least one family replacement preserves score within 1 percentage point on its primary task while changing token cost in the expected direction.
- Collection closure removal degrades closure tasks while leaving the protocol intact, showing that the protocol and closure content are separable.
- Structured native-script reader can reproduce `calc_ready` behavior, showing that `calc_ready` is a protocol payload rather than tied to one parser.

### Refutation Criteria

The replaceability claim is weakened if:

- Tool prompts or agent-facing schemas must change for each reader.
- Swapped readers frequently break artifact continuity.
- Current results depend on hidden reader-specific fields not documented in EvidencePack.
- Replacement readers cannot produce comparable evidence without task-specific code.

## Experiment 4: Robustness To Renaming And Perturbation

### Question

Do SRO gate and closure decisions depend on brittle file names, task IDs, or benchmark-specific phrasing?

### Perturbations

For `task_00012`, `task_00086`, and `task_21`:

- Rename task directory and non-essential files.
- Shuffle file order in directory listings.
- Rewrite task prompt with synonymous wording while preserving required outputs.
- For structured tasks, reorder rows where row order is not semantically required.
- For closure tasks, change numeric values in assets and expected outputs where the judge can be updated or a manual oracle can score.

### Metrics

- Gate decision stability.
- Closure trigger stability.
- Score and token delta versus unperturbed run.
- Exact answer/value changes reflected correctly in output.

### Support Criteria

Robustness is supported if harmless renames and prompt paraphrases do not change the gate decision or closure family, and value perturbations propagate to the final answer.

### Refutation Criteria

The mechanism is brittle if it only works with original benchmark names, original file order, or original numeric values.

## Minimum Executable Priority

The experiments should be run in this order. Stop after each tier if the claim is already refuted, because later tiers depend on earlier validity.

### P0: Gate Ablation On Existing Tasks

Goal: prove the Benefit Gate is not cosmetic.

Run `Native`, `Force SRO`, and `Current Gate` on:

- Qwen: `task_21`, `task_00012`, `task_00067`, `task_00086`
- DeepSeek: `task_21`, `task_00012`, `task_00067`, `task_00086`

Why first:

- Uses existing tasks and metrics.
- Directly addresses the strongest paper risk.
- Produces a clean table for the main paper or appendix.

Success bar:

- Current Gate must match SRO positives on `task_21` and `task_00012`.
- Current Gate must avoid Force-SRO overhead on `task_00067`.
- Current Gate must explain the Qwen/DeepSeek split on `task_00086` as closure compliance, not arbitrary tuning.

### P1: Closure Held-Out And Perturbation

Goal: show closure is shape-level.

Run:

- One held-out long PDF/report QA task.
- One held-out audit/integrity bundle.
- One perturbed `task_00086` command-security variant.

Conditions:

- Current SRO with closure.
- SRO with the relevant closure disabled.
- Native baseline.

Success bar:

- Closure-enabled SRO must beat closure-disabled SRO on at least two of three tasks in either score or trajectory length.
- Perturbed task must not require original task IDs or exact original filenames.

### P2: Typed Reader Swap Smoke

Goal: show the protocol is not identical to one reader implementation.

Run one smoke per family:

- Text/PDF: `task_21` with simple lexical reader.
- Structured: `task_18` or `task_00059` with native-script `calc_ready` reader.
- Collection: `task_00012` with closure-disabled generic collection reader.

Success bar:

- All replacement readers must preserve the same tool schema and produce valid EvidencePack objects.
- At least one replacement should preserve correctness close to current reader.
- Closure-disabled collection should degrade `task_00012`, proving closure content matters while the protocol remains runnable.

### P3: Broader Taxonomy Replication

Goal: turn the appendix from a pilot into a stable result set.

Run the balanced taxonomy across Qwen and DeepSeek:

- `task_21`
- `task_00012`
- `task_00036`
- `task_00058` or `task_18`
- `task_00059`
- `task_00067`
- `task_00073`
- `task_00086`
- `task_00098`

Success bar:

- Report per-taxonomy results, not only averages.
- Keep catastrophic failures in an appendix table even if excluded from main plots.
- Use `figures/sro_experiment_data.csv` as the canonical data source after runs are validated.

## Reporting Template

Each supplementary experiment should record one row per model/task/condition:

| Field | Meaning |
| --- | --- |
| `model` | Qwen, DeepSeek, or other tested model. |
| `task_id` | Benchmark task ID or synthetic variant ID. |
| `taxonomy` | Long-doc QA, audit, structured computation, query/spec, security closure, diagnosis, or boundary. |
| `condition` | Native, Force SRO, Current Gate, Oracle Gate, closure-disabled, reader-swap, etc. |
| `gate_decision` | `force_sro`, `native`, `advisory`, or `disabled`. |
| `reader_family` | text, structured, collection, native. |
| `closure_family` | slot, audit, diagnosis, command-security, none. |
| `score` | Judge score. |
| `total_tokens` | Total token count from validated result JSON. |
| `request_count` | Number of model requests. |
| `sro_calls` | Count of `sro_card` and `sro_read` calls. |
| `ready_to_write_turns` | Turns from ready EvidencePack to first deliverable write. |
| `failure_category` | None, evidence miss, wrong reasoning, incomplete deliverable, timeout, tool failure, repeated verification. |
| `result_path` | Validated result JSON path. |
| `transcript_path` | Validated transcript path. |

## Interpretation Rules

- Do not average away taxonomy. Report long-doc QA, audit, structured computation, query/spec, security closure, and diagnosis separately.
- Do not count native/gate-pass runs as SRO wins. They support the Benefit Gate claim, not the reader claim.
- Do not hide catastrophic failures. Keep them out of main plots only if the plot is explicitly a non-catastrophic comparison, and list them in an appendix table.
- Treat model-specific closure compliance as an empirical capability flag. Avoid adding broad per-model branches unless the failure repeats across several closure families.
- Prefer small held-out or perturbed tasks over adding more benchmark-anchor reruns. The criticism to answer is generalization, not just variance.
