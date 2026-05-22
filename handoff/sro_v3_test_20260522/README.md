# SparseRead/SRO v3 Test Handoff

Date: 2026-05-22

This handoff records the current accepted SparseRead/SRO v3 state and the
minimal DeepSeek/API benchmark fixtures for colleague testing. It intentionally
does not include local Qwen/vLLM setup or historical failed runsets.

## Recommended Project Root

Use the outer workspace as the test root:

```text
/Users/captainliu/sparse-reading
```

or the same layout on another machine:

```text
<workspace>/
  nanobot-sro-v3/
  local_agent_comp/
  local_bin/
  SRO_test/qwenclawbench/
  handoff/sro_v3_test_20260522/
```

`nanobot-sro-v3/` is the actual source project. The outer workspace is still
the better test root because the benchmark scripts expect sibling directories:
`local_agent_comp/`, `local_bin/`, and `SRO_test/qwenclawbench/`.

## What Is Archived Here

```text
code/sro_v3_accepted_changes.patch
code/files/
docs/
tests/scripts/
tests/runtimes/qwenclawbench/
results/sro_experiment_data.csv
```

- `code/sro_v3_accepted_changes.patch`: tracked-code diff against the current
  `nanobot-sro-v3` git base.
- `code/files/`: exact snapshots of changed and newly added source/test files,
  including untracked `sparseread/` public API files.
- `tests/scripts/`: local DeepSeek/API runner helpers and the OpenClaw shim.
- `tests/runtimes/qwenclawbench/`: small, self-contained runtime fixtures for:
  - `task_21_openclaw_comprehension`
  - `task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check`
  - `task_loogle_shortdep_fall_of_outremer_5q`
  - `task_loogle_shortdep_fall_of_outremer_3q_followup`
- `results/sro_experiment_data.csv`: current official result table snapshot.
- `docs/`: current `v3_dev.md` and `runbook.md` snapshots.

## Install Or Sync Code

If testing in this workspace, no install step is needed because the current
working tree already contains the accepted changes.

For a clean clone of `nanobot-sro-v3`, apply the tracked diff first:

```bash
cd <workspace>/nanobot-sro-v3
git apply ../handoff/sro_v3_test_20260522/code/sro_v3_accepted_changes.patch
rsync -a ../handoff/sro_v3_test_20260522/code/files/ ./
```

The `rsync` step is required because the patch does not include newly added
untracked files such as `sparseread/`.

## Verify Unit Tests

Run from the outer workspace:

```bash
uv run --project nanobot-sro-v3 pytest \
  nanobot-sro-v3/tests/sparse_reading/test_sro_text_reader.py \
  nanobot-sro-v3/tests/sparse_reading/test_sro_protocol.py \
  nanobot-sro-v3/tests/sparse_reading/test_sparseread_public_api.py \
  -q
```

Recent local status:

```text
test_sro_text_reader.py: 16 passed
test_sro_protocol.py: 72 passed
```

## Run DeepSeek/API Benchmarks

Set a real API key in the shell; do not commit it.

```bash
export API_KEY="..."
export API_BASE_URL="https://api.deepseek.com/v1"
export BENCH_MODEL="deepseek-v4-flash"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export TIMEOUT_MULTIPLIER=1
```

Dry-run first:

```bash
handoff/sro_v3_test_20260522/tests/scripts/run_deepseek_api_batch.sh \
  --runset colleague_smoke_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_loogle_shortdep_fall_of_outremer_3q_followup \
  --dry-run
```

Then run the same command without `--dry-run`.

Recommended quick smoke:

```bash
handoff/sro_v3_test_20260522/tests/scripts/run_deepseek_api_batch.sh \
  --runset colleague_smoke_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_loogle_shortdep_fall_of_outremer_3q_followup
```

Recommended fuller API-only test:

```bash
handoff/sro_v3_test_20260522/tests/scripts/run_deepseek_api_batch.sh \
  --runset colleague_deepseek_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks \
    task_21_openclaw_comprehension \
    task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
    task_loogle_shortdep_fall_of_outremer_5q \
    task_loogle_shortdep_fall_of_outremer_3q_followup
```

Outputs are written under:

```text
SRO_test/qwenclawbench/<runset>/<mode>/<task>/
  result.json
  task_transcript.jsonl
  judge_transcript.jsonl
  config/manifest.json
```

Mode meanings:

- `baseline`: SRO disabled.
- `gate`: current Benefit Gate SRO behavior.
- `force_sro_without_gate`: SRO enabled with gate override; use only for ablation.

## Bench Notes

- QwenClawBench tasks here are local runtime fixtures adapted to the OpenClaw
  runner. They are not the full QwenClawBench repository.
- LooGLE 3Q/5Q fixtures are shortdep QA tasks over a single long Fall of
  Outremer document, designed to test long-text sparse extraction.
- Current best examples:
  - task 21: long PDF slot collection, score preserved with large token saving.
  - task 00012: audit closure, score improved and token reduced.
  - LooGLE 3Q: readerfix validates duration/date/location extraction.
  - LooGLE 5Q: clean long-document multi-question compression case.


## Verified Results In CSV

These are the rows currently saved in `figures/sro_experiment_data.csv`. `SRO win` means the SRO reader/closure path directly helped. `Gate/pass` means the Benefit Gate preserved or improved behavior, sometimes through native bypass. `Boundary` rows are kept with caveats and should not be presented as clean SRO-tool wins.

| Model | Task | Short name | Verdict | Benchmark | Baseline | SRO/Gate | Tokens baseline -> SRO | Reduction | Note |
|---|---|---|---|---|---:|---:|---:|---:|---|
| Qwen | `task_21` | T21 openclaw PDF | SRO win | PinchBench/QwenClawBench | 0.944 | 1.0 | 72,865 -> 34,154 | 53.1% | slots collect; 53.1% token reduction |
| Qwen | `task_00012` | T12 stock audit | SRO win | QwenClawBench | 0.358 | 1.0 | 124,843 -> 39,085 | 68.7% | audit closure win |
| Qwen | `task_00036` | T36 file size | Gate/pass | QwenClawBench | 0.6875 | 0.6875 | 51,881 -> 44,265 | 14.7% | native gate retest; 14.7% token reduction |
| Qwen | `task_00059` | T59 discount calc | SRO win | QwenClawBench | 0.5 | 0.533 | 343,507 -> 104,449 | 69.6% | selection+script win |
| Qwen | `task_00067` | T67 SPARQL query | Gate/pass | QwenClawBench | 0.75 | 0.875 | 89,871 -> 89,999 | -0.1% | gate fix; near baseline |
| Qwen | `task_00073` | T73 P&L analysis | Gate/pass | QwenClawBench | 0.883 | 0.904 | 336,436 -> 259,955 | 22.7% | gate pass; 22.7% token reduction |
| Qwen | `task_00086` | T86 cmd security | SRO win | QwenClawBench | 0.309 | 0.954 | 140,514 -> 90,695 | 35.5% | command-security closure win |
| Qwen | `task_00098` | T98 book rec diagnosis | Boundary | QwenClawBench | 0.917 | 1.0 | 186,005 -> 143,502 | 22.9% | closure helped; 22.9% token reduction |
| DeepSeek | `task_21` | T21 openclaw PDF | SRO win | PinchBench/QwenClawBench | 1.0 | 1.0 | 714,716 -> 349,224 | 51.1% | DeepSeek-V4-Pro Phase3 slots collect plus native fallback; 51.1% token reduction; verify guard blocked low-quality q3/q4 candidates |
| DeepSeek | `task_00012` | T12 stock audit | SRO win | QwenClawBench | 0.7917 | 0.9688 | 253,685 -> 110,056 | 56.6% | DeepSeek-V4-Pro Phase3 clean audit closure win; score +0.177; token reduction 56.6% |
| DeepSeek | `task_00036` | T36 file size | Gate/pass | QwenClawBench | 0.6875 | 0.6875 | 51,881 -> 44,265 | 14.7% | native gate; 14.7% token reduction |
| DeepSeek | `task_00059` | T59 discount calc | Gate/pass | QwenClawBench | 0.708 | 0.833 | 575,574 -> 173,156 | 69.9% | runtime fix retest; 69.9% token reduction |
| DeepSeek | `task_00067` | T67 SPARQL query | Boundary | QwenClawBench | 0.6208 | 0.5583 | 167,609 -> 148,837 | 11.2% | DeepSeek-V4-Pro Phase3 gate native bypass; no sro calls; score delta likely judge/native variance; not SRO tool loss |
| DeepSeek | `task_00058` | T58 DID regression | Gate/pass | QwenClawBench | 1.0 | 1.0 | 447,300 -> 375,432 | 16.1% | DeepSeek-V4-Pro Phase3 native bypass; no sro calls; same score with lower token/time; not SRO tool win |
| DeepSeek | `task_00073` | T73 P&L analysis | Gate/pass | QwenClawBench | 0.854 | 0.854 | 318,177 -> 213,807 | 32.8% | gate pass low overhead |
| DeepSeek | `task_00086` | T86 cmd security | Gate/pass | QwenClawBench | 0.6 | 0.954 | 1,152,253 -> 859,009 | 25.4% | profile gate; no SRO |
| DeepSeek | `task_00098` | T98 book rec diagnosis | Gate/pass | QwenClawBench | 0.896 | 0.867 | 467,170 -> 312,598 | 33.1% | gate native; token down |
| DeepSeek | `task_loogle_shortdep_fall_of_outremer_5q` | LooGLE Outremer 5Q | SRO win | LooGLE/QwenClawBench | 1.0 | 1.0 | 177,141 -> 61,285 | 65.4% | DeepSeek-V4-Flash readerfix v2; single-line 100k-char LooGLE shortdep task; SRO collect slot digest; 65.4% token reduction; no score drop |
| DeepSeek | `task_loogle_shortdep_fall_of_outremer_3q_followup` | LooGLE Outremer 3Q | SRO win | LooGLE/QwenClawBench | 1.0 | 1.0 | 155,688 -> 40,300 | 74.1% | DeepSeek-V4-Flash readerfix; fixed text reader duration/date/location slots; gate-only rerun vs unchanged native baseline; 74.1% token reduction; no score drop |
| Qwen | `task_loogle_shortdep_fall_of_outremer_3q_followup` | LooGLE Outremer 3Q | SRO win | LooGLE/QwenClawBench | 0.0 | 1.0 | 621,281 -> 27,511 | 95.6% | Qwen3.5-35B-A3B local readerfix; matched fixed runset baseline exhausted 50 tool calls/no answer; SRO collect answer-shaped digest; score +1.0; 95.6% token reduction |

## Do Not Upload By Default

Keep these local unless explicitly needed:

- `SRO_test/qwenclawbench/*/results/`, `transcripts/`, `result.json`, and
  `task_transcript.jsonl` for exploratory runs.
- `tmp/`, `.pytest_cache/`, `.venv/`, `__pycache__/`, `.nanobot/`, `sessions/`.
- `qwenclawbench_full/`, `qwenclawbench_repo/`, and Hugging Face caches.
- Local or remote Qwen/vLLM artifacts.
- Any API keys, shell history, or private endpoint credentials.

Files that are reasonable to share:

- `nanobot-sro-v3/` source after applying the archived changes.
- `local_agent_comp/openclaw_shim.py` and `run_qcb_trusted_batch.sh`.
- `handoff/sro_v3_test_20260522/tests/runtimes/qwenclawbench/`.
- Curated CSV/docs such as `figures/sro_experiment_data.csv` and `v3_dev.md`.
