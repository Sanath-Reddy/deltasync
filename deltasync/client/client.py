"""asyncio client for push and pull."""

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from deltasync.core.engine import iter_delta, signature_fileobj
from deltasync.core.files import mapped
from deltasync.core.patch import HashMismatch, Patcher
from deltasync.core.signature import block_size_for, empty_signature
from deltasync.protocol import messages
from deltasync.protocol.frames import encode_frame, read_frame
from deltasync.protocol.stream import expect, iterate_in_thread, pack_ops, recv_signature, send_signature


class SyncError(Exception):
    pass


class AuthError(SyncError):
    pass


class BaseChanged(SyncError):
    pass


@dataclass
class Result:
    sha256: str
    ops: int
    literal_bytes: int
    matched_bytes: int = 0


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return os.path.getsize(path), h.hexdigest()


class Client:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer

    @classmethod
    async def connect(cls, host, port, token, timeout=30):
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        client = cls(reader, writer)
        try:
            await client._send(messages.HELLO, messages.dump({"version": messages.VERSION, "token": token}))
            kind, payload = await asyncio.wait_for(read_frame(reader), timeout)
            if kind == messages.ERROR:
                err = messages.load(payload)
                raise (AuthError if err["code"] == "auth" else SyncError)(err["message"])
            expect(kind, payload, messages.HELLO)
        except BaseException:
            await client.close()
            raise
        return client

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def _send(self, kind, payload=b""):
        self.writer.write(encode_frame(kind, payload))
        await self.writer.drain()

    async def _read(self, reader=None):
        return await read_frame(self.reader)

    async def push(self, local, remote, after_signature=None, fixed_block=False, transfer_id=None):
        """Send local to the server as remote. Raises BaseChanged if another push won."""
        local = Path(local)
        size, digest = await asyncio.to_thread(file_sha256, local)
        transfer_id = transfer_id or uuid.uuid4().hex
        await self._send(messages.PUSH_BEGIN, messages.dump(
            {"path": remote, "size": size, "sha256": digest, "transfer_id": transfer_id}
        ))
        try:
            sig = await recv_signature(self.reader)
        except messages.ProtocolError as e:
            raise SyncError(str(e)) from None
        if after_signature is not None:
            await after_signature()

        def frames():
            with open(local, "rb") as f, mapped(f) as new:
                yield from pack_ops(iter_delta(sig, new, fixed_block))

        ops = lit = 0
        async for payload, count, nlit in iterate_in_thread(frames):
            ops += count
            lit += nlit
            await self._send(messages.OPS, payload)
        await self._send(messages.PUSH_END, messages.dump({"ops": ops}))

        kind, payload = await self._read()
        reply = messages.load(payload)
        if kind == messages.COMMIT_OK:
            return Result(reply["sha256"], ops, lit, reply["matched_bytes"])
        if kind == messages.COMMIT_FAIL and reply["reason"] == messages.BASE_CHANGED:
            raise BaseChanged(reply["reason"])
        raise SyncError(reply.get("reason") or reply.get("message"))

    async def pull(self, remote, local):
        """Fetch remote into local, reusing whatever local already has."""
        local = Path(local)
        await self._send(messages.PULL_BEGIN, messages.dump({"path": remote}))
        kind, payload = await self._read()
        try:
            expect(kind, payload, messages.PULL_BEGIN)
        except messages.ProtocolError as e:
            raise SyncError(str(e)) from None
        info = messages.load(payload)

        try:
            old = open(local, "rb")
        except FileNotFoundError:
            old = None
        try:
            if old is None:
                sig = empty_signature(block_size_for(info["size"]))
            else:
                sig = await asyncio.to_thread(signature_fileobj, old)
            await send_signature(self.writer, sig)

            local.parent.mkdir(parents=True, exist_ok=True)
            patcher = Patcher(old, sig.block_size, local)
            try:
                while True:
                    kind, payload = await self._read()
                    if kind == messages.OPS:
                        await asyncio.to_thread(patcher.feed, payload)
                    elif kind == messages.PULL_END:
                        break
                    else:
                        expect(kind, payload, messages.OPS)
                if messages.load(payload)["ops"] != patcher.ops:
                    raise SyncError("op count does not match PULL_END")
                await asyncio.to_thread(patcher.finish, bytes.fromhex(info["sha256"]))
                await asyncio.to_thread(patcher.commit)
            except BaseException as e:
                patcher.abort()
                if isinstance(e, (HashMismatch, messages.ProtocolError)):
                    raise SyncError(str(e)) from e
                raise
        finally:
            if old is not None:
                old.close()
        return Result(info["sha256"], patcher.ops, patcher.literal_bytes, patcher.matched_bytes)


async def push(host, port, token, local, remote, retries=3, **kw):
    for attempt in range(retries):
        async with await Client.connect(host, port, token) as c:
            try:
                return await c.push(local, remote, **kw)
            except BaseChanged:
                if attempt == retries - 1:
                    raise


async def pull(host, port, token, remote, local):
    async with await Client.connect(host, port, token) as c:
        return await c.pull(remote, local)
