"""deltasync command line."""

import argparse
import asyncio
import hashlib
import logging
import os
import struct
import sys
from pathlib import Path

from deltasync.core.delta import decode_ops, delta, encode_op
from deltasync.core.files import mapped
from deltasync.core.patch import HashMismatch, patch_file
from deltasync.core.signature import decode_signature, encode_signature, signature_file

# Delta file: magic, block size, SHA-256 of the new file, then encoded ops.
_DELTA_HEADER = struct.Struct(">4sI32s")
_MAGIC = b"DSD1"


def cmd_sig(args):
    sig = signature_file(args.old, args.block_size)
    Path(args.sig).write_bytes(encode_signature(sig))
    return 0


def cmd_delta(args):
    sig = decode_signature(Path(args.sig).read_bytes())
    with mapped(args.new) as new, open(args.delta, "wb") as out:
        out.write(_DELTA_HEADER.pack(_MAGIC, sig.block_size, hashlib.sha256(new).digest()))
        for op in delta(sig, new, fixed_block=args.fixed_block):
            out.write(encode_op(op))
    return 0


def cmd_patch(args):
    raw = Path(args.delta).read_bytes()
    magic, block_size, new_hash = _DELTA_HEADER.unpack_from(raw)
    if magic != _MAGIC:
        print("not a deltasync delta file", file=sys.stderr)
        return 2
    ops = decode_ops(raw[_DELTA_HEADER.size :])
    try:
        patch_file(args.old, ops, block_size, args.out, new_hash)
    except HashMismatch as e:
        print(f"patch failed: {e}", file=sys.stderr)
        return 1
    return 0


def _token(args):
    token = args.token or os.environ.get("DELTASYNC_TOKEN")
    if not token:
        print("set --token or DELTASYNC_TOKEN", file=sys.stderr)
        sys.exit(2)
    return token


def cmd_serve(args):
    from deltasync.server.server import Server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    async def run():
        server = await Server(args.root, _token(args), args.host, args.port,
                              checkpoint_bytes=args.checkpoint_bytes).start()
        if args.metrics_port:
            server.metrics.serve(args.metrics_port, args.host)
        print(f"serving {server.root} on {args.host}:{server.port}", flush=True)
        await server.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    return 0


def _remote(args):
    host, _, port = args.server.rpartition(":")
    return host or "127.0.0.1", int(port)


def cmd_push(args):
    from deltasync.client.client import SyncError, push

    try:
        r = asyncio.run(push(*_remote(args), _token(args), args.local, args.remote,
                             retries=0 if args.no_retry else 3))
    except (SyncError, OSError, asyncio.IncompleteReadError) as e:
        print(f"push failed: {e}", file=sys.stderr)
        return 1
    print(f"pushed {args.remote}: {r.ops} ops, {r.literal_bytes} literal bytes, {r.matched_bytes} reused")
    return 0


def cmd_pull(args):
    from deltasync.client.client import SyncError, pull

    try:
        r = asyncio.run(pull(*_remote(args), _token(args), args.remote, args.local))
    except (SyncError, OSError, asyncio.IncompleteReadError) as e:
        print(f"pull failed: {e}", file=sys.stderr)
        return 1
    print(f"pulled {args.remote}: {r.ops} ops, {r.literal_bytes} literal bytes, {r.matched_bytes} reused")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="deltasync", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sig", help="write the signature of an old file")
    p.add_argument("old")
    p.add_argument("sig")
    p.add_argument("--block-size", type=int)
    p.set_defaults(fn=cmd_sig)

    p = sub.add_parser("delta", help="write the delta from a signature to a new file")
    p.add_argument("sig")
    p.add_argument("new")
    p.add_argument("delta")
    p.add_argument("--fixed-block", action="store_true", help="compare blocks by position only")
    p.set_defaults(fn=cmd_delta)

    p = sub.add_parser("patch", help="rebuild the new file from the old file and a delta")
    p.add_argument("old")
    p.add_argument("delta")
    p.add_argument("out")
    p.set_defaults(fn=cmd_patch)

    p = sub.add_parser("serve", help="run the sync server")
    p.add_argument("--root", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5001)
    p.add_argument("--metrics-port", type=int, help="serve Prometheus metrics on this port")
    p.add_argument("--checkpoint-bytes", type=int, default=4 << 20, help="journal a resume point this often")
    p.add_argument("--token")
    p.set_defaults(fn=cmd_serve)

    for name, fn, helptext in [("push", cmd_push, "send a local file to the server"),
                               ("pull", cmd_pull, "fetch a file from the server")]:
        p = sub.add_parser(name, help=helptext)
        if name == "push":
            p.add_argument("local")
            p.add_argument("remote")
            p.add_argument("--no-retry", action="store_true", help="fail instead of resuming")
        else:
            p.add_argument("remote")
            p.add_argument("local")
        p.add_argument("--server", default="127.0.0.1:5001", help="host:port")
        p.add_argument("--token")
        p.set_defaults(fn=fn)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
