#!/usr/bin/env python3
"""Render the success-rate columns of PostEDA-Bench's main results table.

Source: https://arxiv.org/html/2605.06936v4#S3.T3
The adjacent CSV records the published means, without VRR/NIS or ablations.
Empty DRC cells mean ORFS-Agent is not applicable; they are not zero scores.
Model colors are inspired by the providers' visual identities and the supplied
template, and remain constant across task families and agent frameworks.

Install and run from the repository root:
    python -m pip install -r assets/figures/requirements-plot.txt
    python assets/figures/plot_success_rates.py

Produces two figures, each with two task-family panels, in PNG and vector PDF.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
import tempfile

# Headless rendering; do not require a writable home directory for font caches.
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "posteda-mpl"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator


HERE = Path(__file__).resolve().parent
SOURCE_URL = "https://arxiv.org/html/2605.06936v4#S3.T3"
METRICS = ("drc_essential", "drc_reasoning", "ppa_mono", "ppa_multi")
MODEL_COLORS = {
    "GPT-5": "#242424",
    "GPT-5-mini": "#555555",
    "Gemini-3-Flash-preview": "#4285F4",
    "Claude Opus 5": "#C9795D",
    "DeepSeek-V3.2": "#2459DC",
    "Qwen3.5-122B-A10B": "#7160CC",
    "Gemma-4-31B-it": "#2DA55D",
    "Qwen3.5-27B": "#8874D5",
    "Qwen3.5-9B": "#A18ADE",
}
FRAMEWORK_ORDER = {"ReAct": 0, "Proposer–Critic": 1, "ORFS-Agent": 2}
SUITES = {
    "drc": ("DRC-Bench", (("drc_essential", "DRC-Essential", 40),
                          ("drc_reasoning", "DRC-Reasoning", 30))),
    "ppa": ("PPA-Bench", (("ppa_mono", "PPA-Mono", 35),
                          ("ppa_multi", "PPA-Multi", 40))),
}


def load_scores(path: Path) -> list[dict]:
    """Read percentages, retaining missing observations as None."""
    rows = []
    seen = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        expected = {"model", "framework", *METRICS}
        if set(reader.fieldnames or ()) != expected:
            raise ValueError(f"CSV columns must be {sorted(expected)}")
        for raw in reader:
            model, framework = raw["model"], raw["framework"]
            if model not in MODEL_COLORS or framework not in FRAMEWORK_ORDER:
                raise ValueError(f"Unknown model/framework: {model} / {framework}")
            key = (model, framework)
            if key in seen:
                raise ValueError(f"Duplicate model/framework: {key}")
            seen.add(key)
            row = {"model": model, "framework": framework}
            for metric in METRICS:
                value = float(raw[metric]) if raw[metric].strip() else None
                if value is not None and (not math.isfinite(value) or not 0 <= value <= 100):
                    raise ValueError(f"Invalid percentage for {key}, {metric}: {value}")
                if (value is None) != (framework == "ORFS-Agent" and metric.startswith("drc_")):
                    raise ValueError(f"Unexpected missing/present score: {key}, {metric}")
                row[metric] = value
            rows.append(row)
    if not rows:
        raise ValueError("The score table is empty")
    return rows


def score_text(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def inside_text_color(color: str) -> str:
    rgb = mcolors.to_rgb(color)
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    luminance = sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    return "white" if luminance < 0.18 else "#161616"


def draw_panel(ax, rows: list[dict], metric: str, title: str, task_count: int) -> None:
    ranked = sorted(
        (row for row in rows if row[metric] is not None),
        key=lambda row: (-row[metric], FRAMEWORK_ORDER[row["framework"]], row["model"]),
    )
    values = [row[metric] for row in ranked]
    colors = [MODEL_COLORS[row["model"]] for row in ranked]
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#DCD9DF", linewidth=0.8, linestyle=(0, (2, 4)))
    bars = ax.bar(range(len(ranked)), values, width=0.73, color=colors, zorder=3)
    # Keep a zero baseline, but fit each panel to its observed range so that
    # low PPA-Multi scores remain visible. The footer states this explicitly.
    tick_step = 5 if max(values) <= 25 else 10
    upper_limit = min(100, max(tick_step, math.ceil(max(values) * 1.07 / tick_step) * tick_step))
    ax.set_ylim(0, upper_limit)
    ax.set_xlim(-0.62, len(ranked) - 0.38)
    ax.yaxis.set_major_locator(MultipleLocator(20 if upper_limit == 100 else tick_step))
    ax.set_ylabel("Success rate (%)", fontsize=10.5, labelpad=12, color="#50505A")
    ax.tick_params(axis="y", length=0, labelsize=9, colors="#77727F", pad=7)
    ax.tick_params(axis="x", length=0, pad=9)
    for spine in ("left", "right", "top"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#E4E0E7")
    ax.spines["bottom"].set_linewidth(0.8)
    ax.set_xticks(range(len(ranked)))
    ax.set_xticklabels(
        [f"{row['model']}\n{row['framework']}" for row in ranked],
        rotation=48, ha="right", rotation_mode="anchor", fontsize=9.3,
        linespacing=1.4, color="#34313D",
    )
    ax.set_title(title, loc="left", fontsize=16, fontweight="bold", pad=19, color="#24212B")
    ax.text(1, 1.065, f"{task_count} tasks  ·  {len(ranked)} configurations",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=9.5, color="#77727F")
    for bar, value, color in zip(bars, values, colors):
        # Small/zero results retain their true bar height and a readable label.
        inside = value >= upper_limit * 0.14
        y = value * 0.55 if inside else value + upper_limit * 0.02
        ax.text(bar.get_x() + bar.get_width() / 2, y, score_text(value),
                ha="center", va="center" if inside else "bottom",
                color=inside_text_color(color) if inside else "#34313D",
                fontsize=9.7, fontweight="semibold", zorder=4)


def render_suite(key: str, rows: list[dict], output_dir: Path, dpi: int) -> list[Path]:
    suite_title, panels = SUITES[key]
    fig, axes = plt.subplots(2, 1, figsize=(16.8, 12.0))
    fig.subplots_adjust(left=0.09, right=0.985, bottom=0.205, top=0.855, hspace=0.95)
    fig.patch.set_facecolor("#FFFCFE")
    for ax, (metric, title, count) in zip(axes, panels):
        ax.set_facecolor("#FFFCFE")
        draw_panel(ax, rows, metric, title, count)
    fig.text(0.065, 0.955, suite_title, fontsize=29, fontweight="bold", color="#24212B")
    fig.text(0.065, 0.923, "Success rate by model and agent framework", fontsize=13, color="#696373")
    fig.text(0.985, 0.96, "PostEDA-Bench", fontsize=12, ha="right", fontweight="bold", color="#75618F")
    fig.text(0.985, 0.937, "arXiv v4  ·  Table 3", fontsize=10, ha="right", color="#77727F")
    fig.text(0.065, 0.048, "Mean over five runs per task. Higher is better; panels use separate scales.",
             fontsize=9.3, color="#696373")
    fig.text(0.065, 0.026, "Source: arxiv.org/abs/2605.06936v4  ·  Main comparison; ablations excluded.",
             fontsize=8.8, color="#8A8490", url=SOURCE_URL)
    fig.text(0.985, 0.048, "Color identifies the model; the second label line identifies the framework.",
             fontsize=8.8, ha="right", color="#696373")
    if key == "drc":
        fig.text(0.985, 0.026, "ORFS-Agent is PPA-only and is omitted here.",
                 fontsize=8.8, ha="right", color="#8A8490")
    paths = []
    for extension in ("png", "pdf"):
        path = output_dir / f"{key}-success-rates.{extension}"
        metadata = {"Title": f"{suite_title}: success rates", "Subject": SOURCE_URL,
                    "Creator": "PostEDA-Bench / plot_success_rates.py", "CreationDate": None,
                    "ModDate": None} if extension == "pdf" else {"Source": SOURCE_URL}
        fig.savefig(path, dpi=dpi, facecolor=fig.get_facecolor(), metadata=metadata)
        paths.append(path)
    plt.close(fig)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=HERE / "main_table_v4_success_rates.csv")
    parser.add_argument("--output-dir", type=Path, default=HERE)
    parser.add_argument("--dpi", type=int, default=240, help="PNG resolution (default: 240)")
    args = parser.parse_args()
    if args.dpi < 72:
        parser.error("--dpi must be at least 72")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_scores(args.data)
    plt.rcParams.update({"font.family": "DejaVu Sans", "pdf.fonttype": 42,
                         "axes.unicode_minus": False, "savefig.transparent": False})
    for key in SUITES:
        for path in render_suite(key, rows, args.output_dir, args.dpi):
            print(path)


if __name__ == "__main__":
    main()
