# SparseRead scenario benchmark runner

The four scenario groups used in the main SparseRead figure can be reproduced
through one entry point:

```bash
local_agent_comp/run_sro_scenario_bench.sh --category structured --dry-run
```

The fixtures are versioned in this repository under
`SRO_test/qwenclawbench/{baseline,sro_v3}/<task>/runtime`. The selected 17-task
bundle is about 58.5 MiB across both modes, so no external dataset download is
required after checking out this branch. Run outputs are written under a new
runset directory and remain ignored by Git.

## Categories

| Parameter | Tasks | Sources |
|---|---:|---|
| `long-context` | 5 | LooGLE, QwenClawBench T21, WB-Lite 334 Kaima derived integration task |
| `audit` | 5 | QwenClawBench T12, T55, T86, T94, T98 |
| `structured` | 4 | QwenClawBench T58/T73, SpreadsheetBench Verified 49333/11276 |
| `native-fit` | 3 | QwenClawBench T36, T59, T67 |
| `all` | 17 | All categories above |

Use `--list` to print the full task IDs:

```bash
local_agent_comp/run_sro_scenario_bench.sh --list
```

## Prerequisites

- `uv` available on `PATH`.
- The repository's `local_bin/openclaw` wrapper and `nanobot-sro-v3` source.
- An OpenAI-compatible API key. The runner uses `API_KEY`, or falls back to
  `DEEPSEEK_API_KEY`. Its default endpoint is the Paratera proxy
  `https://llmapi.paratera.com/v1`.

Do not commit credentials or generated run directories.

## Examples

Inspect the exact baseline/SR jobs without calling the API:

```bash
local_agent_comp/run_sro_scenario_bench.sh \
  --category audit \
  --model DeepSeek-V4-Flash \
  --dry-run
```

Run one category as a paired Native versus gated-SR comparison:

```bash
export API_KEY="..."

local_agent_comp/run_sro_scenario_bench.sh \
  --category structured \
  --model DeepSeek-V4-Flash \
  --modes baseline,gate \
  --runset repro-structured-dsflash-$(date +%Y%m%dT%H%M%S)
```

Run all 17 tasks with limited parallelism:

```bash
local_agent_comp/run_sro_scenario_bench.sh \
  --category all \
  --model Qwen3.6-Plus \
  --jobs 2
```

`all` with the default `baseline,gate` modes creates 34 API benchmark jobs.
Use `--dry-run` first and choose `--jobs` conservatively because concurrent
tasks share the same API quota.

Results are stored as:

```text
SRO_test/qwenclawbench/<runset>/<mode>/<task>/
  config/manifest.json
  result.json
  task_transcript.jsonl
  judge_transcript.jsonl
  runtime/
  results/
  transcripts/
```
