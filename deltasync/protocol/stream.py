"""Glue between blocking delta work and the event loop, plus frame packing helpers."""

import asyncio
import threading

from deltasync.core.delta import encode_op
from deltasync.core.signature import ENTRY_SIZE, decode_entries, decode_header, encode_entries, encode_header
from deltasync.protocol import messages
from deltasync.protocol.frames import encode_frame, read_frame

_END = object()


async def iterate_in_thread(make_iter, executor=None, maxsize=8):
    """Run a blocking generator in a worker thread and iterate it from the event loop.

    The bounded queue gives backpressure: the worker pauses while the consumer
    (usually a socket write) is behind. With executor=None it gets its own thread.
    """
    loop = asyncio.get_running_loop()
    q = asyncio.Queue(maxsize)
    stop = threading.Event()

    def put(item):
        asyncio.run_coroutine_threadsafe(q.put(item), loop).result()

    def run():
        try:
            for item in make_iter():
                if stop.is_set():
                    return
                put(item)
            put(_END)
        except BaseException as e:  # hand the error to the consumer
            if not stop.is_set():
                put(e)

    if executor is None:
        done = loop.create_future()

        def thread_main():
            try:
                run()
            finally:
                loop.call_soon_threadsafe(lambda: done.done() or done.set_result(None))

        threading.Thread(target=thread_main, daemon=True).start()
        task = done
    else:
        task = loop.run_in_executor(executor, run)
    try:
        while True:
            item = await q.get()
            if item is _END:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        stop.set()
        while not task.done():  # unblock a worker stuck on a full queue
            while not q.empty():
                q.get_nowait()
            await asyncio.sleep(0.005)


def pack_ops(ops, target=messages.OPS_FRAME_TARGET):
    """Group ops into OPS frame payloads; yields (payload, op count, literal bytes)."""
    buf, count, lit = [], 0, 0
    size = 0
    for op in ops:
        raw = encode_op(op)
        buf.append(raw)
        size += len(raw)
        count += 1
        if raw[:1] == b"L":
            lit += len(op.data)
        if size >= target:
            yield b"".join(buf), count, lit
            buf, count, lit, size = [], 0, 0, 0
    if buf:
        yield b"".join(buf), count, lit


async def send_signature(writer, sig):
    writer.write(encode_frame(messages.SIGNATURE, encode_header(sig)))
    for i in range(0, len(sig.entries), messages.ENTRIES_PER_FRAME):
        writer.write(encode_frame(messages.SIGNATURE, encode_entries(sig.entries[i : i + messages.ENTRIES_PER_FRAME])))
        await writer.drain()
    await writer.drain()


async def recv_signature(reader, read=read_frame):
    kind, payload = await read(reader)
    expect(kind, payload, messages.SIGNATURE)
    sig, count = decode_header(payload)
    while len(sig.entries) < count:
        kind, payload = await read(reader)
        expect(kind, payload, messages.SIGNATURE)
        if len(payload) % ENTRY_SIZE:
            raise messages.ProtocolError("ragged signature frame")
        sig.entries.extend(decode_entries(payload))
    if len(sig.entries) != count:
        raise messages.ProtocolError("too many signature entries")
    return sig


def expect(kind, payload, want):
    if kind == messages.ERROR:
        err = messages.load(payload)
        raise messages.ProtocolError(f"{err.get('code')}: {err.get('message')}")
    if kind != want:
        raise messages.ProtocolError(f"expected message {want}, got {kind}")
