"""Read-only views of files that work with the byte-indexed delta code."""

import mmap
import os
from contextlib import contextmanager


@contextmanager
def mapped(file):
    """An mmap of a path or open file, or b"" for an empty one (which mmap refuses)."""
    if isinstance(file, (str, os.PathLike)):
        with open(file, "rb") as f, mapped(f) as m:
            yield m
        return
    if os.fstat(file.fileno()).st_size == 0:
        yield b""
        return
    m = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
    try:
        yield m
    finally:
        m.close()
