# Building the C++ Extension

The supported build path is Docker through `just`. A host compiler, Python environment, QuantLib installation, pybind11, and nanobind are not required.

## Fresh-clone build and test

Prerequisites:

- Docker with `linux/amd64` container support, runnable without `sudo`
- [`just`](https://github.com/casey/just) 1.58 or newer

From a fresh clone:

```bash
git clone https://github.com/rthickling/merton-market-maker.git
cd merton-market-maker/cpp
just test
```

This builds the default GCC 16.2 / nanobind / CPython 3.14 environment, compiles `merton_online_calibrator.so`, imports it, and runs `cpp/tests/`. All compiler, Python, QuantLib, binding, and test dependencies are installed inside the pinned Docker image. Nothing needs editing first. The first run builds that image from source, which takes about 30 minutes on a 4-core machine; later runs reuse it.

To demonstrate the calibrator on live public market data (no ProfitView):

```bash
just demo
```

`just demo` builds the selected combination, then runs `scripts/run_binance_demo.py` against Binance USD-M futures. Default `MERTON_SYMBOL=BTCUSDT`. Selectable perps: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `XAUUSDT` (`MERTON_SYMBOL=XAUUSDT just demo`). `lastFundingRate` is annualized with that contract’s live `fundingIntervalHours` (8, 4, or 1); fair-value `T` is one interval. Also `MERTON_MARKET=spot|futures` and optional `MERTON_MARKET_MAKER_DATA_PATH` (CSV/Parquet `time` + `price`) to warm the rolling window before the websocket starts.

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

Supported CPython versions (`PYTHON_VERSION`; aliases `3.14` and `3.13` work):

- `3.14.8` (default local demo and CI)
- `3.13.16` (previous stable release)

Examples:

```bash
just test
just demo
PYTHON_VERSION=3.13 just test
COMPILER=clang just test
COMPILER=clang MERTON_PYTHON_BINDING=pybind11 just test
```

Command-line environment values override saved values in `.merton-build.env`. Run `just setup-defaults` for interactive local defaults or copy `.merton-build.env.example`.

Use `DOCKER_NETWORK=bridge` in CI or environments without Docker host networking.

## First-time setup and day-to-day commands

First-time setup needs a network connection. It builds the selected image (compiler, CPython, QuantLib, binding libraries, Numba) and is the slow step:

```bash
just docker-build           # or `just test`, which builds the image if it is missing
just versions               # print the pinned versions from the image
```

After the image exists, day-to-day commands keep it. `just rebuild`, `just api`, `just replay` and `just bench` run the container with the network disabled.

```bash
just rebuild                # incremental compile of the selected combination
just api                    # print the reflected Python interface
just replay                 # replay synthetic ticks, or recorded ticks from MERTON_MARKET_MAKER_DATA_PATH
just bench                  # agreement checks, then timings (nanobind only)
just test                   # clean build and pytest (may reuse the image)
just demo                   # live Binance quotes; needs the network
```

## Editors

The committed `.vscode/settings.json` (Cursor reads it too) holds only settings that work on any machine. It points CMake Tools at `cpp/`, stops it configuring on the host, and turns off the Microsoft C/C++ error squiggles. Put machine-specific settings, such as an interpreter or a `clangd` path, in your user settings rather than in the repository.

Host `clangd` and IntelliSense do not parse C++26 reflection, so `reflection_bind_*.hpp` and `python_module_entry_*.cpp` show errors in the editor even though they build. Only `clangd` from the clang-p2996 install (`/opt/clang-p2996/bin/clangd` in the Clang image) understands them; `cpp/.clangd` holds the flags it needs.

## Commands

```bash
just show-defaults          # show compiler, binding, image and output selection
just shell                  # shell in the image; python3 there imports the built module
just versions               # validate and print pinned versions from the image
just probe                  # compile and run the reflection feature probe
just docker-build-clean     # rebuild the selected image with --pull --no-cache
just demo                   # build then stream Binance quotes into the calibrator
just build                  # clean build of the selected combination (deletes its build directory first)
just rebuild                # incremental build: keeps the build directory, recompiles only what changed
just api                    # incremental build, then print the module's Python interface
just replay                 # incremental build, then replay synthetic (or recorded) ticks offline
just bench                  # time the Python reference, its Numba variant, and reflected and hand-written nanobind bindings
just test                   # build and test the selected combination (may reuse image cache)
just test-compiler-matrix   # test both bindings with the selected compiler (may reuse image cache)
just test-matrix            # test all four compiler/binding combinations (may reuse image cache)
just test-manual-bindings   # also build the hand-written nanobind module and check it matches
```

Build and test commands do not copy modules to deployment locations.

## Build outputs

Each compiler/binding/Python combination has an independent directory:

```text
cpp/build/gcc-16-nanobind-py3.14/merton_online_calibrator.so
cpp/build/gcc-16-pybind11-py3.14/merton_online_calibrator.so
cpp/build/clang-p2996-nanobind-py3.14/merton_online_calibrator.so
cpp/build/gcc-16-nanobind-py3.13/merton_online_calibrator.so
```

Both binding backends deliberately emit the same plain module filename. Tests set `PYTHONPATH` to the selected directory.

`just test-manual-bindings` builds into `<combination>-with-manual` (for example `cpp/build/gcc-16-nanobind-py3.14-with-manual/`), which also contains `merton_manual_bindings.so`. That module is written by hand with nanobind but makes the same binding choices as the reflected one, so it can serve as a like-for-like baseline when measuring binding cost; `tests/test_manual_bindings.py` checks that the two expose the same interface and return the same results. It is built only when the CMake option `MERTON_BUILD_MANUAL_BINDINGS=ON` is set, which requires nanobind.

## Explicit deployment copy

Set `MODULE_DEST_DIR`, build the desired combination, and copy it explicitly:

```bash
PYTHON_VERSION=3.14.8 COMPILER=gcc MERTON_PYTHON_BINDING=nanobind just test
MODULE_DEST_DIR=/path/to/runtime PYTHON_VERSION=3.14.8 COMPILER=gcc MERTON_PYTHON_BINDING=nanobind just copy-module
```

Local demo and tests use 3.14 by default and do not copy the module.

No build or test recipe performs deployment copying.

## Pinned environment

The Dockerfiles pin:

- Ubuntu 22.04 `linux/amd64` base image by digest and Ubuntu packages by snapshot
- CPython 3.14.8 (default) or 3.13.16 source by SHA-256
- Bloomberg Clang P2996 by tested commit, or GCC 16.2 source by official SHA-512
- QuantLib 1.33 and just 1.58.0 by SHA-256
- all Python build/test packages and transitive dependencies by wheel hash

`just versions` reports these versions. Clang and QuantLib/libc++ artifacts stay isolated from GCC and QuantLib/libstdc++ artifacts.

## CI

`.github/workflows/cpp-toolchain-matrix.yml` runs `just docker-build-clean` for each compiler on the default Python 3.14 (so images build with `--pull --no-cache`), then the reflection probe and both binding backends. A separate job clean-builds GCC/nanobind on CPython 3.13. Local `just test-matrix` alone does not disable Docker caching.
