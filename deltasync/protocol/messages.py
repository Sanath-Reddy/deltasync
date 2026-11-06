"""Message types and helpers. Control payloads are JSON; data payloads are raw bytes."""

import json

VERSION = 1

HELLO = 1  # both ways: {"version", "token"} / {"version"}
PUSH_BEGIN = 2  # client -> server: {"path", "size", "sha256", "transfer_id"}
SIGNATURE = 3  # header frame, then entry frames (raw)
OPS = 4  # encoded ops (raw), streamed
PUSH_END = 5  # client -> server: {"ops"}
COMMIT_OK = 6  # server -> client: {"sha256", ...}
COMMIT_FAIL = 7  # server -> client: {"reason"}
RESUME = 8  # client -> server: {"transfer_id", ...}; reply {"offset"}
ERROR = 9  # both ways: {"code", "message"}
PULL_BEGIN = 10  # client -> server: {"path"}, then the client's SIGNATURE frames
PULL_END = 11  # server -> client: {"ops", "sha256", "size"}
RESUME_OK = 12  # server -> client: {"offset", "block_size", "base"}

# Data frames are packed up to this size, well under MAX_FRAME.
OPS_FRAME_TARGET = 256 * 1024
ENTRIES_PER_FRAME = 32 * 1024

# Reasons a commit can fail.
BASE_CHANGED = "base changed, retry"
HASH_MISMATCH = "hash mismatch"


def dump(obj):
    return json.dumps(obj, separators=(",", ":")).encode()


def load(payload):
    return json.loads(payload)


class ProtocolError(Exception):
    pass
