"""Shared Hypothesis strategies: random files and random edit scripts."""

from hypothesis import strategies as st

# Small alphabets make weak-hash collisions and repeated blocks common,
# which is where delta bugs hide.
file_bytes = st.one_of(
    st.binary(max_size=8192),
    st.binary(min_size=0, max_size=8192).map(lambda b: bytes(x % 4 for x in b)),
    st.integers(0, 8192).map(lambda n: b"\0" * n),
)


@st.composite
def edited(draw, old):
    """Apply a random script of inserts, deletes and replaces to old."""
    data = bytearray(old)
    for _ in range(draw(st.integers(0, 6))):
        kind = draw(st.sampled_from(["insert", "delete", "replace"]))
        pos = draw(st.integers(0, len(data)))
        if kind == "insert":
            data[pos:pos] = draw(st.binary(min_size=1, max_size=2048))
        elif kind == "delete":
            end = draw(st.integers(pos, min(len(data), pos + 2048)))
            del data[pos:end]
        else:
            end = draw(st.integers(pos, min(len(data), pos + 512)))
            data[pos:end] = draw(st.binary(min_size=end - pos, max_size=end - pos))
    return bytes(data)


@st.composite
def old_and_new(draw):
    old = draw(file_bytes)
    new = draw(st.one_of(edited(old), file_bytes))
    return old, new


block_sizes = st.sampled_from([1, 2, 7, 64, 700, 1024, 4096])
