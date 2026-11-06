"""asyncio server: one task per connection, blocking delta work in a thread pool."""

import asyncio
import hashlib
import hmac
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from deltasync.core.engine import iter_delta, signature_fileobj
from deltasync.core.files import mapped
from deltasync.core.patch import HashMismatch, Patcher, temp_path
from deltasync.core.signature import block_size_for, empty_signature
from deltasync.protocol import messages
from deltasync.protocol.frames import FrameTooLarge, encode_frame, frame_size, read_frame
from deltasync.protocol.stream import iterate_in_thread, pack_ops, recv_signature, send_signature
from deltasync.server.metrics import Metrics
from deltasync.server.storage import Journal, Locks, UnsafePath, clean_orphans, file_identity, resolve_path

log = logging.getLogger("deltasync.server")


class _Reject(Exception):
    """A request-level error: reply ERROR and keep the connection."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _fd_identity(f):
    st = os.fstat(f.fileno())
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class Server:
    def __init__(self, root, token, host="127.0.0.1", port=5001, idle_timeout=60.0, workers=8,
                 checkpoint_bytes=4 << 20, block_size=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        clean_orphans(self.root)
        self.checkpoint_bytes = checkpoint_bytes
        self.block_size = block_size  # None: about sqrt(file size)
        self.crash_at = os.environ.get("DELTASYNC_CRASH_AT")
        self.token = token
        self.host = host
        self.listen_port = port
        self.idle_timeout = idle_timeout
        self.executor = ThreadPoolExecutor(workers, thread_name_prefix="deltasync")
        self.metrics = Metrics()
        self.locks = Locks()
        self._server = None
        self._conns = set()

    @property
    def port(self):
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        self._server = await asyncio.start_server(self._handle, self.host, self.listen_port)
        return self

    async def close(self):
        self._server.close()
        for w in list(self._conns):
            w.transport.abort()
        await self._server.wait_closed()
        self.executor.shutdown(wait=False, cancel_futures=True)

    async def serve_forever(self):
        async with self._server:
            await self._server.serve_forever()

    def _run(self, fn, *args):
        return asyncio.get_running_loop().run_in_executor(self.executor, fn, *args)

    async def _read(self, reader):
        kind, payload = await asyncio.wait_for(read_frame(reader), self.idle_timeout)
        self.metrics.bytes_received.inc(frame_size(payload))
        return kind, payload

    async def _send(self, writer, kind, payload=b""):
        frame = encode_frame(kind, payload)
        self.metrics.bytes_sent.inc(len(frame))
        writer.write(frame)
        await writer.drain()

    async def _handle(self, reader, writer):
        self._conns.add(writer)
        self.metrics.connections.inc()
        try:
            if not await self._hello(reader, writer):
                return
            while True:
                kind, payload = await self._read(reader)
                try:
                    if kind == messages.PUSH_BEGIN:
                        await self._push(reader, writer, messages.load(payload))
                    elif kind == messages.RESUME:
                        await self._push(reader, writer, messages.load(payload), resume=True)
                    elif kind == messages.PULL_BEGIN:
                        await self._pull(reader, writer, messages.load(payload))
                    else:
                        raise messages.ProtocolError(f"unexpected message type {kind}")
                except _Reject as e:
                    await self._send(writer, messages.ERROR, messages.dump({"code": e.code, "message": str(e)}))
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            pass
        except (messages.ProtocolError, FrameTooLarge, ValueError, KeyError) as e:
            log.info("closing connection: %s", e)
            try:
                await self._send(writer, messages.ERROR, messages.dump({"code": "protocol", "message": str(e)}))
            except ConnectionError:
                pass
        finally:
            self._conns.discard(writer)
            self.metrics.connections.dec()
            writer.close()

    async def _hello(self, reader, writer):
        kind, payload = await self._read(reader)
        hello = messages.load(payload) if kind == messages.HELLO else {}
        if hello.get("version") != messages.VERSION:
            error = {"code": "version", "message": f"server speaks version {messages.VERSION}"}
        elif not hmac.compare_digest(str(hello.get("token", "")).encode(), self.token.encode()):
            error = {"code": "auth", "message": "bad token"}
        else:
            await self._send(writer, messages.HELLO, messages.dump({"version": messages.VERSION}))
            return True
        await self._send(writer, messages.ERROR, messages.dump(error))
        return False

    def _target(self, path):
        try:
            target = resolve_path(self.root, path)
        except UnsafePath as e:
            raise _Reject("unsafe_path", str(e)) from None
        if target.is_dir():
            raise _Reject("is_dir", f"{path} is a directory")
        return target

    async def _push(self, reader, writer, meta, resume=False):
        target = self._target(meta["path"])
        transfer_id = str(meta["transfer_id"])
        if not transfer_id.isalnum() or len(transfer_id) > 64:
            raise _Reject("protocol", "bad transfer id")
        expected = bytes.fromhex(meta["sha256"])
        size = int(meta["size"])
        target.parent.mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        self.metrics.active_transfers.inc()

        # Hold the base file open: COPY ops read this exact version even if a
        # concurrent push replaces the path, and its identity is checked at commit.
        try:
            old = open(target, "rb")
        except FileNotFoundError:
            old = None
        base_id = None if old is None else list(_fd_identity(old))
        journal = Journal(target, transfer_id)
        patcher = None
        committed = keep = False
        try:
            offset = 0
            if resume:
                offset = self._resume_offset(journal, target, transfer_id, meta, base_id)
                await self._send(writer, messages.RESUME_OK, messages.dump({"offset": offset}))
            if offset == 0:
                journal.remove()

            if old is None:
                sig = empty_signature(self.block_size or block_size_for(size))
            else:
                sig = await self._run(signature_fileobj, old, self.block_size)
            await send_signature(writer, sig)

            patcher = await self._run(lambda: Patcher(old, sig.block_size, target, transfer_id, offset))
            state = {"sha256": meta["sha256"], "size": size, "base": base_id, "path": meta["path"]}
            durable = offset
            try:
                while True:
                    kind, payload = await self._read(reader)
                    if kind == messages.OPS:
                        await self._run(patcher.feed, payload)
                        if patcher.size - durable >= self.checkpoint_bytes:
                            durable = await self._run(patcher.make_durable)
                            await self._run(journal.write, {**state, "offset": durable})
                    elif kind == messages.PUSH_END:
                        break
                    else:
                        raise messages.ProtocolError(f"unexpected message type {kind} during push")
            except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
                keep = journal.exists  # resumable from the last checkpoint
                raise
            if messages.load(payload)["ops"] != patcher.ops:
                raise messages.ProtocolError("op count does not match PUSH_END")
            self.metrics.literal_bytes.inc(patcher.literal_bytes)
            self.metrics.matched_bytes.inc(patcher.matched_bytes)

            try:
                await self._run(patcher.finish, expected)
            except HashMismatch:
                return await self._fail(writer, messages.HASH_MISMATCH)
            async with self.locks(target):
                current = file_identity(target)
                if (list(current) if current else None) != base_id:
                    return await self._fail(writer, messages.BASE_CHANGED)
                self._maybe_crash("before_replace")
                await self._run(patcher.commit)
                committed = True
                self._maybe_crash("after_replace")
            self.metrics.commits.labels("ok").inc()
            await self._send(writer, messages.COMMIT_OK, messages.dump({
                "sha256": expected.hex(), "ops": patcher.ops, "resumed_from": offset,
                "literal_bytes": patcher.literal_bytes, "matched_bytes": patcher.matched_bytes,
            }))
        finally:
            if patcher is not None and not committed:
                await self._run(patcher.close if keep else patcher.abort)
            if not keep:
                journal.remove()
            if old is not None:
                old.close()
            self.metrics.active_transfers.dec()
            self.metrics.transfer_seconds.labels("push").observe(time.perf_counter() - start)

    def _resume_offset(self, journal, target, transfer_id, meta, base_id):
        """Where an interrupted push can continue, or 0 to start over."""
        state = journal.read()
        tmp = temp_path(target, transfer_id)
        try:
            ok = (
                state is not None
                and state.get("sha256") == meta["sha256"]
                and state.get("size") == int(meta["size"])
                and state.get("base") == base_id
                and 0 < int(state.get("offset", 0)) <= tmp.stat().st_size
            )
        except (OSError, TypeError, ValueError):
            ok = False
        if ok:
            return int(state["offset"])
        tmp.unlink(missing_ok=True)
        journal.remove()
        return 0

    def _maybe_crash(self, point):
        if self.crash_at == point:  # fault injection for the crash tests
            os._exit(70)

    async def _fail(self, writer, reason):
        self.metrics.commits.labels(reason).inc()
        await self._send(writer, messages.COMMIT_FAIL, messages.dump({"reason": reason}))

    async def _pull(self, reader, writer, meta):
        target = self._target(meta["path"])
        try:
            f = open(target, "rb")
        except FileNotFoundError:
            raise _Reject("not_found", f"{meta['path']} not found") from None
        start = time.perf_counter()
        self.metrics.active_transfers.inc()
        try:
            size = os.fstat(f.fileno()).st_size

            def digest():
                h = hashlib.sha256()
                f.seek(0)
                while chunk := f.read(1 << 20):
                    h.update(chunk)
                return h.hexdigest()

            await self._send(writer, messages.PULL_BEGIN, messages.dump({"size": size, "sha256": await self._run(digest)}))
            sig = await recv_signature(reader, self._read)

            def frames():
                with mapped(f) as new:
                    yield from pack_ops(iter_delta(sig, new))

            ops = lit = 0
            async for payload, count, nlit in iterate_in_thread(frames, self.executor):
                ops += count
                lit += nlit
                await self._send(writer, messages.OPS, payload)
            self.metrics.literal_bytes.inc(lit)
            await self._send(writer, messages.PULL_END, messages.dump({"ops": ops}))
        finally:
            f.close()
            self.metrics.active_transfers.dec()
            self.metrics.transfer_seconds.labels("pull").observe(time.perf_counter() - start)
