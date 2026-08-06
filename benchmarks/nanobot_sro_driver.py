#!/usr/bin/env python3
"""NanoBot SRO 14-task driver: AgentRunner + SparseReadHook (native integration).

Runs each task through the official AgentRunner with the NanoBot tool registry
and, in gate mode, the SparseReadHook.  Grading uses the task's embedded
``grade(transcript, workspace)`` function (same source as lib_grading's
automated path); hybrid tasks get the same partial-credit floor used by the
Claude Code runner.

Usage:
  API_KEY=$DEEPSEEK_API_KEY BENCH_MODEL=DeepSeek-V4-Flash \
    python3 benchmarks/nanobot_sro_driver.py --modes baseline,gate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BASELINE_DIR = REPO / "benchmarks" / "qwenclawbench" / "baseline"
RESULTS_ROOT = REPO / "benchmarks" / "qwenclawbench"
RUNSET = "nanobot_hook_flash_unified14_20260806"

UNIFIED14 = [
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_00036_find_largest_file_in_downloads_directory",
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
    "task_00058_did_regression_on_simulated_panel_data",
    "task_00059_user_discount_calculator",
    "task_00067_write_sparql_query_for_product_reviews_containing_iphone",
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    "task_00086_command_prefix_security_analysis",
    "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site",
    "task_00098_diagnose_scheduled_book_recommendation_failure",
    "task_21_openclaw_comprehension",
    "task_loogle_shortdep_fall_of_outremer",
    "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_loogle_shortdep_fall_of_outremer_5q",
]


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
                    key, value = line.split(":", 1)
                    frontmatter[key.strip()] = value.strip()
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


def build_workspace(info: dict[str, Any]) -> Path:
    ws = Path(tempfile.mkdtemp(prefix=f"nanobot_sro_{info['task_id']}_"))
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


def build_runtime(ws: Path, *, gate: bool) -> tuple[Any, Any, Any]:
    from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.search import GrepTool
    from nanobot.agent.tools.shell import ExecTool

    from sparseread.config import SparseReadConfig
    from sparseread.wrapper import SparseRead

    from sparseread_nanobot.adapter import NanobotAdapter

    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace=ws))
    registry.register(ListDirTool(workspace=ws))
    registry.register(GrepTool(workspace=ws))
    registry.register(ExecTool(working_dir=str(ws)))
    registry.register(WriteFileTool(workspace=ws))

    runtime = SparseRead(SparseReadConfig(mode="auto", workspace=str(ws)))
    agent = SimpleNamespace(tools=registry, _extra_hooks=[])
    installed = NanobotAdapter().install(agent, runtime)
    hook = agent._extra_hooks[0] if gate else None
    if not gate:
        # Keep the registry identical, but drop the hook so baseline is plain.
        agent._extra_hooks.clear()
    return registry, runtime, hook


def _transcript_for_grade(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role", ""))
        content = msg.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            content = "\n".join(parts)
        text = str(content or "")
        if msg.get("tool_calls"):
            for call in msg["tool_calls"]:
                fn = call.get("function") or {}
                text += f"\n[tool_call:{fn.get('name')} args={fn.get('arguments')}]"
        out.append({"type": "message", "message": {"role": role, "content": text}})
    return out


def grade(info: dict[str, Any], messages: list[dict[str, Any]], ws: Path, *, success: bool) -> tuple[float, dict[str, float], str]:
    if not info["grading_code"]:
        return 0.0, {}, "no grading code"
    namespace: dict[str, Any] = {}
    try:
        exec(info["grading_code"], namespace)  # noqa: S102 - task-embedded grader
        grade_func = namespace.get("grade")
        if not callable(grade_func):
            return 0.0, {}, "missing grade()"
        scores = grade_func(_transcript_for_grade(messages), str(ws))
        if not isinstance(scores, dict):
            return 0.0, {}, "grade returned non-dict"
        breakdown = {k: float(v) for k, v in scores.items() if isinstance(v, (int, float))}
        score = sum(breakdown.values()) / len(breakdown) if breakdown else 0.0
    except Exception as exc:
        return 0.0, {}, f"grading error: {exc}"
    if info["grading_type"] in ("llm_judge", "hybrid") and success:
        score = max(score, 0.3)
    return score, breakdown, ""


async def run_task(task_id: str, *, gate: bool, model: str, timeout_s: int) -> dict[str, Any]:
    from nanobot.agent.runner import AgentRunSpec, AgentRunner
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    info = load_task_info(task_id)
    mode = "gate" if gate else "baseline"
    if "error" in info:
        return {"task_id": task_id, "mode": mode, "error": info["error"]}
    ws = build_workspace(info)
    os.environ["SRO_ENABLED"] = "1" if gate else "0"
    registry, _runtime, hook = build_runtime(ws, gate=gate)
    provider = OpenAICompatProvider(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        api_base=os.environ.get("API_BASE_URL", "https://llmapi.paratera.com/v1"),
        default_model=model,
    )
    spec = AgentRunSpec(
        initial_messages=[{"role": "user", "content": info["prompt"]}],
        tools=registry,
        model=model,
        max_iterations=60,
        max_tool_result_chars=20_000,
        hook=hook,
        workspace=ws,
        session_key=task_id,
        context_window_tokens=200_000,
    )
    runner = AgentRunner(provider)
    started = time.time()
    timed_out = False
    try:
        result = await asyncio.wait_for(runner.run(spec), timeout=timeout_s)
    except (asyncio.TimeoutError, TimeoutError):
        timed_out = True
        result = SimpleNamespace(
            final_content=None,
            messages=[],
            tools_used=[],
            usage={},
            stop_reason="timeout",
        )
    elapsed = time.time() - started
    success = not timed_out and result.stop_reason not in ("error", "timeout")
    score, breakdown, notes = grade(
        info,
        list(result.messages),
        ws,
        success=success,
    )
    usage = getattr(result, "usage", None) or {}
    tokens = int(
        usage.get("total_tokens")
        or usage.get("totalTokens")
        or (int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) + int(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
    )
    return {
        "task_id": task_id,
        "mode": mode,
        "score": round(score, 4),
        "breakdown": breakdown,
        "elapsed_s": round(elapsed, 1),
        "tokens": tokens,
        "success": success,
        "stop_reason": str(getattr(result, "stop_reason", "")),
        "notes": notes,
        "timed_out": timed_out,
    }


async def main_async(args: argparse.Namespace) -> list[dict[str, Any]]:
    tasks = args.task or UNIFIED14
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    results: list[dict[str, Any]] = []
    for mode in modes:
        gate = mode != "baseline"
        for task_id in tasks:
            print(f"[{mode}] {task_id} ...", flush=True)
            result = await run_task(task_id, gate=gate, model=args.model, timeout_s=args.timeout)
            results.append(result)
            status = "OK" if result.get("success") else ("TIMEOUT" if result.get("timed_out") else "FAIL")
            print(
                f"  {task_id} {mode} score={result.get('score', 0):.3f} "
                f"tokens={result.get('tokens', 0)} [{result.get('elapsed_s', 0):.0f}s] {status}",
                flush=True,
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="NanoBot SRO 14-task driver (hook integration)")
    parser.add_argument("--task", action="append", default=[], help="Task id (repeatable); default all 14")
    parser.add_argument("--modes", default="baseline,gate")
    parser.add_argument("--model", default=os.environ.get("BENCH_MODEL", "DeepSeek-V4-Flash"))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    tasks = args.task or UNIFIED14
    if args.dry_run:
        for task_id in tasks:
            info = load_task_info(task_id)
            print(f"  {task_id}: {'OK' if 'prompt' in info and info['prompt'] else info.get('error', '?')}")
        return 0
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("missing DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    results = asyncio.run(main_async(args))
    out_dir = RESULTS_ROOT / RUNSET
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "aggregate.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    for mode in sorted({r["mode"] for r in results}):
        rows = [r for r in results if r["mode"] == mode and r.get("score") is not None]
        if rows:
            avg = sum(r["score"] for r in rows) / len(rows)
            tokens = sum(r.get("tokens", 0) for r in rows)
            print(f"{mode}: n={len(rows)} avg_score={avg:.4f} tokens={tokens}")
    print(f"results: {out_dir / 'aggregate.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
