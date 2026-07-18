#!/usr/bin/env python3
"""Build the canonical 17-task, five-model main-experiment CSV.

The source runsets are the paired Native/SR executions produced on 2026-07-15
and 2026-07-16.
The builder records one score-only correction for a T12 run whose LLM judge
returned an empty response despite all automated checks passing. It also
records the post-fix GLM Kaima rerun and the post-fix structured-scenario
reruns; the original results remain in their source runsets.
"""

from __future__ import annotations

import csv
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
QCB = ROOT / "SRO_test" / "qwenclawbench"
sys.path.insert(0, str(ROOT / "local_agent_comp"))

from summarize_qcb_runset import collect, read_result  # noqa: E402


RUNSETS = {
    "DeepSeek-V4-Flash": "main17_dsv4flash_20260715",
    "DeepSeek-V4-Pro": "main17_dsv4pro_20260715",
    "Qwen3.6-Plus": "main17_qwen36plus_20260715",
    "GLM-5.1": "main17_glm51_20260715",
    "Kimi-K2.5": "main17_kimik25_20260716",
}

STRUCTURED_TASKS = {
    "task_00058_did_regression_on_simulated_panel_data",
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    "task_spreadsheetbench_verified_49333_trimmed_vlookup",
    "task_spreadsheetbench_verified_11276_weekday_row_fix",
}

# The structured scenario was rerun after the generic sparse-plan/compute and
# formula-preview convergence repairs.  These paired results replace the older
# structured rows only; all other main-experiment tasks stay on their original
# runsets.
STRUCTURED_RUNSETS = {
    "DeepSeek-V4-Flash": (
        "structured_sparse_compute_dsv4flash_20260716",
        "structured_no_regress_dsv4flash_20260718",
    ),
    "DeepSeek-V4-Pro": (
        "structured_postfix_dsv4pro_20260718",
        "structured_postfix_dsv4pro_20260718",
    ),
    "Qwen3.6-Plus": (
        "structured_sparse_compute_qwen36plus_20260716",
        "structured_sparse_compute_qwen36plus_20260716",
    ),
    "GLM-5.1": (
        "structured_postfix_glm51_20260718",
        "structured_postfix_glm51_20260718",
    ),
    "Kimi-K2.5": (
        "structured_main4_kimik25_20260717",
        "structured_kimi_convergence_k25_20260718",
    ),
}

STRUCTURED_GATE_RUNSET_OVERRIDES = {
    (
        "Qwen3.6-Plus",
        "task_00058_did_regression_on_simulated_panel_data",
    ): "structured_no_regress_qwen36plus_r2_20260718",
    (
        "Qwen3.6-Plus",
        "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    ): "structured_no_regress_qwen36plus_r2_20260718",
    (
        "Qwen3.6-Plus",
        "task_spreadsheetbench_verified_11276_weekday_row_fix",
    ): "structured_no_regress_qwen36plus_20260718",
    (
        "Qwen3.6-Plus",
        "task_spreadsheetbench_verified_49333_trimmed_vlookup",
    ): "structured_no_regress_qwen36plus_retry_20260718",
    (
        "Kimi-K2.5",
        "task_00058_did_regression_on_simulated_panel_data",
    ): "structured_kimi_final_k25_20260718",
    (
        "Kimi-K2.5",
        "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    ): "structured_kimi_final_k25_20260718",
}

TASKS = {
    "task_loogle_shortdep_fall_of_outremer": (
        "L10Q LooGLE",
        "LooGLE",
    ),
    "task_loogle_shortdep_fall_of_outremer_5q": (
        "L5Q LooGLE",
        "LooGLE",
    ),
    "task_loogle_shortdep_fall_of_outremer_3q_followup": (
        "L3Q LooGLE",
        "LooGLE",
    ),
    "task_21_openclaw_comprehension": (
        "T21 PDF QA",
        "QwenClawBench",
    ),
    "task_workspacebench_lite_334_kaima_rd": (
        "Kaima multi-PDF",
        "Derived integration",
    ),
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check": (
        "T12 stock audit",
        "QwenClawBench",
    ),
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix": (
        "T55 literature bot",
        "QwenClawBench",
    ),
    "task_00086_command_prefix_security_analysis": (
        "T86 command security",
        "QwenClawBench",
    ),
    "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site": (
        "T94 exam audit",
        "QwenClawBench",
    ),
    "task_00098_diagnose_scheduled_book_recommendation_failure": (
        "T98 book diagnosis",
        "QwenClawBench",
    ),
    "task_00058_did_regression_on_simulated_panel_data": (
        "T58 DiD",
        "QwenClawBench",
    ),
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis": (
        "T73 P&L",
        "QwenClawBench",
    ),
    "task_spreadsheetbench_verified_49333_trimmed_vlookup": (
        "SB49333 VLOOKUP",
        "SpreadsheetBench Verified",
    ),
    "task_spreadsheetbench_verified_11276_weekday_row_fix": (
        "SB11276 weekday",
        "SpreadsheetBench Verified",
    ),
    "task_00036_find_largest_file_in_downloads_directory": (
        "T36 file size",
        "QwenClawBench",
    ),
    "task_00059_user_discount_calculator": (
        "T59 discount",
        "QwenClawBench",
    ),
    "task_00067_write_sparql_query_for_product_reviews_containing_iphone": (
        "T67 SPARQL",
        "QwenClawBench",
    ),
}

SCORE_OVERRIDES = {
    (
        "DeepSeek-V4-Pro",
        "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    ): (
        1.0,
        "judge-empty main run; low-concurrency judge recheck scored 1.00; main-run tokens/requests retained",
    ),
}

BASELINE_SCORE_OVERRIDES = {
    (
        "DeepSeek-V4-Pro",
        "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    ): (
        1.0,
        "Native judge returned empty; same-deliverable rejudge scored 1.00; original cost metrics retained",
    ),
}

RESULT_OVERRIDES = {
    (
        "GLM-5.1",
        "task_workspacebench_lite_334_kaima_rd",
    ): (
        QCB
        / "main17_glm51_kaima_single_slot_fix_20260715"
        / "gate"
        / "task_workspacebench_lite_334_kaima_rd"
        / "result.json",
        "post-fix single-slot PDF focus recheck; original main gate result retained in its runset",
    ),
}

RUN_NOTES = {
    (
        "Kimi-K2.5",
        "task_21_openclaw_comprehension",
    ): "Native timed out at 300 seconds after 43 requests; raw zero retained",
    (
        "Kimi-K2.5",
        "task_loogle_shortdep_fall_of_outremer",
    ): "Native timed out at 300 seconds after 46 requests; raw zero retained",
    (
        "Kimi-K2.5",
        "task_loogle_shortdep_fall_of_outremer_3q_followup",
    ): "Native reached the 50-tool-call cap without an answer; raw zero retained",
    (
        "Kimi-K2.5",
        "task_loogle_shortdep_fall_of_outremer_5q",
    ): "Native reached the 50-tool-call cap without an answer; raw zero retained",
}

# The Qwen T58 SR run completed with ordinary token/request counts but spent
# roughly one hour waiting on the provider.  Exclude the paired wall-clock
# values instead of treating transport latency as model/SR execution time.
TIME_EXCLUSIONS = {
    (
        "Qwen3.6-Plus",
        "task_00058_did_regression_on_simulated_panel_data",
    ): "paired wall-clock omitted: SR run had a confirmed provider-side stall",
}

FIELDS = [
    "model",
    "task_id",
    "short_name",
    "group",
    "benchmark",
    "baseline_score",
    "sro_score",
    "baseline_tokens",
    "sro_tokens",
    "baseline_req",
    "sro_req",
    "baseline_seconds",
    "sro_seconds",
    "baseline_turns",
    "sro_turns",
    "note",
]


def classify(row: dict[str, object]) -> str:
    if float(row["baseline_score"]) == 0 and float(row["sro_score"]) == 0:
        return "Boundary"
    score_delta = float(row["sro_score"]) - float(row["baseline_score"])
    token_reduction = 1 - float(row["sro_tokens"]) / float(row["baseline_tokens"])
    if score_delta < -0.05:
        return "Boundary"
    if token_reduction <= -0.50 and score_delta < 0.50:
        return "Boundary"
    if token_reduction >= 0.10 and score_delta >= -0.02:
        return "SRO win"
    return "Gate/pass"


def main() -> int:
    output = ROOT / "figures" / "sro_experiment_data.csv"
    rows: list[dict[str, object]] = []
    for expected_model, runset_name in RUNSETS.items():
        runset = QCB / runset_name
        pairs = collect(runset, "baseline", "gate")
        if len(pairs) != len(TASKS):
            raise ValueError(
                f"{runset_name}: expected {len(TASKS)} pairs, got {len(pairs)}"
            )
        by_task = {str(row["task_id"]): row for row in pairs}
        if set(by_task) != set(TASKS):
            raise ValueError(
                f"{runset_name}: missing={sorted(set(TASKS) - set(by_task))}; "
                f"extra={sorted(set(by_task) - set(TASKS))}"
            )
        for task_id in TASKS:
            source = by_task[task_id]
            model = str(source["model"])
            if model != expected_model:
                raise ValueError(
                    f"{runset_name}: expected model {expected_model}, got {model}"
                )
            note = f"main runset: {runset_name}"
            if task_id in STRUCTURED_TASKS:
                baseline_runset, default_gate_runset = STRUCTURED_RUNSETS[model]
                gate_runset = STRUCTURED_GATE_RUNSET_OVERRIDES.get(
                    (model, task_id), default_gate_runset
                )
                baseline_result = read_result(
                    QCB / baseline_runset / "baseline" / task_id / "result.json"
                )
                gate_result = read_result(
                    QCB / gate_runset / "gate" / task_id / "result.json"
                )
                if baseline_result["model"] != model or gate_result["model"] != model:
                    raise ValueError(f"structured result model mismatch for {model} {task_id}")
                source.update(
                    {
                        "baseline_score": baseline_result["score"],
                        "sro_score": gate_result["score"],
                        "baseline_tokens": baseline_result["tokens"],
                        "sro_tokens": gate_result["tokens"],
                        "baseline_req": baseline_result["req"],
                        "sro_req": gate_result["req"],
                        "baseline_seconds": baseline_result["seconds"],
                        "sro_seconds": gate_result["seconds"],
                    }
                )
                note = (
                    f"structured convergence rerun: Native {baseline_runset}; "
                    f"SR {gate_runset}"
                )
            result_override = RESULT_OVERRIDES.get((model, task_id))
            if result_override:
                corrected = read_result(result_override[0])
                source["sro_score"] = corrected["score"]
                source["sro_tokens"] = corrected["tokens"]
                source["sro_req"] = corrected["req"]
                source["sro_seconds"] = corrected["seconds"]
                note += f"; {result_override[1]}"
            override = SCORE_OVERRIDES.get((model, task_id))
            if override:
                source["sro_score"] = override[0]
                note += f"; {override[1]}"
            baseline_override = BASELINE_SCORE_OVERRIDES.get((model, task_id))
            if baseline_override:
                source["baseline_score"] = baseline_override[0]
                note += f"; {baseline_override[1]}"
            run_note = RUN_NOTES.get((model, task_id))
            if run_note:
                note += f"; {run_note}"
            short_name, benchmark = TASKS[task_id]
            row = {
                "model": model,
                "task_id": task_id,
                "short_name": short_name,
                "benchmark": benchmark,
                "baseline_score": source["baseline_score"],
                "sro_score": source["sro_score"],
                "baseline_tokens": source["baseline_tokens"],
                "sro_tokens": source["sro_tokens"],
                "baseline_req": source["baseline_req"],
                "sro_req": source["sro_req"],
                "baseline_seconds": source["baseline_seconds"],
                "sro_seconds": source["sro_seconds"],
                "baseline_turns": "",
                "sro_turns": "",
                "note": note,
            }
            time_exclusion = TIME_EXCLUSIONS.get((model, task_id))
            if time_exclusion:
                row["baseline_seconds"] = ""
                row["sro_seconds"] = ""
                row["note"] += f"; {time_exclusion}"
            row["group"] = classify(row)
            rows.append(row)

    with output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} paired rows to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
