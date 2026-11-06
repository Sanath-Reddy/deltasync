"""Fault injection: whatever dies, the target is the old file or the new one, never a mix."""

import asyncio
import hashlib
import os
import signal
import socket
import subprocess
import sys
import time
import uuid

import pytest

from bench.count_proxy import CountingProxy
from deltasync.client.client import Client, push
from deltasync.server.server import Server

TOKEN = "t"
MB = 1 << 20


@pytest.fixture
async def server(tmp_path):
    s = Server(tmp_path / "store", TOKEN, port=0, checkpoint_bytes=256 * 1024)
    await s.start()
    yield s
    await s.close()


def leftovers(root):
    return sorted(p.name for p in root.rglob(".*") if p.is_file())


async def push_until_cut(port, local, remote, transfer_id):
    with pytest.raises((ConnectionError, asyncio.IncompleteReadError, OSError)):
        async with await Client.connect("127.0.0.1", port, TOKEN) as c:
            await c.push(local, remote, transfer_id=transfer_id)


async def test_dropped_connection_resumes_from_durable_offset(server, tmp_path):
    new = os.urandom(8 * MB)
    (tmp_path / "new").write_bytes(new)
    tid = uuid.uuid4().hex
    async with CountingProxy("127.0.0.1", server.port) as proxy:
        proxy.cut_after_up = 4 * MB
        await push_until_cut(proxy.port, tmp_path / "new", "f.bin", tid)
        await asyncio.sleep(0.2)
        assert not (server.root / "f.bin").exists()
        assert any(n.endswith(".journal") for n in leftovers(server.root))

        proxy.cut_after_up = None
        proxy.reset()
        async with await Client.connect("127.0.0.1", proxy.port, TOKEN) as c:
            r = await c.push(tmp_path / "new", "f.bin", transfer_id=tid, resume=True)
        assert r.resumed_from >= 3 * MB
        assert proxy.up < 5.5 * MB
    assert (server.root / "f.bin").read_bytes() == new
    assert leftovers(server.root) == []


async def test_old_file_intact_after_drop_then_resume_with_base(server, tmp_path):
    old = os.urandom(6 * MB)
    (server.root / "f.bin").write_bytes(old)
    new = old[: 3 * MB] + os.urandom(3 * MB)
    (tmp_path / "new").write_bytes(new)
    tid = uuid.uuid4().hex
    async with CountingProxy("127.0.0.1", server.port) as proxy:
        proxy.cut_after_up = 2 * MB
        await push_until_cut(proxy.port, tmp_path / "new", "f.bin", tid)
        await asyncio.sleep(0.2)
        assert (server.root / "f.bin").read_bytes() == old
        proxy.cut_after_up = None
        async with await Client.connect("127.0.0.1", proxy.port, TOKEN) as c:
            r = await c.push(tmp_path / "new", "f.bin", transfer_id=tid, resume=True)
        assert r.resumed_from > 3 * MB
    assert (server.root / "f.bin").read_bytes() == new


async def test_corrupt_journal_falls_back_to_full_push(server, tmp_path):
    new = os.urandom(4 * MB)
    (tmp_path / "new").write_bytes(new)
    tid = uuid.uuid4().hex
    async with CountingProxy("127.0.0.1", server.port) as proxy:
        proxy.cut_after_up = 2 * MB
        await push_until_cut(proxy.port, tmp_path / "new", "f.bin", tid)
    await asyncio.sleep(0.2)
    (journal,) = server.root.glob(".*.journal")
    journal.write_text("{not json")
    async with await Client.connect("127.0.0.1", server.port, TOKEN) as c:
        r = await c.push(tmp_path / "new", "f.bin", transfer_id=tid, resume=True)
    assert r.resumed_from == 0
    assert (server.root / "f.bin").read_bytes() == new
    assert leftovers(server.root) == []


async def test_changed_base_restarts_from_zero(server, tmp_path):
    (server.root / "f.bin").write_bytes(os.urandom(2 * MB))
    new = os.urandom(4 * MB)
    (tmp_path / "new").write_bytes(new)
    tid = uuid.uuid4().hex
    async with CountingProxy("127.0.0.1", server.port) as proxy:
        proxy.cut_after_up = 2 * MB
        await push_until_cut(proxy.port, tmp_path / "new", "f.bin", tid)
    await asyncio.sleep(0.2)
    (tmp_path / "other").write_bytes(b"someone else's version")
    await push("127.0.0.1", server.port, TOKEN, tmp_path / "other", "f.bin")
    async with await Client.connect("127.0.0.1", server.port, TOKEN) as c:
        r = await c.push(tmp_path / "new", "f.bin", transfer_id=tid, resume=True)
    assert r.resumed_from == 0
    assert (server.root / "f.bin").read_bytes() == new


async def test_resume_with_unknown_id_is_a_full_push(server, tmp_path):
    (tmp_path / "new").write_bytes(b"abc" * 1000)
    async with await Client.connect("127.0.0.1", server.port, TOKEN) as c:
        r = await c.push(tmp_path / "new", "f.bin", transfer_id=uuid.uuid4().hex, resume=True)
    assert r.resumed_from == 0
    assert (server.root / "f.bin").read_bytes() == b"abc" * 1000


async def test_push_helper_resumes_automatically(server, tmp_path):
    new = os.urandom(6 * MB)
    (tmp_path / "new").write_bytes(new)
    async with CountingProxy("127.0.0.1", server.port) as proxy:
        proxy.cut_after_up = 3 * MB
        loop = asyncio.get_running_loop()
        loop.call_later(0.5, setattr, proxy, "cut_after_up", None)
        r = await push("127.0.0.1", proxy.port, TOKEN, tmp_path / "new", "f.bin", retry_delay=0.6)
    assert r.resumed_from > 0
    assert (server.root / "f.bin").read_bytes() == new


# Process-level faults --------------------------------------------------------

CLI = [sys.executable, "-m", "deltasync.client.cli"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port):
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port)).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(port)


def start_server(root, port, **env):
    p = subprocess.Popen(
        CLI + ["serve", "--root", str(root), "--port", str(port), "--token", TOKEN,
               "--checkpoint-bytes", str(256 * 1024)],
        env={**os.environ, **env},
    )
    wait_port(port)
    return p


async def test_client_killed_mid_push(server, tmp_path):
    old = os.urandom(MB)
    (server.root / "f.bin").write_bytes(old)
    (tmp_path / "new").write_bytes(os.urandom(64 * MB))
    async with CountingProxy("127.0.0.1", server.port, rate=16 * MB) as proxy:
        proc = await asyncio.create_subprocess_exec(
            *CLI, "push", str(tmp_path / "new"), "f.bin", "--server", f"127.0.0.1:{proxy.port}",
            "--token", TOKEN,
        )
        while proxy.up < 2 * MB:
            await asyncio.sleep(0.01)
        proc.send_signal(signal.SIGKILL)
        await proc.wait()
        await asyncio.sleep(0.3)
    assert (server.root / "f.bin").read_bytes() == old
    # The partial upload is kept only together with its journal, so it can resume.
    names = leftovers(server.root)
    assert len([n for n in names if n.endswith(".tmp")]) == len([n for n in names if n.endswith(".journal")])


@pytest.mark.parametrize("crash_at, survivor", [("before_replace", "old"), ("after_replace", "new")])
def test_server_killed_mid_commit(tmp_path, crash_at, survivor):
    root = tmp_path / "store"
    root.mkdir()
    old, new = os.urandom(2 * MB), os.urandom(2 * MB)
    (root / "f.bin").write_bytes(old)
    (tmp_path / "new").write_bytes(new)
    port = free_port()
    srv = start_server(root, port, DELTASYNC_CRASH_AT=crash_at)
    try:
        r = subprocess.run(CLI + ["push", str(tmp_path / "new"), "f.bin", "--server", f"127.0.0.1:{port}",
                                  "--token", TOKEN, "--no-retry"])
        assert r.returncode != 0
        assert srv.wait(5) != 0
    finally:
        srv.kill()
    data = (root / "f.bin").read_bytes()
    assert data == (old if survivor == "old" else new)
    assert hashlib.sha256(data).digest() in {hashlib.sha256(old).digest(), hashlib.sha256(new).digest()}


def test_resume_after_server_restart(tmp_path):
    root = tmp_path / "store"
    new = os.urandom(32 * MB)
    (tmp_path / "new").write_bytes(new)
    port = free_port()
    srv = start_server(root, port)

    async def first_attempt():
        async with CountingProxy("127.0.0.1", port, rate=32 * MB) as proxy:
            client = asyncio.create_task(push_until_cut(proxy.port, tmp_path / "new", "f.bin", "abc123"))
            while proxy.up < 8 * MB:
                await asyncio.sleep(0.01)
            srv.send_signal(signal.SIGKILL)
            await client

    asyncio.run(first_attempt())
    srv.wait()
    assert not (root / "f.bin").exists()
    srv = start_server(root, port)
    try:
        async def resume():
            async with await Client.connect("127.0.0.1", port, TOKEN) as c:
                return await c.push(tmp_path / "new", "f.bin", transfer_id="abc123", resume=True)

        r = asyncio.run(resume())
        assert r.resumed_from > 0
        assert (root / "f.bin").read_bytes() == new
    finally:
        srv.terminate()
        srv.wait()


def test_startup_removes_orphan_temp_files(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    (root / ".f.bin.deadbeef.tmp").write_bytes(b"partial")
    (root / ".g.bin.cafe.tmp").write_bytes(b"partial")
    (root / ".g.bin.cafe.journal").write_text("{}")
    Server(root, TOKEN, port=0)
    assert leftovers(root) == [".g.bin.cafe.journal", ".g.bin.cafe.tmp"]
