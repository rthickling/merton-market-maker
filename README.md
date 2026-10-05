![](/assets/images/merton-bot.png)

# Merton Market Maker: C++26 Reflection + Python Bindings
**Solving the Speed-to-Market vs Speed-to-Book Tension in Algorithmic Trading.**

---

⚠️ **The "No Alpha" Disclaimer**

This project is an engineering demonstration. The included Merton Jump Diffusion (MJD) algorithm is a standard, textbook model used to provide a real-world context.
* This is not a money-printing bot.
* It exposes no novel alpha.
* The value is in the infrastructure, not the strategy.

---
## Quick start

You need Docker (runnable without `sudo`) on an x86-64 machine, and [`just`](https://github.com/casey/just) 1.58 or newer. Compilers, Python and libraries live in a pinned Docker image, so nothing else goes on the host and nothing needs editing.

```bash
git clone https://github.com/rthickling/merton-market-maker.git
cd merton-market-maker/cpp
just test   # first run builds the image (about 30 minutes on 4 cores), then builds and tests
just demo   # stream live Binance quotes into the C++ calibrator (paper quotes only)
```

After the first build, `just api`, `just replay` and `just bench` run offline ([details](#offline-once-the-image-exists)). [BUILD](cpp/BUILD.md) covers the other compilers, bindings and Python versions.

## A Practical Hybrid Workflow
The algorithmic trading world is often split into two camps:

1. **Speed-to-Market (Python)**: Rapid iteration, research, and backtesting.
2. **Speed-to-Book (C++)**: Low-latency execution and high-performance pricing math.

While it has always been possible to bridge these worlds using `pybind11`, `nanobind`, or CFFI, the manual boilerplate tax is usually too cumbersome for day-to-day use. Quants writing pricing models should not have to manually update a binding layer just to see the result in their Python strategy. A quant-dev with both C++ and Python should not have to be brought in for every binding change.

C++26 Reflection (P2996) changes this. It is *compiler-supported* compile-time programming. This project uses it to bind public methods and data members of types that are already registered, so a new public method on the calibrator does not need a matching binding edit. Classes and constructors are still registered by hand. The result is a practical hybrid Python/C++ workflow with a working end-to-end example.

## Through the Mirror into a New C++ World
Instead of writing a manual binding stanza for every C++ function, this project uses a generic reflection loop. When you add or change a public method in your C++ math engine, the Python bindings update automatically at compile time.

### Traverse the Parse Tree - and Bind
```c++
template <typename T>
void bind_reflected_member_functions(/* backend class wrapper */& cl) {
    static constexpr auto members = std::define_static_array(
        std::meta::members_of(^^T, std::meta::access_context::current()));

    template for (constexpr auto m : members) {
        if constexpr (std::meta::is_public(m) && std::meta::is_function(m) &&
                      !std::meta::is_constructor(m)) {
            cl.def(std::meta::identifier_of(m).data(), &[:m:]);
        }
    }
}

NB_MODULE(merton_online_calibrator, m) { // Using `nanobind`
    nb::class_<merton::OnlineMertonCalibrator> cl(m, "OnlineMertonCalibrator");
    cl.def(nb::init<merton::MertonParams, merton::CalibratorConfig>(),
           "initial"_a, "config"_a = merton::CalibratorConfig{});
    bind_reflected_member_functions(cl);
}
```

A public method added to an already-registered C++ type is then available from Python without a per-method binding edit. Parameter names come from reflection. C++ default arguments are not turned into Python defaults, and the extension does not release the GIL.

The reflection operator `^^T` converts a type into a reflected meta-object, and the splicer `[:m:]` converts reflected members back into run-time expressions.

### Use it in Python

To try this after `just test`, run `just shell`, then `python3`.

```python
from merton_online_calibrator import OnlineMertonCalibrator, MertonParams

params = MertonParams()
params.sigma, params.lambda_, params.mu_j, params.delta_j = 0.65, 3.5, -0.2, 0.6
calibrator = OnlineMertonCalibrator(params)

for i, price in enumerate([68000.0, 68010.0, 67995.0, 68020.0]):
    calibrator.update_tick(price, 1_700_000_000_000_000 + i * 1_000_000)
calibrator.maybe_update_params()  # refits once enough returns have arrived

p = calibrator.params()
print(f"sigma={p.sigma}, lambda={p.lambda_}, samples={calibrator.sample_count()}")
print(f"fair value, 8h: {calibrator.fair_value(68020.0, 0.0, 8 / (365.25 * 24), 0.0):.2f}")
```

---
## Why Merton Jump Diffusion?
I chose the MJD model because its math is heavy. It requires infinite-series-style mixture summations that would be much slower in pure Python. It illustrates the "Speed-to-Book" need for compiled math, while the high-level trading logic still benefits from Python's "Speed-to-Market."

## Technical Stack
C++26 reflection is built with either released GCC 16.2 and libstdc++ or the pinned Bloomberg Clang P2996 fork and libc++. Both toolchains support the nanobind and pybind11 backends and run entirely in pinned `linux/amd64` Docker environments.

### Live demo

```bash
cd cpp
just demo
```

This builds the default GCC/nanobind/CPython 3.14 module and streams Binance USD-M bookTicker quotes into the C++ calibrator (paper quotes only). Default symbol is `BTCUSDT`. Selectable perps: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `XAUUSDT`. Funding is annualized from each contract’s live interval (8h/4h/1h). Optional historical warmup: set `MERTON_MARKET_MAKER_DATA_PATH` to a CSV/Parquet directory. ProfitView is not required.

```bash
just test                 # build and pytest the selected toolchain
just test-matrix          # GCC and Clang, both bindings (reuses Docker cache)
MERTON_SYMBOL=ETHUSDT just demo  # another high-volume perp
PYTHON_VERSION=3.13 just demo   # previous stable CPython
```

See [BUILD](cpp/BUILD.md) for compiler, Python, output paths, clean image rebuilds, and optional module copying.

### Offline, once the image exists

These do not need a market connection. `just api`, `just replay` and `just bench` run the container with the network disabled when the image is already present.

```bash
cd cpp
just test      # build and test the selected combination
just api       # print the reflected Python interface
just replay    # replay a seeded synthetic path (or recorded ticks)
just bench     # validate, then time Python, Numba, and C++ behind reflected and hand-written bindings and through cppyy
```

Supported combinations: GCC 16.2 (libstdc++) and the pinned clang-p2996 fork (libc++); nanobind and pybind11; CPython 3.14.8 (default) and 3.13.16.

Limits of the reflection layer, as built:

- classes and constructors are registered explicitly;
- Python defaults are not generated (omitting `r` from `fair_value` raises `TypeError`);
- calls hold the GIL; the calibrator is mutable and not synchronised across threads.

`tests/test_reference_agreement.py` compares the C++ calibrator with a line-by-line Python translation on the same seeded ticks, and `tests/test_numba_agreement.py` compares that translation with `scripts/merton_numba.py`, the same class with its likelihood compiled by Numba. `tests/test_cppyy_agreement.py` checks that `scripts/merton_cppyy.py`, which calls the same compiled C++ through cppyy, returns exactly what the nanobind module returns. Matching results show the implementations are consistent with each other. They do not validate the financial model.

`just bench` times that same algorithm in readable Python, in that Python with its likelihood compiled by Numba, and in C++ behind reflected bindings, equivalent hand-written nanobind bindings, and cppyy, which builds its bindings from the header at run time. The three C++ variants run the same core, compiled from the same source with the same flags (the benchmark checks that the machine code is identical), so they differ only in how Python calls it. The benchmark needs the GCC image, the only one with cppyy. A speedup is for this algorithm on the machine that ran the benchmark. There is no comparison with NumPy vectorisation, Cython or Pythran. The report also says whether cheap calls through the reflected module or through cppyy were slower than through the hand-written module by more than the run-to-run spread. On the machine used while writing these notes (AMD Ryzen 7 PRO 7840U, CPython 3.14.8 built without profile-guided or link-time optimisation, GCC 16.2, nanobind 2.12.0, Numba 0.68.0, cppyy 3.5.0), one calibration was about 35× faster in C++ than in the Python reference, through any of the three bindings, and about 37× faster with Numba, which compiles for the host CPU where the C++ build targets generic x86-64. No cheap call was slower through the reflected bindings by more than that spread; through cppyy, each cheap call took about 10–20 ns longer than through the hand-written module. Rerun `just bench` before quoting a number.

## References & Further Reading
* Blog Post: [Stop Choosing: Get C++ Performance in Python Algos with C++26](https://profitview.net/blog/cpp26-reflection-python-algo-trading)
* [P2996 - Reflection for C++26](https://wg21.link/p2996)
* [Callum Piper](https://www.linkedin.com/in/callum-piper-3691373/)'s [talk](https://youtu.be/SJ0NFLpR9vE) from ACCU 2025.
* [nanobind](https://github.com/wjakob/nanobind) and [pybind11](https://github.com/pybind/pybind11), the two binding backends.
* Related work on reflection bindings: [welder](https://github.com/skarndev/welder), [nanobind26](https://github.com/matthewkolbe/nanobind26), [mirror_bridge](https://github.com/FranciscoThiesen/mirror_bridge), and the discussion in [nanobind #1314](https://github.com/wjakob/nanobind/discussions/1314).
* [ProfitView](https://profitview.net/), an earlier deployment path for this calibrator. The current demo uses public Binance quotes and places no orders.
* See [THEORY](cpp/THEORY.md)

## Author

Richard Hickling. Questions and collaboration: [LinkedIn](https://www.linkedin.com/in/rthickling/), richard.t.hickling@gmail.com.
