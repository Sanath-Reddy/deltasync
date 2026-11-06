"""The rsync weak checksum (Tridgell and Mackerras), which rolls forward in O(1)."""

from itertools import accumulate

M = 1 << 16
MASK = M - 1


def weak_checksum(window):
    """s = a + 2^16 b over the whole window."""
    a = sum(window) & MASK
    # b = sum((n - i) * x_i) is the sum of the prefix sums of the window.
    b = sum(accumulate(window)) & MASK
    return a | (b << 16)


class Rolling:
    __slots__ = ("a", "b", "n")

    def __init__(self, window):
        s = weak_checksum(window)
        self.a = s & MASK
        self.b = s >> 16
        self.n = len(window)

    @property
    def digest(self):
        return self.a | (self.b << 16)

    def roll(self, out_byte, in_byte):
        """Slide the window one byte: drop out_byte from the front, add in_byte at the end."""
        self.a = (self.a - out_byte + in_byte) & MASK
        self.b = (self.b - self.n * out_byte + self.a) & MASK
