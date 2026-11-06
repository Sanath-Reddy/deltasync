import asyncio
import hashlib
import random

import pytest

from deltasync.client.client import AuthError, BaseChanged, Client, SyncError
from deltasync.protocol import messages
from deltasync.protocol.frames import encode_frame, read_frame
from deltasync.server.server import Server

TOKEN = "test-token"


@pytest.fixture
async def server(tmp_path):
    s = Server(tmp_path / "store", TOKEN, port=0)
    await s.start()
    yield s
    await s.close()


@pytest.fixture
def local(tmp_path):
    d = tmp_path / "local"
    d.mkdir()
    return d


def connect(server, token=TOKEN):
    return Client.connect("127.0.0.1", server.port, token)


def sha(data):
    return hashlib.sha256(data).hexdigest()


async def test_push_new_file_then_edit(server, local):
    rng = random.Random(1)
    data = rng.randbytes(500_000)
    (local / "f.bin").write_bytes(data)
    async with await connect(server) as c:
        r = await c.push(local / "f.bin", "dir/f.bin")
        assert r.literal_bytes == len(data)
        assert (server.root / "dir/f.bin").read_bytes() == data

        edited = data[:1000] + b"inserted" + data[1000:]
        (local / "f.bin").write_bytes(edited)
        r = await c.push(local / "f.bin", "dir/f.bin")
        assert r.sha256 == sha(edited)
        assert r.literal_bytes < 2 * 1024 + 8
    assert (server.root / "dir/f.bin").read_bytes() == edited


async def test_push_empty_and_odd_names(server, local):
    async with await connect(server) as c:
        for name, data in [("empty.bin", b""), ("with space.bin", b"x"), ("ünïcödé/файл.bin", b"y" * 5000)]:
            (local / "tmp").write_bytes(data)
            await c.push(local / "tmp", name)
            assert (server.root / name).read_bytes() == data


async def test_pull_new_and_existing(server, local):
    rng = random.Random(2)
    data = rng.randbytes(300_000)
    (server.root / "p.bin").write_bytes(data)
    async with await connect(server) as c:
        r = await c.pull("p.bin", local / "p.bin")
        assert (local / "p.bin").read_bytes() == data
        assert r.literal_bytes == len(data)

        (server.root / "p.bin").write_bytes(data[:-5000] + b"tail")
        r = await c.pull("p.bin", local / "p.bin")
        assert (local / "p.bin").read_bytes() == data[:-5000] + b"tail"
        assert r.literal_bytes < 2 * 1024
    assert not [p for p in local.iterdir() if p.name.endswith(".tmp")]


async def test_pull_missing_file(server, local):
    async with await connect(server) as c:
        with pytest.raises(SyncError, match="not found"):
            await c.pull("nope.bin", local / "nope.bin")
        assert not (local / "nope.bin").exists()


async def test_bad_token(server):
    with pytest.raises(AuthError):
        await connect(server, token="wrong")


async def test_bad_version(server):
    r, w = await asyncio.open_connection("127.0.0.1", server.port)
    w.write(encode_frame(messages.HELLO, messages.dump({"version": 999, "token": TOKEN})))
    await w.drain()
    kind, payload = await read_frame(r)
    assert kind == messages.ERROR
    assert await r.read() == b""
    w.close()


@pytest.mark.parametrize("path", ["../escape.bin", "/abs.bin", "a/../b.bin", "nul\0.bin"])
async def test_unsafe_paths_rejected(server, local, path):
    (local / "f").write_bytes(b"data")
    async with await connect(server) as c:
        with pytest.raises(SyncError, match="unsafe path"):
            await c.push(local / "f", path)
    assert not (server.root.parent / "escape.bin").exists()


async def test_50_concurrent_pushes(server, local):
    rng = random.Random(3)
    files = {}
    for i in range(50):
        data = rng.randbytes(rng.randint(0, 200_000))
        (local / f"{i}.bin").write_bytes(data)
        files[i] = data

    async def one(i):
        async with await connect(server) as c:
            return await c.push(local / f"{i}.bin", f"many/{i}.bin")

    results = await asyncio.gather(*(one(i) for i in range(50)))
    assert len(results) == 50
    for i, data in files.items():
        assert (server.root / f"many/{i}.bin").read_bytes() == data
    assert not list((server.root / "many").glob(".*.tmp"))


async def test_racing_pushes_on_one_file(server, local):
    base = random.Random(4).randbytes(100_000)
    (server.root / "race.bin").write_bytes(base)
    (local / "a").write_bytes(base + b"A")
    (local / "b").write_bytes(b"B" + base)

    async def b_wins():
        async with await connect(server) as cb:
            await cb.push(local / "b", "race.bin")

    async with await connect(server) as ca:
        with pytest.raises(BaseChanged):
            await ca.push(local / "a", "race.bin", after_signature=b_wins)
    assert (server.root / "race.bin").read_bytes() == b"B" + base
    assert not list(server.root.glob(".*.tmp"))


async def test_event_loop_not_blocked_by_delta(server, local):
    rng = random.Random(5)
    (server.root / "big.bin").write_bytes(rng.randbytes(8 << 20))
    # Unrelated local file: the server must roll over every byte of big.bin.
    (local / "big.bin").write_bytes(rng.randbytes(1 << 20))

    async def slow_pull():
        async with await connect(server) as c:
            await c.pull("big.bin", local / "big.bin")

    task = asyncio.create_task(slow_pull())
    await asyncio.sleep(0.3)
    worst = 0.0
    samples = 0
    while not task.done():
        samples += 1
        t = asyncio.get_running_loop().time()
        async with await connect(server):
            pass
        worst = max(worst, asyncio.get_running_loop().time() - t)
        await asyncio.sleep(0.05)
    await task
    assert samples >= 5, "the pull finished before the loop could be probed"
    assert worst < 0.1


async def test_idle_connection_closed(tmp_path):
    s = Server(tmp_path / "store", TOKEN, port=0, idle_timeout=0.2)
    await s.start()
    try:
        c = await Client.connect("127.0.0.1", s.port, TOKEN)
        await asyncio.sleep(0.5)
        assert await asyncio.wait_for(c.reader.read(), 1) == b""
        await c.close()
    finally:
        await s.close()


async def test_metrics_count_bytes(server, local):
    (local / "m").write_bytes(b"z" * 70_000)
    async with await connect(server) as c:
        await c.push(local / "m", "m")
        await c.push(local / "m", "m")
    m = server.metrics
    assert m.literal_bytes._value.get() == 70_000
    assert m.matched_bytes._value.get() == 70_000
    assert m.bytes_received._value.get() > 70_000
