import os

import pytest

from deltasync.server.storage import UnsafePath, resolve_path


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "store"
    r.mkdir()
    return r


@pytest.mark.parametrize(
    "bad",
    ["", ".", "..", "../x", "a/../../x", "a/../..", "/etc/passwd", "//etc/passwd", "a\0b"],
)
def test_rejects(root, bad):
    with pytest.raises(UnsafePath):
        resolve_path(root, bad)


@pytest.mark.parametrize("good", ["a.bin", "dir/sub/a.bin", "with space.bin", "ünïcödé/файл.bin"])
def test_accepts(root, good):
    p = resolve_path(root, good)
    assert p == root.resolve() / good
    assert p.is_relative_to(root.resolve())


def test_any_dotdot_is_rejected_even_if_it_stays_inside(root):
    with pytest.raises(UnsafePath):
        resolve_path(root, "a/../b.bin")


def test_symlinked_file_outside_root(root, tmp_path):
    (tmp_path / "secret").write_text("x")
    os.symlink(tmp_path / "secret", root / "link")
    with pytest.raises(UnsafePath):
        resolve_path(root, "link")


def test_symlinked_dir_outside_root(root, tmp_path):
    (tmp_path / "outside").mkdir()
    os.symlink(tmp_path / "outside", root / "dir")
    with pytest.raises(UnsafePath):
        resolve_path(root, "dir/new.bin")
