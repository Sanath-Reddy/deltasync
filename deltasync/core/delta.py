"""Delta generation: describe the new file as COPY runs of old blocks plus LITERAL bytes."""

import struct
from dataclasses import dataclass

from deltasync.core.rolling import MASK, weak_checksum
from deltasync.core.signature import strong_hash

MAX_LITERAL = 64 * 1024


@dataclass(frozen=True)
class Copy:
    first_block: int
    count: int


@dataclass(frozen=True)
class Literal:
    data: bytes


class _Ops:
    def __init__(self):
        self.ops = []

    def copy(self, block):
        last = self.ops[-1] if self.ops else None
        if isinstance(last, Copy) and last.first_block + last.count == block:
            self.ops[-1] = Copy(last.first_block, last.count + 1)
        else:
            self.ops.append(Copy(block, 1))

    def literal(self, data, start, end):
        for i in range(start, end, MAX_LITERAL):
            self.ops.append(Literal(bytes(data[i : min(end, i + MAX_LITERAL)])))


def delta(sig, new, fixed_block=False):
    """Ops that rebuild new from the old file sig describes.

    new can be bytes or an mmap. fixed_block compares blocks by position only,
    the naive design kept as an ablation.
    """
    if fixed_block:
        return _delta_fixed(sig, new)
    b = sig.block_size
    entries = sig.entries
    n = len(new)
    ops = _Ops()

    tail = sig.short_tail
    full = len(entries) - (1 if tail else 0)
    index = {}
    for i in range(full):
        w, s = entries[i]
        index.setdefault(w, {}).setdefault(s, i)

    pos = lit = 0
    expected = -1  # the block after the last COPY; preferred so runs merge
    if index and n >= b:
        s = weak_checksum(new[0:b])
        a, bb = s & MASK, s >> 16
        while pos + b <= n:
            w = a | (bb << 16)
            hit = index.get(w)
            if hit is not None:
                strong = strong_hash(new[pos : pos + b])
                if 0 <= expected < full and entries[expected] == (w, strong):
                    j = expected
                else:
                    j = hit.get(strong)
                if j is not None:
                    ops.literal(new, lit, pos)
                    ops.copy(j)
                    expected = j + 1
                    pos += b
                    lit = pos
                    if pos + b <= n:
                        s = weak_checksum(new[pos : pos + b])
                        a, bb = s & MASK, s >> 16
                    continue
            if pos + b < n:
                out_byte = new[pos]
                a = (a - out_byte + new[pos + b]) & MASK
                bb = (bb - b * out_byte + a) & MASK
            pos += 1

    # The old file's short last block can only match the end of the new file.
    if tail and n - tail >= lit:
        end = new[n - tail : n]
        if entries[-1] == (weak_checksum(end), strong_hash(end)):
            ops.literal(new, lit, n - tail)
            ops.copy(len(entries) - 1)
            lit = n
    ops.literal(new, lit, n)
    return ops.ops


def _delta_fixed(sig, new):
    b = sig.block_size
    ops = _Ops()
    for i in range(0, len(new), b):
        block = new[i : i + b]
        j = i // b
        if j < len(sig.entries) and sig.entries[j] == (weak_checksum(block), strong_hash(block)):
            ops.copy(j)
        else:
            ops.literal(new, i, i + len(block))
    return ops.ops


# Wire and file encoding of ops: b"C" + first, count  |  b"L" + length + bytes.
_COPY = struct.Struct(">cQI")
_LIT = struct.Struct(">cI")


def encode_op(op):
    if isinstance(op, Copy):
        return _COPY.pack(b"C", op.first_block, op.count)
    return _LIT.pack(b"L", len(op.data)) + op.data


def decode_op(buf, offset=0):
    """Decode one op from buf at offset; returns (op, next offset)."""
    kind = buf[offset : offset + 1]
    if kind == b"C":
        _, first, count = _COPY.unpack_from(buf, offset)
        return Copy(first, count), offset + _COPY.size
    if kind == b"L":
        _, length = _LIT.unpack_from(buf, offset)
        start = offset + _LIT.size
        if start + length > len(buf):
            raise ValueError("truncated literal op")
        return Literal(bytes(buf[start : start + length])), start + length
    raise ValueError(f"unknown op type {kind!r}")


def decode_ops(buf):
    ops = []
    offset = 0
    while offset < len(buf):
        op, offset = decode_op(buf, offset)
        ops.append(op)
    return ops
