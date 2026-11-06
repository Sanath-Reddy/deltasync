"""C++ core must match the Python reference exactly. Skips until M3 builds it."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from deltasync.core import delta as py_delta
from deltasync.core import rolling as py_rolling
from deltasync.core import signature as py_signature
from strategies import block_sizes, old_and_new

native = pytest.importorskip("deltasync.native")


@given(st.binary(max_size=4096))
def test_weak_checksum(window):
    assert native.weak_checksum(window) == py_rolling.weak_checksum(window)


@given(st.binary(max_size=8192), block_sizes)
def test_signature(data, b):
    assert native.signature(data, block_size=b) == py_signature.signature(data, block_size=b)


@given(old_and_new(), block_sizes, st.booleans())
def test_same_ops(pair, b, fixed):
    old, new = pair
    sig = py_signature.signature(old, block_size=b)
    assert native.delta(sig, new, fixed_block=fixed) == py_delta.delta(sig, new, fixed_block=fixed)
