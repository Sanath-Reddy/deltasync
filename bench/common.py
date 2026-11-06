"""Helpers shared by the benchmark scripts: environment info and CSV output."""

import csv
import platform
import statistics
import subprocess
from pathlib import Path

RESULTS = Path(__file__).parent / "results"


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def commit_hash():
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=Path(__file__).parent, check=True,
        )
        dirty = subprocess.run(
            # Result files are written by the benchmarks themselves, so they don't count.
            ["git", "status", "--porcelain", "--untracked-files=no", "--", ":/", ":(top,exclude)bench/results"],
            capture_output=True, text=True, cwd=Path(__file__).parent,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


ENV = {"cpu": cpu_model(), "python": platform.python_version(), "commit": commit_hash()}


def write_rows(name, rows, key, metrics):
    """Write every run plus a median row per group to bench/results/<name>.csv.

    rows: dicts with the key fields, a "run" number and the metric fields.
    """
    RESULTS.mkdir(exist_ok=True)
    groups = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in key), []).append(r)
    out = []
    for g, runs in groups.items():
        out.extend(runs)
        med = dict(zip(key, g))
        med["run"] = "median"
        for m in metrics:
            med[m] = statistics.median(r[m] for r in runs)
        for k, v in runs[0].items():
            med.setdefault(k, v)
        out.append(med)
    fields = list(key) + ["run"] + list(metrics)
    fields += [k for k in out[0] if k not in fields]
    fields += list(ENV)
    path = RESULTS / f"{name}.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in out:
            w.writerow({**r, **ENV})
    return path


def medians(path, key):
    with Path(path).open() as f:
        return {tuple(r[k] for k in key): r for r in csv.DictReader(f) if r["run"] == "median"}
