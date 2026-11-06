"""deltasync command line."""

import argparse
import hashlib
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
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
