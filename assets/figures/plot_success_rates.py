#!/usr/bin/env python3
"""Render the success-rate columns of PostEDA-Bench's main results table.

Source: https://arxiv.org/html/2605.06936v4#S3.T3
The adjacent CSV records the published means, without VRR/NIS or ablations.
Empty DRC cells mean ORFS-Agent is not applicable; they are not zero scores.
Model colors are inspired by the providers' visual identities and the supplied
template, and remain constant across task families and agent frameworks.
Bundled model logos come from Lobe Icons; see model-logos/sources.json and
model-logos/LICENSE. Rendering does not require a network connection.

Install and run from the repository root:
    python -m pip install -r assets/figures/requirements-plot.txt
    python assets/figures/plot_success_rates.py

Produces two figures, each with two task-family panels, in PNG and vector PDF.
"""

from __future__ import annotations

import argparse
import csv
from functools import lru_cache
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
from matplotlib.offsetbox import AnnotationBbox, OffsetImage
from matplotlib.patches import PathPatch
from matplotlib.path import Path as PlotPath
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
MODEL_LOGOS = {
    "GPT-5": "openai",
    "GPT-5-mini": "openai",
    "Gemini-3-Flash-preview": "gemini",
    "Claude Opus 5": "claude",
    "DeepSeek-V3.2": "deepseek",
    "Qwen3.5-122B-A10B": "qwen",
    "Gemma-4-31B-it": "gemma",
    "Qwen3.5-27B": "qwen",
    "Qwen3.5-9B": "qwen",
}
FRAMEWORK_ORDER = {"ReAct": 0, "Proposer–Critic": 1, "ORFS-Agent": 2}
SUITES = {
    "drc": ("DRC-Bench", (("drc_essential", "DRC-Essential"),
                          ("drc_reasoning", "DRC-Reasoning"))),
    "ppa": ("PPA-Bench", (("ppa_mono", "PPA-Mono"),
                          ("ppa_multi", "PPA-Multi"))),
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


def rounded_bar(ax, x: int, height: float, color: str, width: float = 0.73) -> None:
    """Round all four corners in physical points, preserving the score height."""
    if height == 0:
        return
    pixels_x = ax.bbox.width / (ax.get_xlim()[1] - ax.get_xlim()[0])
    pixels_y = ax.bbox.height / (ax.get_ylim()[1] - ax.get_ylim()[0])
    radius = min(4.5 * ax.figure.dpi / 72, width * pixels_x / 2, height * pixels_y / 2)
    rx, ry = radius / pixels_x, radius / pixels_y
    left, right = x - width / 2, x + width / 2
    k = 0.5522847498  # Cubic Bezier approximation of a quarter circle.
    vertices = [
        (left + rx, 0), (right - rx, 0),
        (right - rx + k * rx, 0), (right, ry - k * ry), (right, ry),
        (right, height - ry),
        (right, height - ry + k * ry), (right - rx + k * rx, height), (right - rx, height),
        (left + rx, height),
        (left + rx - k * rx, height), (left, height - ry + k * ry), (left, height - ry),
        (left, ry),
        (left, ry - k * ry), (left + rx - k * rx, 0), (left + rx, 0),
        (left + rx, 0),
    ]
    codes = ([PlotPath.MOVETO, PlotPath.LINETO] + [PlotPath.CURVE4] * 3
             + [PlotPath.LINETO] + [PlotPath.CURVE4] * 3
             + [PlotPath.LINETO] + [PlotPath.CURVE4] * 3
             + [PlotPath.LINETO] + [PlotPath.CURVE4] * 3 + [PlotPath.CLOSEPOLY])
    ax.add_patch(PathPatch(PlotPath(vertices, codes), facecolor=color, edgecolor="none", zorder=3))


@lru_cache(maxsize=None)
def logo_image(name: str):
    return plt.imread(HERE / "model-logos" / f"{name}.png")


def add_model_logo(ax, x: int, model: str) -> None:
    image = logo_image(MODEL_LOGOS[model])
    icon = OffsetImage(image, zoom=24 / max(image.shape[:2]), interpolation="antialiased")
    ax.add_artist(AnnotationBbox(
        icon, (x, 0), xycoords=("data", "axes fraction"),
        xybox=(0, -20), boxcoords="offset points", frameon=False,
        box_alignment=(0.5, 0.5), pad=0, annotation_clip=False,
    ))


def draw_panel(ax, rows: list[dict], metric: str, title: str) -> None:
    ranked = sorted(
        (row for row in rows if row[metric] is not None),
        key=lambda row: (-row[metric], FRAMEWORK_ORDER[row["framework"]], row["model"]),
    )
    values = [row[metric] for row in ranked]
    colors = [MODEL_COLORS[row["model"]] for row in ranked]
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#DCD9DF", linewidth=0.8, linestyle=(0, (2, 4)))
    # Keep a zero baseline, but fit each panel to its observed range so that
    # low PPA-Multi scores remain visible; tick labels show each panel's scale.
    tick_step = 5 if max(values) <= 25 else 10
    upper_limit = min(100, max(tick_step, math.ceil(max(values) * 1.07 / tick_step) * tick_step))
    ax.set_ylim(0, upper_limit)
    ax.set_xlim(-0.62, len(ranked) - 0.38)
    for x, (row, value, color) in enumerate(zip(ranked, values, colors)):
        rounded_bar(ax, x, value, color)
        add_model_logo(ax, x, row["model"])
    ax.yaxis.set_major_locator(MultipleLocator(20 if upper_limit == 100 else tick_step))
    ax.set_ylabel("Success rate (%)", fontsize=12.5, fontweight="bold", labelpad=12, color="#50505A")
    ax.tick_params(axis="y", length=0, labelsize=11.5, colors="#68616E", pad=7)
    ax.tick_params(axis="x", length=0, pad=44)
    plt.setp(ax.get_yticklabels(), fontweight="bold")
    for spine in ("left", "right", "top"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#E4E0E7")
    ax.spines["bottom"].set_linewidth(0.8)
    ax.set_xticks(range(len(ranked)))
    ax.set_xticklabels(
        [f"{row['model']}\n{row['framework']}" for row in ranked],
        rotation=48, ha="right", rotation_mode="anchor", fontsize=11.5,
        linespacing=1.4, fontweight="bold", color="#34313D",
    )
    ax.set_title(title, loc="left", fontsize=19, fontweight="heavy", pad=19, color="#24212B")
    for x, (value, color) in enumerate(zip(values, colors)):
        # Small/zero results retain their true bar height and a readable label.
        inside = value >= upper_limit * 0.14
        y = value * 0.55 if inside else value + upper_limit * 0.02
        ax.text(x, y, score_text(value),
                ha="center", va="center" if inside else "bottom",
                color=inside_text_color(color) if inside else "#34313D",
                fontsize=12, fontweight="bold", zorder=4)


def render_suite(key: str, rows: list[dict], output_dir: Path, dpi: int) -> list[Path]:
    suite_title, panels = SUITES[key]
    fig, axes = plt.subplots(2, 1, figsize=(16.8, 12.0))
    fig.subplots_adjust(left=0.09, right=0.985, bottom=0.22, top=0.855, hspace=1.2)
    fig.patch.set_facecolor("#FFFCFE")
    for ax, (metric, title) in zip(axes, panels):
        ax.set_facecolor("#FFFCFE")
        draw_panel(ax, rows, metric, title)
    fig.text(0.065, 0.955, suite_title, fontsize=32, fontweight="heavy", color="#24212B")
    fig.text(0.065, 0.923, "Success rate by model and agent framework", fontsize=14.5,
             fontweight="bold", color="#696373")
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
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.weight": "bold", "pdf.fonttype": 42,
                         "axes.unicode_minus": False, "savefig.transparent": False})
    for key in SUITES:
        for path in render_suite(key, rows, args.output_dir, args.dpi):
            print(path)


if __name__ == "__main__":
    main()
