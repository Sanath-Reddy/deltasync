import random

import pytest

from deltasync.core.delta import MAX_LITERAL, Copy, Literal, delta
from deltasync.core.patch import patch
from deltasync.core.signature import signature

B = 1024


def literal_bytes(ops):
    return sum(len(op.data) for op in ops if isinstance(op, Literal))


@pytest.fixture(scope="module")
def one_mb():
    return random.Random(1).randbytes(2**20)


def test_unchanged_file_is_one_copy(one_mb):
    ops = delta(signature(one_mb, block_size=B), one_mb)
    assert ops == [Copy(0, len(one_mb) // B)]


def test_unchanged_file_with_short_last_block_is_one_copy(one_mb):
    data = one_mb[:-100]
    ops = delta(signature(data, block_size=B), data)
    assert ops == [Copy(0, -(-len(data) // B))]


def test_insert_at_start_sends_about_the_insert(one_mb):
    insert = random.Random(2).randbytes(1024)
    new = insert + one_mb
    ops = delta(signature(one_mb, block_size=B), new)
    assert literal_bytes(ops) <= len(insert) + B
    assert patch(one_mb, ops, B) == new


def test_fixed_block_mode_resends_after_insert(one_mb):
    """The ablation: position-based blocks lose everything after an insert."""
    new = b"x" + one_mb
    ops = delta(signature(one_mb, block_size=B), new, fixed_block=True)
    assert literal_bytes(ops) >= len(one_mb) * 0.99
    assert patch(one_mb, ops, B) == new


def test_fixed_block_mode_matches_in_place_edit(one_mb):
    new = bytearray(one_mb)
    new[5000] ^= 0xFF
    ops = delta(signature(one_mb, block_size=B), bytes(new), fixed_block=True)
    assert literal_bytes(ops) <= B


def test_delete_from_middle(one_mb):
    new = one_mb[: 2**19] + one_mb[2**19 + 100_000 :]
    ops = delta(signature(one_mb, block_size=B), new)
    assert literal_bytes(ops) <= 2 * B
    assert patch(one_mb, ops, B) == new


def test_new_file_is_all_literals(one_mb):
    ops = delta(signature(b"", block_size=B), one_mb)
    assert all(isinstance(op, Literal) for op in ops)
    assert literal_bytes(ops) == len(one_mb)


def test_literals_are_capped(one_mb):
    ops = delta(signature(b"", block_size=B), one_mb)
    assert max(len(op.data) for op in ops) <= MAX_LITERAL
    assert MAX_LITERAL == 64 * 1024


def test_copies_are_merged_into_runs(one_mb):
    new = bytearray(one_mb)
    new[2**19] ^= 0xFF
    ops = delta(signature(one_mb, block_size=B), bytes(new))
    copies = [op for op in ops if isinstance(op, Copy)]
    assert len(copies) == 2


def test_all_zeros_still_matches():
    old = b"\0" * 200_000
    new = old[:100_000] + b"edit" + old[100_000:]
    ops = delta(signature(old, block_size=B), new)
    assert literal_bytes(ops) <= 2 * B
    assert patch(old, ops, B) == new
