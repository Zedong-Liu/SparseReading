"""
Claude Bridge Full Benchmark — All 17 SRO Scenario Tasks.

Runs the ClaudeBridge against ALL 17 QwenClawBench benchmark tasks across
four scenario groups (long-context, audit, structured, native-fit).

Measures:
  - Gate decisions (enforce / advisory / native)
  - Token savings (SRO vs native full-read)
  - Comparison against experimental baselines from SRO_test/qwenclawbench/

Usage:
  cd <REPO>
  uv run --project nanobot-sro-v3 python \\
    nanobot-sro-v3/tests/sparse_reading/benchmark_claude_17tasks.py
"""

from __future__ import annotations

import io, json, os, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from sparseread.bridge.claude import ClaudeBridge
from sparseread.token_tracker import estimate_file_tokens

REPO = Path(__file__).resolve().parents[3]
BASELINE_DIR = REPO / "SRO_test" / "qwenclawbench" / "baseline"

# ── Task definitions (matching run_sro_scenario_bench.sh) ──

LONG_CONTEXT_TASKS = [
    "task_loogle_shortdep_fall_of_outremer",
    "task_loogle_shortdep_fall_of_outremer_5q",
    "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_21_openclaw_comprehension",
    "task_workspacebench_lite_334_kaima_rd",
]

AUDIT_TASKS = [
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
    "task_00086_command_prefix_security_analysis",
    "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site",
    "task_00098_diagnose_scheduled_book_recommendation_failure",
]

STRUCTURED_TASKS = [
    "task_00058_did_regression_on_simulated_panel_data",
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    "task_spreadsheetbench_verified_49333_trimmed_vlookup",
    "task_spreadsheetbench_verified_11276_weekday_row_fix",
]

NATIVE_FIT_TASKS = [
    "task_00036_find_largest_file_in_downloads_directory",
    "task_00059_user_discount_calculator",
    "task_00067_write_sparql_query_for_product_reviews_containing_iphone",
]

ALL_TASKS = LONG_CONTEXT_TASKS + AUDIT_TASKS + STRUCTURED_TASKS + NATIVE_FIT_TASKS

CATEGORY_MAP = {}
for t in LONG_CONTEXT_TASKS:
    CATEGORY_MAP[t] = "long-context"
for t in AUDIT_TASKS:
    CATEGORY_MAP[t] = "audit"
for t in STRUCTURED_TASKS:
    CATEGORY_MAP[t] = "structured"
for t in NATIVE_FIT_TASKS:
    CATEGORY_MAP[t] = "native-fit"

# ── Experimental baselines from p0_skill_generalization_flash_20260526.csv ──
# Format: {task_id_short: {"baseline": (tokens, score), "gate": (tokens, score)}}
# "gate" = p0_current (workspace SKILL.md via SRO v3)
# "baseline" = fresh baseline (no SRO)
REFERENCE_BASELINES = {
    "task_00012":  {"baseline": (234370, 0.802), "gate": (372531, 0.970)},
    "task_00036":  {"baseline": (53738, 0.500),  "gate": (41561, 0.667)},
    "task_00055":  {"baseline": (852486, 0.601), "gate": (609603, 0.913)},
    "task_00058":  {"baseline": (1428715, 1.000), "gate": (466309, 1.000)},
    "task_00059":  {"baseline": (281061, 0.650), "gate": (549151, 0.971)},
    "task_00067":  {"baseline": (163928, 0.496), "gate": (137198, 0.496)},
    "task_00073":  {"baseline": (562285, 0.917), "gate": (390319, 1.000)},
    "task_00086":  {"baseline": (659904, 0.233), "gate": (207557, 0.585)},
    "task_00094":  {"baseline": (256217, 1.000), "gate": (255913, 1.000)},
    "task_00098":  {"baseline": (387489, 0.707), "gate": (364592, 0.917)},
    "task_21":     {"baseline": (466144, 1.000), "gate": (46799, 1.000)},
    "task_loogle": {"baseline": (725743, 1.000), "gate": (49612, 0.909)},
    "task_loogle_5q":   {"baseline": (263004, 1.000), "gate": (39270, 1.000)},
    "task_loogle_3q":   {"baseline": (248460, 1.000), "gate": (78561, 1.000)},
}

def _match_ref(task_id: str) -> dict | None:
    for key, val in REFERENCE_BASELINES.items():
        if key in task_id:
            return val
    return None


@dataclass
class TaskResult:
    task_id: str
    category: str
    asset_count: int
    asset_total_bytes: int
    gate_mode: str
    trajectory: str
    native_tokens: int
    sro_tokens: int
    tokens_saved: int
    savings_ratio: float
    sro_operations: int
    gate_summary: dict = field(default_factory=dict)
    ref_baseline: dict | None = None


def run_single_task(task_id: str) -> TaskResult:
    """Run one benchmark task through ClaudeBridge in both native and SRO modes."""
    task_dir = BASELINE_DIR / task_id
    assets_dir = task_dir / "runtime" / "assets"

    if not assets_dir.exists():
        return TaskResult(
            task_id=task_id, category=CATEGORY_MAP.get(task_id, "unknown"),
            asset_count=0, asset_total_bytes=0,
            gate_mode="error", trajectory="N/A",
            native_tokens=0, sro_tokens=0, tokens_saved=0,
            savings_ratio=0, sro_operations=0,
        )

    # Collect all asset files
    asset_files = []
    for f in sorted(assets_dir.rglob("*")):
        if f.is_file():
            asset_files.append(f)

    total_bytes = sum(f.stat().st_size for f in asset_files)
    if total_bytes == 0:
        return TaskResult(
            task_id=task_id, category=CATEGORY_MAP.get(task_id, "unknown"),
            asset_count=len(asset_files), asset_total_bytes=0,
            gate_mode="empty", trajectory="N/A",
            native_tokens=0, sro_tokens=0, tokens_saved=0,
            savings_ratio=0, sro_operations=0,
        )

    # ── Native mode: estimate tokens for reading all files in full ──
    native_tokens = 0
    for f in asset_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            native_tokens += estimate_file_tokens(len(content.encode("utf-8")), f.suffix.lstrip("."))
        except Exception:
            native_tokens += estimate_file_tokens(f.stat().st_size, f.suffix.lstrip("."))

    # ── SRO mode: run through ClaudeBridge ──
    bridge = ClaudeBridge(workspace=assets_dir.parent, mode="auto")

    sro_tokens_total = 0
    sro_operations = 0
    gate_mode = "native"
    trajectory = "native"

    # Decide + preview for each asset or as a collection
    asset_path = str(assets_dir)

    # decide
    try:
        decide = bridge.handle({"method": "decide", "params": {"path": asset_path}})
        gate = decide.get("claude_gate", {})
        gate_mode = gate.get("mode", "native")
        trajectory = gate.get("trajectory", "native")
    except Exception:
        pass

    # preview
    artifact_id = ""
    try:
        preview = bridge.handle({"method": "preview", "params": {"path": asset_path}})
        pack = preview.get("preview_pack", {})
        artifact_id = pack.get("artifact_id", "")
    except Exception:
        pass

    # sro_read if gate is not native
    if artifact_id and gate_mode != "native":
        try:
            sro_read_result = bridge.handle({
                "method": "read",
                "params": {
                    "target": {"artifact_id": artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": f"extract evidence for {task_id}",
                        "type_hint": "collection" if len(asset_files) > 1 else "text",
                        "slots": [{"id": "main", "question": "extract key information"}],
                    },
                },
            })
        except Exception:
            pass

    # usage
    try:
        usage = bridge.handle({"method": "usage", "params": {}})
        session = usage.get("session", {})
        sro_tokens_total = session.get("sr_response_tokens", 0)
        sro_operations = session.get("operations", 0)
        gate_summary = usage.get("gate_summary", {})
    except Exception:
        sro_tokens_total = 0
        sro_operations = 0
        gate_summary = {}

    tokens_saved = native_tokens - sro_tokens_total
    savings_ratio = tokens_saved / native_tokens if native_tokens > 0 else 0.0

    return TaskResult(
        task_id=task_id,
        category=CATEGORY_MAP.get(task_id, "unknown"),
        asset_count=len(asset_files),
        asset_total_bytes=total_bytes,
        gate_mode=gate_mode,
        trajectory=trajectory,
        native_tokens=native_tokens,
        sro_tokens=sro_tokens_total,
        tokens_saved=tokens_saved,
        savings_ratio=savings_ratio,
        sro_operations=sro_operations,
        gate_summary=gate_summary,
        ref_baseline=_match_ref(task_id),
    )


def print_category_header(cat_name: str, count: int) -> None:
    print(f"\n{'─' * 95}")
    print(f"  Category: {cat_name} ({count} tasks)")
    print(f"{'─' * 95}")


def main():
    results: dict[str, TaskResult] = {}

    categories = [
        ("long-context", LONG_CONTEXT_TASKS),
        ("audit", AUDIT_TASKS),
        ("structured", STRUCTURED_TASKS),
        ("native-fit", NATIVE_FIT_TASKS),
    ]

    for cat_name, task_list in categories:
        print_category_header(cat_name, len(task_list))
        for i, tid in enumerate(task_list, 1):
            print(f"  [{i}/{len(task_list)}] {tid}...", end=" ", flush=True)
            r = run_single_task(tid)
            results[tid] = r
            print(f"gate={r.gate_mode} native={r.native_tokens:,} sro={r.sro_tokens:,} "
                  f"saved={r.tokens_saved:,} ({r.savings_ratio:.1%})")

    # ── Aggregate by category ──
    print(f"\n{'=' * 110}")
    print("  SPARSEREAD CLAUDE BRIDGE — FULL 17-TASK BENCHMARK REPORT")
    print(f"{'=' * 110}")

    for cat_name, task_list in categories:
        cat_results = [results[t] for t in task_list if results[t].asset_total_bytes > 0]
        if not cat_results:
            continue
        total_native = sum(r.native_tokens for r in cat_results)
        total_sro = sum(r.sro_tokens for r in cat_results)
        total_saved = sum(r.tokens_saved for r in cat_results)
        cat_ratio = total_saved / total_native if total_native > 0 else 0

        enforce_n = sum(1 for r in cat_results if r.gate_mode == "enforce")
        advisory_n = sum(1 for r in cat_results if r.gate_mode == "advisory")
        native_n = sum(1 for r in cat_results if r.gate_mode == "native")

        print(f"\n  [{cat_name}] {len(cat_results)} tasks")
        print(f"    Gate: enforce={enforce_n} advisory={advisory_n} native={native_n}")
        print(f"    Native tokens (est): {total_native:>12,}")
        print(f"    SRO tokens:          {total_sro:>12,}")
        print(f"    Tokens saved:        {total_saved:>12,}  ({cat_ratio:.1%})")

    # ── Overall cumulative ──
    all_valid = [r for r in results.values() if r.asset_total_bytes > 0]
    cum_native = sum(r.native_tokens for r in all_valid)
    cum_sro = sum(r.sro_tokens for r in all_valid)
    cum_saved = sum(r.tokens_saved for r in all_valid)
    cum_ratio = cum_saved / cum_native if cum_native > 0 else 0

    print(f"\n{'─' * 110}")
    print(f"  CUMULATIVE (all 17 tasks)")
    print(f"    Native tokens (est): {cum_native:>12,}")
    print(f"    SRO tokens:          {cum_sro:>12,}")
    print(f"    Tokens saved:        {cum_saved:>12,}  ({cum_ratio:.1%})")
    print(f"{'─' * 110}")

    # ── Comparison with reference baselines ──
    print(f"\n{'=' * 110}")
    print("  COMPARISON WITH EXPERIMENTAL BASELINES (DeepSeek-V4-Flash)")
    print(f"{'=' * 110}")
    print(f"  {'Task':<45} {'ClaudeGate':>10} {'Savings':>7}  |  {'RefBL Tok':>10} {'RefSR Tok':>10} {'RefScore':>8}")
    print(f"  {'-'*45} {'-'*10} {'-'*7}  |  {'-'*10} {'-'*10} {'-'*8}")

    matched = 0
    for r in all_valid:
        ref = r.ref_baseline
        if ref:
            matched += 1
            bl = ref["baseline"]
            gate = ref["gate"]
            print(f"  {r.task_id:<45} {r.gate_mode:>10} {r.savings_ratio:>6.1%}  "
                  f"|  {bl[0]:>10,} {gate[0]:>10,} {gate[1]:>7.1%}")
        else:
            print(f"  {r.task_id:<45} {r.gate_mode:>10} {r.savings_ratio:>6.1%}  "
                  f"|  {'(no ref)':>10} {'':>10} {'':>8}")

    print(f"\n  NOTE: Reference baselines measure FULL agent sessions (prompt + reasoning +")
    print(f"  tool calls + grading). This benchmark measures FILE-READING token costs only.")
    print(f"  SRO directly controls the file-reading portion of the total cost.")
    print(f"  {matched}/{len(all_valid)} tasks have reference baselines.")

    # ── JSON output ──
    json_out = {
        "benchmark": "claude_bridge_17tasks",
        "gate_profile": "Claude Code (classify_claude_gate)",
        "tasks": {
            tid: {
                "category": r.category,
                "asset_count": r.asset_count,
                "asset_total_bytes": r.asset_total_bytes,
                "gate_mode": r.gate_mode,
                "trajectory": r.trajectory,
                "native_tokens_est": r.native_tokens,
                "sro_tokens": r.sro_tokens,
                "tokens_saved": r.tokens_saved,
                "savings_ratio": round(r.savings_ratio, 4),
                "sro_operations": r.sro_operations,
                "gate_summary": r.gate_summary,
                "ref_baseline": r.ref_baseline,
            }
            for tid, r in results.items()
        },
        "cumulative": {
            "native_tokens_est": cum_native,
            "sro_tokens": cum_sro,
            "tokens_saved": cum_saved,
            "savings_ratio": round(cum_ratio, 4),
        },
    }
    print(f"\n{'=' * 110}")
    print("  JSON OUTPUT")
    print(f"{'=' * 110}")
    print(json.dumps(json_out, indent=2, ensure_ascii=False, default=str))
    print(f"{'=' * 110}")

    # Write JSON report file
    report_path = REPO / "SRO_test" / "qwenclawbench" / "claude_bridge_17task_results.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(json_out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n  Report saved to: {report_path}")


if __name__ == "__main__":
    main()
