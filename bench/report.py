"""Write the README results tables from the median rows in bench/results/*.csv.

    python -m bench.report          # rewrite the tables in README.md
    python -m bench.report --check  # fail if README.md does not match the CSVs
"""

import argparse
import csv
import sys
from pathlib import Path

from bench.common import RESULTS, medians

README = Path(__file__).parent.parent / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"
CASES = [("inplace_1pct", "1% changed in place"), ("append_1pct", "1% appended"),
         ("insert_1k_start", "1 KB inserted at start"), ("delete_1m_middle", "1 MB deleted mid-file"),
         ("new_file", "Brand-new file")]


def human(nbytes):
    nbytes = float(nbytes)
    if nbytes >= 1 << 20:
        return f"{nbytes / (1 << 20):.1f} MB"
    if nbytes >= 1 << 10:
        return f"{nbytes / (1 << 10):.1f} KB"
    return f"{nbytes:.0f} B"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def env_line():
    with (RESULTS / "edit_patterns.csv").open() as f:
        r = next(csv.DictReader(f))
    return f"Measured on {r['cpu']}, Python {r['python']}, commit `{r['commit']}`. Medians of 5 runs."


def edits_section():
    ours = medians(RESULTS / "edit_patterns.csv", ["size_mb", "case", "tool"])
    base = medians(RESULTS / "baseline.csv", ["size_mb", "case", "tool"])
    rows = {**ours, **base}
    parts = []
    for size in ["100", "10"]:
        body = []
        for case, label in CASES:
            cells = [label]
            for tool in ["deltasync", "rsync", "fixed_block", "full_copy"]:
                r = rows[(size, case, tool)]
                cells.append(f"{human(r['bytes_total'])} ({float(r['pct_of_file']):.2f}%)")
            body.append(cells)
        parts.append(f"**{size} MB file**, bytes on the wire in both directions (% of the new file's size):\n\n"
                     + table(["Edit", "DeltaSync", "rsync", "Fixed blocks", "Full copy"], body))
    return "\n\n".join(parts)


def blocks_section():
    rows = medians(RESULTS / "block_size.csv", ["block_size"])
    names = {"700": "700 B", "4096": "4 KB", "16384": "16 KB", "65536": "64 KB", "sqrt": "√n rule (10 KB)"}
    body = [[names[b], human(rows[(b,)]["bytes_total"]), human(rows[(b,)]["literal_bytes"])] for b in names]
    return table(["Block size", "Total on the wire", "Of which literal data"], body)


def speed_section():
    rows = medians(RESULTS / "delta_speed.csv", ["size_mb", "engine"])
    body = []
    for size, label in [("100", "100 MB"), ("1024", "1 GB")]:
        py, cc = float(rows[(size, "python")]["mb_per_s"]), float(rows[(size, "native")]["mb_per_s"])
        body.append([label, f"{py:,.0f} MB/s", f"{cc:,.0f} MB/s", f"{cc / py:.1f}×"])
    return table(["File (1% changed in place)", "Python", "C++", "Speed-up"], body)


def concurrency_section():
    rows = medians(RESULTS / "concurrency.csv", ["clients"])
    body = []
    for k in sorted(rows, key=lambda k: int(k[0])):
        r = rows[k]
        body.append([k[0], f"{float(r['p95_s']):.2f} s", f"{float(r['synced_mb_per_s']):,.0f} MB/s",
                     f"{float(r['server_peak_rss_mb']):,.0f} MB"])
    return table(["Clients at once", "p95 completion", "File data synced per second", "Server peak memory"], body)


def network_section():
    rows = medians(RESULTS / "bad_network.csv", ["tool", "loss_pct"])
    body = []
    for loss in sorted({k[1] for k in rows}, key=float):
        body.append([f"{float(loss):g}%"] + [f"{float(rows[(t, loss)]['seconds']):.2f} s" for t in ["deltasync", "rsync"]]
                    + [human(rows[(t, loss)]["bytes_total"]) for t in ["deltasync", "rsync"]])
    return table(["Packet loss", "DeltaSync time", "rsync time", "DeltaSync bytes", "rsync bytes"], body)


def resume_section():
    rows = medians(RESULTS / "resume.csv", ["mode"])
    body = [[name, human(rows[(m,)]["bytes_resent"]), f"{float(rows[(m,)]['pct_of_file']):.0f}%"]
            for m, name in [("with_resume", "With resume"), ("without_resume", "Without resume")]]
    return table(["After reconnecting", "Bytes re-sent", "% of file"], body)


def render():
    return "\n\n".join([
        env_line(),
        "### Bytes on the wire by edit pattern\n\n" + edits_section()
        + "\n\n![Bytes per edit pattern](bench/plots/edit_patterns_100mb.png)",
        "### Block size (100 MB file, 10 scattered 100-byte edits)\n\n" + blocks_section()
        + "\n\n![Block size sweep](bench/plots/block_size.png)",
        "### Delta speed: Python reference vs C++ core\n\n" + speed_section(),
        "### Concurrency (each client pushes a 10 MB file with a 1% edit)\n\n" + concurrency_section()
        + "\n\n![p95 by client count](bench/plots/concurrency.png)",
        "### Bad network: 50 ms round trip, with and without 1% loss (100 MB file, 1% edit)\n\n"
        + network_section(),
        "### Resume (100 MB new file, cut at 50%)\n\n" + resume_section(),
    ])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = README.read_text()
    a, b = text.index(START) + len(START), text.index(END)
    new = text[:a] + "\n" + render() + "\n" + text[b:]
    if args.check:
        if new != text:
            print("README results do not match bench/results; run python -m bench.report", file=sys.stderr)
            sys.exit(1)
        print("README results match bench/results")
        return
    README.write_text(new)
    print("updated", README)


if __name__ == "__main__":
    main()
