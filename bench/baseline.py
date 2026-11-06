"""Baselines: full copy over plain TCP, and rsync, both measured at the counting proxy.

scp needs an SSH server, so the full copy is a raw TCP send of the whole file, which
is what scp's payload costs without SSH's framing. rsync runs as a local daemon
with --no-whole-file, so it does real delta transfer.

    python -m bench.baseline --sizes 10 100 --runs 5
"""

import argparse
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from bench.common import write_rows
from bench.count_proxy import ProxyThread
from bench.make_files import DATA

CASES = ["inplace_1pct", "append_1pct", "insert_1k_start", "delete_1m_middle", "new_file"]


class Sink:
    """TCP server that reads one connection to EOF."""

    def __init__(self):
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            conn, _ = self.sock.accept()
            with conn:
                while conn.recv(1 << 20):
                    pass
                conn.sendall(b"ok")


def full_copy(path, proxy):
    with socket.create_connection(("127.0.0.1", proxy.port)) as s, open(path, "rb") as f:
        s.sendfile(f)
        s.shutdown(socket.SHUT_WR)
        s.recv(2)


class RsyncDaemon:
    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ds-rsyncd-"))
        self.store = self.dir / "store"
        self.store.mkdir()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        conf = self.dir / "rsyncd.conf"
        conf.write_text(
            f"pid file = {self.dir}/pid\nuse chroot = no\nport = {self.port}\n"
            f"[m]\npath = {self.store}\nread only = no\n"
        )
        self.proc = subprocess.Popen(
            ["rsync", "--daemon", "--no-detach", f"--config={conf}", "--address=127.0.0.1"]
        )
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port)).close()
                break
            except OSError:
                time.sleep(0.1)

    def close(self):
        self.proc.terminate()
        self.proc.wait()
        shutil.rmtree(self.dir)


def rsync_push(daemon, base, new, proxy):
    target = daemon.store / "f"
    if base is None:
        target.unlink(missing_ok=True)
    else:
        shutil.copyfile(base, target)
    subprocess.run(
        ["rsync", "--stats", "--no-whole-file", "-q", str(new), f"rsync://127.0.0.1:{proxy.port}/m/f"],
        check=True,
    )
    assert target.read_bytes() == new.read_bytes()


def measure(fn, proxy):
    proxy.reset()
    t = time.perf_counter()
    fn()
    secs = time.perf_counter() - t
    time.sleep(0.05)  # let the relay finish counting
    return proxy.up, proxy.down, secs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 100])
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    rows = []
    sink = Sink()
    daemon = RsyncDaemon()
    try:
        with ProxyThread("127.0.0.1", sink.port) as sink_proxy, ProxyThread(
            "127.0.0.1", daemon.port
        ) as rsync_proxy:
            for size in args.sizes:
                base = DATA / f"{size}mb.orig"
                for case in CASES:
                    new = DATA / f"{size}mb.{case}"
                    n = new.stat().st_size
                    tools = {
                        "full_copy": (lambda: full_copy(new, sink_proxy), sink_proxy),
                        "rsync": (
                            lambda: rsync_push(daemon, None if case == "new_file" else base, new, rsync_proxy),
                            rsync_proxy,
                        ),
                    }
                    for tool, (fn, proxy) in tools.items():
                        for run in range(1, args.runs + 1):
                            up, down, secs = measure(fn, proxy)
                            rows.append({
                                "size_mb": size, "case": case, "tool": tool, "run": run,
                                "bytes_up": up, "bytes_down": down, "bytes_total": up + down,
                                "pct_of_file": round(100 * (up + down) / n, 3), "seconds": round(secs, 4),
                            })
                        print(size, case, tool, rows[-1]["bytes_total"], flush=True)
    finally:
        daemon.close()
    path = write_rows(
        "baseline", rows, ["size_mb", "case", "tool"],
        ["bytes_up", "bytes_down", "bytes_total", "pct_of_file", "seconds"],
    )
    print("wrote", path)


if __name__ == "__main__":
    main()
