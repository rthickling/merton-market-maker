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

This builds the default GCC 16.2 / nanobind / CPython 3.12 environment, compiles `merton_online_calibrator.so`, imports it, and runs `cpp/tests/`. All compiler, Python, QuantLib, binding, and test dependencies are installed inside the pinned Docker image.

To demonstrate the calibrator on live public market data (no ProfitView):

```bash
just demo
```

`just demo` builds the selected combination, then runs `scripts/run_binance_demo.py` against Binance USD-M futures `BTCUSDT` by default. Override with `MERTON_SYMBOL`, `MERTON_MARKET=spot|futures`, and optional `MERTON_MARKET_MAKER_DATA_PATH` (CSV/Parquet `time` + `price`) to warm the rolling window before the websocket starts.

To test every supported compiler/binding combination (reuses Docker image layers if present):

```bash
just test-matrix
```

For a cache-free image rebuild matching CI, clean-build each compiler first:

```bash
COMPILER=gcc just docker-build-clean
COMPILER=clang just docker-build-clean
just test-matrix
```

## Selecting a compiler and binding

Supported compilers:

- `COMPILER=gcc`: released GCC 16.2 with libstdc++ (default)
- `COMPILER=clang`: pinned Bloomberg Clang P2996 with libc++

Supported bindings:

- `MERTON_PYTHON_BINDING=nanobind` (default)
- `MERTON_PYTHON_BINDING=pybind11`

Supported CPython versions (`PYTHON_VERSION`; aliases `3.9`, `3.12`, `3.13` work):

- `3.12.11` (default local demo and CI)
- `3.13.7` (recent)
- `3.9.25` (ProfitView module ABI)

Examples:

```bash
just test
just demo
PYTHON_VERSION=3.13 just test
PYTHON_VERSION=3.9.25 COMPILER=gcc just test
COMPILER=clang just test
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
just demo                   # build then stream Binance quotes into the calibrator
just test                   # build and test the selected combination (may reuse image cache)
just test-compiler-matrix   # test both bindings with the selected compiler (may reuse image cache)
just test-matrix            # test all four compiler/binding combinations (may reuse image cache)
```

Build and test commands do not copy modules to deployment locations.

## Build outputs

Each compiler/binding/Python combination has an independent directory:

```text
cpp/build/gcc-16-nanobind-py3.12/merton_online_calibrator.so
cpp/build/gcc-16-pybind11-py3.12/merton_online_calibrator.so
cpp/build/clang-p2996-nanobind-py3.12/merton_online_calibrator.so
cpp/build/gcc-16-nanobind-py3.9/merton_online_calibrator.so
```

Both binding backends deliberately emit the same plain module filename. Tests set `PYTHONPATH` to the selected directory.

## Explicit deployment copy

Set `MODULE_DEST_DIR`, build the desired combination, and copy it explicitly:

```bash
PYTHON_VERSION=3.9.25 COMPILER=gcc MERTON_PYTHON_BINDING=nanobind just test
MODULE_DEST_DIR=/path/to/runtime PYTHON_VERSION=3.9.25 COMPILER=gcc MERTON_PYTHON_BINDING=nanobind just copy-module
```

ProfitView needs the 3.9 ABI. Local demo uses 3.12 by default and does not copy the module.

No build or test recipe performs deployment copying.

## Pinned environment

The Dockerfiles pin:

- Ubuntu 22.04 `linux/amd64` base image by digest and Ubuntu packages by snapshot
- CPython 3.12.11 (default), 3.13.7, or 3.9.25 source by SHA-256
- Bloomberg Clang P2996 by tested commit, or GCC 16.2 source by official SHA-512
- QuantLib 1.33 and just 1.58.0 by SHA-256
- all Python build/test packages and transitive dependencies by wheel hash

`just versions` reports these versions. Clang and QuantLib/libc++ artifacts stay isolated from GCC and QuantLib/libstdc++ artifacts.

## CI

`.github/workflows/cpp-toolchain-matrix.yml` runs `just docker-build-clean` for each compiler on the default Python 3.12 (so images build with `--pull --no-cache`), then the reflection probe and both binding backends. A separate job clean-builds GCC/nanobind on CPython 3.13. Local `just test-matrix` alone does not disable Docker caching.
