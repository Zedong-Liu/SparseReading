"""Full end-to-end benchmark — simulates COMPLETE task execution.

For each QwenClawBench task:
  Native mode: read task.md + read ALL asset files natively → total tokens
  SRO mode:    read task.md + sro_preview + sro_read(collect) → total tokens

Both modes include the fixed cost of reading the task description.
The comparison reflects the total file-reading cost of the entire session.
"""

from __future__ import annotations

import io, json, sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sparseread.bridge.claude import ClaudeBridge
from sparseread.token_tracker import estimate_file_tokens, DEFAULT_CONTEXT_WINDOW

REPO = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO / "SRO_test" / "qwenclawbench" / "baseline"

TASK_DIRS = {
    "task_00012":    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_21":       "task_21_openclaw_comprehension",
    "task_loogle_3q": "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_loogle_5q": "task_loogle_shortdep_fall_of_outremer_5q",
}

TASK_ASSET = {
    "task_00012":    "a_stock_announcements",
    "task_21":       "OpenClaw Agent Use Cases and Gap Analysis for PinchBench.pdf",
    "task_loogle_3q": "document.txt",
    "task_loogle_5q": "document.txt",
}

SLOTS = {
    "task_00012": [
        {"id": "state_vs_output", "question": "35 seen_ids vs 11 output records and orphaned IDs"},
        {"id": "dedup_bug", "question": "deduplicate list(seen)[-5000:] ordering bug and fix"},
        {"id": "important", "question": "exactly five important announcements with IDs and companies"},
        {"id": "config", "question": "max_pages, fetch_sse, request_delay, category, notifications"},
    ],
    "task_21": [
        {"id": "q1", "question": "How many community-built skills before filtering?"},
        {"id": "q2", "question": "How many skills remained after filtering?"},
        {"id": "q3", "question": "Largest skill category and count"},
        {"id": "q4", "question": "Second-largest skill category and count"},
        {"id": "q5", "question": "File name that defines an OpenClaw skill"},
        {"id": "q6", "question": "Type of API exposed by the OpenClaw gateway"},
        {"id": "q7", "question": "Skills registry data collection date"},
        {"id": "q8", "question": "How many new benchmark tasks the paper proposes"},
    ],
    "task_loogle_3q": [
        {"id": "q1f", "question": "Follow-up question 1 from document"},
        {"id": "q2f", "question": "Follow-up question 2 from document"},
        {"id": "q3f", "question": "Follow-up question 3 from document"},
    ],
    "task_loogle_5q": [
        {"id": "q1", "question": "Question 1 from document"},
        {"id": "q2", "question": "Question 2 from document"},
        {"id": "q3", "question": "Question 3 from document"},
        {"id": "q4", "question": "Question 4 from document"},
        {"id": "q5", "question": "Question 5 from document"},
    ],
}

# Experimental baselines from figures/sro_experiment_data.csv (DeepSeek-V4-Flash)
BASELINES = {
    "task_00012":    {"native": 234_370, "sro": 89_103,  "pct": 62.0, "score": 1.0},
    "task_21":       {"native": 466_144, "sro": 99_113,  "pct": 78.8, "score": 1.0},
    "task_loogle_3q":{"native": 248_460, "sro": 65_147,  "pct": 73.8, "score": 1.0},
    "task_loogle_5q":{"native": 263_004, "sro": 45_997,  "pct": 82.5, "score": 1.0},
}

# DeepSeek-V4-Pro baselines (stronger model, higher savings)
BASELINES_PRO = {
    "task_00012":    {"native": 253_685, "sro": 54_246,  "pct": 78.6, "score": 0.970},
    "task_21":       {"native": 714_716, "sro": 49_919,  "pct": 93.0, "score": 1.0},
}


def _token_cost(text: str) -> int:
    """API ground-truth token count for a string."""
    return estimate_file_tokens(len(text.encode("utf-8")), "txt")


def run_full_task(task_id: str) -> dict:
    """Run COMPLETE task simulation in both native and SRO modes.

    Returns total token costs for each mode, with the task description
    included as fixed overhead.
    """
    task_dir = BASELINE_DIR / TASK_DIRS[task_id]
    assets = task_dir / "runtime" / "assets"
    task_md = task_dir / "runtime" / "tasks" / f"{task_id}.md"
    asset_name = TASK_ASSET[task_id]
    asset_path = assets / asset_name

    # ---- Read task description (common overhead) ----
    task_desc = task_md.read_text(encoding="utf-8", errors="replace") if task_md.exists() else ""
    task_tokens = _token_cost(task_desc)  # heuristic, same in both modes

    # ---- Compute native-mode cost: task + all asset content ----
    if asset_path.is_dir():
        native_file_tokens = 0
        for f in sorted(asset_path.rglob("*")):
            if f.is_file():
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    native_file_tokens += _token_cost(content)
                except Exception:
                    native_file_tokens += estimate_file_tokens(f.stat().st_size, f.suffix)
    else:
        try:
            content = asset_path.read_text(encoding="utf-8", errors="replace")
            native_file_tokens = _token_cost(content)
        except Exception:
            native_file_tokens = estimate_file_tokens(asset_path.stat().st_size, asset_path.suffix)

    native_total = task_tokens + native_file_tokens

    # ---- Run SRO mode: task desc + sro_preview + sro_read(collect) ----
    bridge = ClaudeBridge(workspace=assets.parent, mode="auto")

    # sro_preview
    preview = bridge.handle({"method": "preview", "params": {"path": str(asset_path)}})
    pack = preview.get("preview_pack", {})
    artifact_id = pack.get("artifact_id", "")

    # sro_read
    sro_read_tokens = 0
    sro_read_result = {}
    if artifact_id:
        slots = SLOTS.get(task_id, [{"id": "general", "question": "extract evidence"}])
        asset_type = pack.get("card", {}).get("type", "text")
        try:
            sro_read_result = bridge.handle({
                "method": "read",
                "params": {
                    "target": {"artifact_id": artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": f"complete {task_id}",
                        "type_hint": asset_type,
                        "slots": slots,
                    },
                },
            })
        except Exception as e:
            sro_read_result = {"error": str(e)}

    # Collect detailed SRO token breakdown
    usage = bridge.handle({"method": "usage", "params": {}})
    session = usage.get("session", {})

    sro_tool_tokens = session.get("sr_response_tokens", 0)
    sro_total = task_tokens + sro_tool_tokens

    # Gate info
    decide = bridge.handle({"method": "decide", "params": {"path": str(asset_path)}})
    gate = decide.get("claude_gate", {})

    return {
        "task_id": task_id,
        "gate_mode": gate.get("mode", "?"),
        "trajectory": gate.get("trajectory", "?"),
        "task_desc_tokens": task_tokens,
        # Native mode
        "native_file_tokens": native_file_tokens,
        "native_total": native_total,
        # SRO mode
        "sro_preview_tokens": session.get("by_operation", {}).get("preview", {}).get("sr_tokens", 0),
        "sro_read_tokens": session.get("by_operation", {}).get("read", {}).get("sr_tokens", 0),
        "sro_tool_tokens": sro_tool_tokens,
        "sro_total": sro_total,
        # Savings
        "saved_tokens": native_total - sro_total,
        "savings_ratio": (native_total - sro_total) / native_total if native_total else 0,
        "read_ready": (
            (sro_read_result.get("evidence_pack", {}).get("slot_digest") or {}).get("overall_status", "")
            if sro_read_result else ""
        ),
        # Per-operation breakdown
        "by_operation": session.get("by_operation", {}),
        "gate_summary": usage.get("gate_summary", {}),
    }


def main():
    tasks = ["task_00012", "task_21", "task_loogle_3q", "task_loogle_5q"]
    results = {}

    for tid in tasks:
        print(f"Running {tid} (native + SRO)...", file=sys.stderr)
        results[tid] = run_full_task(tid)

    # Totals
    total_native = sum(r["native_total"] for r in results.values())
    total_sro = sum(r["sro_total"] for r in results.values())
    total_saved = sum(r["saved_tokens"] for r in results.values())
    cum_ratio = total_saved / total_native if total_native else 0

    # ---- Report ----
    print()
    print("=" * 120)
    print("  SparseRead Full-Task Benchmark — Native vs SRO (Complete Session Simulation)")
    print("=" * 120)
    print()
    print(f"  {'Task':<22} {'Gate':<10} {'Native':>10} {'SRO':>10} {'Saved':>10} {'Ratio':>7}  "
          f"|  {'Exp Native':>10} {'Exp SRO':>10} {'Exp%':>6}  {'vs Exp':>7}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*7}  "
          f"|  {'-'*10} {'-'*10} {'-'*6}  {'-'*7}")

    for tid in tasks:
        r = results[tid]
        bl = BASELINES.get(tid, {})
        n = r["native_total"]
        s = r["sro_total"]
        sv = r["saved_tokens"]
        ratio = r["savings_ratio"]

        bl_native = bl.get("native", 0)
        bl_sro = bl.get("sro", 0)
        bl_pct = bl.get("pct", 0)
        delta = ratio * 100 - bl_pct
        delta_s = f"+{delta:+.1f}pp" if delta > 0 else f"{delta:+.1f}pp" if delta != 0 else "=0.0pp"

        print(
            f"  {tid:<22} {r['gate_mode']:<10} "
            f"{n:>10,} {s:>10,} {sv:>10,} {ratio:>6.1%}  "
            f"|  {bl_native:>10,} {bl_sro:>10,} {bl_pct:>5.1f}%  {delta_s:>7}"
        )

    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*7}  "
          f"|  {'-'*10} {'-'*10} {'-'*6}  {'-'*7}")
    print(f"  {'CUMULATIVE':<22} {'':>10} {total_native:>10,} {total_sro:>10,} "
          f"{total_saved:>10,} {cum_ratio:>6.1%}")
    print()

    # Per-task breakdown
    for tid in tasks:
        r = results[tid]
        bl = BASELINES.get(tid, {})
        print(f"  ── {tid} ({r['gate_mode']}/{r['trajectory']}) ──")
        print(f"     Task desc:      {r['task_desc_tokens']:>8,} tokens  (fixed, same in both modes)")
        print(f"     ─────────────────────────────────────────")
        print(f"     Native files:   {r['native_file_tokens']:>8,} tokens")
        print(f"     Native TOTAL:   {r['native_total']:>8,} tokens")
        print(f"     ─────────────────────────────────────────")
        print(f"     SRO preview:    {r['sro_preview_tokens']:>8,} tokens")
        print(f"     SRO read:       {r['sro_read_tokens']:>8,} tokens")
        print(f"     SRO tools:      {r['sro_tool_tokens']:>8,} tokens")
        print(f"     SRO TOTAL:      {r['sro_total']:>8,} tokens")
        print(f"     ─────────────────────────────────────────")
        print(f"     SAVED:          {r['saved_tokens']:>8,} tokens  ({r['savings_ratio']:.1%})")
        print(f"     Read ready:     {r['read_ready'] or 'n/a'}")
        if bl:
            print(f"     Exp baseline:   {bl['native']:,} → {bl['sro']:,} ({bl['pct']:.1f}%, score={bl['score']})")
        print()

    # Comparison note
    print(f"  NOTE: Experiment baselines measure FULL agent sessions (system prompt + reasoning +")
    print(f"        grading + all tool calls). This benchmark measures complete file-reading cost")
    print(f"        (task description + all asset files). File reading is the portion SRO directly")
    print(f"        controls. For a true end-to-end comparison, this same task would need to be")
    print(f"        run inside Claude Code with SRO enabled vs disabled.")
    print()

    # JSON
    json_out = {
        "tasks": {tid: {k: v for k, v in r.items() if k != "by_operation"} for tid, r in results.items()},
        "cumulative": {
            "native_total": total_native,
            "sro_total": total_sro,
            "saved": total_saved,
            "savings_ratio": round(cum_ratio, 4),
        },
    }
    print("  JSON:")
    print(json.dumps(json_out, indent=2, ensure_ascii=False))
    print("=" * 120)


if __name__ == "__main__":
    main()
