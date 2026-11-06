"""Server-side storage: safe path resolution and per-file commit locks."""

import asyncio
import os
from pathlib import Path, PurePosixPath


class UnsafePath(Exception):
    pass


def resolve_path(root, client_path):
    """Map a client path onto a file under root, or raise UnsafePath.

    Any ".." component is refused outright, and the result is checked again
    after resolving symlinks, so a link cannot lead outside the root either.
    """
    if not client_path or "\0" in client_path or "\\" in client_path:
        raise UnsafePath(f"unsafe path {client_path!r}")
    parts = PurePosixPath(client_path)
    if parts.is_absolute() or ".." in parts.parts:
        raise UnsafePath(f"unsafe path {client_path!r}")
    root = Path(root).resolve()
    resolved = (root / parts).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise UnsafePath(f"unsafe path {client_path!r}")
    return resolved


def file_identity(path):
    """Changes whenever a file is replaced, since every commit is an os.replace."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class Locks:
    """One asyncio.Lock per target file, dropped when nobody holds it."""

    def __init__(self):
        self._locks = {}
        self._users = {}

    def __call__(self, path):
        return _Held(self, path)

    def _acquire_obj(self, path):
        self._users[path] = self._users.get(path, 0) + 1
        return self._locks.setdefault(path, asyncio.Lock())

    def _release_obj(self, path):
        self._users[path] -= 1
        if not self._users[path]:
            del self._users[path], self._locks[path]


class _Held:
    def __init__(self, locks, path):
        self.locks, self.path = locks, path

    async def __aenter__(self):
        self.lock = self.locks._acquire_obj(self.path)
        await self.lock.acquire()

    async def __aexit__(self, *exc):
        self.lock.release()
        self.locks._release_obj(self.path)
