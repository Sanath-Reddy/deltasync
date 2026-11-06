"""Length-prefixed frames: 4-byte big-endian payload length, 1-byte type, payload.

TCP is a byte stream, so one read can hold half a frame or several; readexactly()
restores the boundaries.
"""

import struct

MAX_FRAME = 1 << 20
_HEADER = struct.Struct(">IB")


class FrameTooLarge(Exception):
    pass


def encode_frame(type, payload=b""):
    if len(payload) > MAX_FRAME:
        raise FrameTooLarge(f"payload of {len(payload)} bytes exceeds {MAX_FRAME}")
    return _HEADER.pack(len(payload), type) + payload


async def read_frame(reader):
    length, type = _HEADER.unpack(await reader.readexactly(_HEADER.size))
    if length > MAX_FRAME:
        raise FrameTooLarge(f"peer announced a {length}-byte frame")
    payload = await reader.readexactly(length) if length else b""
    return type, payload


def frame_size(payload):
    return _HEADER.size + len(payload)
