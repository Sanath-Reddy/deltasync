"""TCP relay that counts bytes in each direction, so every tool is measured the same way.

It can also add one-way delay and cut connections after a byte budget, for the
bad-network and fault-injection experiments.

    python -m bench.count_proxy --listen 6000 --target 127.0.0.1:5001
"""

import argparse
import asyncio
import threading
import time


class CountingProxy:
    def __init__(self, target_host, target_port, delay=0.0, listen_port=0):
        self.target = (target_host, target_port)
        self.delay = delay
        self.listen_port = listen_port
        self.up = 0  # client -> server
        self.down = 0  # server -> client
        self.cut_after_up = None  # close every connection once this many bytes went up
        self._server = None
        self._conns = set()

    @property
    def port(self):
        return self._server.sockets[0].getsockname()[1]

    def reset(self):
        self.up = self.down = 0

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", self.listen_port)
        return self

    async def close(self):
        self._server.close()
        for w in list(self._conns):
            w.close()
        await self._server.wait_closed()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()

    def cut_all(self):
        for w in list(self._conns):
            w.transport.abort()

    async def _handle(self, c_reader, c_writer):
        try:
            s_reader, s_writer = await asyncio.open_connection(*self.target)
        except OSError:
            c_writer.close()
            return
        self._conns |= {c_writer, s_writer}
        try:
            await asyncio.gather(
                self._pipe(c_reader, s_writer, "up"),
                self._pipe(s_reader, c_writer, "down"),
            )
        finally:
            self._conns -= {c_writer, s_writer}
            for w in (c_writer, s_writer):
                w.close()

    async def _pipe(self, reader, writer, direction):
        queue = asyncio.Queue()

        async def send():
            while True:
                due, chunk = await queue.get()
                if chunk is None:
                    break
                wait = due - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                writer.write(chunk)
                await writer.drain()
            if writer.can_write_eof():
                writer.write_eof()

        sender = asyncio.create_task(send())
        try:
            while chunk := await reader.read(65536):
                if direction == "up":
                    self.up += len(chunk)
                    if self.cut_after_up is not None and self.up >= self.cut_after_up:
                        self.cut_all()
                        break
                else:
                    self.down += len(chunk)
                queue.put_nowait((time.monotonic() + self.delay, chunk))
                # Keep memory bounded when the far side is slow.
                if queue.qsize() > 256:
                    await asyncio.sleep(0.001)
        except (ConnectionError, OSError):
            pass
        finally:
            queue.put_nowait((0, None))
            try:
                await sender
            except (ConnectionError, OSError):
                pass


class ProxyThread:
    """Runs a CountingProxy on its own event loop, for synchronous benchmark scripts."""

    def __init__(self, target_host, target_port, delay=0.0):
        self.loop = asyncio.new_event_loop()
        self.proxy = CountingProxy(target_host, target_port, delay)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        asyncio.run_coroutine_threadsafe(self.proxy.start(), self.loop).result()
        return self.proxy

    def __exit__(self, *exc):
        asyncio.run_coroutine_threadsafe(self.proxy.close(), self.loop).result()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--listen", type=int, required=True)
    ap.add_argument("--target", required=True, help="host:port")
    ap.add_argument("--delay", type=float, default=0.0, help="one-way delay in seconds")
    args = ap.parse_args()
    host, port = args.target.rsplit(":", 1)

    async def run():
        proxy = await CountingProxy(host, int(port), args.delay, args.listen).start()
        try:
            while True:
                await asyncio.sleep(1)
                print(f"up={proxy.up} down={proxy.down}", flush=True)
        finally:
            await proxy.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
