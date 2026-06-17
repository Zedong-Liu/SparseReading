# Project Runbook

This project wires PinchBench's `openclaw` interface into local nanobot and stores evaluation traces plus analysis artifacts.

## Start Point

```bash id="h5w1yu"
ssh 6000p
docker exec -it lzd-docker bash
cd /data/lzd/nanobot
git -c safe.directory=/data/lzd/nanobot status --short --branch
```

Check status before edits. `/data/lzd/...` paths are container runtime paths; `/data1/lzd/...` are host-mounted equivalents.

---

## Project Map

```text id="59u44r"
/data/lzd/agent-comp
  openclaw_shim.py              openclaw -> nanobot bridge
  pinchbench/qwen35/            transcripts + readable outputs

/data/lzd/nanobot
  main runtime repo

/data/lzd/nanobot-sro-v3
  SRO v3 implementation repo

/data/lzd/pinchbench-skill
  benchmark runners
```

---

## Evaluation Flow

* PinchBench calls `openclaw agent --message ...`
* `openclaw_shim.py` runs nanobot `AgentLoop`
* nanobot writes `sessions/cli_*.jsonl`
* shim converts sessions into openclaw-style transcripts
* outputs mirror into `agent-comp/pinchbench/qwen35/`

---

## Important Artifacts

```text id="m8l7gq"
/data/lzd/agent-comp/pinchbench/qwen35/task_*.jsonl
/data/lzd/agent-comp/pinchbench/qwen35/results/readable/*.md
/data/lzd/agent-comp/pinchbench/qwen35/token_context_analysis.md
```

Fresh benchmark runs write to:

```text id="m7pgh3"
/data/lzd/agent-comp/pinchbench/phase1_runs/
```

Only selected baseline/canonical outputs should be copied into:

```text id="g58xv6"
/data/lzd/agent-comp/pinchbench/SRO_test/
```

ContextLens outputs:

```text id="a6d2n5"
/data/lzd/agent-comp/pinchbench/context_lens_tests/{BASELINE,L1,L2,L3,HYBRID}/
```

* **QwenClawBench runs must use `--enable-auto-tool-choice --tool-call-parser qwen3_xml`** — without tool-call support the model emits no tools and scores 0%
---

## Shared Benchmark Rules

Before any vLLM-backed run:

* check `nvidia-smi` first
* reuse one vLLM instance across runs
* wait coarsely before `/health` polling
* if one GPU is busy, lower TP and pin CUDA devices
* keep a small timeout buffer for transcript persistence

---

## ContextLens Online Test

Run from remote host:

```bash id="1f4bzz"
ssh 6000p
docker exec -it lzd-docker bash -lc '
set -uo pipefail
export CUDA_VISIBLE_DEVICES=0,1

nohup /root/miniconda3/envs/kvserve-qwen35/bin/vllm serve /data/Qwen3.5-35B-A3B \
  --host 127.0.0.1 --port 8000 \
  --served-model-name qwen35-local \
  --tensor-parallel-size 2 \
  > /tmp/vllm_qwen35.log 2>&1 &

sleep 30

CONTEXT_LENS_BUDGET_CHARS=800 \
/root/miniconda3/envs/kvserve-qwen35/bin/python \
/data/lzd/nanobot/tests/context_lens/run_online_tests.py
'
```

If running outside the container, replace container-only paths.

---

## SRO v3

Paths:

```text id="o5ttxv"
local: /Users/captainliu/sparse-reading/nanobot-sro-v3
host: /data1/lzd/nanobot-sro-v3
container: /data/lzd/nanobot-sro-v3
```

Workflow:

* develop locally
* rsync into remote runtime path before experiments
* commit milestone states locally
* avoid GitHub sync workflows during phase-1

Pre-sync checks:

```bash id="e1c1y9"
git -C /Users/captainliu/sparse-reading/nanobot-sro-v3 status --short

ssh 6000p '
docker exec lzd-docker \
git -c safe.directory=/data/lzd/nanobot-sro-v3 \
-C /data/lzd/nanobot-sro-v3 status --short
'
```

Sync:

```bash id="s5w5qf"
rsync -az --delete \
  --exclude .git \
  --exclude __pycache__ \
  --exclude "*.pyc" \
  --exclude .pytest_cache \
  --exclude .ruff_cache \
  /Users/captainliu/sparse-reading/nanobot-sro-v3/ \
  6000p:/data1/lzd/nanobot-sro-v3/
```

Enable SRO:

```bash id="xj7q5j"
export SRO_ENABLED=1
export NANOBOT_SOURCE_PATH=/data/lzd/nanobot-sro-v3
export PYTHONPATH=/data/lzd/nanobot-sro-v3${PYTHONPATH:+:$PYTHONPATH}
unset CONTEXT_LENS_STRATEGY
```

Experiment-only SRO switches:

```bash
# Force every supported object through SRO; use for Benefit Gate ablation only.
export SRO_BENEFIT_GATE_OVERRIDE=force_sro

# Disable every collection closure while keeping generic SRO cards/readers.
export SRO_COLLECTION_CLOSURES_ENABLED=0

# Disable selected collection closure families.
export SRO_DISABLED_CLOSURE_FAMILIES=audit
export SRO_DISABLED_CLOSURE_FAMILIES=command_security
export SRO_DISABLED_CLOSURE_FAMILIES=diagnosis,panel_did,rule_table_script
```

Unset these variables before canonical SRO runs unless the runset explicitly
tests an ablation.

For QwenClaw/PinchBench runs, keep both `NANOBOT_SOURCE_PATH` and
`PYTHONPATH` set. `NANOBOT_SOURCE_PATH` is the intended shim selector;
`PYTHONPATH` is the guard that prevents the benchmark subprocess from
falling back to `/data/lzd/nanobot`.

Model/API override for benchmark shim:

```bash
export MODEL="GLM-5.1"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export API_KEY="..."  # do not commit real keys
```

Preferred names if several benchmark processes share the same shell:

```bash
export NANOBOT_BENCH_MODEL="GLM-5.1"
export NANOBOT_BENCH_API_BASE_URL="https://llmapi.paratera.com/v1"
export NANOBOT_BENCH_API_KEY="..."
```

The shim applies these at runtime and leaves `nanobot_bench_config.json`
unchanged. Optional overrides: `MAX_TOKENS`, `CONTEXT_WINDOW_TOKENS`
or their `NANOBOT_BENCH_...` equivalents.

Local GLM API benchmark path:

```bash
export API_KEY="..."  # GLM-compatible API key
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
local_agent_comp/run_glm51_qcb_one.sh baseline task_00059_user_discount_calculator
local_agent_comp/run_glm51_qcb_one.sh sro_v3 task_00059_user_discount_calculator
```

Trusted local QwenClaw batch examples:

```bash
BENCH_MODEL=DeepSeek-V4-Flash \
  local_agent_comp/run_qcb_trusted_batch.sh \
  --runset deepseek_gate_ablation_v1 \
  --modes baseline,gate,force_sro_without_gate \
  --tasks task_21_openclaw_comprehension \
          task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
          task_00067_write_sparql_query_for_product_reviews_containing_iphone \
          task_00086_command_prefix_security_analysis \
  --dry-run

BENCH_MODEL=DeepSeek-V4-Flash \
  local_agent_comp/run_qcb_trusted_batch.sh \
  --runset deepseek_closure_ablation_v1 \
  --modes gate,no_audit_closure,no_command_security_closure,no_collection_closures \
  --tasks task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
          task_00086_command_prefix_security_analysis \
  --dry-run
```

## API Policy

**All local API tests MUST use the Paratera proxy.** Keep the API key outside
the repository and expose it locally as `DEEPSEEK_API_KEY`.

- Endpoint: `https://llmapi.paratera.com/v1`
- Model name format: Paratera accepts the mixed-case `DeepSeek-V4-Flash`
- Scripts `run_glm51_qcb_one.sh` and `run_qcb_trusted_batch.sh` already hardcode
  this endpoint; the DeepSeek official API (`api.deepseek.com`) is NOT used
  for local tests

When running benchmarks, ensure `API_KEY=$DEEPSEEK_API_KEY` is exported:

```bash
export API_KEY="$DEEPSEEK_API_KEY"
export API_BASE_URL="https://llmapi.paratera.com/v1"
```

Speed baseline (2026-05-24, DeepSeek-V4-Flash):
- Non-streaming roundtrip: ~1,155 ms
- TTFT (streaming): ~1,314 ms
- TPOT: ~35.9 ms/token
- Throughput: ~11 tok/s

Note: Paratera adds extra per-token latency vs direct DeepSeek API
(~3-4× on TPOT) due to gateway streaming overhead.


Local prerequisites:

```text
local_bin/openclaw -> uv-based wrapper for local_agent_comp/openclaw_shim.py
SRO_test/qwenclawbench/{baseline,sro_v3}/<task>/runtime
```

The local wrapper must preserve the benchmark workspace cwd. Do not `cd` into
`nanobot-sro-v3` inside the wrapper; use `uv --project nanobot-sro-v3 run ...`
instead. Otherwise the shim treats the repo root as the task workspace and the
agent scans the codebase.
For local PDF SRO tests, include `pymupdf` in the wrapper's `uv run` packages
unless `pdftotext` is available in the runtime image.

For local API runs, `local_agent_comp/openclaw_shim.py` must honor the
benchmark-provided `NANOBOT_TIMEOUT`; do not clamp it to the old 179s default.
The benchmark runner already sets it slightly below `task.timeout_seconds *
timeout_multiplier` so transcript persistence still has a buffer.
The copied QwenClaw tasks currently use `timeout_seconds: 1800`, so the default
`TIMEOUT_MULTIPLIER=1` is already enough once the shim honors this variable.

SparseRead public API local check:

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_sparseread_public_api.py -q
```

Current nanobot-style integration:

```python
from sparseread import wrap

agent = wrap(agent, mode="auto", workspace=".")
agent.run("Audit this folder and write the report.")
```

For explicit setup in a nanobot-like project:

```python
from sparseread.adapters.nanobot import install

sparseread = install(agent, mode="auto", workspace=".")
```

Trusted local QwenClaw batch entry:

```bash
export API_KEY="..."  # do not commit real keys
export BENCH_MODEL=DeepSeek-V4-Flash
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000

local_agent_comp/run_qcb_trusted_batch.sh \
  --runset deepseek_v4_flash_trusted_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate,force_sro_without_gate \
  --tasks \
    task_21_openclaw_comprehension \
    task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
    task_00058_did_regression_on_simulated_panel_data \
    task_00059_user_discount_calculator
```

Use `--dry-run` first to confirm source runtimes and destination directories.
The runner writes one isolated directory per runset/mode/task:

```text
SRO_test/qwenclawbench/<runset>/<mode>/<task>/
  runtime/
  results/
  transcripts/
  config/manifest.json
  result.json
  task_transcript.jsonl
  judge_transcript.jsonl
```

Mode meanings:

* `baseline`: `SRO_ENABLED=0`.
* `gate`: current gated SRO behavior.
* `sro_v3`: current SRO behavior; currently equivalent to `gate` in this codebase.
* `force_sro_without_gate`: `SRO_ENABLED=1` plus `SRO_BENEFIT_GATE_OVERRIDE=force_sro`.

The override is only for controlled A/B evaluation. Leave
`SRO_BENEFIT_GATE_OVERRIDE` unset for normal gated runs.

Fresh benchmark run:

```bash id="5k9w1i"
ssh 6000p 'docker exec lzd-docker bash -lc '"'"'
set -euo pipefail

RUN_ID=phase1-task21-$(date +%Y%m%dT%H%M%S)
RUN_ROOT=/data/lzd/agent-comp/pinchbench/phase1_runs/$RUN_ID

mkdir -p "$RUN_ROOT/results" "$RUN_ROOT/transcripts"

cd /data/lzd/pinchbench-skill/scripts

export SRO_ENABLED=1
export NANOBOT_SOURCE_PATH=/data/lzd/nanobot-sro-v3
export PINCHBENCH_HISTORY_DIR="$RUN_ROOT/transcripts"

unset CONTEXT_LENS_STRATEGY

/root/miniconda3/envs/kvserve-qwen35/bin/python benchmark.py \
  --model qwen35-local \
  --suite task_21_openclaw_comprehension \
  --output-dir "$RUN_ROOT/results" \
  --no-upload
'"'"''
```

Use single quotes around outer `ssh` commands to avoid local shell expansion.

`openclaw_shim.py` respects `NANOBOT_SOURCE_PATH` before fallback paths.

SRO tools:

```text id="e8h6gr"
sro_preview(target | path | artifact_id)
sro_raw(raw_ref, range?, selector?)
sro_card(path)
sro_read(target, mode, hint)
```

Production `auto` should start with `sro_preview`; `sro_card -> sro_read` is
kept for compatibility/debugging and `SR_PROFILE=bench_protocol`.

Local Auto/L0 regression:

```bash id="sr-auto-l0-local-tests"
cd /Users/captainliu/sparse-reading-sr-auto-l0-preview
uv run --project nanobot-sro-v3 --with pytest pytest nanobot-sro-v3/tests/sparse_reading -q
```

OpenClaw plugin TypeScript validation:

```bash id="sr-openclaw-plugin-build"
cd /Users/captainliu/sparse-reading-sr-auto-l0-preview/openclaw_pilot/plugin
npm install --ignore-scripts
npm run build
rm -rf node_modules dist
```

Verification:

```bash id="l3x1cf"
docker exec lzd-docker bash -lc '
cd /data/lzd/nanobot-sro-v3 &&

/root/miniconda3/envs/kvserve-qwen35/bin/python -m pytest \
  tests/sparse_reading/test_sro_protocol.py \
  tests/sparse_reading/test_sro_text_reader.py -q &&

/root/miniconda3/envs/kvserve-qwen35/bin/python -m compileall \
  nanobot/sparse_reading \
  tests/sparse_reading
'
```

Notes:

* XLSX reader uses `openpyxl` when available
* container lacks `ruff`

---

## QwenClawBench Pilot

Source:

```text id="qcb-src"
/data/lzd/agent-comp/qwenclawbench-src
/data/lzd/agent-comp/qwenclawbench-src/data/qwenclawbench-v1.1-100
```

Comparison root:

```text id="qcb-results"
/data/lzd/agent-comp/pinchbench/SRO_test/qwenclawbench/
```

The QwenClawBench official assets are stored under:

```text id="qcb-assets"
data/qwenclawbench-v1.1-100/assets/<task_id>/...
```

PinchBench's current local runner expects flat assets under `runtime/assets/<source>`, so each task runtime should copy one task's asset directory into `runtime/assets/` before running.

vLLM for qwen35-local must be started with tool-call support:

```bash id="qcb-vllm"
docker exec lzd-docker bash -lc '
export CUDA_VISIBLE_DEVICES=0,1
nohup /root/miniconda3/envs/kvserve-qwen35/bin/vllm serve /data/Qwen3.5-35B-A3B \
  --host 127.0.0.1 --port 8000 \
  --served-model-name qwen35-local \
  --dtype auto --trust-remote-code \
  --gpu-memory-utilization 0.92 \
  --max-model-len 32768 \
  --tensor-parallel-size 2 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  > /tmp/vllm_qwen35.log 2>&1 &
'
```

Prepare one runtime:

```bash id="qcb-runtime"
TASK=task_00059_user_discount_calculator
SRC=/data/lzd/agent-comp/qwenclawbench-src/data/qwenclawbench-v1.1-100
PINCH=/data/lzd/pinchbench-skill
RUN_ROOT=/data/lzd/agent-comp/pinchbench/SRO_test/qwenclawbench/baseline/$TASK
RUNTIME=$RUN_ROOT/runtime

rm -rf "$RUNTIME"
mkdir -p "$RUNTIME/scripts" "$RUNTIME/tasks" "$RUNTIME/assets" "$RUN_ROOT/results" "$RUN_ROOT/transcripts"
cp -a "$PINCH/scripts/." "$RUNTIME/scripts/"
cp "$SRC/tasks/$TASK.md" "$RUNTIME/tasks/"
cp -a "$SRC/assets/$TASK/." "$RUNTIME/assets/"
```

Run baseline:

```bash id="qcb-baseline"
cd "$RUNTIME/scripts"
export NANOBOT_SOURCE_PATH=/data/lzd/nanobot-sro-v3
export PINCHBENCH_HISTORY_DIR="$RUN_ROOT/transcripts"
export PINCHBENCH_RUN_ID=qcb-baseline-$(date +%Y%m%dT%H%M%S)
unset SRO_ENABLED CONTEXT_LENS_STRATEGY

/root/miniconda3/envs/kvserve-qwen35/bin/python benchmark.py \
  --model qwen35-local \
  --suite "$TASK" \
  --output-dir "$RUN_ROOT/results" \
  --no-upload
```

Run SRO:

```bash id="qcb-sro"
cd "$RUNTIME/scripts"
export NANOBOT_SOURCE_PATH=/data/lzd/nanobot-sro-v3
export SRO_ENABLED=1
export PINCHBENCH_HISTORY_DIR="$RUN_ROOT/transcripts"
export PINCHBENCH_RUN_ID=qcb-sro-$(date +%Y%m%dT%H%M%S)
unset CONTEXT_LENS_STRATEGY

/root/miniconda3/envs/kvserve-qwen35/bin/python benchmark.py \
  --model qwen35-local \
  --suite "$TASK" \
  --output-dir "$RUN_ROOT/results" \
  --no-upload
```


## Gate SRO Batch Test (QwenClawBench)

Runs multiple QwenClawBench tasks under the gate evaluation set, reusing `run_qwen_gate_one.sh`.

### Helper Script

`/data/lzd/agent-comp/run_qwen_gate_one.sh` — copies a task runtime from `sro_v3/` into a named runset, then runs benchmark.py. Usage:

```
RUNSET=qwen35_benefit_gate_toolparser /data/lzd/agent-comp/run_qwen_gate_one.sh sro_v3 <task_full_name>
```

The script sources the runtime from `/data/lzd/agent-comp/pinchbench/SRO_test/qwenclawbench/sro_v3/<task>/runtime` — if a task doesn't have a runtime there yet, create it first (see [Prepare one runtime](#qcb-runtime), using `sro_v3` instead of `baseline` as the RUN_ROOT).

### Start vLLM (with tool-call support)

```bash id="qcb-vllm-gate"
ssh 6000p 'docker exec lzd-docker bash -lc "
export CUDA_VISIBLE_DEVICES=0,1
nohup /root/miniconda3/envs/kvserve-qwen35/bin/vllm serve /data/Qwen3.5-35B-A3B \
  --host 127.0.0.1 --port 8000 \
  --served-model-name qwen35-local \
  --dtype auto --trust-remote-code \
  --gpu-memory-utilization 0.92 \
  --max-model-len 32768 \
  --tensor-parallel-size 2 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_xml \
  > /tmp/vllm_qwen35_tool.log 2>&1 &
sleep 5
for i in \$(seq 1 30); do
  curl -sf --max-time 5 http://127.0.0.1:8000/v1/models > /dev/null && break
  sleep 10
done
"'
```

Log output goes to `/tmp/vllm_qwen35_tool.log`.

### Run Batch

```bash id="qcb-gate-batch"
ssh 6000p 'docker exec lzd-docker bash -lc "
set -euo pipefail

RUNSET=qwen35_benefit_gate_toolparser

TASKS=(
  task_00021_generate_canonical_dsl_v1_1_scripts_for_scratch_survival_game
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
  task_00058_did_regression_on_simulated_panel_data
  task_00059_user_discount_calculator
  task_00098_diagnose_scheduled_book_recommendation_failure
)

export NANOBOT_SOURCE_PATH=/data/lzd/nanobot-sro-v3
export PYTHONPATH=/data/lzd/nanobot-sro-v3
export MODEL=qwen35-local
export NANOBOT_BENCH_MODEL=qwen35-local
export NANOBOT_TIMEOUT=178
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export SRO_ENABLED=1
unset CONTEXT_LENS_STRATEGY
unset NANOBOT_OMIT_TOOL_CHOICE_AUTO

for task in \"\${TASKS[@]}\"; do
  echo \"=== RUN \$task \$(date -Is) ===\"
  RUNSET=\"\$RUNSET\" /data/lzd/agent-comp/run_qwen_gate_one.sh sro_v3 \"\$task\"
  echo \"=== DONE \$task \$(date -Is) ===\"
done
"'
```

To re-run a subset, edit the `TASKS` array.

### Results

```text id="qcb-gate-results"
/data/lzd/agent-comp/pinchbench/SRO_test/qwenclawbench/qwen35_benefit_gate_toolparser/sro_v3/
  task_00012_.../results/<run_id>_qwen35-local.json
  task_00021_.../results/<run_id>_qwen35-local.json
  task_00058_.../results/<run_id>_qwen35-local.json
  task_00059_.../results/<run_id>_qwen35-local.json
  task_00098_.../results/<run_id>_qwen35-local.json
```

Each task dir also has `transcripts/` (agent + judge transcripts) and `runtime/` (benchmark scripts, assets).

### Pre-install Required Packages

Task 58 needs `statsmodels` and `linearmodels` — without them the DID regression runs fail:

```bash id="qcb-gate-pkgs"
ssh 6000p 'docker exec lzd-docker bash -lc "
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
proxychains4 -q /root/miniconda3/envs/kvserve-qwen35/bin/pip install --no-cache-dir statsmodels linearmodels
"'
```

---

## Proxychains + pip

Install proxychains:

```bash id="qg5q1n"
ssh 6000p 'docker exec lzd-docker bash -lc "
export proxy=http://10.18.81.5:7897
export http_proxy=\$proxy https_proxy=\$proxy

apt-get update -y
apt-get install -y proxychains4
"'
```

Install packages through proxychains:

```bash id="z9yxyl"
ssh 6000p 'docker exec lzd-docker bash -lc "
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy

proxychains4 -q pip install --no-cache-dir \
  --force-reinstall openpyxl==3.1.5
"'
```

## Diagnostic Ledger Tests

```bash id="diag-ledger-tests"
cd /Users/captainliu/sparse-reading/nanobot-sro-v3
uv run --with pytest python3.12 -m pytest tests/sparse_reading/test_sro_protocol.py -q -k "diagnostic_ledger or ledger or over_10 or readiness or preserves_audit or task44_integration"
```

All SRO tests:

```bash id="sro-all-tests"
cd /Users/captainliu/sparse-reading/nanobot-sro-v3
uv run --with pytest python3.12 -m pytest tests/sparse_reading/test_sro_protocol.py -q
```

## Diagnostic Ledger Compact View + Detail Expansion (2026-05-23)

### Local Tests

```bash id="diag-compact-tests"
cd /Users/captainliu/sparse-reading/nanobot-sro-v3
uv run --with pytest python3.12 -m pytest tests/sparse_reading/ -q
```

### Detail Expansion Syntax

Model requests specific diagnostic sections via `sro_read` with needles:

| Section | Needle |
|---|---|
| Config snapshot + disabled flags | `diagnostic_detail_config` |
| Config diffs | `diagnostic_detail_diffs` |
| Log events / eviction | `diagnostic_detail_loss` |
| Metric tables / precision | `diagnostic_detail_metrics` |
| Methodology / evaluation flags | `diagnostic_detail_evaluation` |
| Proposal inventory | `diagnostic_detail_proposals` |
| Full section index | `diagnostic_detail_full` |

Example:

```json
{
  "target": {"artifact_id": "sro_..."},
  "mode": "collect",
  "hint": {
    "goal": "config detail",
    "needles": ["diagnostic_detail_config"],
    "want": "fact",
    "type_hint": "collection"
  }
}
```

### Remote Sync

```bash id="diag-sync"
rsync -az --delete \
  --exclude .git \
  --exclude __pycache__ \
  --exclude "*.pyc" \
  --exclude .pytest_cache \
  --exclude .ruff_cache \
  --exclude .venv \
  /Users/captainliu/sparse-reading/nanobot-sro-v3/ \
  6000p:/data1/lzd/nanobot-sro-v3/
```

### Remote Verification

```bash id="diag-remote-verify"
ssh 6000p 'docker exec lzd-docker bash -lc "
cd /data/lzd/nanobot-sro-v3
/root/miniconda3/envs/kvserve-qwen35/bin/python -m pytest tests/sparse_reading/ -q
"'
```

## 2026-05-26: P0 SKILL.md Presentation A/B

This experiment changes only `nanobot-sro-v3/nanobot/skills/sparse-reading/SKILL.md`.
The legacy comparison is a frozen local source snapshot created before editing:

```bash id="p0-skill-freeze-legacy"
rm -rf /tmp/sro_p0_legacy_root
mkdir -p /tmp/sro_p0_legacy_root
rsync -a --exclude .git --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  /Users/captainliu/sparse-reading/nanobot-sro-v3/ \
  /tmp/sro_p0_legacy_root/nanobot-sro-v3/
ln -s /Users/captainliu/sparse-reading/SRO_test /tmp/sro_p0_legacy_root/SRO_test
ln -s /Users/captainliu/sparse-reading/local_bin /tmp/sro_p0_legacy_root/local_bin
```

Local protocol regression:

```bash id="p0-skill-unit-tests"
cd /Users/captainliu/sparse-reading/nanobot-sro-v3
uv run --with pytest python3.12 -m pytest tests/sparse_reading/ -q
```

Legacy DeepSeek comparisons:

```bash id="p0-skill-legacy-flash"
cd /Users/captainliu/sparse-reading
SRO_PROJECT_ROOT=/tmp/sro_p0_legacy_root \
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_skill_legacy_flash_20260526 --modes gate --tasks \
  task_21_openclaw_comprehension \
  task_loogle_shortdep_fall_of_outremer_3q_followup \
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

```bash id="p0-skill-legacy-pro"
cd /Users/captainliu/sparse-reading
SRO_PROJECT_ROOT=/tmp/sro_p0_legacy_root \
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Pro TIMEOUT_MULTIPLIER=2 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_skill_legacy_pro_20260526 --modes gate --tasks \
  task_21_openclaw_comprehension \
  task_loogle_shortdep_fall_of_outremer_3q_followup \
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

Accepted compact-skill comparisons:

```bash id="p0-skill-v6-flash"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_skill_compact_v6_flash_20260526 --modes gate --tasks \
  task_21_openclaw_comprehension \
  task_loogle_shortdep_fall_of_outremer_3q_followup \
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

```bash id="p0-skill-v6-pro"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Pro TIMEOUT_MULTIPLIER=2 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_skill_compact_v6_pro_20260526 --modes gate --tasks \
  task_21_openclaw_comprehension \
  task_loogle_shortdep_fall_of_outremer_3q_followup \
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

Diagnostic runsets retained for analysis: `p0_skill_compact_flash_20260526`
(ready-status over-verification), `p0_skill_compact_v2_pro_20260526`
(collection write-ready ignored), `p0_skill_compact_v3_flash_20260526`
(model service error), and `p0_skill_compact_v5_flash_20260526`
(`scope: "anchored"` invalid call and timeout).

## 2026-05-26: P1 Tool Interface Schema Smoke

P1 local regression:

```bash id="p1-schema-unit-tests"
cd /Users/captainliu/sparse-reading/nanobot-sro-v3
uv run --with pytest python3.12 -m pytest tests/sparse_reading/ -q
```

Bounded DeepSeek-V4-Flash smoke run:

```bash id="p1-schema-smoke-flash"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p1_schema_smoke_flash_20260526 --modes gate --tasks \
  task_21_openclaw_comprehension \
  task_loogle_shortdep_fall_of_outremer_3q_followup \
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

Results are under
`SRO_test/qwenclawbench/p1_schema_smoke_flash_20260526/gate/`.

Expanded Flash comparison run, P1 condition only:

```bash id="p1-schema-generalization-flash"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
PARALLEL_JOBS=4 local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p1_schema_generalization_flash_20260526 --modes gate --tasks \
  task_loogle_shortdep_fall_of_outremer \
  task_00059_user_discount_calculator \
  task_00058_did_regression_on_simulated_panel_data \
  task_00067_write_sparql_query_for_product_reviews_containing_iphone \
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix \
  task_00086_command_prefix_security_analysis \
  task_loogle_shortdep_fall_of_outremer_5q \
  task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site
```

Comparison data are recorded in
`SRO_test/qwenclawbench/p1_schema_generalization_flash_20260526.csv`.

Targeted P1 adjustment validation after normalizing `.txt` hints and retaining
one ready-to-write terminal cue:

```bash id="p1-schema-targeted-fix-flash"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
PARALLEL_JOBS=4 local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p1_schema_targeted_fix_flash_20260526 --modes gate --tasks \
  task_loogle_shortdep_fall_of_outremer_5q \
  task_loogle_shortdep_fall_of_outremer \
  task_00059_user_discount_calculator \
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix
```

The two LooGLE results are recorded in
`SRO_test/qwenclawbench/p1_schema_targeted_fix_flash_20260526.csv`. The
`00059` and `00055` control reruns crashed before producing scored results and
did not invoke `sro_read`; use their prior scored P0/P1 runs for trajectory
diagnosis only, not for attribution to the targeted fix.

P0+C+B ablation after removing the expanded `sro_read` schema (`A`), tested
from commit `8ec7b7d` on branch `codex/p1-ablation-p0-c-b`:

```bash id="p0-cb-ablation-flash-r1"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
PARALLEL_JOBS=3 local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_cb_flash_r1_20260527 --modes gate --tasks \
  task_00059_user_discount_calculator \
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix \
  task_loogle_shortdep_fall_of_outremer_5q
```

```bash id="p0-cb-ablation-flash-r2"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
PARALLEL_JOBS=3 local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_cb_flash_r2_20260527 --modes gate --tasks \
  task_00059_user_discount_calculator \
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix \
  task_loogle_shortdep_fall_of_outremer_5q
```

Results are recorded in
`SRO_test/qwenclawbench/p0_cb_ablation_flash_20260527.csv`.

P0+C ablation after also removing the shortened description / terminal cue
change (`B`), tested from commit `b8d34fd` on branch
`codex/p1-ablation-p0-c`:

```bash id="p0-c-ablation-flash"
cd /Users/captainliu/sparse-reading
API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
PARALLEL_JOBS=2 local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_c_flash_20260527 --modes gate --tasks \
  task_00059_user_discount_calculator \
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix
```

Results are recorded in
`SRO_test/qwenclawbench/p0_c_ablation_flash_20260527.csv`.

Repeated native-path control for unmodified P0 versus P0+C:

- P0 was run from isolated worktree
  `/Users/captainliu/sparse-reading_bench/p0_repeat_20260527` at commit
  `026d7cf`.
- P0+C was run from isolated worktree
  `/Users/captainliu/sparse-reading_bench/p0c_repeat_20260527` at commit
  `b8d34fd`.
- Both worktrees received the same benchmark-runner empty-array fix
  (`pids=("${new_pids[@]:-}")`) and identical task fixture symlinks; neither
  changes the SRO variant under comparison.

```bash id="p0-native-repeat-flash-r1-r2"
cd /Users/captainliu/sparse-reading_bench/p0_repeat_20260527
for runset in p0_native_repeat_r1_20260527 p0_native_repeat_r2_20260527; do
  API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
  BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
  PARALLEL_JOBS=2 local_agent_comp/run_qcb_trusted_batch.sh \
    --runset "$runset" --modes gate --tasks \
    task_00059_user_discount_calculator \
    task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix
done
```

```bash id="p0c-native-repeat-flash-r1-r2"
cd /Users/captainliu/sparse-reading_bench/p0c_repeat_20260527
for runset in p0c_native_repeat_r1_20260527 p0c_native_repeat_r2_20260527; do
  API_KEY="$DEEPSEEK_API_KEY" API_BASE_URL=https://llmapi.paratera.com/v1 \
  BENCH_MODEL=DeepSeek-V4-Flash TIMEOUT_MULTIPLIER=1 PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
  PARALLEL_JOBS=2 local_agent_comp/run_qcb_trusted_batch.sh \
    --runset "$runset" --modes gate --tasks \
    task_00059_user_discount_calculator \
    task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix
done
```

Results are recorded in
`SRO_test/qwenclawbench/p0_vs_p0c_native_repeat_flash_20260527.csv`.

## P1.5 Fix3/Fix4 DeepSeek Pro Verification

Use these commands to reproduce the P1.5 fix3/fix4 checks from 2026-05-30.
The fix4 deliverable-first variant was exploratory and was rejected after the
full 8-task rerun because it worsened native-bypass tasks `00055` and `00094`.

```bash id="p15-fix3-final-pro-8task"
cd /Users/captainliu/sparse-reading
source ~/.zshrc
export API_KEY="$DEEPSEEK_API_KEY"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL=DeepSeek-V4-Pro
export TIMEOUT_MULTIPLIER=2
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export PARALLEL_JOBS=4
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p15_fix3_final_pro_8task_20260530 --modes gate --tasks \
  task_loogle_shortdep_fall_of_outremer \
  task_loogle_shortdep_fall_of_outremer_5q \
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix \
  task_00058_did_regression_on_simulated_panel_data \
  task_00059_user_discount_calculator \
  task_00067_write_sparql_query_for_product_reviews_containing_iphone \
  task_00086_command_prefix_security_analysis \
  task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site
```

```bash id="p15-fix4-deliverable-pro-00058"
cd /Users/captainliu/sparse-reading
source ~/.zshrc
export API_KEY="$DEEPSEEK_API_KEY"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL=DeepSeek-V4-Pro
export TIMEOUT_MULTIPLIER=2
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export PARALLEL_JOBS=1
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p15_fix4_deliverable_pro_00058_20260530 --modes gate --tasks \
  task_00058_did_regression_on_simulated_panel_data
```

Final sparse-reading regression used:

```bash id="p15-fix4-sparse-tests"
cd /Users/captainliu/sparse-reading/nanobot-sro-v3
uv run --with pytest --with pytest-asyncio pytest tests/sparse_reading -q
```

P0-current backfill for `00073` and `00098` used the archived P0 worktree:

```bash id="p0-current-pro-73-98-backfill"
cd /Users/captainliu/sparse-reading_bench/p0_repeat_20260527
source ~/.zshrc
export API_KEY="$DEEPSEEK_API_KEY"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL=DeepSeek-V4-Pro
export TIMEOUT_MULTIPLIER=2
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export PARALLEL_JOBS=2
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_current_pro_73_98_20260531 --modes gate --tasks \
  task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis \
  task_00098_diagnose_scheduled_book_recommendation_failure
```

```bash id="p0-current-flash-73-98-backfill"
cd /Users/captainliu/sparse-reading_bench/p0_repeat_20260527
source ~/.zshrc
export API_KEY="$DEEPSEEK_API_KEY"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL=DeepSeek-V4-Flash
export TIMEOUT_MULTIPLIER=1
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export PARALLEL_JOBS=2
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset p0_current_flash_73_98_20260531 --modes gate --tasks \
  task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis \
  task_00098_diagnose_scheduled_book_recommendation_failure
```

The comparison with P1.5 fix3 Pro is recorded in
`SRO_test/qwenclawbench/p15_fix3_vs_p0_current_73_98_20260531.csv`.
