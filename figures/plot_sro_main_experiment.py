#!/usr/bin/env python3
"""Plot the complete five-model SparseRead main experiment.

The canonical input is ``figures/sro_experiment_data.csv``.  The plot is
generated only when every model has one paired Native/SR result for each of
the 17 tasks in the main matrix.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


OUT_DIR = Path(__file__).resolve().parent
CSV_PATH = OUT_DIR / "sro_experiment_data.csv"

MODELS = [
    "DeepSeek-V4-Flash",
    "DeepSeek-V4-Pro",
    "Qwen3.6-Plus",
    "GLM-5.1",
    "Kimi-K2.5",
]
MODEL_LABELS = {
    "DeepSeek-V4-Flash": "DS-Flash",
    "DeepSeek-V4-Pro": "DS-Pro",
    "Qwen3.6-Plus": "Qwen3.6+",
    "GLM-5.1": "GLM-5.1",
    "Kimi-K2.5": "Kimi-K2.5",
}
MODEL_COLORS = {
    "DeepSeek-V4-Flash": "#D95F4C",
    "DeepSeek-V4-Pro": "#E59B39",
    "Qwen3.6-Plus": "#3D82C4",
    "GLM-5.1": "#8065B5",
    "Kimi-K2.5": "#289681",
}

SCENARIOS = {
    "Long-context reading": {
        "task_loogle_shortdep_fall_of_outremer",
        "task_loogle_shortdep_fall_of_outremer_5q",
        "task_loogle_shortdep_fall_of_outremer_3q_followup",
        "task_21_openclaw_comprehension",
        "task_workspacebench_lite_334_kaima_rd",
    },
    "Multi-file audit and diagnosis": {
        "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
        "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
        "task_00086_command_prefix_security_analysis",
        "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site",
        "task_00098_diagnose_scheduled_book_recommendation_failure",
    },
    "Structured analysis": {
        "task_00058_did_regression_on_simulated_panel_data",
        "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
        "task_spreadsheetbench_verified_49333_trimmed_vlookup",
        "task_spreadsheetbench_verified_11276_weekday_row_fix",
    },
    "Native-fit controls": {
        "task_00036_find_largest_file_in_downloads_directory",
        "task_00059_user_discount_calculator",
        "task_00067_write_sparql_query_for_product_reviews_containing_iphone",
    },
}


def setup_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 8.4,
            "axes.titlesize": 9.2,
            "axes.labelsize": 8.8,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.6,
            "axes.linewidth": 0.7,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "legend.fontsize": 8.5,
            "legend.frameon": False,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def load_and_validate() -> pd.DataFrame:
    df = pd.read_csv(CSV_PATH)
    expected_tasks = set().union(*SCENARIOS.values())
    actual_models = set(df["model"])
    if actual_models != set(MODELS):
        raise ValueError(
            f"Expected models {MODELS}; got {sorted(actual_models)}"
        )
    duplicates = df.duplicated(["model", "task_id"], keep=False)
    if duplicates.any():
        rows = df.loc[duplicates, ["model", "task_id"]].to_dict("records")
        raise ValueError(f"Duplicate model/task rows: {rows}")
    for model in MODELS:
        tasks = set(df.loc[df["model"] == model, "task_id"])
        if tasks != expected_tasks:
            missing = sorted(expected_tasks - tasks)
            extra = sorted(tasks - expected_tasks)
            raise ValueError(f"{model}: missing={missing}, extra={extra}")
    if len(df) != len(MODELS) * len(expected_tasks):
        raise ValueError(f"Expected 85 paired rows, got {len(df)}")
    if df[
        [
            "baseline_score",
            "sro_score",
            "baseline_tokens",
            "sro_tokens",
            "baseline_req",
            "sro_req",
        ]
    ].isna().any().any():
        raise ValueError("Canonical main-experiment core metrics contain missing values")

    task_to_scenario = {
        task: scenario for scenario, tasks in SCENARIOS.items() for task in tasks
    }
    df = df.copy()
    df["scenario"] = df["task_id"].map(task_to_scenario)
    df["token_reduction_pct"] = (
        (df["baseline_tokens"] - df["sro_tokens"]) / df["baseline_tokens"] * 100
    )
    df["request_reduction_pct"] = (
        (df["baseline_req"] - df["sro_req"]) / df["baseline_req"] * 100
    )
    df["score_delta"] = df["sro_score"] - df["baseline_score"]
    df["time_reduction_pct"] = (
        (df["baseline_seconds"] - df["sro_seconds"]) / df["baseline_seconds"] * 100
    )
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    timed = df["baseline_seconds"].notna() & df["sro_seconds"].notna()
    df["paired_baseline_seconds"] = df["baseline_seconds"].where(timed)
    df["paired_sro_seconds"] = df["sro_seconds"].where(timed)
    df["timed_pair"] = timed.astype(int)
    summary = (
        df.groupby(["model", "scenario"], as_index=False)
        .agg(
            task_count=("task_id", "nunique"),
            baseline_score_sum=("baseline_score", "sum"),
            sro_score_sum=("sro_score", "sum"),
            baseline_mean_score=("baseline_score", "mean"),
            sro_mean_score=("sro_score", "mean"),
            baseline_tokens_sum=("baseline_tokens", "sum"),
            sro_tokens_sum=("sro_tokens", "sum"),
            baseline_requests_sum=("baseline_req", "sum"),
            sro_requests_sum=("sro_req", "sum"),
            timed_task_count=("timed_pair", "sum"),
            baseline_seconds_sum=("paired_baseline_seconds", "sum"),
            sro_seconds_sum=("paired_sro_seconds", "sum"),
            median_token_reduction_pct=("token_reduction_pct", "median"),
            median_request_reduction_pct=("request_reduction_pct", "median"),
            median_time_reduction_pct=("time_reduction_pct", "median"),
            mean_score_delta=("score_delta", "mean"),
        )
    )
    summary["combined_token_reduction_pct"] = (
        (summary["baseline_tokens_sum"] - summary["sro_tokens_sum"])
        / summary["baseline_tokens_sum"]
        * 100
    )
    summary["combined_request_reduction_pct"] = (
        (summary["baseline_requests_sum"] - summary["sro_requests_sum"])
        / summary["baseline_requests_sum"]
        * 100
    )
    summary["combined_time_reduction_pct"] = (
        (summary["baseline_seconds_sum"] - summary["sro_seconds_sum"])
        / summary["baseline_seconds_sum"]
        * 100
    )
    order = {name: idx for idx, name in enumerate(SCENARIOS)}
    model_order = {name: idx for idx, name in enumerate(MODELS)}
    summary["scenario_order"] = summary["scenario"].map(order)
    summary["model_order"] = summary["model"].map(model_order)
    summary = summary.sort_values(["scenario_order", "model_order"])
    summary.drop(columns=["scenario_order", "model_order"]).to_csv(
        OUT_DIR / "sro_main_scenario_summary.csv", index=False
    )
    return summary


def draw(summary: pd.DataFrame) -> None:
    scenarios = list(SCENARIOS)
    fig, axes = plt.subplots(
        3,
        len(scenarios),
        figsize=(7.25, 3.75),
        sharey="row",
        gridspec_kw={"height_ratios": [1.0, 0.88, 0.88], "hspace": 0.24, "wspace": 0.16},
    )
    x = np.arange(len(MODELS))
    width = 0.62

    token_limit = max(100.0, float(summary["combined_token_reduction_pct"].abs().max()) * 1.2)
    score_limit = max(0.10, float(summary["mean_score_delta"].abs().max()) * 1.25)
    time_limit = max(100.0, float(summary["combined_time_reduction_pct"].abs().max()) * 1.2)

    for col, scenario in enumerate(scenarios):
        sub = summary[summary["scenario"] == scenario].set_index("model")
        token_values = [float(sub.loc[model, "combined_token_reduction_pct"]) for model in MODELS]
        score_values = [float(sub.loc[model, "mean_score_delta"]) for model in MODELS]
        time_values = [float(sub.loc[model, "combined_time_reduction_pct"]) for model in MODELS]
        for row, values in enumerate((token_values, score_values, time_values)):
            ax = axes[row, col]
            for idx, (model, value) in enumerate(zip(MODELS, values)):
                ax.bar(
                    x[idx],
                    value,
                    width=width,
                    color=MODEL_COLORS[model],
                    edgecolor="white",
                    linewidth=0.45,
                    zorder=3,
                )
            ax.axhline(0, color="#30363D", linewidth=0.65, zorder=2)
            ax.grid(axis="y", color="#E1E6EB", linewidth=0.5, zorder=0)
            ax.set_axisbelow(True)
            ax.set_xlim(-0.65, len(MODELS) - 0.35)
            ax.set_xticks([])
            if row == 0:
                ax.set_ylim(-token_limit, token_limit)
                ax.set_title(scenario, pad=4, fontweight="bold")
                if col == 0:
                    ax.set_ylabel("Token reduction (%)")
                else:
                    ax.tick_params(axis="y", labelleft=False)
            else:
                if row == 1:
                    ax.set_ylim(-score_limit, score_limit)
                    if col == 0:
                        ax.set_ylabel("Mean score change")
                    else:
                        ax.tick_params(axis="y", labelleft=False)
                else:
                    ax.set_ylim(-time_limit, time_limit)
                    if col == 0:
                        ax.set_ylabel("Time reduction (%)")
                    else:
                        ax.tick_params(axis="y", labelleft=False)

    handles = [
        mpl.patches.Patch(
            facecolor=MODEL_COLORS[model],
            edgecolor="white",
            label=MODEL_LABELS[model],
        )
        for model in MODELS
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        ncol=len(MODELS),
        bbox_to_anchor=(0.53, 1.0),
        columnspacing=0.9,
        handlelength=1.05,
    )
    axes[0, 0].text(
        0.015,
        0.965,
        "a",
        transform=axes[0, 0].transAxes,
        fontsize=10.2,
        fontweight="bold",
        ha="left",
        va="top",
    )
    axes[1, 0].text(
        0.015,
        0.965,
        "b",
        transform=axes[1, 0].transAxes,
        fontsize=10.2,
        fontweight="bold",
        ha="left",
        va="top",
    )
    axes[2, 0].text(
        0.015,
        0.965,
        "c",
        transform=axes[2, 0].transAxes,
        fontsize=10.2,
        fontweight="bold",
        ha="left",
        va="top",
    )
    fig.subplots_adjust(left=0.095, right=0.995, top=0.88, bottom=0.045)
    for ext, kwargs in {
        "png": {"dpi": 600},
        "svg": {},
        "pdf": {},
    }.items():
        path = OUT_DIR / f"sro_main_scenario_matrix.{ext}"
        fig.savefig(
            path,
            bbox_inches="tight",
            **kwargs,
        )
        if ext == "svg":
            path.write_text(
                "\n".join(line.rstrip() for line in path.read_text().splitlines())
                + "\n"
            )
    plt.close(fig)


def main() -> None:
    setup_style()
    df = load_and_validate()
    summary = summarize(df)
    draw(summary)
    print("Generated complete five-model main-experiment figures")


if __name__ == "__main__":
    main()
