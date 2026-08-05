#!/usr/bin/env python3
"""Offline Claude bridge file-read savings benchmark.

Mirrors the colleague's ``benchmark_claude_17tasks.py`` methodology but uses
the shared-core ClaudeBridge: for every task asset it estimates native tokens
vs the actual SRO preview response tokens and aggregates savings by category.

Usage:
  python3 scripts/benchmark_claude_bridge.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packages" / "sparseread-core" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages" / "sparseread-core" / "src"))
if str(ROOT / "integrations" / "claude" / "python" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "integrations" / "claude" / "python" / "src"))

from sparseread.core.benefit_gate import BenefitGate  # noqa: E402
from sparseread.core.detector import inspect_file  # noqa: E402
from sparseread.core.readers.collection import CollectionReader  # noqa: E402
from sparseread_claude.bridge import classify_claude_gate  # noqa: E402
from sparseread_claude.claude_mcp import SparseReadClaudeMCP  # noqa: E402
from sparseread_claude.token_tracker import estimate_file_tokens, estimate_response_tokens  # noqa: E402


BASELINE_DIR = ROOT / "SRO_test" / "qwenclawbench" / "baseline"
SUPPORTED_SUFFIXES = {
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".csv",
    ".tsv",
    ".xlsx",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".py",
    ".sh",
}

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
ALL_TASKS = LONG_CONTEXT + AUDIT + STRUCTURED + NATIVE_FIT
CATEGORY = {t: "long-context" for t in LONG_CONTEXT}
CATEGORY.update({t: "audit" for t in AUDIT})
CATEGORY.update({t: "structured" for t in STRUCTURED})
CATEGORY.update({t: "native-fit" for t in NATIVE_FIT})


def asset_files(task_id: str) -> list[Path]:
    assets = BASELINE_DIR / task_id / "runtime" / "assets"
    if not assets.exists():
        return []
    return [
        p
        for p in sorted(assets.rglob("*"))
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    ]


def main() -> int:
    gate = BenefitGate(CollectionReader(), override=None)
    per_task: dict[str, dict[str, float]] = {}
    category_totals: dict[str, dict[str, float]] = {}
    mode_counts: dict[str, int] = {}

    for task_id in ALL_TASKS:
        files = asset_files(task_id)
        native_total = 0.0
        sr_total = 0.0
        sr_routed_total = 0.0
        task_modes: dict[str, int] = {}
        for path in files:
            info = inspect_file(path)
            decision = gate.decide(info)
            profile = classify_claude_gate(info, decision)
            mode = str(profile.get("mode", "native"))
            task_modes[mode] = task_modes.get(mode, 0) + 1
            mode_counts[mode] = mode_counts.get(mode, 0) + 1
            native_tokens = estimate_file_tokens(info.size_bytes, path.suffix)
            try:
                mcp = SparseReadClaudeMCP(workspace=str(path.parent))
                response_text = mcp.handle_tool("sro_preview", {"path": str(path)})
                sr_tokens = estimate_response_tokens(response_text)
            except Exception:
                sr_tokens = native_tokens
            native_total += native_tokens
            sr_total += sr_tokens
            sr_routed_total += sr_tokens if mode != "native" else native_tokens

        per_task[task_id] = {
            "category": CATEGORY[task_id],
            "files": len(files),
            "native_tokens": native_total,
            "sr_tokens": sr_total,
            "savings_ratio": 1 - (sr_total / native_total) if native_total else 0.0,
            "sr_routed_tokens": sr_routed_total,
            "routed_savings_ratio": (
                1 - (sr_routed_total / native_total) if native_total else 0.0
            ),
            "modes": task_modes,
        }
        cat = CATEGORY[task_id]
        bucket = category_totals.setdefault(
            cat, {"native": 0.0, "sr": 0.0, "sr_routed": 0.0, "files": 0}
        )
        bucket["native"] += native_total
        bucket["sr"] += sr_total
        bucket["sr_routed"] += sr_routed_total
        bucket["files"] += len(files)

    print("=== per-task ===")
    for task_id, row in per_task.items():
        print(
            f"{row['category']:<12} {task_id:<58} files={row['files']:>2} "
            f"native={row['native_tokens']:>10.0f} sr={row['sr_tokens']:>8.0f} "
            f"save={row['savings_ratio']*100:>6.1f}% routed={row['routed_savings_ratio']*100:>6.1f}% "
            f"modes={row['modes']}"
        )
    print("\n=== by category ===")
    overall_native = overall_sr = 0.0
    for cat, bucket in sorted(category_totals.items()):
        ratio = 1 - (bucket["sr"] / bucket["native"]) if bucket["native"] else 0.0
        routed_ratio = 1 - (bucket["sr_routed"] / bucket["native"]) if bucket["native"] else 0.0
        overall_native += bucket["native"]
        overall_sr += bucket["sr"]
        print(
            f"{cat:<12} files={bucket['files']:>3} save={ratio*100:>6.1f}% "
            f"routed={routed_ratio*100:>6.1f}%"
        )
    overall_routed = 1 - (sum(b["sr_routed"] for b in category_totals.values()) / overall_native)
    print(f"{'overall':<12} save={(1 - overall_sr/overall_native)*100:>6.1f}% routed={overall_routed*100:>6.1f}%")
    print(f"gate modes: {mode_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
