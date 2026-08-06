#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 <baseline|sro_v3> <task_id>" >&2
  exit 2
fi

MODE="$1"
TASK="$2"
ROOT="/Users/captainliu/sparse-reading"
BENCH_MODEL="${BENCH_MODEL:-DeepSeek-V4-Flash}"
RESULT_MODEL_DIR="${RESULT_MODEL_DIR:-deepseek_v4_flash}"
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
SRC="$ROOT/benchmarks/qwenclawbench/$MODE/$TASK/runtime"
DST="$ROOT/benchmarks/qwenclawbench/$RESULT_MODEL_DIR/$MODE/$TASK"

if [[ ! -d "$SRC" ]]; then
  echo "missing source runtime: $SRC" >&2
  exit 1
fi

rm -rf "$DST/runtime" "$DST/results" "$DST/transcripts"
mkdir -p "$DST/results" "$DST/transcripts"
cp -R "$SRC" "$DST/runtime"

export PATH="$ROOT/local_bin:$PATH"
export MODEL="$BENCH_MODEL"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export API_KEY="${API_KEY:?set API_KEY}"
export NANOBOT_SOURCE_PATH="$ROOT/nanobot-sro-v3"
export PYTHONPATH="$ROOT/nanobot-sro-v3${PYTHONPATH:+:$PYTHONPATH}"
export PINCHBENCH_HISTORY_DIR="$DST/transcripts"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS="${PINCHBENCH_JUDGE_MAX_MSG_CHARS:-200000}"

if [[ "$MODE" == "sro_v3" ]]; then
  export SRO_ENABLED=1
  unset CONTEXT_LENS_STRATEGY
else
  export SRO_ENABLED=0
  unset CONTEXT_LENS_STRATEGY
fi

cd "$DST/runtime/scripts"
uv run --with openai --with tqdm --with requests --with pyyaml python benchmark.py \
  --model "$BENCH_MODEL" \
  --suite "$TASK" \
  --output-dir "$DST/results" \
  --runs 1 \
  --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
  --no-upload
