// Rolling checksum, signatures and delta generation. Mirrors deltasync/core
// exactly, so the differential tests can compare op streams one to one.
#pragma once

#include <cstddef>
#include <cstdint>
#include <unordered_map>
#include <vector>

#define XXH_INLINE_ALL
#include "xxhash.h"

namespace deltasync {

constexpr uint32_t MASK = 0xFFFF;

struct Strong {
    uint64_t hi, lo;
    bool operator==(const Strong& o) const { return hi == o.hi && lo == o.lo; }
};

inline Strong strong_hash(const uint8_t* p, size_t n) {
    XXH128_hash_t h = XXH3_128bits(p, n);
    return {h.high64, h.low64};
}

inline uint32_t weak_checksum(const uint8_t* p, size_t n) {
    uint32_t a = 0, b = 0;
    for (size_t i = 0; i < n; ++i) {
        a += p[i];
        b += a;  // b is the sum of prefix sums: sum((n - i) * x_i)
    }
    return (a & MASK) | ((b & MASK) << 16);
}

struct Entry {
    uint32_t weak;
    Strong strong;
};

inline std::vector<Entry> signature(const uint8_t* p, size_t n, size_t block) {
    std::vector<Entry> out;
    out.reserve(n / block + 1);
    for (size_t i = 0; i < n; i += block) {
        size_t len = n - i < block ? n - i : block;
        out.push_back({weak_checksum(p + i, len), strong_hash(p + i, len)});
    }
    return out;
}

enum OpKind : uint8_t { COPY = 0, LITERAL = 1 };

// COPY: a = first block, b = count. LITERAL: a = offset into new, b = length.
struct Op {
    OpKind kind;
    uint64_t a, b;
};

class OpList {
public:
    explicit OpList(bool merge_literals) : merge_literals_(merge_literals) {}

    void copy(uint64_t block) {
        if (!ops_.empty() && ops_.back().kind == COPY && ops_.back().a + ops_.back().b == block) {
            ops_.back().b++;
        } else {
            ops_.push_back({COPY, block, 1});
        }
    }

    void literal(uint64_t start, uint64_t end) {
        if (end <= start) return;
        if (merge_literals_ && !ops_.empty() && ops_.back().kind == LITERAL &&
            ops_.back().a + ops_.back().b == start) {
            ops_.back().b += end - start;
        } else {
            ops_.push_back({LITERAL, start, end - start});
        }
    }

    std::vector<Op> take() { return std::move(ops_); }

private:
    bool merge_literals_;
    std::vector<Op> ops_;
};

inline std::vector<Op> delta(const uint8_t* buf, size_t n, const std::vector<Entry>& entries,
                             uint64_t file_size, size_t block, bool fixed_block) {
    if (fixed_block) {
        OpList ops(false);
        for (size_t i = 0; i < n; i += block) {
            size_t len = n - i < block ? n - i : block;
            size_t j = i / block;
            if (j < entries.size() && entries[j].weak == weak_checksum(buf + i, len) &&
                entries[j].strong == strong_hash(buf + i, len)) {
                ops.copy(j);
            } else {
                ops.literal(i, i + len);
            }
        }
        return ops.take();
    }

    OpList ops(true);
    // A signature with no entries (or a lying file_size) has no tail to match.
    const size_t tail = entries.empty() ? 0 : file_size % block;
    const size_t full = entries.size() - (tail ? 1 : 0);

    // weak -> (strong, first block index); first occurrence of each strong wins.
    std::unordered_map<uint32_t, std::vector<std::pair<Strong, size_t>>> index;
    index.reserve(full * 2);
    for (size_t i = 0; i < full; ++i) {
        auto& bucket = index[entries[i].weak];
        bool seen = false;
        for (auto& [s, _] : bucket) seen |= s == entries[i].strong;
        if (!seen) bucket.push_back({entries[i].strong, i});
    }

    size_t pos = 0, lit = 0;
    int64_t expected = -1;  // the block after the last COPY; preferred so runs merge
    if (full > 0 && n >= block) {
        uint32_t s = weak_checksum(buf, block);
        uint32_t a = s & MASK, b = s >> 16;
        while (pos + block <= n) {
            uint32_t w = a | (b << 16);
            auto it = index.find(w);
            if (it != index.end()) {
                Strong strong = strong_hash(buf + pos, block);
                int64_t j = -1;
                if (expected >= 0 && static_cast<size_t>(expected) < full &&
                    entries[expected].weak == w && entries[expected].strong == strong) {
                    j = expected;
                } else {
                    for (auto& [s2, idx] : it->second) {
                        if (s2 == strong) {
                            j = static_cast<int64_t>(idx);
                            break;
                        }
                    }
                }
                if (j >= 0) {
                    ops.literal(lit, pos);
                    ops.copy(static_cast<uint64_t>(j));
                    expected = j + 1;
                    pos += block;
                    lit = pos;
                    if (pos + block <= n) {
                        s = weak_checksum(buf + pos, block);
                        a = s & MASK;
                        b = s >> 16;
                    }
                    continue;
                }
            }
            if (pos + block < n) {
                uint32_t out = buf[pos];
                a = (a - out + buf[pos + block]) & MASK;
                b = (b - static_cast<uint32_t>(block) * out + a) & MASK;
            }
            ++pos;
        }
    }

    // The old file's short last block can only match the end of the new file.
    if (tail && n >= tail && n - tail >= lit) {
        const uint8_t* end = buf + n - tail;
        const Entry& last = entries.back();
        if (last.weak == weak_checksum(end, tail) && last.strong == strong_hash(end, tail)) {
            ops.literal(lit, n - tail);
            ops.copy(entries.size() - 1);
            lit = n;
        }
    }
    ops.literal(lit, n);
    return ops.take();
}

}  // namespace deltasync
