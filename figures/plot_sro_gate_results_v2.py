#!/usr/bin/env python3
"""Generate v2 paper-style figures from sro_experiment_data.csv.

Single source of truth: figures/sro_experiment_data.csv.
Update only that CSV, then re-run this script.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).resolve().parent
CSV_PATH = OUT_DIR / "sro_experiment_data.csv"
README_PATH = OUT_DIR / "README_v2.md"

@dataclass(frozen=True)
class Pair:
    model: str
    task: str
    label: str
    group: str
    baseline_score: float
    candidate_score: float
    baseline_tokens: int
    candidate_tokens: int
    baseline_requests: float | None
    candidate_requests: float | None
    baseline_turns: float | None = None
    candidate_turns: float | None = None
    note: str = ""

COLORS = {
    "Baseline": "#8A8F98",
    "Qwen": "#4A9ED6",
    "DeepSeek": "#E05555",
    "DeepSeek-V4-Flash": "#E05555",
    "DeepSeek-V4-Pro": "#F0A050",
    "grid": "#E8EBEF",
    "text": "#202833",
    "muted": "#6B7280",
    "warn": "#A65300",
}

TITLE_KW = {"fontweight": 800, "color": COLORS["text"]}
BADGE_KW = {
    "boxstyle": "round,pad=0.24,rounding_size=0.12",
    "facecolor": "white",
    "edgecolor": "#D7DCE3",
    "linewidth": 0.7,
    "alpha": 0.96,
}

def _maybe_int(s: str) -> int | None:
    s = s.strip()
    return int(s) if s else None

def _load_pairs(csv_path: Path) -> list[Pair]:
    with csv_path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    pairs = []
    for r in rows:
        pairs.append(Pair(
            model=r["model"].strip(),
            task=r["task_id"].strip(),
            label=r["short_name"].strip(),
            group=r["group"].strip(),
            baseline_score=float(r["baseline_score"]),
            candidate_score=float(r["sro_score"]),
            baseline_tokens=int(r["baseline_tokens"]),
            candidate_tokens=int(r["sro_tokens"]),
            baseline_requests=_maybe_int(r.get("baseline_req", "")),
            candidate_requests=_maybe_int(r.get("sro_req", "")),
            baseline_turns=_maybe_int(r.get("baseline_turns", "")),
            candidate_turns=_maybe_int(r.get("sro_turns", "")),
            note=r.get("note", "").strip(),
        ))
    return pairs

PAIRS = _load_pairs(CSV_PATH)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def setup() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Arial", "Helvetica Neue", "Helvetica", "DejaVu Sans"],
        "font.size": 10.5,
        "font.weight": "medium",
        "axes.titlesize": 15,
        "axes.titleweight": 800,
        "axes.labelsize": 12,
        "axes.labelweight": 700,
        "axes.labelcolor": COLORS["text"],
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "axes.edgecolor": COLORS["grid"],
        "axes.grid": True,
        "grid.alpha": 0.42,
        "grid.color": COLORS["grid"],
        "grid.linewidth": 0.8,
        "legend.fontsize": 12,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
    })

def kfmt(v: int) -> str:
    return f"{v / 1000:.0f}K" if v >= 1000 else str(v)

def pct(v: float) -> str:
    return f"{v * 100:+.0f}%"

def pct1(v: float) -> str:
    return f"{v * 100:+.1f}%"

def md_escape(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|")

def fmt_float(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")

def fmt_optional(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return str(int(value)) if float(value).is_integer() else fmt_float(float(value))

def save(fig: plt.Figure, stem: str) -> None:
    for ext in ("png", "svg"):
        fig.savefig(OUT_DIR / f"{stem}.{ext}", bbox_inches="tight", facecolor="white")

def style_axis(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#DDE2E8")
    ax.spines["bottom"].set_color("#DDE2E8")
    ax.tick_params(axis="both", colors=COLORS["text"], width=0.8, length=3.5)
    for label in ax.get_xticklabels():
        label.set_fontweight(600)
        label.set_linespacing(0.9)
    for label in ax.get_yticklabels():
        label.set_fontweight(600)

def annotate_badge(
    ax: plt.Axes,
    x: float,
    y: float,
    text: str,
    color: str,
    *,
    fontsize: float = 10.5,
    va: str = "bottom",
) -> None:
    ax.text(
        x,
        y,
        text,
        ha="center",
        va=va,
        fontsize=fontsize,
        fontweight=800,
        linespacing=0.95,
        color=color,
        clip_on=False,
        zorder=10,
    )


# ---------------------------------------------------------------------------
# chart 1 – accuracy / token / trajectory grouped bars
# ---------------------------------------------------------------------------

def chart_accuracy_token_trajectory(df: pd.DataFrame) -> None:
    preferred = ["Qwen", "DeepSeek", "DeepSeek-V4-Flash", "DeepSeek-V4-Pro"]
    present = list(dict.fromkeys(df.model))
    models = [model for model in preferred if model in present] + [
        model for model in present if model not in preferred
    ]
    n_models = len(models)
    fig, axes = plt.subplots(3, n_models, figsize=(11 * n_models, 15.5), squeeze=False)
    for mi, model in enumerate(models):
        sub = df[df.model == model].reset_index(drop=True)
        cand_color = COLORS[model]
        labels = sub.label.str.replace("_", "\n", regex=False).str.replace(" ", "\n", regex=False)
        width = 0.36
        x = np.arange(len(sub))

        # accuracy
        tax, kax, rax = axes[0, mi], axes[1, mi], axes[2, mi]
        ax = tax
        ax.bar(x - width / 2, sub.baseline_score, width, color=COLORS["Baseline"], alpha=0.82)
        bars = ax.bar(x + width / 2, sub.candidate_score, width, color=cand_color, alpha=0.96)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_title(f"{model}  accuracy", fontsize=16, pad=10, **TITLE_KW)
        if mi == 0: ax.set_ylabel("Score")
        ax.set_ylim(0, 1.22)
        style_axis(ax)
        for bar, delta in zip(bars, sub.score_delta):
            gain = delta > 0.005
            drop = delta < -0.005
            color = "#1F7A3A" if gain else "#A65300" if drop else COLORS["text"]
            annotate_badge(
                ax,
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.035,
                f"{delta:+.2f}",
                color,
                fontsize=10.5 if gain else 9.5,
            )

        # token
        ax = kax
        ax.bar(x - width / 2, sub.baseline_tokens / 1000, width, color=COLORS["Baseline"], alpha=0.82)
        bars = ax.bar(x + width / 2, sub.candidate_tokens / 1000, width, color=cand_color, alpha=0.96)
        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_title(f"{model}  token cost", fontsize=16, pad=10, **TITLE_KW)
        if mi == 0: ax.set_ylabel("Tokens (K, log)")
        ax.set_yscale("log")
        ymax = max(sub.baseline_tokens.max(), sub.candidate_tokens.max()) / 1000
        ymin_val = max(20, min(sub.baseline_tokens.min(), sub.candidate_tokens.min()) / 1000 * 0.6)
        ax.set_ylim(ymin_val, ymax * 1.72)
        style_axis(ax)
        for bar, row in zip(bars, sub.itertuples()):
            token_down = row.token_ratio <= 1
            tcolor = "#1F7A3A" if token_down else COLORS["warn"]
            annotate_badge(
                ax,
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() * 1.11,
                f"{kfmt(row.candidate_tokens)}\n{pct(-row.token_ratio + 1)}",
                tcolor,
                fontsize=10 if token_down else 8.5,
            )

        # requests
        ax = rax
        req = sub[pd.notna(sub.baseline_requests) & pd.notna(sub.candidate_requests)]
        if len(req) == 0:
            ax.set_title(f"{model}: requests (no data)", fontsize=16, pad=10, **TITLE_KW)
            continue
        rx = np.arange(len(req))
        ax.bar(rx - width / 2, req.baseline_requests, width, color=COLORS["Baseline"], alpha=0.82)
        bars = ax.bar(rx + width / 2, req.candidate_requests, width, color=cand_color, alpha=0.96)
        ax.set_xticks(rx)
        ax.set_xticklabels(req.label.str.replace("_", "\n", regex=False).str.replace(" ", "\n", regex=False))
        ax.set_title(f"{model}  requests", fontsize=16, pad=10, **TITLE_KW)
        if mi == 0: ax.set_ylabel("Requests")
        ax.set_ylim(0, max(req.baseline_requests.max(), req.candidate_requests.max()) * 1.5)
        style_axis(ax)
        for bar, row in zip(bars, req.itertuples()):
            extra = ""
            if not pd.isna(row.baseline_turns) and not pd.isna(row.candidate_turns):
                extra = f"\nturns {int(row.baseline_turns)}->{int(row.candidate_turns)}"
            req_down = row.request_ratio <= 1
            annotate_badge(
                ax,
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.85,
                f"{int(row.candidate_requests)}\n{pct(-row.request_ratio + 1)}{extra}",
                "#1F7A3A" if req_down else COLORS["warn"],
                fontsize=10 if req_down else 9,
            )

    # legend at top
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=COLORS["Baseline"], alpha=0.86, label="Baseline"),
        *[
            Patch(
                facecolor=COLORS.get(model, COLORS["text"]),
                alpha=0.94,
                label=f"SRO / Gate  ({model})",
            )
            for model in models
        ],
    ]
    fig.legend(
        handles=legend_elements,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.985),
        ncol=min(len(legend_elements), 4),
        fontsize=12.5,
        frameon=True,
        edgecolor=COLORS["grid"],
        facecolor="white",
        framealpha=0.92,
        borderpad=0.5,
        handlelength=1.7,
        columnspacing=2.4,
    )

    fig.suptitle(
        "SRO/Gate Accuracy / Token Cost / Trajectory",
        fontsize=23,
        y=1.012,
        fontweight=900,
        color=COLORS["text"],
    )
    fig.tight_layout(rect=(0, 0, 1, 0.965), h_pad=1.2, w_pad=1.4)
    save(fig, "sro_gate_v2_accuracy_token_trajectory")
    plt.close(fig)


# ---------------------------------------------------------------------------
# chart 2 – benefit map (token reduction vs accuracy change)
# ---------------------------------------------------------------------------

def chart_benefit_map(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(10, 7))
    for model, mdf in df.groupby("model", sort=False):
        color = COLORS[model]
        ax.scatter(
            mdf.token_reduction * 100,
            mdf.score_delta,
            s=85 + np.clip(mdf.baseline_tokens / 12000, 0, 110),
            color=color, edgecolors="white", linewidths=0.5, alpha=0.88,
            label=model, zorder=5,
        )
        for r in mdf.itertuples():
            dx = 4 if r.token_reduction >= 0 else -5
            ha = "left" if r.token_reduction >= 0 else "right"
            ax.annotate(r.label.split()[0],
                        (r.token_reduction * 100, r.score_delta),
                        xytext=(dx, 3), textcoords="offset points",
                        fontsize=8.0, ha=ha, color=color)

    ax.axhline(0, color=COLORS["grid"], linewidth=0.8)
    ax.axvline(0, color=COLORS["grid"], linewidth=0.8)
    ax.set_xlabel("Token reduction vs baseline (%)")
    ax.set_ylabel("Accuracy change vs baseline")
    ax.set_title("SRO/Gate benefit map")
    ax.legend(frameon=False, fontsize=8.5)
    fig.text(0.01, 0.01,
             "Point size scales with baseline token cost. "
             "Catastrophic zero-output failures excluded.",
             fontsize=8.0, color=COLORS["muted"])
    fig.tight_layout()
    save(fig, "sro_gate_v2_benefit_map")
    plt.close(fig)


# ---------------------------------------------------------------------------
# chart 3 – outcome board (compact text table)
# ---------------------------------------------------------------------------

def chart_outcome_board(df: pd.DataFrame) -> None:
    rows = df.to_dict("records")
    fig, ax = plt.subplots(figsize=(12, 0.52 * len(rows) + 1.2))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, len(rows) + 0.5)

    ax.text(0, len(rows) + 0.25,
            "Accuracy, token cost, and trajectory length for all tested non-catastrophic tasks.",
            fontsize=9, color=COLORS["muted"])

    col_heads = ["Task", "Model", "Group", "Score", "Tokens", "Reqs"]
    col_x = [0.02, 0.22, 0.33, 0.47, 0.59, 0.74]
    for cx, head in zip(col_x, col_heads):
        ax.text(cx, len(rows) + 0.05, head, fontsize=8, fontweight="bold", color=COLORS["text"])

    for i, row in enumerate(rows):
        y = len(rows) - i - 0.5
        color = COLORS.get(row["model"], COLORS["text"])
        ax.text(0.02, y, row["label"], ha="left", va="center", fontsize=8.4, color=COLORS["text"])
        ax.text(0.22, y, row["model"], ha="left", va="center", fontsize=8.4, color=COLORS["text"])
        ax.text(0.33, y, row["group"], ha="left", va="center", fontsize=8.4, color=color, fontweight="bold")
        ax.text(0.47, y, f"{row['baseline_score']:.2f}->{row['candidate_score']:.2f}",
                ha="left", va="center", fontsize=8.4)
        ax.text(0.59, y, f"{kfmt(row['baseline_tokens'])}->{kfmt(row['candidate_tokens'])}",
                ha="left", va="center", fontsize=8.4)
        req_str = ""
        if pd.notna(row.get("baseline_requests")) and pd.notna(row.get("candidate_requests")):
            req_str = f"{int(row['baseline_requests'])}->{int(row['candidate_requests'])}"
        ax.text(0.74, y, req_str, ha="left", va="center", fontsize=8.4, color=COLORS["muted"])
        # verdict icon
        if row["group"] == "SRO win":
            verdict = "OK"
        elif row["group"] == "Gate/pass":
            verdict = "—"
        else:
            verdict = "~"
        ax.text(0.93, y, verdict, ha="center", va="center", fontsize=10, color=color)

    fig.tight_layout()
    save(fig, "sro_gate_v2_outcome_board")
    plt.close(fig)


# ---------------------------------------------------------------------------
# generated README
# ---------------------------------------------------------------------------

def generate_readme(df: pd.DataFrame) -> None:
    headers = [
        "model",
        "task",
        "label",
        "group",
        "baseline_score",
        "candidate_score",
        "baseline_tokens",
        "candidate_tokens",
        "baseline_requests",
        "candidate_requests",
        "note",
        "token_change",
    ]
    lines = [
        "# SRO/Gate Figures v2",
        "",
        "Generated by:",
        "",
        "```bash",
        "python3 figures/plot_sro_gate_results_v2.py",
        "```",
        "",
        "## Outputs",
        "",
        "- `sro_gate_v2_accuracy_token_trajectory.png` / `.svg`",
        "- `sro_gate_v2_benefit_map.png` / `.svg`",
        "- `sro_gate_v2_outcome_board.png` / `.svg`",
        "",
        "## Scope",
        "",
        "- Data source: `figures/sro_experiment_data.csv`.",
        "- Includes previously tested PinchBench and QwenClawBench tasks.",
        "- Excludes catastrophic zero-deliverable failures `task_00020` and `task_00089` from the plotted set; they remain documented in `v3_dev.md`.",
        "- Boundary cases are retained when they reveal SRO/gate limits without dominating the scale.",
        "",
        "## Data",
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in df.itertuples():
        values = [
            row.model,
            row.task,
            row.label,
            row.group,
            fmt_float(row.baseline_score),
            fmt_float(row.candidate_score),
            str(row.baseline_tokens),
            str(row.candidate_tokens),
            fmt_optional(row.baseline_requests),
            fmt_optional(row.candidate_requests),
            row.note,
            pct1(row.token_reduction),
        ]
        lines.append("| " + " | ".join(md_escape(v) for v in values) + " |")
    README_PATH.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    setup()
    df = pd.DataFrame([p.__dict__ for p in PAIRS])
    df["score_delta"] = df.candidate_score - df.baseline_score
    df["token_ratio"] = df.candidate_tokens / df.baseline_tokens
    df["token_reduction"] = 1 - df.token_ratio
    df["request_ratio"] = df.candidate_requests / df.baseline_requests

    chart_accuracy_token_trajectory(df)
    chart_benefit_map(df)
    chart_outcome_board(df)
    generate_readme(df)
    print("Generated v2 charts from sro_experiment_data.csv")

if __name__ == "__main__":
    main()
