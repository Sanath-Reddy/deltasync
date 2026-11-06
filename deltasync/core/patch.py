"""Patching: replay ops into a fresh file, never in place over the old one."""

import hashlib
import os
import uuid
from pathlib import Path

from deltasync.core.delta import Copy

CHUNK = 1 << 20


class HashMismatch(Exception):
    pass


def patch(old, ops, block_size):
    out = bytearray()
    for op in ops:
        if isinstance(op, Copy):
            out += old[op.first_block * block_size : (op.first_block + op.count) * block_size]
        else:
            out += op.data
    return bytes(out)


def apply_ops(old_file, ops, block_size, write):
    """Stream ops into write(), reading COPY ranges from the open old_file."""
    for op in ops:
        if isinstance(op, Copy):
            old_file.seek(op.first_block * block_size)
            remaining = op.count * block_size
            while remaining:
                chunk = old_file.read(min(CHUNK, remaining))
                if not chunk:
                    break
                write(chunk)
                remaining -= len(chunk)
        else:
            write(op.data)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def temp_path(target, tag=None):
    target = Path(target)
    return target.parent / f".{target.name}.{tag or uuid.uuid4().hex}.tmp"


def commit_temp(tmp, target):
    """Atomically swap a finished, fsynced temp file into place."""
    os.replace(tmp, target)
    fsync_dir(Path(target).parent)


def patch_file(old_path, ops, block_size, out_path, expected_sha256):
    """Rebuild into a temp file, check SHA-256, fsync, then os.replace onto out_path."""
    tmp = temp_path(out_path)
    h = hashlib.sha256()
    try:
        old = open(old_path, "rb") if old_path is not None and Path(old_path).exists() else None
        try:
            with open(tmp, "wb") as f:

                def write(data):
                    h.update(data)
                    f.write(data)

                if old is None:
                    if any(isinstance(op, Copy) for op in ops):
                        raise HashMismatch("COPY op with no base file")
                    for op in ops:
                        write(op.data)
                else:
                    apply_ops(old, ops, block_size, write)
                if h.digest() != expected_sha256:
                    raise HashMismatch("patched file does not match the expected SHA-256")
                f.flush()
                os.fsync(f.fileno())
        finally:
            if old is not None:
                old.close()
        commit_temp(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
