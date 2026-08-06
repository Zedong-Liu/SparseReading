#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SRO_PROJECT_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
QCB_ROOT="$ROOT/SRO_test/qwenclawbench"
QCB_FIXTURE_ROOT="${QCB_FIXTURE_ROOT:-$QCB_ROOT}"
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
  local nanobot_version source_revision source_dirty source_fingerprint
  nanobot_version="$(
    python -c \
      'import pathlib, tomllib; print(tomllib.loads(pathlib.Path(__import__("sys").argv[1]).read_text())["project"]["version"])' \
      "$ROOT/nanobot-sro-v3/pyproject.toml"
  )"
  source_revision="$(git -C "$ROOT" rev-parse HEAD)"
  if [[ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]]; then
    source_dirty="true"
  else
    source_dirty="false"
  fi
  source_fingerprint="$({
    find "$ROOT/packages/sparseread-core/src" "$ROOT/nanobot-sro-v3/nanobot" -type f \
      \( -name '*.py' -o -name '*.md' \) -print
  } | LC_ALL=C sort | while IFS= read -r source_file; do
    shasum -a 256 "$source_file"
  done | shasum -a 256 | awk '{print $1}')"
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
    printf '  "nanobot_version": "%s",\n' "$(json_escape "$nanobot_version")"
    printf '  "source_revision": "%s",\n' "$(json_escape "$source_revision")"
    printf '  "source_dirty": %s,\n' "$source_dirty"
    printf '  "source_fingerprint": "%s",\n' "$(json_escape "$source_fingerprint")"
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

PARALLEL_JOBS="${PARALLEL_JOBS:-1}"

_run_one_task() {
  local mode="$1"
  local task="$2"
  local src="$3"
  local dst="$4"
  local command="$5"

  configure_mode_env "$mode"

  echo
  echo "=== PLAN $mode $task ==="
  echo "source: $src"
  echo "dest:   $dst"
  echo "env:    SRO_ENABLED=${SRO_ENABLED:-} SRO_BENEFIT_GATE_OVERRIDE=${SRO_BENEFIT_GATE_OVERRIDE:-} SRO_COLLECTION_CLOSURES_ENABLED=${SRO_COLLECTION_CLOSURES_ENABLED:-} SRO_DISABLED_CLOSURE_FAMILIES=${SRO_DISABLED_CLOSURE_FAMILIES:-}"
  echo "cmd:    $command"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    return 0
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
  # Isolate agent and judge sessions directories per task to avoid concurrency races
  export PINCHBENCH_AGENT_SUFFIX="-${mode}-${task}"
  export PINCHBENCH_JUDGE_AGENT_SUFFIX="-${mode}-${task}"

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
}

# Collect all (mode, task) pairs and validate sources
declare -a JOBS=()
for raw_mode in "${MODE_LIST[@]}"; do
  mode="$(echo "$raw_mode" | tr -d '[:space:]')"
  [[ -n "$mode" ]] || continue
  source_mode="$(source_mode_for "$mode")"

  for task in "${TASKS[@]}"; do
    src="$QCB_FIXTURE_ROOT/$source_mode/$task/runtime"
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

    JOBS+=("${mode}|${task}|${src}|${dst}|${command}")
  done
done

# Execute jobs (parallel when PARALLEL_JOBS > 1)
if [[ "$PARALLEL_JOBS" -le 1 ]]; then
  for job in "${JOBS[@]}"; do
    IFS='|' read -r mode task src dst command <<< "$job"
    _run_one_task "$mode" "$task" "$src" "$dst" "$command"
  done
else
  pids=()
  all_pids=()
  for job in "${JOBS[@]}"; do
    # Wait for a slot if at max capacity
    while [[ ${#pids[@]} -ge $PARALLEL_JOBS ]]; do
      new_pids=()
      for pid in "${pids[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
          new_pids+=("$pid")
        fi
      done
      pids=("${new_pids[@]:-}")
      [[ ${#pids[@]} -ge $PARALLEL_JOBS ]] && sleep 1
    done

    IFS='|' read -r mode task src dst command <<< "$job"
    _run_one_task "$mode" "$task" "$src" "$dst" "$command" &
    pids+=($!)
    all_pids+=($!)
  done

  # Reap every job and propagate any failure to the batch exit status.
  failed=0
  for pid in "${all_pids[@]}"; do
    wait "$pid" 2>/dev/null || failed=1
  done
  exit "$failed"
fi
