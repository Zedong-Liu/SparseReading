# Supplemental-experiment pilot record — GLM-4.5-Flash

Runtime: `codex/sr-paper-nanobot-bench` at `0cceef6`, with
`SPARSEREAD_MODE=bench_protocol` and Paratera `GLM-4.5-Flash`.

## Retain for formal follow-up

`task_qasper_1912_13337_3q` is a candidate only, not a paper result yet.

| mode | score | total tokens | requests | time |
| --- | ---: | ---: | ---: | ---: |
| Native | 0.50 | 177,639 | 11 | 32 s |
| SparseRead | 0.75 | 53,226 | 4 | 16 s |

SparseRead used the observed path `read_file -> sro_read(collect, 3 slots) ->
write_file`, without grep/raw fallback.  It reduced tokens by 70.0% and requests
by 63.6%, but the single run did not achieve full score.  Before formal use,
repeat 3–5 seeds and add similarly shaped multi-question QASPER papers.

Source artifacts:
`SRO_test/qwenclawbench/paper_retest_glm45_20260711/{baseline,gate}/task_qasper_1912_13337_3q/`.

## Rejected structured pilots

The two SpreadsheetBench XLSX tasks are not candidates for the current SR
protocol.  In ordinary gate mode, the structured benefit gate is advisory and
the agent used native `pandas`/`openpyxl` paths rather than `sro_read`; their
apparent token differences are therefore not attributable to SparseRead.

Forcing the benefit gate did not rescue them:

| task | force score | total tokens | requests | reason to reject |
| --- | ---: | ---: | ---: | --- |
| `80_42_multisheet_cohort` | 0.00 | 863,703 | 24 | Used SR but did not converge; native was 1.00 at 171,616 tokens / 11 requests. |
| `55427_schooltype_join` | 1.00 | 418,871 | 19 | Still mostly native `exec`; native was 1.00 at 108,455 tokens / 7 requests. |

## Existing structured collection audit (not a new candidate)

The public QwenClawBench task 12 A-stock audit was already collected.  This
rerun only confirms that it is a valid SR-shaped structured task: it requires
selecting and cross-checking JSON state, JSON output, YAML config, and code,
then performing a small exact cohort check.  It is not counted as a newly found
candidate in this pilot.

| mode | score | total tokens | requests | time |
| --- | ---: | ---: | ---: | ---: |
| Native | 0.72 | 601,422 | 27 | 131 s |
| SparseRead | 1.00 | 88,463 | 5 | 41 s |

Observed SparseRead path: `list_dir -> sro_read(collect, collection) ->
write_file -> read_file`.  The native trace instead made 21 `read_file` calls.
This is an 85.3% token and 81.5% request reduction with higher score in one
seed.  Before formal use, repeat 3–5 seeds and retain trajectory audits.

Source artifacts:
`SRO_test/qwenclawbench/structured_task12_glm45_20260711/{baseline,gate}/task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check/`.

## Additional external ClawBench probe: rejected

The public ClawBench `xdom-013-incident-response-pipeline` adapter was rejected:
both native and gate scored 0.00, and the gate trace made only `list_dir` plus
five native `read_file` calls (no `sro_read`).  Its 15 KB collection is too
small and lacks the current audit gate's code-plus-state/output closure, so it
is not a valid SparseRead attribution case.

QwenClawBench task 29 has since been found in the local canonical checkout at
`/Users/captainliu/sparse-reading/qwenclawbench_repo/data/qwenclawbench-v1.1-100`.
It is an OpenClaw runtime health audit with configuration, state, shell script,
logs, and 187 session records.  The Hugging Face mirror is access-gated, but
the local task and assets are complete.  The first Native/gate attempt was
stopped without scoring after both agents followed a stale path embedded in the
fixture metadata instead of the workspace-root `sessions/` directory; it is
not evidence about SparseRead.
