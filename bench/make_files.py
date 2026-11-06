"""Generate benchmark files and edited copies, plus a manifest describing each edit.

    python -m bench.make_files --sizes 10 100 [--out bench/data]
"""

import argparse
import json
import random
from pathlib import Path

MB = 1 << 20
DATA = Path(__file__).parent / "data"


def make_cases(orig, rng):
    """Return {case: (new_bytes, edits)}; edit offsets are in the original's coordinates."""
    n = len(orig)
    cases = {}

    # 1% changed in place, as 10 scattered regions.
    new = bytearray(orig)
    region = n // 1000
    edits = []
    for i in range(10):
        off = i * (n // 10) + n // 20
        new[off : off + region] = rng.randbytes(region)
        edits.append({"op": "replace", "offset": off, "length": region})
    cases["inplace_1pct"] = (bytes(new), edits)

    extra = rng.randbytes(n // 100)
    cases["append_1pct"] = (orig + extra, [{"op": "insert", "offset": n, "length": len(extra)}])

    ins = rng.randbytes(1024)
    cases["insert_1k_start"] = (ins + orig, [{"op": "insert", "offset": 0, "length": 1024}])

    mid = n // 2
    cases["delete_1m_middle"] = (
        orig[:mid] + orig[mid + MB :],
        [{"op": "delete", "offset": mid, "length": MB}],
    )

    cases["new_file"] = (rng.randbytes(n), [{"op": "new"}])
    return cases


def check(orig, new, edits):
    """True when new differs from orig exactly as the edits say."""
    if edits == [{"op": "new"}]:
        return len(new) == len(orig) and new != orig
    if all(e["op"] == "replace" for e in edits):
        if len(new) != len(orig):
            return False
        last = 0
        for e in sorted(edits, key=lambda e: e["offset"]):
            off, ln = e["offset"], e["length"]
            if new[last:off] != orig[last:off] or new[off : off + ln] == orig[off : off + ln]:
                return False
            last = off + ln
        return new[last:] == orig[last:]
    (e,) = edits
    off, ln = e["offset"], e["length"]
    if e["op"] == "insert":
        return len(new) == len(orig) + ln and new[:off] == orig[:off] and new[off + ln :] == orig[off:]
    if e["op"] == "delete":
        return len(new) == len(orig) - ln and new[:off] == orig[:off] and new[off:] == orig[off + ln :]
    return False


def generate(size_mb, out=DATA, seed=0):
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed + size_mb)
    orig = rng.randbytes(size_mb * MB)
    (out / f"{size_mb}mb.orig").write_bytes(orig)
    manifest = {}
    for case, (new, edits) in make_cases(orig, rng).items():
        name = f"{size_mb}mb.{case}"
        (out / name).write_bytes(new)
        manifest[name] = {"base": f"{size_mb}mb.orig", "edits": edits}
    path = out / "manifest.json"
    full = json.loads(path.read_text()) if path.exists() else {}
    full.update(manifest)
    path.write_text(json.dumps(full, indent=2))
    return manifest


def verify(out=DATA):
    manifest = json.loads((out / "manifest.json").read_text())
    bad = [
        name
        for name, m in manifest.items()
        if not check((out / m["base"]).read_bytes(), (out / name).read_bytes(), m["edits"])
    ]
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 100], help="sizes in MB")
    ap.add_argument("--out", type=Path, default=DATA)
    args = ap.parse_args()
    for s in args.sizes:
        generate(s, args.out)
        print(f"wrote {s} MB cases to {args.out}")
    bad = verify(args.out)
    print("manifest check:", "all cases match" if not bad else f"MISMATCH {bad}")
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
