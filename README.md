# SparseReading

SparseRead/SRO v3 is a sparse-reading protocol for tool-using agents. It adds a deterministic Benefit Gate and typed readers so large files, long text, PDFs, and audit-style file collections can be routed through compact evidence packs instead of repeated broad reads.

Current protocol surface:

```text
sro_card(path) -> FileCard
sro_read(target, mode=scout|focus|refine|verify, hint=HintSpec) -> EvidencePack
```

The current working source lives in `nanobot-sro-v3/`. The outer repository is the recommended benchmark workspace because the test runners also use `local_agent_comp/`, `local_bin/`, and `SRO_test/qwenclawbench/` runtime fixtures.

## Quick Start

Run focused unit tests:

```bash
uv run --project nanobot-sro-v3 pytest \
  nanobot-sro-v3/tests/sparse_reading/test_sro_text_reader.py \
  nanobot-sro-v3/tests/sparse_reading/test_sro_protocol.py \
  nanobot-sro-v3/tests/sparse_reading/test_sparseread_public_api.py \
  -q
```

Run a DeepSeek API benchmark smoke test:

```bash
export API_KEY="..."
export API_BASE_URL="https://api.deepseek.com/v1"
export BENCH_MODEL="deepseek-v4-flash"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export TIMEOUT_MULTIPLIER=1

handoff/sro_v3_test_20260522/tests/scripts/run_deepseek_api_batch.sh \
  --runset smoke_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_loogle_shortdep_fall_of_outremer_3q_followup
```

See `handoff/sro_v3_test_20260522/README.md` for the packaged colleague test workflow.

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

## Repository Layout

```text
nanobot-sro-v3/                  SRO source project
local_agent_comp/                OpenClaw shim and local benchmark runners
local_bin/                       Local command wrappers
SRO_test/qwenclawbench/          Curated benchmark runtime fixtures only
figures/sro_experiment_data.csv  Official compact result table
handoff/sro_v3_test_20260522/    Colleague test handoff package
```

Do not commit API keys, generated transcripts, historical runsets, local Qwen/vLLM assets, caches, or virtual environments.
