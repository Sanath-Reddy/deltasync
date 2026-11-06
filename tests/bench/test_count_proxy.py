import asyncio
import random

from bench.count_proxy import CountingProxy
from bench.make_files import check, make_cases


async def echo_server():
    async def handle(r, w):
        while data := await r.read(65536):
            w.write(data[: len(data) // 2])  # reply with half, so the two counts differ
            await w.drain()
        w.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


async def test_counts_each_direction_exactly():
    server = await echo_server()
    port = server.sockets[0].getsockname()[1]
    async with CountingProxy("127.0.0.1", port) as proxy:
        r, w = await asyncio.open_connection("127.0.0.1", proxy.port)
        sent = 0
        received = 0
        for size in [1, 1000, 300_000]:
            w.write(b"x" * size)
            await w.drain()
            sent += size
            want = received + size // 2
            while received < want:
                received += len(await r.read(65536))
        w.write_eof()
        assert await r.read() == b""
        w.close()
        await asyncio.sleep(0.05)
        assert (proxy.up, proxy.down) == (sent, received)
    server.close()


async def test_delay_is_added_each_way():
    server = await echo_server()
    port = server.sockets[0].getsockname()[1]
    async with CountingProxy("127.0.0.1", port, delay=0.05) as proxy:
        r, w = await asyncio.open_connection("127.0.0.1", proxy.port)
        t = asyncio.get_running_loop().time()
        w.write(b"ab")
        await r.readexactly(1)
        assert asyncio.get_running_loop().time() - t >= 0.1
        w.close()
    server.close()


def test_edited_copies_match_manifest():
    rng = random.Random(0)
    orig = rng.randbytes(3 << 20)
    for case, (new, edits) in make_cases(orig, rng).items():
        assert check(orig, new, edits), case
    # And the checker catches a stray change.
    new, edits = make_cases(orig, rng)["insert_1k_start"]
    assert not check(orig, new[:-1] + b"\0", edits)
