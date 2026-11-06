import hashlib
import math

import pytest
import xxhash
from hypothesis import given
from hypothesis import strategies as st

from deltasync.core.rolling import weak_checksum
from deltasync.core.signature import (
    ENTRY_SIZE,
    MAX_BLOCK,
    MIN_BLOCK,
    Signature,
    block_size_for,
    decode_signature,
    encode_signature,
    signature,
    strong_hash,
)


def test_entry_size():
    assert ENTRY_SIZE == 20


def test_strong_hash_is_xxh3_128():
    assert strong_hash(b"hello") == xxhash.xxh3_128_digest(b"hello")
    assert len(strong_hash(b"")) == 16


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, MIN_BLOCK),
        (10_000, MIN_BLOCK),
        (100 * 2**20, math.isqrt(100 * 2**20)),
        (2**40, MAX_BLOCK),
    ],
)
def test_block_size_rule(n, expected):
    assert abs(block_size_for(n) - expected) <= 1
    assert MIN_BLOCK <= block_size_for(n) <= MAX_BLOCK


def test_empty_file_has_no_entries():
    sig = signature(b"", block_size=16)
    assert sig.entries == []
    assert sig.file_hash == hashlib.sha256(b"").digest()


@given(st.binary(max_size=5000), st.sampled_from([1, 3, 64, 1024]))
def test_one_entry_per_block_including_short_last(data, b):
    sig = signature(data, block_size=b)
    assert sig.block_size == b
    assert len(sig.entries) == math.ceil(len(data) / b)
    for i, (weak, strong) in enumerate(sig.entries):
        block = data[i * b : (i + 1) * b]
        assert weak == weak_checksum(block)
        assert strong == strong_hash(block)
    assert sig.file_hash == hashlib.sha256(data).digest()


def test_default_block_size():
    data = bytes(range(256)) * 400
    assert signature(data).block_size == block_size_for(len(data))


@given(st.binary(max_size=5000), st.sampled_from([1, 64, 1024]))
def test_encoding_round_trips(data, b):
    sig = signature(data, block_size=b)
    raw = encode_signature(sig)
    assert decode_signature(raw) == sig
    # Entries are fixed-size binary, not hex or JSON.
    assert len(raw) - len(encode_signature(Signature(b, sig.file_hash, []))) == (
        ENTRY_SIZE * len(sig.entries)
    )
