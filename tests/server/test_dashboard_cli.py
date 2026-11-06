import os
import socket
import subprocess
import sys
import time

from prometheus_client import generate_latest

from deltasync.dashboard.app import create_app
from deltasync.server.metrics import Metrics


def test_dashboard_reads_server_metrics():
    m = Metrics()
    m.literal_bytes.inc(250)
    m.matched_bytes.inc(750)
    app = create_app("unused", fetch=lambda: generate_latest(m.registry).decode())
    data = app.test_client().get("/api/metrics").get_json()
    assert data["saved_pct"] == 75.0
    assert data["metrics"]["deltasync_literal_bytes_total"] == 250
    page = app.test_client().get("/").get_data(as_text=True)
    assert "75.0%" in page


def test_dashboard_survives_missing_server():
    def down():
        raise OSError("connection refused")

    page = create_app("unused", fetch=down).test_client().get("/").get_data(as_text=True)
    assert "unavailable" in page


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_cli_serve_push_pull(tmp_path):
    port = free_port()
    env = {**os.environ, "DELTASYNC_TOKEN": "t"}
    cli = [sys.executable, "-m", "deltasync.client.cli"]
    server = subprocess.Popen(cli + ["serve", "--root", str(tmp_path / "store"), "--port", str(port)], env=env)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port)).close()
                break
            except OSError:
                time.sleep(0.05)
        data = os.urandom(200_000)
        (tmp_path / "a b.bin").write_bytes(data)
        run = lambda *a: subprocess.run(cli + list(a) + ["--server", f"127.0.0.1:{port}"], env=env)
        assert run("push", str(tmp_path / "a b.bin"), "x/a b.bin").returncode == 0
        assert (tmp_path / "store/x/a b.bin").read_bytes() == data
        assert run("pull", "x/a b.bin", str(tmp_path / "back.bin")).returncode == 0
        assert (tmp_path / "back.bin").read_bytes() == data
        assert run("pull", "missing", str(tmp_path / "m")).returncode == 1
    finally:
        server.terminate()
        server.wait()
