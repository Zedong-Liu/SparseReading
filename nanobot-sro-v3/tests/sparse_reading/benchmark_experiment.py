"""Real benchmark runner — tests SRO against actual experimental datasets.

Runs the ClaudeBridge against the 4 QwenClawBench benchmark tasks'
real assets, collecting token metrics via the Anthropic count_tokens API.
Compares results against the experimental baselines from figures/.
"""

from __future__ import annotations

import io, json, sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sparseread.bridge.claude import ClaudeBridge
from sparseread.token_tracker import estimate_file_tokens

REPO = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO / "SRO_test" / "qwenclawbench" / "baseline"

# Experimental baselines from figures/sro_experiment_data.csv (DeepSeek-V4-Flash)
BASELINES = {
    "task_00012": {"native_tokens": 234_370, "sro_tokens": 89_103, "savings_pct": 62.0, "score": 1.0},
    "task_21":    {"native_tokens": 466_144, "sro_tokens": 99_113, "savings_pct": 78.8, "score": 1.0},
    "task_loogle_3q": {"native_tokens": 248_460, "sro_tokens": 65_147, "savings_pct": 73.8, "score": 1.0},
    "task_loogle_5q": {"native_tokens": 263_004, "sro_tokens": 45_997, "savings_pct": 82.5, "score": 1.0},
}

# DeepSeek-V4-Pro baselines (stronger model, better savings)
BASELINES_PRO = {
    "task_00012": {"native_tokens": 253_685, "sro_tokens": 54_246, "savings_pct": 78.6, "score": 0.970},
    "task_21":    {"native_tokens": 714_716, "sro_tokens": 49_919, "savings_pct": 93.0, "score": 1.0},
}

SLOTS = {
    "task_00012": [
        {"id": "state_vs_output", "question": "35 seen_ids vs 11 output records and orphaned IDs"},
        {"id": "dedup_bug", "question": "deduplicate list(seen)[-5000:] ordering bug and fix"},
        {"id": "important_items", "question": "exactly five important announcements with IDs and companies"},
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
        {"id": "q1f", "question": "Follow-up question 1 based on document context"},
        {"id": "q2f", "question": "Follow-up question 2 based on document context"},
        {"id": "q3f", "question": "Follow-up question 3 based on document context"},
    ],
    "task_loogle_5q": [
        {"id": "q1", "question": "Primary fact question 1 from the document"},
        {"id": "q2", "question": "Primary fact question 2 from the document"},
        {"id": "q3", "question": "Primary fact question 3 from the document"},
        {"id": "q4", "question": "Primary fact question 4 from the document"},
        {"id": "q5", "question": "Primary fact question 5 from the document"},
    ],
}

TASK_DIRS = {
    "task_00012":    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_21":       "task_21_openclaw_comprehension",
    "task_loogle_3q": "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_loogle_5q": "task_loogle_shortdep_fall_of_outremer_5q",
}

TASK_ASSET = {
    "task_00012": "a_stock_announcements",
    "task_21": "OpenClaw Agent Use Cases and Gap Analysis for PinchBench.pdf",
    "task_loogle_3q": "document.txt",
    "task_loogle_5q": "document.txt",
}


def run_task(task_id: str) -> dict:
    """Run one benchmark task through the SRO bridge, return metrics."""
    task_dir = BASELINE_DIR / TASK_DIRS[task_id]
    assets = task_dir / "runtime" / "assets"
    asset_name = TASK_ASSET[task_id]
    asset_path = assets / asset_name
    asset_type = "collection" if asset_path.is_dir() else "collection" if asset_name == "a_stock_announcements" else "pdf" if asset_path.suffix == ".pdf" else "text"

    if not asset_path.exists():
        return {"error": f"asset not found: {asset_path}"}

    # Compute native full-file token cost
    if asset_path.is_dir():
        total_bytes = sum(f.stat().st_size for f in asset_path.rglob("*") if f.is_file())
    else:
        total_bytes = asset_path.stat().st_size
    native_tokens = estimate_file_tokens(total_bytes, asset_path.suffix if not asset_path.is_dir() else "")

    # Run SRO bridge
    bridge = ClaudeBridge(workspace=assets.parent, mode="auto")

    # Step 1: decide
    decide = bridge.handle({"method": "decide", "params": {"path": str(asset_path)}})

    # Step 2: preview
    preview = bridge.handle({"method": "preview", "params": {"path": str(asset_path)}})
    pack = preview.get("preview_pack", {})
    artifact_id = pack.get("artifact_id", "")

    # Step 3: collect evidence
    read_result = {}
    if artifact_id:
        slots = SLOTS.get(task_id, [{"id": "general", "question": "extract key evidence"}])
        try:
            read_result = bridge.handle({
                "method": "read",
                "params": {
                    "target": {"artifact_id": artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": f"extract evidence for {task_id}",
                        "type_hint": asset_type,
                        "slots": slots,
                    },
                },
            })
        except Exception as e:
            read_result = {"error": str(e)}

    # Step 4: usage
    usage = bridge.handle({"method": "usage", "params": {}})

    session = usage.get("session", {})
    gate = decide.get("claude_gate", {})

    return {
        "task_id": task_id,
        "asset": str(asset_path),
        "asset_type": asset_type,
        "asset_size_bytes": total_bytes,
        "native_tokens_est": native_tokens,
        "gate_mode": gate.get("mode", "?"),
        "trajectory": gate.get("trajectory", "?"),
        "sro_ops": session.get("operations", 0),
        "sro_tokens": session.get("sr_response_tokens", 0),
        "full_file_tokens_est": session.get("full_file_tokens", 0),
        "tokens_saved": session.get("tokens_saved", 0),
        "savings_ratio": session.get("savings_ratio", 0),
        "read_ready": (
            (read_result.get("evidence_pack", {}).get("slot_digest") or {}).get("overall_status", "")
            if read_result else ""
        ),
        "gate_summary": usage.get("gate_summary", {}),
    }


def main():
    tasks = ["task_00012", "task_21", "task_loogle_3q", "task_loogle_5q"]
    results = {}
    for tid in tasks:
        print(f"Running {tid}...", file=sys.stderr)
        results[tid] = run_task(tid)

    # Compute cumulative — use API-counted full_file_tokens (ground truth), not heuristic
    total_api_full = sum(r.get("full_file_tokens_est", 0) for r in results.values())
    total_sro = sum(r.get("sro_tokens", 0) for r in results.values())
    total_saved_api = sum(r.get("tokens_saved", 0) for r in results.values())
    cum_savings_api = total_saved_api / total_api_full if total_api_full else 0

    # Print report
    print()
    print("=" * 112)
    print("  SparseRead vs Benchmark Baselines — Real Assets Comparison")
    print("=" * 112)
    print()
    print(f"  {'Task':<22} {'Type':>10} {'API Full':>10} {'SR Resp':>10} {'Saved':>10} {'Ratio':>7}  {'Exp BL':>8} {'Exp SR':>8} {'Exp%':>6}  {'vs Exp':>7}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*7}  {'-'*8} {'-'*8} {'-'*6}  {'-'*7}")

    for tid in tasks:
        r = results[tid]
        bl = BASELINES.get(tid, {})
        f = r.get("full_file_tokens_est", 0)
        s = r.get("sro_tokens", 0)
        sv = r.get("tokens_saved", 0)
        ratio = r.get("savings_ratio", 0)

        bl_native = bl.get("native_tokens", 0)
        bl_sro = bl.get("sro_tokens", 0)
        bl_pct = bl.get("savings_pct", 0)
        delta = ratio * 100 - bl_pct

        delta_s = f"+{delta:+.1f}pp" if delta > 0 else f"{delta:+.1f}pp"

        print(
            f"  {tid:<22} {r.get('asset_type','?'):>10} "
            f"{f:>10,} {s:>10,} {sv:>10,} {ratio:>6.1%}  "
            f"{bl_native:>8,} {bl_sro:>8,} {bl_pct:>5.1f}%  {delta_s:>7}"
        )

    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*7}  {'-'*8} {'-'*8} {'-'*6}  {'-'*7}")
    print(f"  {'CUMULATIVE (API)':<22} {'':>10} {total_api_full:>10,} {total_sro:>10,} {total_saved_api:>10,} {cum_savings_api:>6.1%}")
    print()

    # Per-task detail
    for tid in tasks:
        r = results[tid]
        bl = BASELINES.get(tid, {})
        print(f"  ── {tid} ──")
        print(f"     Asset:        {r['asset']}")
        print(f"     Gate:         {r['gate_mode']} / {r['trajectory']}")
        print(f"     SRO ops:      {r['sro_ops']}")
        print(f"     Read ready:   {r['read_ready'] or '(preview only)'}")
        print(f"     Full est:     {r['full_file_tokens_est']:,} tokens")
        print(f"     SR response:  {r['sro_tokens']:,} tokens")
        print(f"     Saved:        {r['tokens_saved']:,} tokens ({r['savings_ratio']:.1%})")
        print(f"     Gate summary: {r['gate_summary'].get('by_mode', {})}")
        if bl:
            print(f"     Baseline:     {bl['native_tokens']:,} → {bl['sro_tokens']:,} ({bl['savings_pct']:.1f}%, score={bl['score']})")
        print()

    # JSON
    print("  JSON:")
    json_out = {tid: {k: str(v) if isinstance(v, Path) else v for k, v in r.items()} for tid, r in results.items()}
    json_out["cumulative"] = {
        "total_native_tokens": total_native,
        "total_sro_tokens": total_sro,
        "total_saved": total_saved,
        "savings_ratio": round(cum_savings, 4),
    }
    print(json.dumps(json_out, indent=2, ensure_ascii=False, default=str))
    print("=" * 112)


if __name__ == "__main__":
    main()
