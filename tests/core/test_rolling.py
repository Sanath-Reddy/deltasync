from hypothesis import given
from hypothesis import strategies as st

from deltasync.core.rolling import M, Rolling, weak_checksum


def reference(window):
    """a, b and s straight from the rsync tech report, no rolling."""
    n = len(window)
    a = sum(window) % M
    b = sum((n - i) * x for i, x in enumerate(window)) % M
    return a + (b << 16)


def test_modulus():
    assert M == 2**16


@given(st.binary(max_size=4096))
def test_matches_paper_formula(window):
    assert weak_checksum(window) == reference(window)


def test_high_bytes_are_unsigned():
    assert weak_checksum(b"\xff" * 10) == reference(b"\xff" * 10)


def test_fits_in_32_bits():
    assert 0 <= weak_checksum(b"\xff" * 65536) < 2**32


@given(st.data())
def test_rolling_equals_recomputing(data):
    buf = data.draw(st.binary(min_size=1, max_size=300))
    width = data.draw(st.integers(1, len(buf)))
    start = data.draw(st.integers(0, len(buf) - width))
    r = Rolling(buf[start : start + width])
    for k in range(start, len(buf) - width):
        r.roll(buf[k], buf[k + width])
        assert r.digest == weak_checksum(buf[k + 1 : k + 1 + width])
