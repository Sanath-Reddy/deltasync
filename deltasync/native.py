"""The C++ core with the same API as the Python reference in deltasync.core.

Importing this raises ImportError when the extension has not been built.
"""

import hashlib

from deltasync import _native
from deltasync.core.delta import MAX_LITERAL, Copy, Literal
from deltasync.core.files import mapped
from deltasync.core.signature import Signature, block_size_for


def weak_checksum(window):
    return _native.weak_checksum(window)


def signature(data, block_size=None):
    b = block_size or block_size_for(len(data))
    return Signature(b, hashlib.sha256(data).digest(), _native.signature_entries(data, b), len(data))


def signature_fileobj(f, block_size=None):
    with mapped(f) as data:
        return signature(data, block_size)


def iter_delta(sig, new, fixed_block=False):
    """The whole scan runs in C++ with the GIL released; literals are sliced lazily."""
    ops = _native.delta_ops(new, sig.entries, sig.file_size, sig.block_size, fixed_block)
    for kind, a, b in ops:
        if kind == 0:
            yield Copy(a, b)
        else:
            for i in range(a, a + b, MAX_LITERAL):
                yield Literal(bytes(new[i : min(a + b, i + MAX_LITERAL)]))


def delta(sig, new, fixed_block=False):
    return list(iter_delta(sig, new, fixed_block))
