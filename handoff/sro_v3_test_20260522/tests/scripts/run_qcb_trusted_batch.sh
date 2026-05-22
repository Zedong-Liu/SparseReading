#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SRO_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
QCB_ROOT="$ROOT/SRO_test/qwenclawbench"
BENCH_MODEL="${BENCH_MODEL:-DeepSeek-V4-Flash}"
TIMEOUT_MULTIPLIER="${TIMEOUT_MULTIPLIER:-1}"
RUNSET=""
MODES="baseline,gate"
DRY_RUN=0
FORCE=0
TASKS=()

usage() {
  cat >&2 <<'EOF'
usage:
  local_agent_comp/run_qcb_trusted_batch.sh --runset <name> --modes <csv> --tasks <task_id> [task_id ...]

modes:
  baseline                SRO disabled
  sro_v3                  current SRO behavior
  gate                    current gated SRO behavior
  force_sro_without_gate  SRO enabled with SRO_BENEFIT_GATE_OVERRIDE=force_sro
  no_collection_closures  SRO enabled with all collection closures disabled
  no_audit_closure        SRO enabled with audit closure disabled
  no_command_security_closure
                          SRO enabled with command-security closure disabled

env:
  API_KEY=...             required unless --dry-run
  BENCH_MODEL=DeepSeek-V4-Flash
  API_BASE_URL=https://llmapi.paratera.com/v1
  TIMEOUT_MULTIPLIER=1

options:
  --dry-run               print planned directories and commands only
  --force                 replace an existing runset/mode/task directory
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runset)
      RUNSET="${2:-}"
      shift 2
      ;;
    --modes)
      MODES="${2:-}"
      shift 2
      ;;
    --tasks)
      shift
      while [[ $# -gt 0 && "$1" != --* ]]; do
        TASKS+=("$1")
        shift
      done
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$RUNSET" || ${#TASKS[@]} -eq 0 ]]; then
  usage
  exit 2
fi

if [[ "$RUNSET" == *"/"* || "$RUNSET" == *".."* ]]; then
  echo "runset must be a simple directory name: $RUNSET" >&2
  exit 2
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  : "${API_KEY:?set API_KEY or use --dry-run}"
fi

IFS=',' read -r -a MODE_LIST <<< "$MODES"

source_mode_for() {
  case "$1" in
    baseline) echo "baseline" ;;
    sro_v3|gate|force_sro_without_gate|no_collection_closures|no_audit_closure|no_command_security_closure) echo "sro_v3" ;;
    *)
      echo "unsupported mode: $1" >&2
      return 2
      ;;
  esac
}

configure_mode_env() {
  local mode="$1"
  unset SRO_BENEFIT_GATE_OVERRIDE
  unset SRO_COLLECTION_CLOSURES_ENABLED
  unset SRO_DISABLED_CLOSURE_FAMILIES
  unset CONTEXT_LENS_STRATEGY
  case "$mode" in
    baseline)
      export SRO_ENABLED=0
      ;;
    sro_v3|gate)
      export SRO_ENABLED=1
      ;;
    force_sro_without_gate)
      export SRO_ENABLED=1
      export SRO_BENEFIT_GATE_OVERRIDE=force_sro
      ;;
    no_collection_closures)
      export SRO_ENABLED=1
      export SRO_COLLECTION_CLOSURES_ENABLED=0
      ;;
    no_audit_closure)
      export SRO_ENABLED=1
      export SRO_DISABLED_CLOSURE_FAMILIES=audit
      ;;
    no_command_security_closure)
      export SRO_ENABLED=1
      export SRO_DISABLED_CLOSURE_FAMILIES=command_security
      ;;
    *)
      echo "unsupported mode: $mode" >&2
      return 2
      ;;
  esac
}

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  printf '%s' "$value"
}

write_manifest() {
  local path="$1"
  local mode="$2"
  local task="$3"
  local source_runtime="$4"
  local dst="$5"
  local command="$6"
  mkdir -p "$(dirname "$path")"
  {
    printf '{\n'
    printf '  "runset": "%s",\n' "$(json_escape "$RUNSET")"
    printf '  "mode": "%s",\n' "$(json_escape "$mode")"
    printf '  "task": "%s",\n' "$(json_escape "$task")"
    printf '  "created_at": "%s",\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf '  "source_runtime": "%s",\n' "$(json_escape "$source_runtime")"
    printf '  "run_dir": "%s",\n' "$(json_escape "$dst")"
    printf '  "bench_model": "%s",\n' "$(json_escape "$BENCH_MODEL")"
    printf '  "timeout_multiplier": "%s",\n' "$(json_escape "$TIMEOUT_MULTIPLIER")"
    printf '  "sro_enabled": "%s",\n' "$(json_escape "${SRO_ENABLED:-}")"
    printf '  "sro_benefit_gate_override": "%s",\n' "$(json_escape "${SRO_BENEFIT_GATE_OVERRIDE:-}")"
    printf '  "sro_collection_closures_enabled": "%s",\n' "$(json_escape "${SRO_COLLECTION_CLOSURES_ENABLED:-}")"
    printf '  "sro_disabled_closure_families": "%s",\n' "$(json_escape "${SRO_DISABLED_CLOSURE_FAMILIES:-}")"
    printf '  "nanobot_source_path": "%s",\n' "$(json_escape "$ROOT/nanobot-sro-v3")"
    printf '  "pinchbench_history_dir": "%s",\n' "$(json_escape "$dst/transcripts")"
    printf '  "command": "%s"\n' "$(json_escape "$command")"
    printf '}\n'
  } > "$path"
}

copy_latest_artifacts() {
  local dst="$1"
  local task="$2"
  local latest_result latest_task latest_judge

  latest_result="$(find "$dst/results" -maxdepth 1 -type f -name '*.json' -print | sort | tail -n 1 || true)"
  if [[ -n "$latest_result" ]]; then
    cp "$latest_result" "$dst/result.json"
  else
    echo "missing benchmark result JSON under $dst/results" >&2
    return 1
  fi

  latest_task="$(find "$dst/transcripts" -maxdepth 1 -type f -name "${task}_*.jsonl" -print | sort | tail -n 1 || true)"
  if [[ -n "$latest_task" ]]; then
    cp "$latest_task" "$dst/task_transcript.jsonl"
  else
    echo "warning: missing task transcript under $dst/transcripts" >&2
  fi

  latest_judge="$(find "$dst/transcripts" -maxdepth 1 -type f -name 'judge_*.jsonl' -print | sort | tail -n 1 || true)"
  if [[ -n "$latest_judge" ]]; then
    cp "$latest_judge" "$dst/judge_transcript.jsonl"
  else
    echo "warning: missing judge transcript under $dst/transcripts" >&2
  fi
}

for raw_mode in "${MODE_LIST[@]}"; do
  mode="$(echo "$raw_mode" | tr -d '[:space:]')"
  [[ -n "$mode" ]] || continue
  source_mode="$(source_mode_for "$mode")"

  for task in "${TASKS[@]}"; do
    src="$QCB_ROOT/$source_mode/$task/runtime"
    dst="$QCB_ROOT/$RUNSET/$mode/$task"
    command="uv run --with openai --with tqdm --with requests --with pyyaml python benchmark.py --model $BENCH_MODEL --suite $task --output-dir $dst/results --runs 1 --timeout-multiplier $TIMEOUT_MULTIPLIER --no-upload"

    if [[ ! -d "$src" ]]; then
      echo "missing source runtime: $src" >&2
      exit 1
    fi
    if [[ -e "$dst" && "$FORCE" -ne 1 ]]; then
      echo "refusing to replace existing run dir: $dst" >&2
      echo "use --force or choose a new --runset" >&2
      exit 1
    fi

    configure_mode_env "$mode"

    echo
    echo "=== PLAN $mode $task ==="
    echo "source: $src"
    echo "dest:   $dst"
    echo "env:    SRO_ENABLED=${SRO_ENABLED:-} SRO_BENEFIT_GATE_OVERRIDE=${SRO_BENEFIT_GATE_OVERRIDE:-} SRO_COLLECTION_CLOSURES_ENABLED=${SRO_COLLECTION_CLOSURES_ENABLED:-} SRO_DISABLED_CLOSURE_FAMILIES=${SRO_DISABLED_CLOSURE_FAMILIES:-}"
    echo "cmd:    $command"

    if [[ "$DRY_RUN" -eq 1 ]]; then
      continue
    fi

    rm -rf "$dst"
    mkdir -p "$dst/results" "$dst/transcripts" "$dst/config"
    cp -R "$src" "$dst/runtime"

    export PATH="$ROOT/local_bin:$PATH"
    export MODEL="$BENCH_MODEL"
    export API_BASE_URL="${API_BASE_URL:-https://llmapi.paratera.com/v1}"
    export API_KEY
    export NANOBOT_SOURCE_PATH="$ROOT/nanobot-sro-v3"
    export PYTHONPATH="$ROOT/nanobot-sro-v3${PYTHONPATH:+:$PYTHONPATH}"
    export PINCHBENCH_HISTORY_DIR="$dst/transcripts"
    export PINCHBENCH_JUDGE_MAX_MSG_CHARS="${PINCHBENCH_JUDGE_MAX_MSG_CHARS:-200000}"
    export PINCHBENCH_RUN_ID="${RUNSET}-${mode}-${task}-$(date +%Y%m%dT%H%M%S)"

    write_manifest "$dst/config/manifest.json" "$mode" "$task" "$src" "$dst" "$command"

    (
      cd "$dst/runtime/scripts"
      uv run --with openai --with tqdm --with requests --with pyyaml python benchmark.py \
        --model "$BENCH_MODEL" \
        --suite "$task" \
        --output-dir "$dst/results" \
        --runs 1 \
        --timeout-multiplier "$TIMEOUT_MULTIPLIER" \
        --no-upload
    )

    copy_latest_artifacts "$dst" "$task"
    echo "=== DONE $mode $task ==="
    echo "result: $dst/result.json"
  done
done
