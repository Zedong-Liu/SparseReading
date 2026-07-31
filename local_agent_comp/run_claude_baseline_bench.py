#!/usr/bin/env python3
"""
Claude Code Baseline Benchmark — real claude -p end-to-end task execution.

Runs representative benchmark tasks through claude -p (no SRO MCP, no hooks)
to establish baseline completion quality. Compares Claude Bridge SRO results
against reference experimental baselines.

Usage:
  cd C:/Users/xule/Desktop/SparseReading
  python local_agent_comp/run_claude_baseline_bench.py --tasks all --timeout 300
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ──
REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO_ROOT / "SRO_test" / "qwenclawbench" / "baseline"
SRO_V3_DIR = REPO_ROOT / "SRO_test" / "qwenclawbench" / "sro_v3"
CLAUDE_BIN = "claude"
CLAUDE_RESULTS_JSON = REPO_ROOT / "SRO_test" / "qwenclawbench" / "claude_bridge_17task_results.json"

# ── Task selection ──
REPRESENTATIVE_TASKS = {
    "long-context": "task_21_openclaw_comprehension",
    "audit": "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "structured": "task_00058_did_regression_on_simulated_panel_data",
    "native-fit": "task_00036_find_largest_file_in_downloads_directory",
}

ALL_TASK_NAMES = {
    "task_loogle_shortdep_fall_of_outremer",
    "task_loogle_shortdep_fall_of_outremer_5q",
    "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_21_openclaw_comprehension",
    "task_workspacebench_lite_334_kaima_rd",
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
    "task_00086_command_prefix_security_analysis",
    "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site",
    "task_00098_diagnose_scheduled_book_recommendation_failure",
    "task_00058_did_regression_on_simulated_panel_data",
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    "task_spreadsheetbench_verified_49333_trimmed_vlookup",
    "task_spreadsheetbench_verified_11276_weekday_row_fix",
    "task_00036_find_largest_file_in_downloads_directory",
    "task_00059_user_discount_calculator",
    "task_00067_write_sparql_query_for_product_reviews_containing_iphone",
}


@dataclass
class RunResult:
    task_id: str
    category: str
    exit_code: int
    elapsed_seconds: float
    stdout_len: int
    stderr_len: int
    success: bool
    output_snippet: str = ""


def prepare_workspace(task_id: str) -> Path:
    """Copy task runtime to a temp dir and return the workspace path."""
    src = BASELINE_DIR / task_id / "runtime"
    if not src.exists():
        raise FileNotFoundError(f"Task runtime not found: {src}")
    tmp = Path(tempfile.mkdtemp(prefix=f"claude_bench_{task_id}_"))
    dst = tmp / "workspace"
    shutil.copytree(src, dst, symlinks=False)
    return dst


def run_claude_task(task_id: str, category: str, workspace: Path,
                    timeout_seconds: int = 300) -> RunResult:
    """Run a single task through claude -p."""
    task_file = workspace / "tasks" / f"{task_id}.md"
    if not task_file.exists():
        # Try finding any .md in tasks/
        tasks_dir = workspace / "tasks"
        md_files = list(tasks_dir.glob("*.md"))
        if md_files:
            task_file = md_files[0]
        else:
            return RunResult(task_id, category, -1, 0, 0, 0, False,
                             "No task .md file found")

    task_content = task_file.read_text(encoding="utf-8", errors="replace")

    # Extract just the Prompt section
    prompt = task_content
    if "## Prompt" in task_content:
        prompt = task_content.split("## Prompt")[1]
        if "## Expected" in prompt:
            prompt = prompt.split("## Expected")[0]
    prompt = prompt.strip()

    if not prompt:
        return RunResult(task_id, category, -1, 0, 0, 0, False, "Empty prompt")

    # Build claude command - use a temporory settings without hooks for baseline
    # This ensures claude can read files normally (baseline = no SRO)
    tmp_settings = workspace.parent / "settings.json"
    tmp_settings.write_text(json.dumps({
        "hooks": {}  # No hooks = baseline mode
    }), encoding="utf-8")

    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--max-turns", "15",
        "--dangerously-skip-permissions",
        "--add-dir", str(workspace),
        "--settings", str(tmp_settings),
        "--output-format", "text",
    ]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=timeout_seconds,
            cwd=str(workspace),
            env={**os.environ,
                 "PYTHONIOENCODING": "utf-8",
                 "CLAUDE_CODE_SIMPLE": "1"},
        )
        elapsed = time.time() - start
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        # Extract meaningful output (skip system messages)
        output_snippet = stdout[-2000:] if len(stdout) > 2000 else stdout
        output_snippet = output_snippet.replace("\r", "")

        return RunResult(
            task_id=task_id,
            category=category,
            exit_code=proc.returncode,
            elapsed_seconds=round(elapsed, 1),
            stdout_len=len(stdout),
            stderr_len=len(stderr),
            success=(proc.returncode == 0 and len(stdout) > 100),
            output_snippet=output_snippet,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        return RunResult(task_id, category, -1, round(elapsed, 1), 0, 0, False,
                         "TIMEOUT")
    except FileNotFoundError:
        return RunResult(task_id, category, -1, 0, 0, 0, False,
                         "claude CLI not found")


def load_prior_claude_bridge_results() -> dict:
    """Load results from the previously run Claude Bridge 17-task benchmark."""
    if CLAUDE_RESULTS_JSON.exists():
        return json.loads(CLAUDE_RESULTS_JSON.read_text(encoding="utf-8"))
    return {}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Claude Code Baseline Benchmark")
    parser.add_argument("--tasks", default="representative",
                        choices=["representative", "all"],
                        help="Which tasks to run")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Timeout per task in seconds")
    args = parser.parse_args()

    if args.tasks == "all":
        tasks_to_run = [(tid, "various") for tid in sorted(ALL_TASK_NAMES)]
    else:
        tasks_to_run = [(tid, cat) for cat, tid in REPRESENTATIVE_TASKS.items()]

    print("=" * 90)
    print("  CLAUDE CODE BASELINE BENCHMARK (no SRO, no hooks)")
    print(f"  Tasks: {len(tasks_to_run)} | Timeout: {args.timeout}s per task")
    print("=" * 90)

    results: list[RunResult] = []
    for i, (task_id, category) in enumerate(tasks_to_run, 1):
        print(f"\n[{i}/{len(tasks_to_run)}] {task_id} ({category})")
        try:
            workspace = prepare_workspace(task_id)
            print(f"  Workspace: {workspace}")
            print(f"  Running claude -p ...", end=" ", flush=True)
            result = run_claude_task(task_id, category, workspace, args.timeout)
            results.append(result)
            status = "OK" if result.success else f"FAIL (rc={result.exit_code})"
            print(f"{status} [{result.elapsed_seconds}s] [{result.stdout_len}B]")
            if result.output_snippet:
                preview = result.output_snippet[:300].replace("\n", " ")
                print(f"  Output: {preview}...")
        except Exception as exc:
            print(f"  ERROR: {exc}")
            results.append(RunResult(task_id, category, -1, 0, 0, 0, False, str(exc)))
        finally:
            # Clean up temp workspace
            if 'workspace' in dir() and workspace.parent.exists():
                try:
                    shutil.rmtree(workspace.parent, ignore_errors=True)
                except Exception:
                    pass

    # ── Summary ──
    print(f"\n{'=' * 90}")
    print("  BASELINE RESULTS SUMMARY")
    print(f"{'=' * 90}")
    successful = sum(1 for r in results if r.success)
    total_time = sum(r.elapsed_seconds for r in results)
    print(f"  Completed: {successful}/{len(results)}")
    print(f"  Total time: {total_time:.1f}s")
    for r in results:
        status = "PASS" if r.success else "FAIL"
        print(f"  {status:5s} | {r.task_id[:45]:45s} | {r.category:15s} | {r.elapsed_seconds:>6.1f}s | {r.stdout_len:>8d}B")

    # ── Load SRO results for comparison ──
    sro_results = load_prior_claude_bridge_results()
    if sro_results:
        print(f"\n{'=' * 90}")
        print("  COMPARISON: Baseline vs Claude Bridge SRO")
        print(f"{'=' * 90}")
        sro_tasks = sro_results.get("tasks", {})
        for r in results:
            sro = sro_tasks.get(r.task_id, {})
            if sro:
                print(f"  {r.task_id[:50]:50s} | Baseline: {'OK' if r.success else 'FAIL'} | "
                      f"SRO Gate: {sro.get('gate_mode', '?'):10s} | "
                      f"SRO Savings: {sro.get('savings_ratio', 0):.1%}")

    # ── Reference comparison ──
    if sro_results:
        print(f"\n{'=' * 90}")
        print("  ALIGNMENT WITH REFERENCE BASELINES (DeepSeek-V4-Flash)")
        print(f"{'=' * 90}")
        cum = sro_results.get("cumulative", {})
        print(f"  Claude Bridge SRO Total Savings:   {cum.get('savings_ratio', 0):.1%}")
        print(f"  Reference (OpenClaw) SRO Savings:   45.2% (6.58M → 3.61M tokens)")
        print(f"  Reference Score Gain (SRO vs BL):   +14.0% (0.779 → 0.888)")
        print(f"  Note: Claude Bridge measures file-reading only;")
        print(f"  reference measures full agent sessions (system+reasoning+tools+judge)")

    print(f"\n{'=' * 90}")
    print("  DONE")
    print(f"{'=' * 90}")


if __name__ == "__main__":
    main()
