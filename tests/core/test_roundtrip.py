import pytest
from hypothesis import given

from deltasync.core.delta import delta
from deltasync.core.patch import patch
from deltasync.core.signature import signature
from strategies import block_sizes, old_and_new


@given(old_and_new(), block_sizes)
def test_patch_rebuilds_new(pair, b):
    old, new = pair
    assert patch(old, delta(signature(old, block_size=b), new), b) == new


@given(old_and_new(), block_sizes)
def test_patch_rebuilds_new_fixed_block(pair, b):
    old, new = pair
    ops = delta(signature(old, block_size=b), new, fixed_block=True)
    assert patch(old, ops, b) == new


EDGE_CASES = {
    "both empty": (b"", b""),
    "old empty": (b"", b"new data"),
    "new empty": (b"old data", b""),
    "smaller than a block": (b"abc", b"abd"),
    "identical": (b"0123456789" * 100, b"0123456789" * 100),
    "nothing in common": (bytes(range(256)) * 4, b"\xaa" * 1024),
    "all zeros": (b"\0" * 5000, b"\0" * 5001),
    "short last block": (b"x" * 1030, b"y" + b"x" * 1030),
    "new shorter than old": (b"abcdefgh" * 500, b"abcdefgh" * 100),
}


@pytest.mark.parametrize("old, new", EDGE_CASES.values(), ids=EDGE_CASES.keys())
@pytest.mark.parametrize("b", [1, 16, 1024])
def test_edge_cases(old, new, b):
    assert patch(old, delta(signature(old, block_size=b), new), b) == new
