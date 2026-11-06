"""Read-only views of files that work with the byte-indexed delta code."""

import mmap
from contextlib import contextmanager


@contextmanager
def mapped(path):
    """An mmap of the file, or b"" for an empty one (which mmap refuses)."""
    with open(path, "rb") as f:
        if f.seek(0, 2) == 0:
            yield b""
            return
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            yield m
        finally:
            m.close()
