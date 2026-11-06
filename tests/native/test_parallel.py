"""The C++ delta releases the GIL, so threads run on separate cores. Skips until built."""

import os
import random
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from deltasync.core.signature import signature

native = pytest.importorskip("deltasync.native")


@pytest.mark.skipif((os.cpu_count() or 1) < 4, reason="needs 4 cores")
def test_threads_run_in_parallel():
    rng = random.Random(0)
    # Unrelated files: the scan rolls over every byte, the slowest case.
    sig = signature(rng.randbytes(4 << 20), block_size=1024)
    news = [rng.randbytes(16 << 20) for _ in range(4)]

    def work(new):
        return native.delta(sig, new)

    t = time.perf_counter()
    for new in news:
        work(new)
    serial = time.perf_counter() - t

    with ThreadPoolExecutor(4) as pool:
        t = time.perf_counter()
        list(pool.map(work, news))
        parallel = time.perf_counter() - t

    assert parallel < 0.6 * serial, (serial, parallel)


def test_engine_defaults_to_native():
    from deltasync.core import engine

    assert engine.NATIVE == (os.environ.get("DELTASYNC_ENGINE", "native") != "python")
