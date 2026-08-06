#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <baseline|sro_v3> <result_model_dir> <task_id> [task_id ...]" >&2
  echo "env: API_KEY=... [BENCH_MODEL=DeepSeek-V4-Flash] [TIMEOUT_MULTIPLIER=1]" >&2
  exit 2
fi

MODE="$1"
RESULT_DIR="$2"
shift 2

for TASK in "$@"; do
  echo
  echo "=== ${MODE} ${TASK} $(date -Is) ==="
  RESULT_MODEL_DIR="$RESULT_DIR" local_agent_comp/run_glm51_qcb_one.sh "$MODE" "$TASK"
  echo "=== DONE ${TASK} $(date -Is) ==="
done
