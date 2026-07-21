#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SRO_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
RUNNER="$SCRIPT_DIR/run_qcb_trusted_batch.sh"

CATEGORY=""
MODEL="${BENCH_MODEL:-DeepSeek-V4-Flash}"
MODES="${MODES:-baseline,gate}"
RUNSET=""
JOBS="${PARALLEL_JOBS:-1}"
DRY_RUN=0
FORCE=0
LIST_ONLY=0
TASKS=()

LONG_CONTEXT_TASKS=(
  task_loogle_shortdep_fall_of_outremer
  task_loogle_shortdep_fall_of_outremer_5q
  task_loogle_shortdep_fall_of_outremer_3q_followup
  task_21_openclaw_comprehension
  task_workspacebench_lite_334_kaima_rd
)
AUDIT_TASKS=(
  task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
  task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix
  task_00086_command_prefix_security_analysis
  task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site
  task_00098_diagnose_scheduled_book_recommendation_failure
)
STRUCTURED_TASKS=(
  task_00058_did_regression_on_simulated_panel_data
  task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis
  task_spreadsheetbench_verified_49333_trimmed_vlookup
  task_spreadsheetbench_verified_11276_weekday_row_fix
)
NATIVE_FIT_TASKS=(
  task_00036_find_largest_file_in_downloads_directory
  task_00059_user_discount_calculator
  task_00067_write_sparql_query_for_product_reviews_containing_iphone
)

usage() {
  cat <<'EOF'
Run one of the four SparseRead benchmark scenarios.

usage:
  local_agent_comp/run_sro_scenario_bench.sh --category <name> [options]
  local_agent_comp/run_sro_scenario_bench.sh --list

categories:
  long-context  Long text and PDF reading (5 tasks)
  audit         Multi-file audit and diagnosis (5 tasks)
  structured    Structured analysis (4 tasks)
  native-fit    Native-fit controls (3 tasks)
  all           All 17 tasks

options:
  --model <name>       API model name (default: DeepSeek-V4-Flash)
  --modes <csv>        Runner modes (default: baseline,gate)
  --runset <name>      Output runset name (default: generated timestamp name)
  --jobs <n>           Parallel jobs (default: 1)
  --dry-run            Validate fixtures and print the execution plan
  --force              Replace existing runset/mode/task directories
  --list               List categories and task IDs, then exit
  -h, --help            Show this help

credentials:
  Export API_KEY. If it is unset, DEEPSEEK_API_KEY is used when available.
  API_BASE_URL defaults to https://llmapi.paratera.com/v1 in the trusted runner.
EOF
}

select_tasks() {
  case "$1" in
    long-context) TASKS=("${LONG_CONTEXT_TASKS[@]}") ;;
    audit) TASKS=("${AUDIT_TASKS[@]}") ;;
    structured) TASKS=("${STRUCTURED_TASKS[@]}") ;;
    native-fit) TASKS=("${NATIVE_FIT_TASKS[@]}") ;;
    all)
      TASKS=(
        "${LONG_CONTEXT_TASKS[@]}"
        "${AUDIT_TASKS[@]}"
        "${STRUCTURED_TASKS[@]}"
        "${NATIVE_FIT_TASKS[@]}"
      )
      ;;
    *) return 2 ;;
  esac
}

category_source() {
  case "$1" in
    long-context) echo "LooGLE, QwenClawBench, WorkspaceBench-Lite-derived" ;;
    audit) echo "QwenClawBench" ;;
    structured) echo "QwenClawBench, SpreadsheetBench Verified" ;;
    native-fit) echo "QwenClawBench" ;;
  esac
}

print_catalog() {
  local category task
  for category in long-context audit structured native-fit; do
    select_tasks "$category"
    printf '%s (%s)\n' "$category" "$(category_source "$category")"
    for task in "${TASKS[@]}"; do
      printf '  %s\n' "$task"
    done
    echo
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --category)
      CATEGORY="${2:-}"
      shift 2
      ;;
    --model)
      MODEL="${2:-}"
      shift 2
      ;;
    --modes)
      MODES="${2:-}"
      shift 2
      ;;
    --runset)
      RUNSET="${2:-}"
      shift 2
      ;;
    --jobs)
      JOBS="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --list)
      LIST_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$LIST_ONLY" -eq 1 ]]; then
  print_catalog
  exit 0
fi

if [[ -z "$CATEGORY" ]]; then
  echo "--category is required" >&2
  usage >&2
  exit 2
fi

if ! select_tasks "$CATEGORY"; then
  echo "unsupported category: $CATEGORY" >&2
  usage >&2
  exit 2
fi

if [[ ! "$JOBS" =~ ^[1-9][0-9]*$ ]]; then
  echo "--jobs must be a positive integer: $JOBS" >&2
  exit 2
fi

if [[ ! -x "$RUNNER" ]]; then
  echo "missing trusted batch runner: $RUNNER" >&2
  exit 1
fi

missing=0
for mode in baseline sro_v3; do
  for task in "${TASKS[@]}"; do
    runtime="$ROOT/SRO_test/qwenclawbench/$mode/$task/runtime"
    if [[ ! -d "$runtime" ]]; then
      echo "missing versioned fixture: $runtime" >&2
      missing=1
    fi
  done
done
if [[ "$missing" -ne 0 ]]; then
  echo "The benchmark fixtures are stored in Git; restore them from this branch before running." >&2
  exit 1
fi

if [[ -z "$RUNSET" ]]; then
  model_slug="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"
  model_slug="${model_slug%-}"
  RUNSET="repro-${CATEGORY}-${model_slug}-$(date +%Y%m%dT%H%M%S)"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  if [[ -z "${API_KEY:-}" && -n "${DEEPSEEK_API_KEY:-}" ]]; then
    export API_KEY="$DEEPSEEK_API_KEY"
  fi
  : "${API_KEY:?set API_KEY (or DEEPSEEK_API_KEY), or use --dry-run}"
  command -v uv >/dev/null 2>&1 || {
    echo "uv is required but was not found on PATH" >&2
    exit 1
  }
fi

args=(
  --runset "$RUNSET"
  --modes "$MODES"
  --tasks "${TASKS[@]}"
)
if [[ "$DRY_RUN" -eq 1 ]]; then
  args+=(--dry-run)
fi
if [[ "$FORCE" -eq 1 ]]; then
  args+=(--force)
fi

echo "category: $CATEGORY (${#TASKS[@]} tasks)"
echo "model:    $MODEL"
echo "modes:    $MODES"
echo "runset:   $RUNSET"
echo "jobs:     $JOBS"

BENCH_MODEL="$MODEL" PARALLEL_JOBS="$JOBS" "$RUNNER" "${args[@]}"
