"""DeltaSync benchmarks. Every number on the resume comes from a CSV this writes.

    python -m bench.run_bench all
    python -m bench.run_bench edits|blocks|speed|concurrency|network|resume [--runs 5]

The server runs as its own process; bytes are counted at bench/count_proxy.py.
"""

import argparse
import asyncio
import hashlib
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from bench.baseline import RsyncDaemon, rsync_push
from bench.common import RESULTS, write_rows
from bench.count_proxy import ProxyThread
from bench.make_files import DATA, MB
from deltasync.client.client import Client

TOKEN = "bench"
CASES = ["inplace_1pct", "append_1pct", "insert_1k_start", "delete_1m_middle", "new_file"]
WORK = DATA / "work"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProc:
    def __init__(self, *extra, env=None):
        WORK.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="store-", dir=WORK))
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "deltasync.client.cli", "serve", "--root", str(self.root),
             "--port", str(self.port), "--token", TOKEN, *extra],
            env={**os.environ, **(env or {})}, stdout=subprocess.DEVNULL,
        )
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", self.port)).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("server did not start")

    def peak_rss_mb(self):
        for line in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024
        return float("nan")

    def place(self, name, src):
        """Put a base file in the store. A hard link is safe: commits replace, never modify."""
        dst = self.root / name
        dst.unlink(missing_ok=True)
        if src is not None:
            os.link(src, dst)

    def close(self):
        self.proc.terminate()
        self.proc.wait()
        shutil.rmtree(self.root)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


async def _push(port, local, remote, **kw):
    async with await Client.connect("127.0.0.1", port, TOKEN) as c:
        return await c.push(local, remote, **kw)


def push(port, local, remote, **kw):
    return asyncio.run(_push(port, local, remote, **kw))


def same(a, b):
    return hashlib.sha256(Path(a).read_bytes()).digest() == hashlib.sha256(Path(b).read_bytes()).digest()


def timed(proxy, fn):
    proxy.reset()
    t = time.perf_counter()
    fn()
    secs = time.perf_counter() - t
    time.sleep(0.05)
    return proxy.up, proxy.down, secs


# Experiments -----------------------------------------------------------------


def exp_edits(runs, sizes=(10, 100), name="edit_patterns"):
    """Bytes on the wire per edit pattern: DeltaSync vs fixed-position blocks."""
    rows = []
    with ServerProc() as srv, ProxyThread("127.0.0.1", srv.port) as proxy:
        for size in sizes:
            base = DATA / f"{size}mb.orig"
            for case in CASES:
                new = DATA / f"{size}mb.{case}"
                n = new.stat().st_size
                for tool, fixed in [("deltasync", False), ("fixed_block", True)]:
                    for run in range(1, runs + 1):
                        srv.place("f", None if case == "new_file" else base)
                        up, down, secs = timed(proxy, lambda: push(proxy.port, new, "f", fixed_block=fixed))
                        assert same(srv.root / "f", new)
                        rows.append({
                            "size_mb": size, "case": case, "tool": tool, "run": run,
                            "bytes_up": up, "bytes_down": down, "bytes_total": up + down,
                            "pct_of_file": round(100 * (up + down) / n, 4), "seconds": round(secs, 4),
                        })
                    print("edits", size, case, tool, rows[-1]["bytes_total"], flush=True)
    return write_rows(name, rows, ["size_mb", "case", "tool"],
                      ["bytes_up", "bytes_down", "bytes_total", "pct_of_file", "seconds"])


def scattered_edits_file():
    path = DATA / "100mb.scattered10"
    if not path.exists():
        data = bytearray((DATA / "100mb.orig").read_bytes())
        rng = random.Random(10)
        for _ in range(10):
            off = rng.randrange(len(data) - 100)
            data[off : off + 100] = rng.randbytes(100)
        path.write_bytes(data)
    return path


def exp_blocks(runs):
    """Signature plus literal bytes against block size, for 10 small edits in 100 MB."""
    new = scattered_edits_file()
    base = DATA / "100mb.orig"
    rows = []
    for label in ["700", "4096", "16384", "65536", "sqrt"]:
        extra = [] if label == "sqrt" else ["--block-size", label]
        with ServerProc(*extra) as srv, ProxyThread("127.0.0.1", srv.port) as proxy:
            for run in range(1, runs + 1):
                srv.place("f", base)
                result = {}

                def go():
                    result["r"] = push(proxy.port, new, "f")

                up, down, secs = timed(proxy, go)
                assert same(srv.root / "f", new)
                rows.append({
                    "block_size": label, "run": run, "bytes_up": up, "bytes_down": down,
                    "bytes_total": up + down, "literal_bytes": result["r"].literal_bytes, "seconds": round(secs, 4),
                })
        print("blocks", label, rows[-1]["bytes_total"], flush=True)
    return write_rows("block_size", rows, ["block_size"],
                      ["bytes_up", "bytes_down", "bytes_total", "literal_bytes", "seconds"])


def large_files(size_mb=1024):
    """A 1 GB original and a 1%-changed copy, written in chunks to keep memory low."""
    orig = DATA / f"{size_mb}mb.orig"
    new = DATA / f"{size_mb}mb.inplace_1pct"
    if not (orig.exists() and new.exists()):
        rng = random.Random(size_mb)
        with open(orig, "wb") as f:
            for _ in range(size_mb // 64):
                f.write(rng.randbytes(64 * MB))
        shutil.copyfile(orig, new)
        n = size_mb * MB
        with open(new, "r+b") as f:
            for i in range(10):
                f.seek(i * (n // 10) + n // 20)
                f.write(rng.randbytes(n // 1000))
    return orig, new


def exp_speed(runs):
    """MB/s of new file scanned by the delta, Python reference vs C++ core."""
    from deltasync import native
    from deltasync.core import delta as py_delta
    from deltasync.core.files import mapped

    large_files()
    rows = []
    for size in [100, 1024]:
        orig, new = DATA / f"{size}mb.orig", DATA / f"{size}mb.inplace_1pct"
        with open(orig, "rb") as f:
            sig = native.signature_fileobj(f)
        for engine, iter_delta in [("python", py_delta.iter_delta), ("native", native.iter_delta)]:
            for run in range(1, runs + 1):
                with mapped(new) as data:
                    t = time.perf_counter()
                    for _ in iter_delta(sig, data):
                        pass
                    secs = time.perf_counter() - t
                rows.append({"size_mb": size, "engine": engine, "run": run, "seconds": round(secs, 4),
                             "mb_per_s": round(new.stat().st_size / MB / secs, 2)})
            print("speed", size, engine, rows[-1]["mb_per_s"], flush=True)
    return write_rows("delta_speed", rows, ["size_mb", "engine"], ["seconds", "mb_per_s"])


async def _many(port, n, new):
    loop = asyncio.get_running_loop()
    start = loop.time()

    async def one(i):
        async with await Client.connect("127.0.0.1", port, TOKEN, timeout=120) as c:
            await c.push(new, f"c{i}.bin")
        return loop.time() - start

    times = await asyncio.gather(*(one(i) for i in range(n)))
    return loop.time() - start, times


def exp_concurrency(runs, levels=(10, 50, 100, 200)):
    """N clients each pushing a 10 MB file with a 1% edit, all at once."""
    base, new = DATA / "10mb.orig", DATA / "10mb.inplace_1pct"
    rows = []
    for n in levels:
        for run in range(1, runs + 1):
            with ServerProc() as srv:
                for i in range(n):
                    srv.place(f"c{i}.bin", base)
                wall, times = asyncio.run(_many(srv.port, n, new))
                ok = all(same(srv.root / f"c{i}.bin", new) for i in range(n))
                assert ok
                times.sort()
                rows.append({
                    "clients": n, "run": run, "wall_s": round(wall, 4),
                    "p95_s": round(times[max(0, int(0.95 * n) - 1)], 4), "p50_s": round(statistics.median(times), 4),
                    "synced_mb_per_s": round(n * new.stat().st_size / MB / wall, 2),
                    "server_peak_rss_mb": round(srv.peak_rss_mb(), 1),
                })
        print("concurrency", n, rows[-1], flush=True)
    return write_rows("concurrency", rows, ["clients"],
                      ["wall_s", "p95_s", "p50_s", "synced_mb_per_s", "server_peak_rss_mb"])


NETNS_FLAG = "DELTASYNC_IN_NETNS"


def netem(ports, delay_ms=25, loss_pct=1.0):
    """Impair only traffic to and from these ports on loopback with tc netem.

    Needs CAP_NET_ADMIN, which the unprivileged user+network namespace gives us.
    Delay and loss apply to each direction, so the round trip is 2 * delay_ms.
    """
    def tc(*args):
        subprocess.run(["tc", *args], check=True)

    subprocess.run(["tc", "qdisc", "del", "dev", "lo", "root"], capture_output=True)
    tc("qdisc", "add", "dev", "lo", "root", "handle", "1:", "prio")
    tc("qdisc", "add", "dev", "lo", "parent", "1:3", "handle", "30:", "netem",
       "delay", f"{delay_ms}ms", "loss", f"{loss_pct}%")
    for port in ports:
        for field in ["dport", "sport"]:
            tc("filter", "add", "dev", "lo", "protocol", "ip", "parent", "1:0", "prio", "3",
               "u32", "match", "ip", field, str(port), "0xffff", "flowid", "1:3")


def exp_network(runs, delay_ms=25, losses=(0.0, 1.0)):
    """Wall-clock sync of a 1% edit to 100 MB over a 50 ms RTT link, DeltaSync vs rsync.

    Runs inside a user+network namespace (unshare -rn) so tc netem can add real
    delay and packet loss without root. The relay sits on an unimpaired hop and
    only counts bytes; netem impairs the relay-to-server hop.
    """
    if os.environ.get(NETNS_FLAG) != "1":
        subprocess.run(["unshare", "-rn", sys.executable, "-m", "bench.run_bench", "network",
                        "--runs", str(runs)], env={**os.environ, NETNS_FLAG: "1"}, check=True)
        return RESULTS / "bad_network.csv"
    subprocess.run(["ip", "link", "set", "lo", "up", "mtu", "1500"], check=True)
    base, new = DATA / "100mb.orig", DATA / "100mb.inplace_1pct"
    rows = []
    # As namespace root, rsyncd calls setgroups, which the namespace forbids; a nested
    # user namespace runs it as an ordinary user in the same network namespace.
    daemon = RsyncDaemon(prefix=["unshare", "-U", "--map-user=1000", "--map-group=1000"])
    try:
        with ServerProc() as srv, ProxyThread("127.0.0.1", srv.port) as dp, \
                ProxyThread("127.0.0.1", daemon.port) as rp:
            for loss in losses:
                netem([srv.port, daemon.port], delay_ms, loss)
                for run in range(1, runs + 1):
                    srv.place("f", base)
                    up, down, secs = timed(dp, lambda: push(dp.port, new, "f"))
                    assert same(srv.root / "f", new)
                    rows.append({"tool": "deltasync", "loss_pct": loss, "run": run, "seconds": round(secs, 4),
                                 "bytes_total": up + down})
                    up, down, secs = timed(rp, lambda: rsync_push(daemon, base, new, rp))
                    rows.append({"tool": "rsync", "loss_pct": loss, "run": run, "seconds": round(secs, 4),
                                 "bytes_total": up + down})
                print("network", loss, rows[-2:], flush=True)
    finally:
        daemon.close()
    for r in rows:
        r["rtt_ms"] = 2 * delay_ms
        r["method"] = "tc netem in user netns, mtu 1500"
    return write_rows("bad_network", rows, ["tool", "loss_pct"], ["seconds", "bytes_total"])


def exp_resume(runs):
    """Bytes re-sent after a push of a new 100 MB file is cut at 50%."""
    new = DATA / "100mb.new_file"
    n = new.stat().st_size
    rows = []
    with ServerProc("--checkpoint-bytes", str(4 * MB)) as srv, ProxyThread("127.0.0.1", srv.port) as proxy:
        for mode in ["with_resume", "without_resume"]:
            for run in range(1, runs + 1):
                srv.place("f", None)
                tid = uuid.uuid4().hex
                proxy.reset()
                proxy.cut_after_up = n // 2
                try:
                    push(proxy.port, new, "f", transfer_id=tid)
                    raise AssertionError("push was not cut")
                except (OSError, asyncio.IncompleteReadError):
                    pass
                proxy.cut_after_up = None
                time.sleep(0.3)
                resume = mode == "with_resume"
                result = {}

                def go():
                    result["r"] = push(proxy.port, new, "f", transfer_id=tid if resume else uuid.uuid4().hex,
                                       resume=resume)

                up, down, secs = timed(proxy, go)
                assert same(srv.root / "f", new)
                rows.append({"mode": mode, "run": run, "bytes_resent": up, "pct_of_file": round(100 * up / n, 2),
                             "resumed_from": result["r"].resumed_from, "seconds": round(secs, 4)})
            print("resume", mode, rows[-1], flush=True)
    return write_rows("resume", rows, ["mode"], ["bytes_resent", "pct_of_file", "resumed_from", "seconds"])


def smoke(runs=1):
    """CI check on 10 MB files: every edit case round-trips and an insert stays cheap."""
    from bench.make_files import generate

    if not (DATA / "10mb.orig").exists():
        generate(10)
    path = exp_edits(1, sizes=(10,), name="_smoke_edit_patterns")
    from bench.common import medians

    m = medians(path, ["size_mb", "case", "tool"])
    insert = float(m[("10", "insert_1k_start", "deltasync")]["pct_of_file"])
    fixed = float(m[("10", "insert_1k_start", "fixed_block")]["pct_of_file"])
    print(f"insert 1 KB into 10 MB: deltasync {insert}% of file, fixed-block {fixed}%")
    assert insert < 1.0 and fixed > 90.0
    return path


EXPERIMENTS = {
    "edits": exp_edits, "blocks": exp_blocks, "speed": exp_speed,
    "concurrency": exp_concurrency, "network": exp_network, "resume": exp_resume,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("experiment", choices=[*EXPERIMENTS, "all", "smoke"])
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    if args.experiment == "smoke":
        smoke()
        return
    names = list(EXPERIMENTS) if args.experiment == "all" else [args.experiment]
    for name in names:
        print("wrote", EXPERIMENTS[name](args.runs), flush=True)


if __name__ == "__main__":
    main()
