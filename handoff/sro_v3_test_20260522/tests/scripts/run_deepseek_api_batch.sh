#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat >&2 <<'EOF'
usage:
  SRO_PROJECT_ROOT=/path/to/sparse-reading API_KEY=... tests/scripts/run_deepseek_api_batch.sh \
    --runset <name> --modes baseline,gate --tasks <task_id> [task_id ...]

This wrapper installs the packaged runtime fixtures into the project root, then
delegates to local_agent_comp/run_qcb_trusted_batch.sh.

Recommended env for native DeepSeek API:
  API_BASE_URL=https://api.deepseek.com/v1
  BENCH_MODEL=deepseek-v4-flash
  PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
  TIMEOUT_MULTIPLIER=1
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_ROOT="${SRO_PROJECT_ROOT:-$(cd "$PACKAGE_ROOT/../.." && pwd)}"

if [[ ! -d "$PROJECT_ROOT/nanobot-sro-v3" || ! -x "$PROJECT_ROOT/local_agent_comp/run_qcb_trusted_batch.sh" ]]; then
  echo "SRO_PROJECT_ROOT must point at the outer sparse-reading workspace." >&2
  echo "Expected: nanobot-sro-v3/ and local_agent_comp/run_qcb_trusted_batch.sh under $PROJECT_ROOT" >&2
  exit 2
fi

mkdir -p "$PROJECT_ROOT/SRO_test/qwenclawbench"
rsync -a "$PACKAGE_ROOT/tests/runtimes/qwenclawbench/" "$PROJECT_ROOT/SRO_test/qwenclawbench/"

export API_BASE_URL="${API_BASE_URL:-https://api.deepseek.com/v1}"
export BENCH_MODEL="${BENCH_MODEL:-deepseek-v4-flash}"
export TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS="${PINCHBENCH_JUDGE_MAX_MSG_CHARS:-200000}"

cd "$PROJECT_ROOT"
exec local_agent_comp/run_qcb_trusted_batch.sh "$@"
