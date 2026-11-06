from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup

setup(
    ext_modules=[
        Pybind11Extension(
            "deltasync._native",
            ["deltasync/native_src/bindings.cpp"],
            include_dirs=["deltasync/native_src"],
            cxx_std=17,
            extra_compile_args=["-O3"],
        )
    ],
    cmdclass={"build_ext": build_ext},
)
