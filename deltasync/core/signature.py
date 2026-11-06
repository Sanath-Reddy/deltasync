"""Block signatures: one 20-byte (weak, strong) entry per block of the old file."""

import hashlib
import math
import struct
from dataclasses import dataclass, field

import xxhash

from deltasync.core.rolling import weak_checksum

ENTRY_SIZE = 20
MIN_BLOCK = 1024
MAX_BLOCK = 65536

_HEADER = struct.Struct(">IQ32sI")  # block size, file size, SHA-256, entry count
_ENTRY = struct.Struct(">I16s")


def block_size_for(n):
    """About sqrt(n), clamped to 1-64 KB."""
    return min(MAX_BLOCK, max(MIN_BLOCK, math.isqrt(n)))


def strong_hash(block):
    return xxhash.xxh3_128_digest(block)


@dataclass
class Signature:
    block_size: int
    file_hash: bytes
    entries: list = field(default_factory=list)
    file_size: int = 0

    @property
    def short_tail(self):
        """Length of a last block shorter than block_size, or 0."""
        return self.file_size % self.block_size


def signature(data, block_size=None):
    b = block_size or block_size_for(len(data))
    entries = []
    for i in range(0, len(data), b):
        block = data[i : i + b]
        entries.append((weak_checksum(block), strong_hash(block)))
    return Signature(b, hashlib.sha256(data).digest(), entries, len(data))


def signature_file(path, block_size=None):
    """Signature of a file on disk, read one block at a time."""
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        f.seek(0)
        b = block_size or block_size_for(size)
        h = hashlib.sha256()
        entries = []
        while block := f.read(b):
            h.update(block)
            entries.append((weak_checksum(block), strong_hash(block)))
    return Signature(b, h.digest(), entries, size)


def encode_header(sig):
    return _HEADER.pack(sig.block_size, sig.file_size, sig.file_hash, len(sig.entries))


def encode_entries(entries):
    return b"".join(_ENTRY.pack(w, s) for w, s in entries)


def encode_signature(sig):
    return encode_header(sig) + encode_entries(sig.entries)


def decode_header(raw):
    block_size, file_size, file_hash, count = _HEADER.unpack_from(raw)
    return Signature(block_size, file_hash, [], file_size), count


def decode_entries(raw):
    return [_ENTRY.unpack_from(raw, i) for i in range(0, len(raw), ENTRY_SIZE)]


def decode_signature(raw):
    sig, count = decode_header(raw)
    sig.entries = decode_entries(raw[_HEADER.size :])
    if len(sig.entries) != count:
        raise ValueError("signature entry count does not match header")
    return sig


HEADER_SIZE = _HEADER.size
