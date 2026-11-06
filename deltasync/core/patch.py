"""Patching: replay ops into a fresh file, never in place over the old one."""

import hashlib
import os
import uuid
from pathlib import Path

from deltasync.core.delta import Copy, decode_op

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


class Patcher:
    """Applies a stream of encoded ops to a temp file next to the target.

    The caller decides when to commit, so the server can take its lock and
    check the base first.
    """

    def __init__(self, old_file, block_size, target, tag=None, resume_offset=0):
        self.old = old_file
        self.block_size = block_size
        self.target = Path(target)
        self.tmp = temp_path(target, tag)
        self.hash = hashlib.sha256()
        if resume_offset:
            # Keep the durable prefix of an interrupted transfer and rehash it.
            self.out = open(self.tmp, "r+b")
            self.out.truncate(resume_offset)
            while chunk := self.out.read(CHUNK):
                self.hash.update(chunk)
        else:
            self.out = open(self.tmp, "wb")
        self.ops = 0
        self.literal_bytes = 0
        self.matched_bytes = 0

    def _write(self, data):
        self.hash.update(data)
        self.out.write(data)

    def feed(self, payload):
        """Apply one OPS frame, which always holds whole ops."""
        offset = 0
        while offset < len(payload):
            op, offset = decode_op(payload, offset)
            self.ops += 1
            if isinstance(op, Copy):
                if self.old is None:
                    raise HashMismatch("COPY op with no base file")
                before = self.out.tell()
                apply_ops(self.old, [op], self.block_size, self._write)
                self.matched_bytes += self.out.tell() - before
            else:
                self.literal_bytes += len(op.data)
                self._write(op.data)

    @property
    def size(self):
        return self.out.tell()

    def make_durable(self):
        """fsync what has been written so far; returns the durable size."""
        self.out.flush()
        os.fsync(self.out.fileno())
        return self.out.tell()

    def close(self):
        """Stop without deleting the temp file, so the transfer can resume."""
        self.out.close()

    def finish(self, expected_sha256):
        """Check the hash and make the temp file durable; raises HashMismatch."""
        if self.hash.digest() != expected_sha256:
            raise HashMismatch("patched file does not match the expected SHA-256")
        self.out.flush()
        os.fsync(self.out.fileno())
        self.out.close()

    def commit(self):
        commit_temp(self.tmp, self.target)

    def abort(self):
        self.out.close()
        self.tmp.unlink(missing_ok=True)
