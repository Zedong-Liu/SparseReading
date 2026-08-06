#!/usr/bin/env python3
"""Claude Code SRO benchmark runner (adapted for the shared-core adapter).

Runs tasks through ``claude --print`` against the Paratera proxy:
- baseline: plain settings, no SRO tools/hooks.
- gate: real SRO integration (MCP server + PreToolUse/PostToolUse session
  hooks) using ``sparseread_claude`` from the dev venv.

Grading uses the task's embedded ``grade()`` function, matching the colleague
report methodology for comparability.

Usage:
  CLAUDE_PROXY_BASE=http://127.0.0.1:18766 python3 \\
    local_agent_comp/run_claude_sro_bench.py \\
    --category long-context --model DeepSeek-V4-Flash --modes baseline
"""

from __future__ import annotations

import argparse
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

REPO = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO / "benchmarks" / "qwenclawbench" / "baseline"
RESULTS_ROOT = REPO / "benchmarks" / "qwenclawbench"
DEV_VENV_PYTHON = REPO / "nanobot-sro-v3" / ".venv" / "bin" / "python"

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

TASK_CATEGORIES: dict[str, str] = {}
for _t in LONG_CONTEXT:
    TASK_CATEGORIES[_t] = "long-context"
for _t in AUDIT:
    TASK_CATEGORIES[_t] = "audit"
for _t in STRUCTURED:
    TASK_CATEGORIES[_t] = "structured"
for _t in NATIVE_FIT:
    TASK_CATEGORIES[_t] = "native-fit"

ALL_TASKS = LONG_CONTEXT + AUDIT + STRUCTURED + NATIVE_FIT


@dataclass
class BenchResult:
    task_id: str = ""
    category: str = ""
    mode: str = ""
    grading_type: str = ""
    score: float = 0.0
    elapsed_s: float = 0.0
    tokens_est: int = 0
    success: bool = False
    error: str = ""
    breakdown: dict[str, float] = field(default_factory=dict)
    notes: str = ""


def load_task_info(task_id: str) -> dict[str, Any]:
    src_dir = BASELINE_DIR / task_id / "runtime"
    if not src_dir.exists():
        return {"error": f"Source not found: {src_dir}"}
    tasks_dir = src_dir / "tasks"
    md_files = list(tasks_dir.glob("*.md")) if tasks_dir.exists() else []
    if not md_files:
        return {"error": f"No task .md in {tasks_dir}"}
    content = md_files[0].read_text(encoding="utf-8", errors="replace")
    frontmatter: dict[str, str] = {}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    frontmatter[k.strip()] = v.strip()
    prompt = ""
    if "## Prompt" in content:
        prompt = content.split("## Prompt", 1)[1]
        if "## Expected" in prompt:
            prompt = prompt.split("## Expected", 1)[0]
    grading_code = ""
    if "```python" in content:
        parts = content.split("```python")
        if len(parts) > 1:
            grading_code = parts[1].split("```", 1)[0]
    return {
        "task_id": task_id,
        "frontmatter": frontmatter,
        "prompt": prompt.strip(),
        "grading_type": frontmatter.get("grading_type", "automated"),
        "grading_code": grading_code,
        "source_dir": str(src_dir),
    }


def _build_workspace(info: dict[str, Any]) -> Path:
    ws = Path(tempfile.mkdtemp(prefix=f"claude_sro_{info['task_id']}_"))
    assets_dir = Path(info["source_dir"]) / "assets"
    if assets_dir.exists():
        for item in assets_dir.iterdir():
            dest = ws / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=False)
            else:
                shutil.copy2(item, dest)
    tasks_dir = Path(info["source_dir"]) / "tasks"
    if tasks_dir.exists():
        md_files = list(tasks_dir.glob("*.md"))
        if md_files:
            shutil.copy2(md_files[0], ws / "task.md")
    return ws


def _gate_settings(ws: Path) -> Path:
    settings = ws / "gate_settings.json"
    hook_entry = {
        "type": "session",
        "command": str(DEV_VENV_PYTHON),
        "args": ["-m", "sparseread_claude.hook", "--workspace", str(ws)],
    }
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"matcher": "Read|Bash", "hooks": [hook_entry]}],
                    "PostToolUse": [{"matcher": "Read|Bash", "hooks": [hook_entry]}],
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return settings


def _gate_mcp_config(ws: Path) -> Path:
    config = ws / "gate_mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "sparseread": {
                        "command": str(DEV_VENV_PYTHON),
                        "args": [
                            "-m",
                            "sparseread_claude.claude_mcp",
                            "--workspace",
                            str(ws),
                            "--mode",
                            "auto",
                        ],
                        "env": {"SRO_ENABLED": "1"},
                    }
                }
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return config


def run_task_with_claude(
    task_id: str,
    *,
    sro_enabled: bool,
    timeout_s: int,
) -> BenchResult:
    info = load_task_info(task_id)
    mode = "gate" if sro_enabled else "baseline"
    if "error" in info:
        return BenchResult(task_id=task_id, mode=mode, error=info["error"])
    if not info["prompt"]:
        return BenchResult(task_id=task_id, mode=mode, error="Empty prompt")

    ws = _build_workspace(info)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    child_env["CLAUDE_CONFIG_DIR"] = str(ws / "claude-config")
    child_env["ANTHROPIC_BASE_URL"] = os.environ.get(
        "CLAUDE_PROXY_BASE", "http://127.0.0.1:18766"
    )
    child_env["ANTHROPIC_AUTH_TOKEN"] = "proxy-key"
    child_env["ANTHROPIC_MODEL"] = os.environ.get("BENCH_MODEL", "DeepSeek-V4-Flash")
    child_env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = child_env["ANTHROPIC_MODEL"]

    cmd = [
        "claude",
        "--print",
        info["prompt"],
        "--model",
        child_env["ANTHROPIC_MODEL"],
        "--max-turns",
        "20",
        "--dangerously-skip-permissions",
        "--add-dir",
        str(ws),
        "--output-format",
        "text",
    ]
    if sro_enabled:
        cmd += ["--settings", str(_gate_settings(ws)), "--mcp-config", str(_gate_mcp_config(ws))]
    else:
        cmd += ["--settings", str(ws / "plain_settings.json")]
        (ws / "plain_settings.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    print(f"  [{task_id}] {mode} claude -p (timeout={timeout_s}s)...", file=sys.stderr, flush=True)
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            cwd=str(ws),
            env=child_env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return BenchResult(
            task_id=task_id,
            category=TASK_CATEGORIES.get(task_id, "unknown"),
            mode=mode,
            grading_type=info["grading_type"],
            success=False,
            error="timeout",
            elapsed_s=round(time.time() - start, 1),
        )
    elapsed = time.time() - start
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
    success = proc.returncode == 0 and len(stdout) > 60 and "Connection closed" not in stdout[:200]

    transcript = [
        {
            "type": "message",
            "message": {"role": "user", "content": [{"type": "text", "text": info["prompt"]}]},
        }
    ]
    assistant_lines = [
        line.strip()
        for line in stdout.replace("\r", "").split("\n")
        if line.strip() and not line.strip().startswith("⏺") and not line.strip().startswith("⎿")
    ]
    response = "\n".join(assistant_lines)
    if response:
        transcript.append(
            {
                "type": "message",
                "message": {"role": "assistant", "content": [{"type": "text", "text": response}]},
            }
        )

    score = 0.0
    breakdown: dict[str, float] = {}
    notes = ""
    if info["grading_code"]:
        try:
            ns: dict[str, Any] = {}
            exec(info["grading_code"], ns)  # noqa: S102 - task-embedded grader
            grade_func = ns.get("grade")
            if callable(grade_func):
                scores = grade_func(transcript, str(ws))
                if isinstance(scores, dict):
                    breakdown = {k: float(v) for k, v in scores.items() if isinstance(v, (int, float))}
                    score = (
                        sum(breakdown.values()) / len(breakdown)
                        if breakdown
                        else 0.0
                    )
        except Exception as exc:
            notes = f"Grading error: {exc}"
    if info["grading_type"] in ("llm_judge", "hybrid") and success and len(response) > 200:
        score = max(score, 0.3)

    result_dir = RESULTS_ROOT / "claude_sro_bench_results" / task_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / f"claude_output_{mode}.txt").write_text(stdout, encoding="utf-8", errors="replace")
    (result_dir / f"claude_stderr_{mode}.txt").write_text(stderr, encoding="utf-8", errors="replace")
    (result_dir / f"workspace_file_list_{mode}.txt").write_text(
        "\n".join(str(p.relative_to(ws)) for p in sorted(ws.rglob("*")) if p.is_file()),
        encoding="utf-8",
    )
    shutil.rmtree(ws, ignore_errors=True)

    return BenchResult(
        task_id=task_id,
        category=TASK_CATEGORIES.get(task_id, "unknown"),
        mode=mode,
        grading_type=info["grading_type"],
        score=round(score, 4),
        elapsed_s=round(elapsed, 1),
        tokens_est=len(stdout.split()) * 4,
        success=success,
        breakdown=breakdown,
        notes=notes,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code SRO Benchmark")
    parser.add_argument(
        "--category",
        choices=["long-context", "audit", "structured", "native-fit", "all"],
        default="all",
    )
    parser.add_argument("--model", default="DeepSeek-V4-Flash")
    parser.add_argument("--modes", default="baseline")
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Run a single task id (repeatable); overrides --category.",
    )
    args = parser.parse_args()
    os.environ["BENCH_MODEL"] = args.model

    if args.task:
        tasks = args.task
    else:
        tasks = ALL_TASKS if args.category == "all" else {
            "long-context": LONG_CONTEXT,
            "audit": AUDIT,
            "structured": STRUCTURED,
            "native-fit": NATIVE_FIT,
        }[args.category]
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    if args.dry_run:
        for mode in modes:
            for tid in tasks:
                info = load_task_info(tid)
                status = "OK" if "prompt" in info and info["prompt"] else f"ERR: {info.get('error', '?')}"
                print(f"  [{mode}] {tid}: {status}")
        return 0

    results: list[BenchResult] = []
    for mode in modes:
        sro = mode != "baseline"
        print(f"\n  MODE: {mode} (SRO={'on' if sro else 'off'})", flush=True)
        for tid in tasks:
            result = run_task_with_claude(tid, sro_enabled=sro, timeout_s=args.timeout)
            results.append(result)
            status = "✅" if result.success else ("⏱" if result.error == "timeout" else "❌")
            print(
                f"  [{result.category}] {tid} {status} score={result.score:.3f} "
                f"[{result.elapsed_s:.0f}s] {result.error or ''}",
                flush=True,
            )

    print("\n" + "=" * 80)
    print(f"  RESULTS — {args.category} x {args.modes}")
    print("=" * 80)
    aggregates: dict[str, list[float]] = {}
    for r in results:
        key = f"{r.mode}:{r.category}"
        aggregates.setdefault(key, []).append(r.score)
        print(
            f"  {r.task_id:<58} {r.mode:>6} {r.score:>6.3f} "
            f"{r.elapsed_s:>5.0f}s {'OK' if r.success else r.error or 'FAIL'}"
        )
    print()
    for key in sorted(aggregates):
        scores = aggregates[key]
        print(f"  {key:<30} n={len(scores):>2} avg={sum(scores)/len(scores):.4f}")

    out = RESULTS_ROOT / "claude_sro_bench_results" / "aggregate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if out.exists():
        try:
            loaded = json.loads(out.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except json.JSONDecodeError:
            existing = []
    merged = {
        (item.get("task_id"), item.get("mode")): item
        for item in existing
        if isinstance(item, dict)
    }
    for r in results:
        merged[(r.task_id, r.mode)] = {
            "task_id": r.task_id,
            "category": r.category,
            "mode": r.mode,
            "score": r.score,
            "elapsed_s": r.elapsed_s,
            "tokens_est": r.tokens_est,
            "success": r.success,
            "error": r.error,
            "notes": r.notes,
            "breakdown": r.breakdown,
        }
    out.write_text(
        json.dumps(list(merged.values()), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n  Results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
