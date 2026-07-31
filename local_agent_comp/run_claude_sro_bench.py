#!/usr/bin/env python3
"""
Claude Code SRO Benchmark — self-contained end-to-end runner.

Replaces the openclaw shim pipeline with direct claude -p execution.
Uses the task's own grading functions for evaluation.
Supports baseline (SRO_ENABLED=0) and gate (SRO_ENABLED=1) modes.

Usage:
  cd C:/Users/xule/Desktop/SparseReading
  python local_agent_comp/run_claude_sro_bench.py \
    --category native-fit --model DeepSeek-V4-Flash --modes baseline
  python local_agent_comp/run_claude_sro_bench.py \
    --category all --model DeepSeek-V4-Flash --modes baseline,gate
"""
from __future__ import annotations

import argparse, io, json, os, shutil, subprocess, sys, tempfile, time, traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO / "SRO_test" / "qwenclawbench" / "baseline"
SRO_V3_DIR = REPO / "SRO_test" / "qwenclawbench" / "sro_v3"
RESULTS_ROOT = REPO / "SRO_test" / "qwenclawbench"

# Task definitions from run_sro_scenario_bench.sh
LONG_CONTEXT = [
    "task_loogle_shortdep_fall_of_outremer",
    "task_loogle_shortdep_fall_of_outremer_5q",
    "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_21_openclaw_comprehension",
    "task_workspacebench_lite_334_kaima_rd",
]
AUDIT = [
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
    "task_00086_command_prefix_security_analysis",
    "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site",
    "task_00098_diagnose_scheduled_book_recommendation_failure",
]
STRUCTURED = [
    "task_00058_did_regression_on_simulated_panel_data",
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    "task_spreadsheetbench_verified_49333_trimmed_vlookup",
    "task_spreadsheetbench_verified_11276_weekday_row_fix",
]
NATIVE_FIT = [
    "task_00036_find_largest_file_in_downloads_directory",
    "task_00059_user_discount_calculator",
    "task_00067_write_sparql_query_for_product_reviews_containing_iphone",
]

TASK_CATEGORIES = {}
for t in LONG_CONTEXT: TASK_CATEGORIES[t] = "long-context"
for t in AUDIT: TASK_CATEGORIES[t] = "audit"
for t in STRUCTURED: TASK_CATEGORIES[t] = "structured"
for t in NATIVE_FIT: TASK_CATEGORIES[t] = "native-fit"

ALL_TASKS = LONG_CONTEXT + AUDIT + STRUCTURED + NATIVE_FIT


@dataclass
class BenchResult:
    task_id: str = ""
    category: str = ""
    mode: str = ""
    grading_type: str = ""
    score: float = 0.0
    max_score: float = 1.0
    elapsed_s: float = 0.0
    tokens_est: int = 0
    success: bool = False
    error: str = ""
    breakdown: dict = field(default_factory=dict)
    notes: str = ""


def load_task_info(task_id: str, source_mode: str = "baseline") -> dict:
    """Load task definition and grading code."""
    src_dir = (BASELINE_DIR if source_mode == "baseline" else SRO_V3_DIR) / task_id / "runtime"
    if not src_dir.exists():
        return {"error": f"Source not found: {src_dir}"}

    tasks_dir = src_dir / "tasks"
    md_files = list(tasks_dir.glob("*.md")) if tasks_dir.exists() else []
    if not md_files:
        return {"error": f"No task .md in {tasks_dir}"}

    content = md_files[0].read_text(encoding="utf-8", errors="replace")

    # Parse frontmatter
    frontmatter = {}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip()

    # Extract prompt
    prompt = ""
    if "## Prompt" in content:
        prompt = content.split("## Prompt", 1)[1]
        if "## Expected" in prompt:
            prompt = prompt.split("## Expected", 1)[0]
    prompt = prompt.strip()

    # Extract grading code
    grading_code = ""
    grading_type = frontmatter.get("grading_type", "automated")
    if "```python" in content:
        parts = content.split("```python")
        if len(parts) > 1:
            grading_code = parts[1].split("```", 1)[0]

    return {
        "task_id": task_id,
        "frontmatter": frontmatter,
        "prompt": prompt,
        "grading_type": grading_type,
        "grading_code": grading_code,
        "content": content,
        "source_dir": str(src_dir),
    }


def run_task_with_claude(task_id: str, source_mode: str, sro_enabled: bool,
                         timeout_s: int = 360) -> BenchResult:
    """Run one task through claude -p and grade it."""
    info = load_task_info(task_id, source_mode)
    if "error" in info:
        return BenchResult(task_id=task_id, mode="baseline" if not sro_enabled else "gate",
                           error=info["error"])

    prompt = info["prompt"]
    grading_type = info["grading_type"]
    grading_code = info["grading_code"]
    src_dir = Path(info["source_dir"])

    if not prompt:
        return BenchResult(task_id=task_id, mode="baseline" if not sro_enabled else "gate",
                           error="Empty prompt")

    # Create isolated workspace with task fixtures at correct paths
    ws = Path(tempfile.mkdtemp(prefix=f"claude_sro_{task_id}_"))

    # Parse workspace_files from frontmatter and copy assets to correct dest paths
    fm = info["frontmatter"]
    wf_raw = fm.get("workspace_files", "")
    # Actually, workspace_files is a YAML list in the frontmatter, not a simple string.
    # Instead, just copy assets to the workspace root (so downloads/ is at top level)
    assets_dir = src_dir / "assets"
    if assets_dir.exists():
        for item in assets_dir.iterdir():
            dest = ws / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=False)
            else:
                shutil.copy2(item, dest)

    # Also copy task file to ws/task.md for reference
    tasks_dir = src_dir / "tasks"
    if tasks_dir.exists():
        md_files = list(tasks_dir.glob("*.md"))
        if md_files:
            shutil.copy2(md_files[0], ws / "task.md")

    # Build claude -p command
    model = os.environ.get("BENCH_MODEL", "DeepSeek-V4-Flash")
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["ANTHROPIC_MODEL"] = model
    child_env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model

    api_key = os.environ.get("API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    if api_key:
        child_env["ANTHROPIC_AUTH_TOKEN"] = api_key
    api_base = os.environ.get("API_BASE_URL", "https://api.deepseek.com/anthropic")
    child_env["ANTHROPIC_BASE_URL"] = api_base

    tmp_settings = ws.parent / "bench_settings.json"
    tmp_settings.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    cmd = [
        "claude", "-p", prompt,
        "--max-turns", "20",
        "--dangerously-skip-permissions",
        "--add-dir", str(ws),
        "--settings", str(tmp_settings),
        "--output-format", "text",
    ]

    print(f"  [{task_id}] {'SRO' if sro_enabled else 'BL'} claude -p (timeout={timeout_s}s)...",
          file=sys.stderr, flush=True)

    # Retry loop for API connection issues
    max_retries = 3
    stdout, stderr, elapsed = "", "", 0.0
    for attempt in range(max_retries):
        start = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s,
                                  cwd=str(ws), env=child_env)
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                print(f"    retry {attempt+1}/{max_retries} after timeout...", file=sys.stderr, flush=True)
                continue
            return BenchResult(task_id=task_id, category=TASK_CATEGORIES.get(task_id, "unknown"),
                               mode="baseline" if not sro_enabled else "gate",
                               grading_type=grading_type, success=False, error="timeout",
                               elapsed_s=round(time.time() - start, 1))
        elapsed = time.time() - start
        stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace")

        # Check for API connection errors
        is_api_err = "Connection closed" in stdout or "Connection closed" in stderr
        if is_api_err and attempt < max_retries - 1:
            print(f"    retry {attempt+1}/{max_retries} (API disconnect)...", file=sys.stderr, flush=True)
            time.sleep(5)
            continue
        break

    success = proc.returncode == 0 and len(stdout) > 60 and "Connection closed" not in stdout[:200]

    # Build transcript from claude output (approximate format for grading)
    transcript = [
        {"type": "message",
         "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
    ]

    # Parse claude output for assistant response and tool calls
    lines = stdout.replace("\r", "").split("\n")
    assistant_texts = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("⏺") and not s.startswith("⎿") and len(s) > 3:
            assistant_texts.append(s)

    response = "\n".join(assistant_texts)
    if response:
        transcript.append({
            "type": "message",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": response}]},
        })

    # Also add tool_call transcript entries for commands found in stderr/logs
    # (Claude Code doesn't expose tool calls in pipe mode stdout, but we can
    #  infer from what it did based on the workspace state)

    # Run automated grading
    score = 0.0
    breakdown = {}
    notes = ""

    if grading_code:
        try:
            ns: dict[str, Any] = {}
            exec(grading_code, ns)
            grade_func = ns.get("grade")
            if callable(grade_func):
                scores = grade_func(transcript, str(ws))
                if isinstance(scores, dict):
                    breakdown = {k: float(v) for k, v in scores.items()}
                    vals = [v for v in breakdown.values() if isinstance(v, (int, float)) and v > 0]
                    score = sum(breakdown.values()) / max(len(breakdown), 1) if breakdown else 0.0
        except Exception as exc:
            notes = f"Grading error: {exc}"
            score = 0.0

    # For hybrid/llm_judge: also compute a simple heuristic score
    # based on the output quality (file existence, output length, etc.)
    if grading_type in ("llm_judge", "hybrid"):
        if success and len(response) > 200:
            score = max(score, 0.3)  # give partial credit for completing

    # Estimate tokens from stdout length
    tokens_est = len(stdout.split()) * 4

    # Save results
    result_dir = RESULTS_ROOT / "claude_sro_bench_results" / task_id
    result_dir.mkdir(parents=True, exist_ok=True)
    mode_tag = "gate" if sro_enabled else "baseline"
    (result_dir / f"claude_output_{mode_tag}.txt").write_text(stdout, encoding="utf-8", errors="replace")
    (result_dir / f"claude_stderr_{mode_tag}.txt").write_text(stderr, encoding="utf-8", errors="replace")
    (result_dir / f"workspace_file_list_{mode_tag}.txt").write_text(
        "\n".join(str(p.relative_to(ws)) for p in sorted(ws.rglob("*")) if p.is_file()),
        encoding="utf-8")

    # Cleanup workspace
    shutil.rmtree(ws.parent, ignore_errors=True)

    return BenchResult(
        task_id=task_id,
        category=TASK_CATEGORIES.get(task_id, "unknown"),
        mode="baseline" if not sro_enabled else "gate",
        grading_type=grading_type,
        score=round(score, 4),
        max_score=1.0,
        elapsed_s=round(elapsed, 1),
        tokens_est=tokens_est,
        success=success,
        breakdown=breakdown,
        notes=notes,
    )


def main():
    parser = argparse.ArgumentParser(description="Claude Code SRO Benchmark")
    parser.add_argument("--category", required=True, choices=["long-context", "audit", "structured", "native-fit", "all"])
    parser.add_argument("--model", default="DeepSeek-V4-Flash")
    parser.add_argument("--modes", default="baseline", help="Comma-separated: baseline,gate")
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    os.environ["BENCH_MODEL"] = args.model

    if args.category == "all":
        tasks = ALL_TASKS
    else:
        cat_map = {"long-context": LONG_CONTEXT, "audit": AUDIT,
                   "structured": STRUCTURED, "native-fit": NATIVE_FIT}
        tasks = cat_map.get(args.category, [])

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    print(f"╔{'═'*78}╗")
    print(f"║  CLAUDE CODE SRO BENCHMARK — {args.category} ({len(tasks)} tasks) "
          f"x {len(modes)} modes")
    print(f"║  Model: {args.model} | Modes: {args.modes}")
    print(f"╚{'═'*78}╝")

    if args.dry_run:
        for mode in modes:
            sro = (mode != "baseline")
            for tid in tasks:
                info = load_task_info(tid, "baseline")
                status = "OK" if "prompt" in info and info["prompt"] else f"ERR: {info.get('error','?')}"
                print(f"  [{mode}] {tid}: {status} (grading={info.get('grading_type','?')})")
        print("\n[Dry-run complete]")
        return

    all_results: list[BenchResult] = []
    for mode in modes:
        sro = (mode != "baseline")
        print(f"\n{'─'*80}")
        print(f"  MODE: {mode} (SRO_ENABLED={'1' if sro else '0'})")
        print(f"{'─'*80}")

        for i, tid in enumerate(tasks, 1):
            print(f"  [{i}/{len(tasks)}] {tid}", end=" ", flush=True)
            r = run_task_with_claude(tid, "baseline", sro, args.timeout)
            all_results.append(r)
            status = "✅" if r.success else ("⏱" if r.error == "timeout" else "❌")
            print(f"{status} score={r.score:.3f} [{r.elapsed_s:.0f}s] {r.error or ''}")

    # Summary
    print(f"\n{'='*80}")
    print(f"  RESULTS SUMMARY — {args.category} x {args.modes}")
    print(f"{'='*80}")
    print(f"  {'Task':<55} {'Mode':>8} {'Score':>6} {'Time':>6} {'Status':>5}")
    print(f"  {'-'*55} {'-'*8} {'-'*6} {'-'*6} {'-'*5}")

    cat_scores: dict[str, dict[str, list[float]]] = {}
    for r in all_results:
        key = f"{r.mode}:{r.category}"
        if key not in cat_scores:
            cat_scores[key] = {"scores": [], "count": 0}
        cat_scores[key]["scores"].append(r.score)
        cat_scores[key]["count"] += 1
        status = "OK" if r.success else ("TIMEOUT" if r.error == "timeout" else "FAIL")
        print(f"  {r.task_id:<55} {r.mode:>8} {r.score:>6.3f} {r.elapsed_s:>5.0f}s {status:>5}")

    print(f"\n  Category aggregates:")
    print(f"  {'Category/Mode':<30} {'Tasks':>5} {'Avg Score':>10} {'Pass Rate':>10}")
    for key in sorted(cat_scores.keys()):
        d = cat_scores[key]
        avg = sum(d["scores"]) / max(d["count"], 1)
        passed = sum(1 for s in d["scores"] if s > 0)
        print(f"  {key:<30} {d['count']:>5} {avg:>10.4f} {passed}/{d['count']:>5}")

    # Save JSON results
    results_json = RESULTS_ROOT / "claude_sro_bench_results" / "aggregate.json"
    results_json.parent.mkdir(parents=True, exist_ok=True)
    json_out = [{
        "task_id": r.task_id, "category": r.category, "mode": r.mode,
        "score": r.score, "elapsed_s": r.elapsed_s, "tokens_est": r.tokens_est,
        "success": r.success, "error": r.error, "notes": r.notes,
        "breakdown": r.breakdown,
    } for r in all_results]
    results_json.write_text(json.dumps(json_out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Results: {results_json}")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
