import hashlib

import pytest

from deltasync.core.delta import delta
from deltasync.core.patch import HashMismatch, patch_file
from deltasync.core.signature import signature

B = 64


def setup(tmp_path, old, new):
    old_path = tmp_path / "old.bin"
    old_path.write_bytes(old)
    ops = delta(signature(old, block_size=B), new)
    return old_path, ops


def test_writes_new_file(tmp_path):
    old, new = b"a" * 1000, b"b" + b"a" * 900
    old_path, ops = setup(tmp_path, old, new)
    out = tmp_path / "out.bin"
    patch_file(old_path, ops, B, out, hashlib.sha256(new).digest())
    assert out.read_bytes() == new


def test_shorter_version_leaves_no_stale_tail(tmp_path):
    old, new = b"0123456789" * 500, b"0123456789" * 50
    old_path, ops = setup(tmp_path, old, new)
    # Patch over the old file itself, the case where in-place patching breaks.
    patch_file(old_path, ops, B, old_path, hashlib.sha256(new).digest())
    assert old_path.read_bytes() == new


def test_bad_hash_keeps_old_output(tmp_path):
    old, new = b"a" * 1000, b"b" * 1000
    old_path, ops = setup(tmp_path, old, new)
    out = tmp_path / "out.bin"
    out.write_bytes(b"previous version")
    with pytest.raises(HashMismatch):
        patch_file(old_path, ops, B, out, b"\0" * 32)
    assert out.read_bytes() == b"previous version"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["old.bin", "out.bin"]
