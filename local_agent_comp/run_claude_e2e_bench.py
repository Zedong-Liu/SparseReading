#!/usr/bin/env python3
"""
Claude Code End-to-End Benchmark — runs tasks through claude -p.

Captures real Claude Code task execution (baseline mode: no SRO MCP, no hooks).
Compares against Claude Bridge SRO results and reference experimental baselines.

Usage:
  python local_agent_comp/run_claude_e2e_bench.py
"""

import json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path
from dataclasses import dataclass

# Force UTF-8
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO / "SRO_test" / "qwenclawbench" / "baseline"
BRIDGE_RESULTS = REPO / "SRO_test" / "qwenclawbench" / "claude_bridge_17task_results.json"
OUTPUT_DIR = REPO / "SRO_test" / "qwenclawbench" / "claude_e2e_baseline"

# Representative tasks: one from each category
TASKS = [
    ("task_21_openclaw_comprehension", "long-context"),
    ("task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check", "audit"),
    ("task_00058_did_regression_on_simulated_panel_data", "structured"),
    ("task_00036_find_largest_file_in_downloads_directory", "native-fit"),
]


@dataclass
class E2EResult:
    task_id: str
    category: str
    exit_code: int
    elapsed_s: float
    output_bytes: int
    success: bool
    output_file: str  # path to saved output


def run_one(task_id: str, category: str, timeout_s: int = 360) -> E2EResult:
    src = BASELINE_DIR / task_id / "runtime"
    if not src.exists():
        return E2EResult(task_id, category, -99, 0, 0, False, "")

    tmp = Path(tempfile.mkdtemp(prefix=f"e2e_{task_id}_"))
    workspace = tmp / "workspace"
    shutil.copytree(src, workspace, symlinks=False)

    # Read task prompt
    tasks_dir = workspace / "tasks"
    md_files = list(tasks_dir.glob("*.md"))
    if not md_files:
        shutil.rmtree(tmp, ignore_errors=True)
        return E2EResult(task_id, category, -98, 0, 0, False, "")
    content = md_files[0].read_text(encoding="utf-8")
    prompt = content.split("## Prompt")[-1].split("## Expected")[0].strip()

    # Settings: no hooks = baseline mode
    (tmp / "baseline_settings.json").write_text(
        json.dumps({"hooks": {}}), encoding="utf-8")

    start = time.time()
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt,
             "--max-turns", "12",
             "--dangerously-skip-permissions",
             "--add-dir", str(workspace),
             "--settings", str(tmp / "baseline_settings.json"),
             "--output-format", "text"],
            capture_output=True,
            timeout=timeout_s,
            cwd=str(workspace),
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(tmp, ignore_errors=True)
        return E2EResult(task_id, category, -1, timeout_s, 0, False, "")

    elapsed = time.time() - start
    stdout_b = proc.stdout if proc.stdout else b""
    stderr_b = proc.stderr if proc.stderr else b""

    # Save output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result_dir = OUTPUT_DIR / task_id
    result_dir.mkdir(exist_ok=True)
    output_file = result_dir / "claude_output.txt"
    output_file.write_bytes(stdout_b)
    (result_dir / "claude_stderr.txt").write_bytes(stderr_b)
    (result_dir / "manifest.json").write_text(json.dumps({
        "task_id": task_id, "category": category,
        "elapsed_s": round(elapsed, 1), "exit_code": proc.returncode,
        "output_bytes": len(stdout_b),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Check workspace for generated output files
    generated_files = []
    for p in workspace.rglob("*"):
        if p.is_file() and p.parent != tasks_dir and p.parent != md_files[0].parent:
            if "scripts" not in str(p) and "__pycache__" not in str(p):
                generated_files.append(str(p.relative_to(workspace)))

    success = proc.returncode == 0 and len(stdout_b) > 50

    shutil.rmtree(tmp, ignore_errors=True)
    return E2EResult(task_id, category, proc.returncode, round(elapsed, 1),
                     len(stdout_b), success, str(output_file))


def load_bridge_results():
    if BRIDGE_RESULTS.exists():
        return json.loads(BRIDGE_RESULTS.read_text(encoding="utf-8"))
    return {}


def main():
    print("=" * 95)
    print("  CLAUDE CODE END-TO-END BASELINE BENCHMARK")
    print("  Mode: claude -p (native reads, no SRO, no hooks)")
    print(f"  Tasks: {len(TASKS)} | Model: DeepSeek-V4-Flash")
    print("=" * 95)

    results = {}
    for i, (tid, cat) in enumerate(TASKS, 1):
        print(f"\n[{i}/{len(TASKS)}] {tid} ...", end=" ", flush=True)
        r = run_one(tid, cat)
        results[tid] = r
        status = "OK" if r.success else f"FAIL(rc={r.exit_code})"
        print(f"{status} [{r.elapsed_s:.0f}s] [{r.output_bytes}B]")

    # Summary
    print(f"\n{'=' * 95}")
    print("  BASELINE E2E RESULTS")
    print(f"{'=' * 95}")
    ok = sum(1 for r in results.values() if r.success)
    print(f"  Completed: {ok}/{len(results)}")
    print(f"  {'Task':<50} {'Status':>6} {'Time':>7} {'Output':>8}")
    print(f"  {'-'*50} {'-'*6} {'-'*7} {'-'*8}")
    for tid, r in results.items():
        status = "OK" if r.success else "FAIL"
        print(f"  {tid:<50} {status:>6} {r.elapsed_s:>5.0f}s {r.output_bytes:>7,}B")

    # Compare with SRO Bridge results
    bridge = load_bridge_results()
    if bridge:
        print(f"\n{'=' * 95}")
        print("  BASELINE (claude -p) vs SRO (Claude Bridge) COMPARISON")
        print(f"{'=' * 95}")
        print(f"  {'Task':<50} {'BL OK':>5} {'BL Time':>7} {'SRO Gate':>9} {'SRO Saved':>9} {'SRO Rate':>7}")
        print(f"  {'-'*50} {'-'*5} {'-'*7} {'-'*9} {'-'*9} {'-'*7}")
        brt = bridge.get("tasks", {})
        for tid, r in results.items():
            bt = brt.get(tid, {})
            gm = bt.get("gate_mode", "?")
            sv = bt.get("tokens_saved", 0)
            sr = bt.get("savings_ratio", 0)
            print(f"  {tid:<50} {'OK' if r.success else 'FAIL':>5} {r.elapsed_s:>5.0f}s {gm:>9} {sv:>9,} {sr:>6.1%}")

    # Reference comparison
    print(f"\n{'=' * 95}")
    print("  ALIGNMENT WITH REFERENCE EXPERIMENTAL DATA (DeepSeek-V4-Flash)")
    print(f"{'=' * 95}")
    print(f"  Reference (OpenClaw, full agent sessions):")
    print(f"    Baseline Score:  0.779 (avg over 14 tasks)")
    print(f"    Gate/SRO Score:  0.888 (avg over 14 tasks)")
    print(f"    Score Gain:      +14.0%")
    print(f"    Token Savings:   45.2% (6.58M -> 3.61M)")
    print(f"")
    print(f"  Claude Bridge (file-reading portion only):")
    if bridge:
        cum = bridge.get("cumulative", {})
        print(f"    Token Savings:   {cum.get('savings_ratio', 0):.1%} ({cum.get('native_tokens_est', 0):,} -> {cum.get('sro_tokens', 0):,})")
    print(f"")
    print(f"  Claude Code Baseline (claude -p, native reads):")
    print(f"    Task Completion:  {ok}/{len(results)} tasks completed successfully")
    print(f"    Note: claude -p runs baseline (no SRO). Claude Bridge measures")
    print(f"    the file-reading savings SRO would provide at the gate level.")
    print(f"{'=' * 95}")
    print(f"  Results saved to: {OUTPUT_DIR}")
    print(f"{'=' * 95}")


if __name__ == "__main__":
    main()
