"""Charts for the README, drawn from the median rows in bench/results/*.csv.

    python -m bench.plots
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from bench.common import RESULTS, medians  # noqa: E402

PLOTS = Path(__file__).parent / "plots"
# Fixed categorical order: each tool keeps its color in every chart.
TOOLS = [("deltasync", "DeltaSync", "#2a78d6"), ("rsync", "rsync", "#eb6834"),
         ("fixed_block", "Fixed blocks", "#1baf7a"), ("full_copy", "Full copy", "#eda100")]
CASES = [("inplace_1pct", "1% changed in place"), ("append_1pct", "1% appended"),
         ("insert_1k_start", "1 KB inserted at start"), ("delete_1m_middle", "1 MB deleted mid-file"),
         ("new_file", "Brand-new file")]
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ["top", "right", "left"]:
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def human(nbytes):
    for unit, size in [("MB", 1 << 20), ("KB", 1 << 10)]:
        if nbytes >= size:
            return f"{nbytes / size:.1f} {unit}" if nbytes < 10 * size else f"{nbytes / size:.0f} {unit}"
    return f"{nbytes:.0f} B"


def edit_patterns(size="100"):
    ours = medians(RESULTS / "edit_patterns.csv", ["size_mb", "case", "tool"])
    base = medians(RESULTS / "baseline.csv", ["size_mb", "case", "tool"])
    rows = {**ours, **base}
    fig, ax = plt.subplots(figsize=(8, 4.6), facecolor=SURFACE)
    style(ax)
    offsets = [-0.27, -0.09, 0.09, 0.27]
    for (tool, label, color), dy in zip(TOOLS, offsets):
        xs, ys = [], []
        for i, (case, _) in enumerate(CASES):
            r = rows.get((size, case, tool))
            if r:
                xs.append(float(r["bytes_total"]))
                ys.append(i + dy)
        ax.scatter(xs, ys, s=64, color=color, label=label, zorder=3, edgecolors=SURFACE, linewidths=2)
        if tool == "deltasync":
            for x, y in zip(xs, ys):
                ax.annotate(human(x), (x, y), xytext=(8, 0), textcoords="offset points",
                            va="center", fontsize=8.5, color=INK)
    ax.set_xscale("log")
    ax.set_yticks(range(len(CASES)), [c[1] for c in CASES], color=INK)
    ax.invert_yaxis()
    ax.set_xlabel(f"Bytes on the wire, both directions (log scale) · {size} MB file", color=MUTED)
    ax.set_title(f"Bytes sent to sync each edit of a {size} MB file", loc="left", color=INK, fontsize=12)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=4, frameon=False, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(PLOTS / f"edit_patterns_{size}mb.png", dpi=150)
    plt.close(fig)


def block_size():
    rows = medians(RESULTS / "block_size.csv", ["block_size"])
    order = ["700", "4096", "16384", "65536"]
    labels = ["700 B", "4 KB", "16 KB", "64 KB"]
    ys = [float(rows[(b,)]["bytes_total"]) / 1024 for b in order]
    sqrt_kb = float(rows[("sqrt",)]["bytes_total"]) / 1024
    fig, ax = plt.subplots(figsize=(7, 3.8), facecolor=SURFACE)
    style(ax)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    ax.plot(range(4), ys, color="#2a78d6", linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    for x, y in enumerate(ys):
        ax.annotate(f"{y:,.0f} KB", (x, y), xytext=(0, 10), textcoords="offset points",
                    ha="center", fontsize=9, color=INK)
    ax.axhline(sqrt_kb, color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    ax.annotate(f"√n rule (10 KB blocks): {sqrt_kb:,.0f} KB", (0.15, sqrt_kb), xytext=(0, -14),
                textcoords="offset points", ha="left", fontsize=9, color=MUTED)
    ax.set_xticks(range(4), labels, color=INK)
    ax.set_ylim(0, max(ys) * 1.2)
    ax.set_ylabel("Signature + literal bytes (KB)", color=MUTED)
    ax.set_title("10 small edits in a 100 MB file: total bytes by block size", loc="left", color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(PLOTS / "block_size.png", dpi=150)
    plt.close(fig)


def concurrency():
    rows = medians(RESULTS / "concurrency.csv", ["clients"])
    ns = sorted(rows, key=lambda k: int(k[0]))
    xs = [int(n[0]) for n in ns]
    ys = [float(rows[n]["p95_s"]) for n in ns]
    fig, ax = plt.subplots(figsize=(7, 3.8), facecolor=SURFACE)
    style(ax)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    ax.plot(xs, ys, color="#2a78d6", linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
    for x, y in zip(xs, ys):
        ax.annotate(f"{y:.2f} s", (x, y), xytext=(0, 10), textcoords="offset points",
                    ha="center", fontsize=9, color=INK)
    ax.set_xticks(xs, [str(x) for x in xs], color=INK)
    ax.set_ylim(0, max(ys) * 1.25)
    ax.set_xlabel("Clients pushing at once (10 MB file, 1% edit each)", color=MUTED)
    ax.set_ylabel("p95 completion time (s)", color=MUTED)
    ax.set_title("p95 sync time as concurrent clients grow", loc="left", color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(PLOTS / "concurrency.png", dpi=150)
    plt.close(fig)


def main():
    PLOTS.mkdir(exist_ok=True)
    edit_patterns("100")
    block_size()
    concurrency()
    print("wrote", sorted(p.name for p in PLOTS.glob("*.png")))


if __name__ == "__main__":
    main()
