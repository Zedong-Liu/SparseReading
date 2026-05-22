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

When using a native DeepSeek API key rather than the Paratera proxy, set the
native endpoint and lowercase model id:

```bash
API_KEY="$DEEPSEEK_API_KEY" \
API_BASE_URL="https://api.deepseek.com/v1" \
BENCH_MODEL=deepseek-v4-flash \
TIMEOUT_MULTIPLIER=1 \
PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset ds_closure_ablation_20260520 \
  --modes gate,no_audit_closure \
  --tasks task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

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
sro_card(path)
sro_read(target, mode, hint)
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
