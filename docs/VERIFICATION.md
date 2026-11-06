# DeltaSync verification plan

This plan says how each milestone is checked before it counts as done. Tests are written before the code they cover, so each milestone starts red and ends green.

## How to run

```bash
python -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/pytest                               # CI profile: 200 Hypothesis examples
HYPOTHESIS_PROFILE=thorough .venv/bin/pytest   # 10,000 examples, run before closing a milestone
```

A milestone is done only when its tests pass under the `thorough` profile and the benchmark checks listed for it hold.

## API the tests pin down

The tests call these names, so the implementation has to provide them.

| Module | Names |
| --- | --- |
| `deltasync.core.rolling` | `M = 2**16`; `weak_checksum(window: bytes) -> int`; `Rolling(window)` with `.digest` and `.roll(out_byte, in_byte)` |
| `deltasync.core.signature` | `ENTRY_SIZE = 20`; `MIN_BLOCK = 1024`; `MAX_BLOCK = 65536`; `block_size_for(n) -> int`; `strong_hash(block) -> bytes` (XXH3-128, 16 bytes); `Signature(block_size, file_hash, entries)` where `entries` is a list of `(weak, strong)`; `signature(data, block_size=None) -> Signature`; `encode_signature(sig) -> bytes`; `decode_signature(raw) -> Signature` |
| `deltasync.core.delta` | `Copy(first_block, count)`; `Literal(data)`; `MAX_LITERAL = 65536`; `delta(sig, new, fixed_block=False) -> list[Copy \| Literal]` |
| `deltasync.core.patch` | `patch(old, ops, block_size) -> bytes`; `patch_file(old_path, ops, block_size, out_path, expected_sha256)`, which raises `HashMismatch` and leaves `out_path` untouched on a bad hash |
| `deltasync.client.cli` | `main(argv) -> int`, with the `sig`, `delta` and `patch` subcommands |
| `deltasync.protocol.frames` | `MAX_FRAME = 1 << 20`; `encode_frame(type, payload) -> bytes`; `async read_frame(reader) -> (type, payload)`; `FrameTooLarge` |
| `deltasync.server.storage` | `resolve_path(root, client_path) -> Path`; `UnsafePath` |
| `deltasync.native` | The same `weak_checksum`, `signature` and `delta` as the Python core (M3) |

The delta file written by `deltasync delta` carries the SHA-256 of the new file, so `patch` against the wrong base fails instead of writing garbage.

Frame layout: a 4-byte big-endian payload length, a 1-byte type, then the payload. `MAX_FRAME` limits the payload.

## M0 · Repo setup and baselines

| Check | How | Pass when |
| --- | --- | --- |
| Test suite runs in CI | GitHub Actions runs `pytest` on every push | The workflow runs and reports on the first push |
| Byte-counting proxy is accurate | Send a known number of bytes through `bench/count_proxy.py` in each direction | The count matches exactly, both ways |
| Edited test files are what they claim | `make_files.py` writes a manifest with each case's edit offset and size; a script diffs each pair | Each edited copy differs from the original only where the manifest says |
| Baselines exist | `scp` and `rsync --stats --no-whole-file` through the proxy, for every case | One CSV row per case and tool, with CPU, Python version and commit hash |

## M1 · Delta engine (tests written: `tests/core/`, `tests/test_cli.py`)

| Check | Test | Pass when |
| --- | --- | --- |
| Rolling equals recomputing | `test_rolling.py` | For random data, window and offset, rolling forward gives the same value as `weak_checksum` on that window |
| Weak checksum matches the paper's formula | `test_rolling.py` | Equals a direct implementation of a, b and s, including bytes ≥ 128 |
| Signature format | `test_signature.py` | One entry per block, short last block included; each entry encodes to 20 bytes and round-trips; strong hash is XXH3-128 |
| Block size rule | `test_signature.py` | About √n, clamped to 1–64 KB |
| Round trip | `test_roundtrip.py` (Hypothesis) | `patch(old, delta(sig(old), new)) == new` for random files and random insert, delete and replace scripts, in both modes |
| Edge cases | `test_roundtrip.py` | Empty files, files smaller than a block, identical files, unrelated files, all zeros, short last block, a shorter new version |
| Insert is cheap | `test_delta.py` | A 1 KB insert at the start of a 1 MB file sends at most the insert plus one block as literals |
| Fixed-block ablation | `test_delta.py` | The same insert in fixed-block mode resends nearly the whole file, which proves the comparison is fair |
| Unchanged file | `test_delta.py` | Exactly one op: `Copy(0, block_count)` |
| New file | `test_delta.py` | An empty signature gives only literals, totalling the file size |
| Literal cap | `test_delta.py` | No literal op is over 64 KB |
| Safe patch to disk | `test_patch_file.py` | A good patch replaces the output; a bad hash raises `HashMismatch`, leaves the old output intact and leaves no temp file behind |
| Offline CLI | `test_cli.py` | `sig`, `delta`, `patch` in sequence rebuild the new file byte for byte, including names with spaces and non-ASCII |

Benchmark check: on the 10 MB and 100 MB files, the 1 KB-insert case sends about 1 KB plus the signature, measured at the proxy and compared with the fixed-block and rsync CSVs.

## M2 · Async server and protocol

Tests already written: `tests/protocol/test_frames.py` and `tests/server/test_paths.py`. The integration tests below get written at the start of M2, once the client API is fixed.

| Check | Test | Pass when |
| --- | --- | --- |
| Framing survives TCP splitting | `test_frames.py` | A frame fed one byte at a time, and several frames in one read, decode the same |
| Oversized frame | `test_frames.py` | A declared length over 1 MiB raises `FrameTooLarge` before reading the payload |
| Truncated stream | `test_frames.py` | EOF mid-frame raises `IncompleteReadError`, never a short frame |
| Path safety | `test_paths.py` | Any `..` component (even one that stays inside), absolute paths, NUL bytes, empty paths and symlinks out of the root are rejected; nested, spaced and non-ASCII names are accepted |
| Push and pull end to end | `tests/server/test_integration.py` (M2) | A pushed file's SHA-256 matches on the server; a pulled file's matches on the client |
| Auth and version | same | A wrong token or protocol version gets ERROR and the connection closes |
| 50 concurrent pushes | same | All 50 commit and every file's hash is right |
| Racing pushes on one file | same | One commits; the other gets "base changed, retry"; the file equals the winner's version. The base check compares the file's inode, size and mtime with the version the signature was built from; every commit is an `os.replace`, so any commit changes the inode |
| Event loop never stalls | same | While a large delta runs, a second client's HELLO is answered within 100 ms |
| Idle timeout | same | A silent connection closes after the timeout (shortened for the test) |

## M3 · C++ core

Test already written: `tests/native/test_differential.py`, which skips until `deltasync.native` builds.

| Check | Test | Pass when |
| --- | --- | --- |
| Same results as Python | `test_differential.py` | Weak checksums, signatures and op streams are identical on Hypothesis inputs, 10,000 cases |
| GIL released | `tests/native/test_parallel.py` (M3) | Four threads running deltas at once take clearly less wall time than four in a row |
| Speed | `bench/run_bench.py` | MB/s for Python and C++ on 100 MB and 1 GB, median of five runs, in a CSV |

## M4 · Resume, faults and benchmarks (tests written: `tests/faults/test_faults.py`)

Server crashes are injected with `DELTASYNC_CRASH_AT=before_replace|after_replace`, which makes the server exit at that point of the commit. Network faults use `bench/count_proxy.py`, which can cut every connection after a byte budget, cap bandwidth and add delay.

| Check | Pass when |
| --- | --- |
| Client killed mid-push | The server keeps the old file intact; the temp file is cleaned up or resumable |
| Connection dropped at the proxy | Same as above; RESUME continues from the journal's last durable offset |
| Server killed mid-commit | After restart the file is fully old or fully new, checked by SHA-256 |
| Resume saves bytes | After a kill at 50%, bytes re-sent with resume are about half those without, measured at the proxy |
| Journal is durable | The journal is written temp-then-rename, and a corrupt journal falls back to a full push |
| Server restarted mid-push | After a SIGKILL and restart, RESUME continues from the journal |
| Base changed before resume | Resume starts over from offset 0 against the new base |
| Orphans | On startup, temp files without a journal are deleted; journalled ones are kept |

## Benchmark verification

Substitutions forced by the test machine (no root, Docker or SSH server):

- The full-copy baseline is a raw TCP send of the whole file instead of `scp`. rsync runs as a local daemon with `--no-whole-file`.
- The bad-network experiment runs real `tc netem` (25 ms each way, 0% and 1% loss) inside an unprivileged user and network namespace instead of between two containers.
- Edit patterns use 10 MB and 100 MB files. The 1 GB file appears only in the delta-speed experiment, because generating every 1 GB edit case at once does not fit in memory.


These apply to every number that goes on the resume:

- Bytes are counted at the proxy, never from the program's own estimate.
- Five runs on a warm cache; the CSV stores all five and the median.
- Each row records CPU model, Python version and commit hash.
- `python -m bench.report` writes the README results tables from the CSV medians, and `--check` (run in CI) fails if the README and the CSVs disagree.
