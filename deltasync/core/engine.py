"""Picks the delta engine: the C++ core when built, else the Python reference.

Set DELTASYNC_ENGINE=python to force the reference implementation.
"""

import os

from deltasync.core import delta as _py_delta
from deltasync.core import signature as _py_signature

NATIVE = False
if os.environ.get("DELTASYNC_ENGINE", "native") != "python":
    try:
        from deltasync import native as _native

        NATIVE = True
    except ImportError:
        pass

if NATIVE:
    iter_delta = _native.iter_delta
    signature_fileobj = _native.signature_fileobj
else:
    iter_delta = _py_delta.iter_delta
    signature_fileobj = _py_signature.signature_fileobj

NAME = "native" if NATIVE else "python"
