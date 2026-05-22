#!/usr/bin/env python3
"""Generate paper-style comparison figures for SRO/gate runs.

The data below is intentionally hand-curated from v3_dev.md notes plus the
latest user-provided valid results. Mixed/native/variance cases are annotated
instead of being treated as clean SRO wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import numpy as np
import pandas as pd


OUT_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Result:
    model: str
    task: str
    task_short: str
    variant: str
    score: float
    tokens: int
    requests: Optional[int]
    turns: Optional[int] = None
    note: str = ""


DATA: list[Result] = [
    Result("Qwen", "task_21", "21", "Baseline", 0.9444, 72865, 7, 14),
    Result("Qwen", "task_21", "21", "SRO/gate", 1.0000, 34154, 4, 8),
    Result("Qwen", "task_00012", "12", "Baseline", 0.3583, 124843, 10, 23),
    Result("Qwen", "task_00012", "12", "SRO/gate", 1.0000, 39085, 4, 8),
    Result("Qwen", "task_00067", "67", "Baseline", 0.7500, 89871, 10),
    Result("Qwen", "task_00067", "67", "SRO/gate", 0.8750, 89999, 10, note="native-like gate"),
    Result("Qwen", "task_00086", "86", "Baseline", 0.3091, 140514, 8),
    Result("Qwen", "task_00086", "86", "SRO/gate", 0.9538, 90695, 5),
    Result("Qwen", "task_00098", "98", "Baseline", 0.9170, 186005, None, note="old baseline; mixed/variance"),
    Result("Qwen", "task_00098", "98", "SRO/gate", 0.8317, 97142, 10, note="current gate retest; native path"),
    Result("DeepSeek", "task_21", "21", "Baseline", 1.0000, 507433, 25),
    Result("DeepSeek", "task_21", "21", "SRO/gate", 1.0000, 56170, 4),
    Result("DeepSeek", "task_00012", "12", "Baseline", 0.6550, 286816, 15),
    Result("DeepSeek", "task_00012", "12", "SRO/gate", 1.0000, 131161, 8),
    Result("DeepSeek", "task_00098", "98", "Baseline", 0.8958, 467170, 19),
    Result("DeepSeek", "task_00098", "98", "Gate/native", 0.8667, 312598, 15, note="native/no SRO"),
    Result("DeepSeek", "task_00086", "86", "Baseline", 0.6000, 1152253, 40, note="current comparable baseline"),
    Result("DeepSeek", "task_00086", "86", "Gate/native", 0.9538, 859009, 32, note="native/no SRO markers"),
    Result("DeepSeek", "task_00067", "67", "Baseline", 0.5583, 124636, 7),
    Result("DeepSeek", "task_00067", "67", "Gate/native", 0.5583, 203194, 12, note="no SRO markers; native variance"),
]


PALETTE = {
    "baseline": "#8A8F98",
    "qwen": "#0072B2",
    "deepseek": "#D55E00",
    "sro": "#009E73",
    "grid": "#ECEFF3",
    "text": "#1F2933",
    "muted": "#68707D",
    "warn": "#B36B00",
}


def setup_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#9AA3AF",
            "axes.labelcolor": PALETTE["text"],
            "axes.titlecolor": PALETTE["text"],
            "xtick.color": PALETTE["text"],
            "ytick.color": PALETTE["text"],
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.titlesize": 10,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 8.2,
            "ytick.labelsize": 8.2,
            "legend.fontsize": 8,
            "legend.title_fontsize": 8,
            "axes.linewidth": 0.8,
            "svg.fonttype": "none",
            "savefig.dpi": 320,
        }
    )


def to_frame() -> pd.DataFrame:
    rows = [r.__dict__ for r in DATA]
    df = pd.DataFrame(rows)
    df["family"] = np.where(df["variant"].eq("Baseline"), "baseline", "candidate")
    return df


def paired(df: pd.DataFrame) -> pd.DataFrame:
    base = df[df.variant == "Baseline"].set_index(["model", "task"])
    cand = df[df.variant != "Baseline"].set_index(["model", "task"])
    rows = []
    for idx, c in cand.iterrows():
        b = base.loc[idx]
        rows.append(
            {
                "model": idx[0],
                "task": idx[1],
                "task_short": c.task_short,
                "candidate": c.variant,
                "score_delta": c.score - b.score,
                "token_ratio": c.tokens / b.tokens,
                "request_ratio": np.nan if pd.isna(b.requests) or pd.isna(c.requests) else c.requests / b.requests,
                "baseline_score": b.score,
                "candidate_score": c.score,
                "baseline_tokens": b.tokens,
                "candidate_tokens": c.tokens,
                "baseline_requests": b.requests,
                "candidate_requests": c.requests,
                "baseline_turns": b.turns,
                "candidate_turns": c.turns,
                "note": "; ".join(x for x in [str(b.note), str(c.note)] if x and x != "nan"),
            }
        )
    return pd.DataFrame(rows)


def save(fig: plt.Figure, stem: str) -> None:
    for ext in ("png", "svg"):
        fig.savefig(OUT_DIR / f"{stem}.{ext}", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def add_caption(fig: plt.Figure, text: str) -> None:
    fig.text(0.01, 0.01, text, ha="left", va="bottom", fontsize=8.5, color="#4A4A4A")


def pct_change(candidate: float, baseline: float) -> float:
    return (candidate / baseline - 1.0) * 100.0


def pct_label(candidate: float, baseline: float) -> str:
    change = pct_change(candidate, baseline)
    if change <= 0:
        return f"{change:.0f}%"
    return f"+{change:.0f}%"


def value_k(value: float) -> str:
    return f"{value / 1000:.0f}K"


def is_native_candidate(row: pd.Series) -> bool:
    text = f"{row.get('candidate', '')} {row.get('note', '')}".lower()
    return "native" in text or "no-sro" in text or "mixed" in text


def plot_tradeoff(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8.7, 5.2))
    task_offsets = {"21": (2, 4), "12": (2, -11), "67": (2, 4), "86": (2, -11), "98": (2, 4)}

    for (model, task), group in df.groupby(["model", "task"], sort=False):
        group = group.sort_values("variant")
        b = group[group.variant == "Baseline"].iloc[0]
        c = group[group.variant != "Baseline"].iloc[0]
        color = PALETTE["qwen"] if model == "Qwen" else PALETTE["deepseek"]
        cand_marker = "D" if "native" in c.variant.lower() or "native" in str(c.note).lower() else "o"

        ax.scatter(b.tokens / 1000, b.score, s=46, marker="s", color=PALETTE["baseline"], zorder=3)
        ax.scatter(c.tokens / 1000, c.score, s=66, marker=cand_marker, color=color, edgecolor="white", linewidth=0.8, zorder=4)
        ax.annotate(
            "",
            xy=(c.tokens / 1000, c.score),
            xytext=(b.tokens / 1000, b.score),
            arrowprops={"arrowstyle": "->", "color": color, "lw": 1.4, "alpha": 0.72, "shrinkA": 4, "shrinkB": 4},
        )
        dx, dy = task_offsets[c.task_short]
        ax.annotate(
            f"{model[0]}-{c.task_short}",
            (c.tokens / 1000, c.score),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8.2,
            color=color,
        )

    ax.set_xscale("log")
    ax.set_xlabel("Total tokens, log scale (K)")
    ax.set_ylabel("Judge score")
    ax.set_title("SRO/Gate shifts trajectories toward fewer tokens while preserving or improving score on force-SRO shapes")
    ax.grid(True, axis="both", color=PALETTE["grid"], lw=0.6, alpha=0.8)
    ax.set_ylim(0.25, 1.05)
    ax.set_xlim(28, 1400)
    ax.legend(
        handles=[
            plt.Line2D([0], [0], marker="s", color="none", markerfacecolor=PALETTE["baseline"], label="Baseline", markersize=7),
            plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=PALETTE["qwen"], label="Qwen SRO/gate", markersize=8),
            plt.Line2D([0], [0], marker="o", color="none", markerfacecolor=PALETTE["deepseek"], label="DeepSeek SRO/gate", markersize=8),
            plt.Line2D([0], [0], marker="D", color="none", markerfacecolor=PALETTE["deepseek"], label="Gate/native or mixed", markersize=7),
        ],
        loc="lower right",
        frameon=True,
        framealpha=0.94,
    )
    add_caption(
        fig,
        "Caption: arrows point from baseline to current SRO/gate candidate. Diamonds mark native/mixed gate paths; Qwen task_00098 uses an old baseline and current gate retest, so treat as variance/mixed.",
    )
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    save(fig, "sro_gate_score_token_tradeoff")


def annotate_bar(ax: plt.Axes, bar: plt.Rectangle, text: str, *, dy: float = 3, color: str = "#111827") -> None:
    height = bar.get_height()
    if height <= 0 or np.isnan(height):
        return
    ax.annotate(
        text,
        xy=(bar.get_x() + bar.get_width() / 2, height),
        xytext=(0, dy),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=6.8,
        color=color,
        fontweight="semibold",
    )


def plot_combined_dashboard(pairs: pd.DataFrame) -> None:
    """Primary paper-style figure: Qwen on top, DeepSeek on bottom."""
    task_order = ["21", "12", "67", "86", "98"]
    fig, axes = plt.subplots(
        2,
        3,
        figsize=(11.2, 6.2),
        gridspec_kw={"width_ratios": [1.0, 1.35, 1.05], "hspace": 0.42, "wspace": 0.27},
    )
    width = 0.34

    for row_idx, model in enumerate(["Qwen", "DeepSeek"]):
        sub = pairs[pairs.model == model].copy()
        sub["order"] = sub.task_short.map({task: i for i, task in enumerate(task_order)})
        sub = sub.sort_values("order").reset_index(drop=True)
        x = np.arange(len(sub))
        model_color = PALETTE["qwen"] if model == "Qwen" else PALETTE["deepseek"]
        cand_colors = [model_color for _ in range(len(sub))]
        labels = [f"T{task}" for task in sub.task_short]

        # Score panel.
        ax = axes[row_idx, 0]
        b1 = ax.bar(
            x - width / 2,
            sub.baseline_score,
            width,
            color=PALETTE["baseline"],
            alpha=0.88,
            label="Baseline" if row_idx == 0 else None,
        )
        b2 = ax.bar(
            x + width / 2,
            sub.candidate_score,
            width,
            color=cand_colors,
            alpha=0.94,
            label="SRO/Gate" if row_idx == 0 else None,
        )
        for bar, (_, r) in zip(b2, sub.iterrows()):
            delta = r.candidate_score - r.baseline_score
            annotate_bar(ax, bar, f"{delta:+.2f}", dy=2, color=PALETTE["text"] if delta >= -0.02 else PALETTE["warn"])
        ax.set_ylim(0, 1.12)
        ax.set_title("Accuracy")
        ax.set_ylabel("Judge score")
        ax.set_xticks(x, labels)
        ax.grid(False)

        # Token panel, paired columns.
        ax = axes[row_idx, 1]
        base_k = sub.baseline_tokens / 1000
        cand_k = sub.candidate_tokens / 1000
        tb1 = ax.bar(x - width / 2, base_k, width, color=PALETTE["baseline"], alpha=0.88)
        tb2 = ax.bar(x + width / 2, cand_k, width, color=cand_colors, alpha=0.94)
        ax.set_yscale("log")
        ax.set_title("Token Cost")
        ax.set_ylabel("Tokens (K, log)")
        ax.set_xticks(x, labels)
        ax.grid(False)
        ymax = max(float(base_k.max()), float(cand_k.max()))
        ymin = max(20, min(float(base_k.min()), float(cand_k.min())) * 0.55)
        ax.set_ylim(ymin, ymax * 2.15)
        for bar, value in zip(tb1, sub.baseline_tokens):
            annotate_bar(ax, bar, value_k(value), dy=2, color=PALETTE["muted"])
        for bar, (_, r) in zip(tb2, sub.iterrows()):
            label = f"{value_k(r.candidate_tokens)}\n{pct_label(r.candidate_tokens, r.baseline_tokens)}"
            color = PALETTE["sro"] if r.candidate_tokens <= r.baseline_tokens else PALETTE["warn"]
            annotate_bar(ax, bar, label, dy=2, color=color)

        # Requests panel, paired columns.
        ax = axes[row_idx, 2]
        req_sub = sub.dropna(subset=["baseline_requests", "candidate_requests"]).copy()
        req_x = np.array([sub.index.get_loc(i) for i in req_sub.index])
        rb1 = ax.bar(req_x - width / 2, req_sub.baseline_requests, width, color=PALETTE["baseline"], alpha=0.88)
        rb2 = ax.bar(
            req_x + width / 2,
            req_sub.candidate_requests,
            width,
            color=[model_color for _ in range(len(req_sub))],
            alpha=0.94,
        )
        missing = sub[sub[["baseline_requests", "candidate_requests"]].isna().any(axis=1)]
        for idx in missing.index:
            ax.text(idx, 0.9, "n/a", ha="center", va="bottom", fontsize=8, color=PALETTE["muted"])
        ax.set_title("Trajectory Length")
        ax.set_ylabel("API requests")
        ax.set_xticks(x, labels)
        req_max = max(float(req_sub.baseline_requests.max()), float(req_sub.candidate_requests.max())) if len(req_sub) else 1
        ax.set_ylim(0, req_max * 1.42)
        ax.grid(False)
        for bar, value in zip(rb1, req_sub.baseline_requests):
            annotate_bar(ax, bar, f"{int(value)}", dy=2, color=PALETTE["muted"])
        for bar, (_, r) in zip(rb2, req_sub.iterrows()):
            label = f"{int(r.candidate_requests)}\n{pct_label(r.candidate_requests, r.baseline_requests)}"
            color = PALETTE["sro"] if r.candidate_requests <= r.baseline_requests else PALETTE["warn"]
            annotate_bar(ax, bar, label, dy=2, color=color)

        for ax in axes[row_idx, :]:
            ax.tick_params(axis="x", length=0)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color("#9CA3AF")
            ax.spines["bottom"].set_color("#9CA3AF")
            ax.spines["left"].set_linewidth(0.8)
            ax.spines["bottom"].set_linewidth(0.8)

    legend_handles = [
        plt.Line2D([0], [0], marker="s", color="none", markerfacecolor=PALETTE["baseline"], label="Baseline", markersize=9),
        plt.Line2D([0], [0], marker="s", color="none", markerfacecolor=PALETTE["qwen"], label="Qwen SRO/Gate", markersize=9),
        plt.Line2D([0], [0], marker="s", color="none", markerfacecolor=PALETTE["deepseek"], label="DeepSeek SRO/Gate", markersize=9),
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.965),
        ncol=4,
        frameon=False,
    )
    fig.text(0.5, 0.995, "Selective Sparse Reading: Accuracy, Cost, and Trajectory", ha="center", va="top", fontsize=12.5, fontweight="bold")
    fig.text(0.012, 0.695, "Qwen", ha="left", va="center", rotation=90, fontsize=11, fontweight="bold", color=PALETTE["qwen"])
    fig.text(0.012, 0.300, "DeepSeek", ha="left", va="center", rotation=90, fontsize=11, fontweight="bold", color=PALETTE["deepseek"])
    add_caption(
        fig,
        "Labels above SRO/Gate bars show score delta, token change, or request change vs baseline. Gate/native cases are included under the corresponding model color.",
    )
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.10, top=0.89, hspace=0.48, wspace=0.30)
    save(fig, "sro_gate_combined_metrics")


def plot_accuracy_compression_map(pairs: pd.DataFrame) -> None:
    """Direct benefit map: token compression vs accuracy change."""
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    sub = pairs.copy()
    sub["token_reduction"] = 1.0 - sub.candidate_tokens / sub.baseline_tokens
    sub["score_delta"] = sub.candidate_score - sub.baseline_score
    sub["task_label"] = sub.apply(lambda r: f"{r.model[0]}-T{r.task_short}", axis=1)

    ax.axhline(0, color="#9AA3AF", lw=1.0)
    ax.axvline(0, color="#9AA3AF", lw=1.0)
    ax.fill_between([0, 1], 0, 0.75, color="#E8F5EF", alpha=0.9, zorder=0)
    ax.fill_between([-1.2, 0], 0, 0.75, color="#FFF5E5", alpha=0.85, zorder=0)
    ax.fill_between([-1.2, 1], -0.30, 0, color="#F4F5F7", alpha=0.85, zorder=0)

    for model, group in sub.groupby("model", sort=False):
        color = PALETTE["qwen"] if model == "Qwen" else PALETTE["deepseek"]
        marker = "o" if model == "Qwen" else "s"
        sizes = 90 + np.clip(group.baseline_tokens / 10000, 0, 90)
        ax.scatter(
            group.token_reduction * 100,
            group.score_delta,
            s=sizes,
            color=color,
            marker=marker,
            edgecolor="white",
            linewidth=1.0,
            alpha=0.92,
            label=model,
            zorder=3,
        )
        for _, r in group.iterrows():
            dx = 5 if r.token_reduction >= 0 else -7
            ha = "left" if r.token_reduction >= 0 else "right"
            ax.annotate(
                f"T{r.task_short}",
                (r.token_reduction * 100, r.score_delta),
                xytext=(dx, 3),
                textcoords="offset points",
                ha=ha,
                va="bottom",
                fontsize=8.5,
                color=color,
                fontweight="semibold",
            )

    ax.text(50, 0.62, "ideal\nless token + higher score", ha="center", va="center", fontsize=9, color="#207A55", fontweight="semibold")
    ax.text(-55, 0.62, "accuracy helps,\nbut token regresses", ha="center", va="center", fontsize=9, color="#9A5A00", fontweight="semibold")
    ax.set_xlim(-75, 95)
    ax.set_ylim(-0.15, 0.72)
    ax.set_xlabel("Token reduction vs baseline (%)")
    ax.set_ylabel("Accuracy change vs baseline")
    ax.set_title("SRO/Gate Benefit Map: Accuracy vs Token Compression", pad=10, fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#9AA3AF")
    ax.spines["bottom"].set_color("#9AA3AF")
    ax.legend(loc="lower right", frameon=False)
    ax.grid(False)
    add_caption(
        fig,
        "Each point is one task/model pair. Right means fewer tokens; up means higher accuracy. Marker size reflects baseline token scale.",
    )
    fig.tight_layout(rect=(0, 0.065, 1, 1))
    save(fig, "sro_gate_accuracy_compression_map")


def outcome_status(score_delta: float, token_reduction: float) -> tuple[str, str, str]:
    """Return status label, face color, and text color for an executive summary cell."""
    if token_reduction >= 0.25 and score_delta >= -0.02:
        return "CLEAR WIN", "#DDF3E8", "#126B45"
    if token_reduction >= 0.05 and score_delta >= -0.08:
        return "TOKEN WIN\nQUALITY WATCH", "#FFF2CC", "#8A5A00"
    if token_reduction >= -0.05 and score_delta >= -0.02:
        return "SAFE GATE", "#E8EEF7", "#315A8A"
    if token_reduction < 0 and score_delta >= -0.02:
        return "NATIVE VARIANCE", "#FDE7D7", "#A64700"
    return "MIXED / WATCH", "#F3E8FF", "#6B35A8"


def plot_executive_outcome_board(pairs: pd.DataFrame) -> None:
    """Boss-facing summary: one glance shows whether score and token both improved."""
    selected = [
        ("task_21", "Long PDF QA"),
        ("task_00012", "Audit bundle"),
        ("task_00086", "Security audit"),
        ("task_00067", "Query/spec"),
        ("task_00098", "Diagnosis"),
    ]
    models = ["Qwen", "DeepSeek"]
    fig, ax = plt.subplots(figsize=(10.8, 5.6))
    ax.set_xlim(0, 3)
    ax.set_ylim(-0.35, len(selected) + 1.2)
    ax.axis("off")

    # Title and column headers.
    ax.text(0.05, len(selected) + 0.92, "SRO/Gate Executive Outcome Board", fontsize=17, fontweight="bold", color=PALETTE["text"], va="center")
    ax.text(
        0.05,
        len(selected) + 0.55,
        "Each cell shows accuracy change and token reduction vs baseline. Green means both quality and cost improved.",
        fontsize=9.2,
        color=PALETTE["muted"],
        va="center",
    )
    ax.text(0.24, len(selected) + 0.10, "Task type", fontsize=10, fontweight="semibold", color=PALETTE["muted"], ha="center")
    ax.text(1.25, len(selected) + 0.10, "Qwen", fontsize=12, fontweight="bold", color=PALETTE["qwen"], ha="center")
    ax.text(2.25, len(selected) + 0.10, "DeepSeek", fontsize=12, fontweight="bold", color=PALETTE["deepseek"], ha="center")

    pair_map = {(r.model, r.task): r for _, r in pairs.iterrows()}
    cell_w, cell_h = 0.82, 0.72
    for row_idx, (task, task_name) in enumerate(selected):
        y = len(selected) - row_idx - 0.35
        short = task.replace("task_000", "T").replace("task_", "T")
        ax.text(0.05, y + 0.16, short, fontsize=11, fontweight="bold", color=PALETTE["text"], ha="left", va="center")
        ax.text(0.05, y - 0.08, task_name, fontsize=8.5, color=PALETTE["muted"], ha="left", va="center")

        for col_idx, model in enumerate(models):
            x = 0.84 + col_idx
            r = pair_map.get((model, task))
            if r is None:
                status, face, text_color = "NO DATA", "#F3F4F6", PALETTE["muted"]
                score_text = "score n/a"
                token_text = "token n/a"
            else:
                token_reduction = 1.0 - r.candidate_tokens / r.baseline_tokens
                status, face, text_color = outcome_status(r.score_delta, token_reduction)
                score_text = f"Score {r.baseline_score:.2f}→{r.candidate_score:.2f}  ({r.score_delta:+.2f})"
                if token_reduction >= 0.005:
                    token_text = f"Cost saved {token_reduction * 100:.0f}%"
                elif token_reduction <= -0.005:
                    token_text = f"Cost up {abs(token_reduction) * 100:.0f}%"
                else:
                    token_text = "Cost ~ baseline"

            patch = FancyBboxPatch(
                (x, y - cell_h / 2),
                cell_w,
                cell_h,
                boxstyle="round,pad=0.018,rounding_size=0.045",
                facecolor=face,
                edgecolor="#FFFFFF",
                linewidth=1.2,
            )
            ax.add_patch(patch)
            ax.text(x + cell_w / 2, y + 0.17, status, fontsize=8.4, fontweight="bold", color=text_color, ha="center", va="center")
            ax.text(x + cell_w / 2, y - 0.04, score_text, fontsize=8.0, color=PALETTE["text"], ha="center", va="center")
            ax.text(x + cell_w / 2, y - 0.23, token_text, fontsize=9.3, fontweight="bold", color=text_color, ha="center", va="center")

    # Bottom legend.
    legend_items = [
        ("CLEAR WIN", "#DDF3E8", "#126B45", "enable SRO/gate"),
        ("TOKEN WIN / WATCH", "#FFF2CC", "#8A5A00", "cost down, quality varies"),
        ("SAFE GATE", "#E8EEF7", "#315A8A", "near-baseline behavior"),
        ("NATIVE VARIANCE", "#FDE7D7", "#A64700", "not SRO overhead"),
    ]
    lx = 0.05
    for label, face, color, meaning in legend_items:
        patch = FancyBboxPatch((lx, -0.16), 0.18, 0.16, boxstyle="round,pad=0.01,rounding_size=0.02", facecolor=face, edgecolor="none")
        ax.add_patch(patch)
        ax.text(lx + 0.22, -0.08, f"{label}: {meaning}", fontsize=7.8, color=color, ha="left", va="center")
        lx += 0.72

    fig.tight_layout()
    save(fig, "sro_gate_executive_outcome_board")


def plot_model_dashboard(pairs: pd.DataFrame, model: str, stem: str) -> None:
    sub = pairs[pairs.model == model].copy()
    sub["label"] = sub["task_short"].map(lambda x: f"task {x}")
    x = np.arange(len(sub))
    width = 0.36
    color = PALETTE["qwen"] if model == "Qwen" else PALETTE["deepseek"]

    fig, axes = plt.subplots(1, 3, figsize=(10.8, 3.9))

    # Score bars.
    ax = axes[0]
    ax.bar(x - width / 2, sub.baseline_score, width, color=PALETTE["baseline"], label="Baseline")
    ax.bar(x + width / 2, sub.candidate_score, width, color=color, label="SRO/gate")
    ax.set_title("Score")
    ax.set_ylim(0, 1.08)
    ax.set_ylabel("Judge score")
    ax.set_xticks(x, sub.label, rotation=0)
    ax.grid(True, axis="y", color=PALETTE["grid"], lw=0.6)

    for i, row in sub.reset_index(drop=True).iterrows():
        ax.text(i + width / 2, row.candidate_score + 0.025, f"{row.candidate_score:.2f}", ha="center", va="bottom", fontsize=8)

    # Token bars as candidate ratio.
    ax = axes[1]
    ratios = sub.token_ratio.to_numpy()
    bars = ax.bar(x, ratios, width=0.58, color=np.where(ratios <= 1, PALETTE["sro"], "#E69F00"))
    ax.axhline(1, color="#333333", lw=1)
    ax.set_title("Token cost")
    ax.set_ylabel("Candidate / baseline")
    ax.set_xticks(x, sub.label)
    ax.set_ylim(0, max(1.25, float(np.nanmax(ratios)) * 1.22))
    ax.grid(True, axis="y", color=PALETTE["grid"], lw=0.6)
    for bar, ratio in zip(bars, ratios):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.035, f"{ratio:.2f}x", ha="center", va="bottom", fontsize=8)

    # Requests bars, with known Qwen turns annotated.
    ax = axes[2]
    req_ratios = sub.request_ratio.to_numpy(dtype=float)
    valid = ~np.isnan(req_ratios)
    bars = ax.bar(x[valid], req_ratios[valid], width=0.58, color=np.where(req_ratios[valid] <= 1, PALETTE["sro"], "#E69F00"))
    ax.axhline(1, color="#333333", lw=1)
    ax.set_title("Requests / trajectory")
    ax.set_ylabel("Candidate / baseline requests")
    ax.set_xticks(x, sub.label)
    ax.set_ylim(0, max(1.25, float(np.nanmax(req_ratios[valid])) * 1.25))
    ax.grid(True, axis="y", color=PALETTE["grid"], lw=0.6)
    for bar, (_, row) in zip(bars, sub[valid].iterrows()):
        label = f"{row.request_ratio:.2f}x"
        if not pd.isna(row.baseline_turns) and not pd.isna(row.candidate_turns):
            label += f"\nturns {int(row.baseline_turns)}->{int(row.candidate_turns)}"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.04, label, ha="center", va="bottom", fontsize=8)
    for i, row in sub[~valid].iterrows():
        idx = sub.index.get_loc(i)
        ax.text(idx, 0.16, "req n/a", ha="center", va="center", fontsize=8, color="#666666")
        ax.bar(idx, 0.08, width=0.58, color="#CFCFCF", hatch="//", edgecolor="#999999")

    axes[0].legend(loc="lower left", frameon=True, framealpha=0.94)
    for ax in axes:
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)

    if model == "Qwen":
        caption = "Caption: task_00098 is mixed/variance: old baseline score/token is compared with the current native-gated retest; baseline requests were not provided."
    else:
        caption = "Caption: task_00098/00086/00067 candidates are gate/native or no-SRO-marker paths; interpret as selective-gate behavior, not forced SRO reading."
    fig.suptitle(f"{model}: score, token, and request trajectory relative to baseline", y=1.04, fontsize=13)
    add_caption(fig, caption)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    save(fig, stem)


def write_readme(pairs: pd.DataFrame) -> None:
    output_files = sorted(p.name for p in OUT_DIR.glob("sro_gate_*.png")) + sorted(p.name for p in OUT_DIR.glob("sro_gate_*.svg"))
    caveats = [
        "Data is manually curated from v3_dev.md and the latest user-provided result list; no additional benchmark values are inferred.",
        "Qwen task_00098 compares an old baseline score/token against the current gate retest; it is marked mixed/variance and has no baseline request count.",
        "DeepSeek task_00098, task_00086, and task_00067 candidate rows are gate/native or no-SRO-marker paths; they show selective gating rather than forced sparse reading.",
        "Turns are only available for Qwen task_21 and task_00012 in the supplied data; other trajectory panels use request count only.",
    ]
    summary = pairs[["model", "task", "candidate", "score_delta", "token_ratio", "request_ratio", "note"]].copy()
    summary["score_delta"] = summary["score_delta"].map(lambda x: f"{x:+.4f}")
    summary["token_ratio"] = summary["token_ratio"].map(lambda x: f"{x:.3f}x")
    summary["request_ratio"] = summary["request_ratio"].map(lambda x: "n/a" if pd.isna(x) else f"{x:.3f}x")
    lines = [
        "# SRO/Gate Figures",
        "",
        "Generated by:",
        "",
        "```bash",
        "python3 figures/plot_sro_gate_results.py",
        "```",
        "",
        "## Outputs",
        "",
    ]
    lines.extend(f"- `{name}`" for name in output_files)
    lines.extend(["", "## Caveats", ""])
    lines.extend(f"- {item}" for item in caveats)
    lines.extend(["", "## Pair Summary", "", markdown_table(summary), ""])
    (OUT_DIR / "README.md").write_text("\n".join(lines), encoding="utf-8")


def markdown_table(df: pd.DataFrame) -> str:
    columns = list(df.columns)
    rows = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(str(row[col]) for col in columns) + " |")
    return "\n".join(rows)


def main() -> None:
    setup_style()
    df = to_frame()
    pairs = paired(df)
    plot_combined_dashboard(pairs)
    plot_accuracy_compression_map(pairs)
    plot_executive_outcome_board(pairs)
    plot_tradeoff(df)
    plot_model_dashboard(pairs, "Qwen", "sro_gate_qwen_metrics")
    plot_model_dashboard(pairs, "DeepSeek", "sro_gate_deepseek_metrics")
    write_readme(pairs)
    for path in sorted(OUT_DIR.glob("sro_gate_*.*")) + [OUT_DIR / "README.md"]:
        print(path)


if __name__ == "__main__":
    main()
