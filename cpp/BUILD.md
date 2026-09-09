# Building the C++ Extension

The supported build path is Docker through `just`. A host compiler, Python environment, QuantLib installation, pybind11, and nanobind are not required.

## Fresh-clone build and test

Prerequisites:

- Docker with `linux/amd64` container support
- [`just`](https://github.com/casey/just) 1.58 or newer

From a fresh clone:

```bash
git clone https://github.com/rthickling/merton-market-maker.git
cd merton-market-maker/cpp
just test
```

This builds the default Bloomberg Clang P2996/nanobind environment, compiles `merton_online_calibrator.so`, imports it, and runs `cpp/tests/`. All compiler, Python, QuantLib, binding, and test dependencies are installed inside the pinned Docker image.

To clean-build and test every supported compiler/binding combination:

```bash
just test-matrix
```

## Selecting a compiler and binding

Supported compilers:

- `COMPILER=clang`: pinned Bloomberg Clang P2996 with libc++ (default)
- `COMPILER=gcc`: released GCC 16.2 with libstdc++

Supported bindings:

- `MERTON_PYTHON_BINDING=nanobind` (default)
- `MERTON_PYTHON_BINDING=pybind11`

Examples:

```bash
COMPILER=gcc just test
COMPILER=gcc MERTON_PYTHON_BINDING=pybind11 just test
COMPILER=clang MERTON_PYTHON_BINDING=pybind11 just test
```

Command-line environment values override saved values in `.merton-build.env`. Run `just setup-defaults` for interactive local defaults or copy `.merton-build.env.example`.

Use `DOCKER_NETWORK=bridge` in CI or environments without Docker host networking.

## Commands

```bash
just show-defaults          # show compiler, binding, image and output selection
just versions               # validate and print pinned versions from the image
just probe                  # compile and run the reflection feature probe
just docker-build-clean     # rebuild the selected image with --pull --no-cache
just test                   # build and test the selected combination
just test-compiler-matrix   # test both bindings with the selected compiler
just test-matrix            # test all four compiler/binding combinations
```

Build and test commands do not copy modules to deployment locations.

## Build outputs

Each compiler/binding combination has an independent directory:

```text
cpp/build/clang-p2996-nanobind/merton_online_calibrator.so
cpp/build/clang-p2996-pybind11/merton_online_calibrator.so
cpp/build/gcc-16-nanobind/merton_online_calibrator.so
cpp/build/gcc-16-pybind11/merton_online_calibrator.so
```

Both binding backends deliberately emit the same plain module filename. Tests set `PYTHONPATH` to the selected directory.

## Explicit deployment copy

Set `MODULE_DEST_DIR`, build the desired combination, and copy it explicitly:

```bash
COMPILER=gcc MERTON_PYTHON_BINDING=nanobind just test
MODULE_DEST_DIR=/path/to/runtime COMPILER=gcc MERTON_PYTHON_BINDING=nanobind just copy-module
```

No build or test recipe performs deployment copying.

## Pinned environment

The Dockerfiles pin:

- Ubuntu 22.04 `linux/amd64` base image by digest and Ubuntu packages by snapshot
- CPython 3.9.25 source by SHA-256
- Bloomberg Clang P2996 by tested commit, or GCC 16.2 source by official SHA-512
- QuantLib 1.33 and just 1.58.0 by SHA-256
- all Python build/test packages and transitive dependencies by wheel hash

`just versions` reports these versions. Clang and QuantLib/libc++ artifacts stay isolated from GCC and QuantLib/libstdc++ artifacts.

## CI

`.github/workflows/cpp-toolchain-matrix.yml` clean-builds each compiler image without Docker cache, runs the reflection probe, then builds, imports, and tests both binding backends. This covers all four combinations.
