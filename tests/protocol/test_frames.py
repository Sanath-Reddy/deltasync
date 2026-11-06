import asyncio
import struct

import pytest

from deltasync.protocol.frames import MAX_FRAME, FrameTooLarge, encode_frame, read_frame


def reader_with(*chunks, eof=True):
    r = asyncio.StreamReader()
    for c in chunks:
        r.feed_data(c)
    if eof:
        r.feed_eof()
    return r


def test_layout():
    assert encode_frame(7, b"abc") == struct.pack(">IB", 3, 7) + b"abc"
    assert MAX_FRAME == 1 << 20


async def test_one_frame():
    assert await read_frame(reader_with(encode_frame(1, b"hello"))) == (1, b"hello")


async def test_empty_payload():
    assert await read_frame(reader_with(encode_frame(2, b""))) == (2, b"")


async def test_frame_split_one_byte_at_a_time():
    raw = encode_frame(3, b"split across many reads")
    r = asyncio.StreamReader()
    task = asyncio.create_task(read_frame(r))
    for i in range(len(raw)):
        r.feed_data(raw[i : i + 1])
        await asyncio.sleep(0)
    assert await task == (3, b"split across many reads")


async def test_several_frames_in_one_read():
    r = reader_with(encode_frame(1, b"a") + encode_frame(2, b"bb") + encode_frame(3, b""))
    assert [await read_frame(r) for _ in range(3)] == [(1, b"a"), (2, b"bb"), (3, b"")]


async def test_max_size_frame_is_accepted():
    payload = b"x" * MAX_FRAME
    assert await read_frame(reader_with(encode_frame(4, payload))) == (4, payload)


async def test_oversized_frame_rejected_before_payload():
    # Only the header arrives: the reader must refuse without waiting for 1 MiB.
    r = reader_with(struct.pack(">IB", MAX_FRAME + 1, 1), eof=False)
    with pytest.raises(FrameTooLarge):
        await asyncio.wait_for(read_frame(r), timeout=1)


def test_encoding_oversized_frame_rejected():
    with pytest.raises(FrameTooLarge):
        encode_frame(1, b"x" * (MAX_FRAME + 1))


async def test_eof_mid_frame():
    raw = encode_frame(1, b"truncated")
    with pytest.raises(asyncio.IncompleteReadError):
        await read_frame(reader_with(raw[:-3]))
