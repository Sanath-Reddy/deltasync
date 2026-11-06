# DeltaSync

An rsync-style file sync engine: a rolling checksum finds unchanged blocks at any offset, so only changed bytes cross the wire.

Work in progress. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for how each part is tested.
