// pybind11 bindings. Buffers are read while holding the GIL, then the hot loops
// run with it released so several deltas can use several cores.
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstring>
#include <stdexcept>

#include "core.hpp"

namespace py = pybind11;
using namespace deltasync;

namespace {

struct View {
    const uint8_t* data;
    size_t size;
};

View view(const py::buffer_info& info) {
    if (info.ndim > 1) throw std::invalid_argument("expected a 1-D byte buffer");
    return {static_cast<const uint8_t*>(info.ptr), static_cast<size_t>(info.size * info.itemsize)};
}

py::bytes canonical(const Strong& s) {
    XXH128_hash_t h{s.lo, s.hi};
    XXH128_canonical_t c;
    XXH128_canonicalFromHash(&c, h);
    return py::bytes(reinterpret_cast<const char*>(c.digest), sizeof(c.digest));
}

Strong from_canonical(const std::string& raw) {
    if (raw.size() != 16) throw std::invalid_argument("strong hash must be 16 bytes");
    XXH128_canonical_t c;
    std::memcpy(c.digest, raw.data(), 16);
    XXH128_hash_t h = XXH128_hashFromCanonical(&c);
    return {h.high64, h.low64};
}

uint32_t py_weak(py::buffer data) {
    py::buffer_info info = data.request();
    View v = view(info);
    py::gil_scoped_release release;
    return weak_checksum(v.data, v.size);
}

py::list py_signature(py::buffer data, size_t block) {
    if (block == 0) throw std::invalid_argument("block size must be positive");
    py::buffer_info info = data.request();
    View v = view(info);
    std::vector<Entry> entries;
    {
        py::gil_scoped_release release;
        entries = signature(v.data, v.size, block);
    }
    py::list out(entries.size());
    for (size_t i = 0; i < entries.size(); ++i) {
        out[i] = py::make_tuple(entries[i].weak, canonical(entries[i].strong));
    }
    return out;
}

py::list py_delta(py::buffer data, py::list py_entries, uint64_t file_size, size_t block, bool fixed) {
    if (block == 0) throw std::invalid_argument("block size must be positive");
    std::vector<Entry> entries;
    entries.reserve(py_entries.size());
    for (auto item : py_entries) {
        auto t = item.cast<py::tuple>();
        entries.push_back({t[0].cast<uint32_t>(), from_canonical(t[1].cast<std::string>())});
    }
    py::buffer_info info = data.request();
    View v = view(info);
    std::vector<Op> ops;
    {
        py::gil_scoped_release release;
        ops = delta(v.data, v.size, entries, file_size, block, fixed);
    }
    py::list out(ops.size());
    for (size_t i = 0; i < ops.size(); ++i) {
        out[i] = py::make_tuple(static_cast<int>(ops[i].kind), ops[i].a, ops[i].b);
    }
    return out;
}

}  // namespace

PYBIND11_MODULE(_native, m) {
    m.doc() = "DeltaSync C++ core";
    m.def("weak_checksum", &py_weak, py::arg("window"));
    m.def("signature_entries", &py_signature, py::arg("data"), py::arg("block_size"));
    m.def("delta_ops", &py_delta, py::arg("new"), py::arg("entries"), py::arg("file_size"),
          py::arg("block_size"), py::arg("fixed_block") = false);
}
