![](/assets/images/merton-bot.png)

# Merton Market Maker: C++26 Reflection + Python Bindings
**Solving the Speed-to-Market vs Speed-to-Book Tension in Algorithmic Trading.**

An online jump-diffusion calibration example with an illustrative quoting demo: a C++ calibrator refits a Merton jump-diffusion model to a rolling window of prices, and C++26 reflection generates its Python bindings.

---

⚠️ **The "No Alpha" Disclaimer**

This project is an engineering demonstration. The included Merton Jump Diffusion (MJD) algorithm is a standard, textbook model used to provide a real-world context.
* This is not a money-printing bot.
* It exposes no novel alpha.
* The demo's paper quotes are centred on the market midpoint. The fitted parameters set how wide they are, through an illustrative risk rule, not an optimal or profitable quoting strategy.
* The value is in the infrastructure, not the strategy.

---
## Quick start

You need Docker (runnable without `sudo`) on an x86-64 machine, and [`just`](https://github.com/casey/just) 1.58 or newer. Compilers, Python and libraries live in a pinned Docker image, so nothing else goes on the host and nothing needs editing.

```bash
git clone https://github.com/rthickling/merton-market-maker.git
cd merton-market-maker/cpp
just test   # first run builds the image (about 30 minutes on 4 cores), then builds and tests
just demo   # stream live Binance quotes into the C++ calibrator (paper quotes, no orders)
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

A public method added to an already-registered C++ type is then available from Python without a per-method binding edit. For example, renaming `fair_value` to `no_jump_conditional_mean`, with the old name kept as an inline alias, needed no change to the reflected bindings; only the hand-written comparison module needed new lines. Parameter names come from reflection. C++ default arguments are not turned into Python defaults, and the extension does not release the GIL.

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
# A model diagnostic, E[S_T | no jumps], not a fair value; fair_value is its former name.
t_years = 8 / (365.25 * 24)
print(f"no-jump conditional mean, 8h: {calibrator.no_jump_conditional_mean(68020.0, 0.0, t_years, 0.0):.2f}")
```

---
## Why Merton Jump Diffusion?
I chose the MJD model because its math is heavy. It requires infinite-series-style mixture summations that would be much slower in pure Python. It illustrates the "Speed-to-Book" need for compiled math, while the high-level trading logic still benefits from Python's "Speed-to-Market."

The substantial work is the calibration: each refit evaluates that mixture likelihood over the whole rolling window many times. The quote width the demo then derives from the fitted parameters is a square root, and the no-jump conditional mean it prints is a single exponential.

## Technical Stack
C++26 reflection is built with either released GCC 16.2 and libstdc++ or the pinned Bloomberg Clang P2996 fork and libc++. Both toolchains support the nanobind and pybind11 backends and run entirely in pinned `linux/amd64` Docker environments.

### Live demo

```bash
cd cpp
just demo
```

This builds the default GCC/nanobind/CPython 3.14 module and streams Binance USD-M bookTicker quotes into the C++ calibrator. It places no orders. The calibrator estimates continuous volatility and jump behaviour. The demo uses the resulting short-horizon return variance to adjust quote width around the current market midpoint. Each book update reaches the calibrator first and is then quoted at mid ± the largest of half the market spread, 2 bp of mid (`MERTON_MIN_HALF_SPREAD_BPS`), and mid × 1 (`MERTON_RISK_MULTIPLIER`) × √(T(σ² + λ(μ_J² + δ_J²))) with T = 60 s (`MERTON_RISK_HORIZON_SECONDS`). In that variance, λ(μ_J² + δ_J²) is the jump contribution to log-return variance. These defaults are demonstration choices. Quote lines, at most one a second (`MERTON_PRINT_SECONDS`), say which of the three set the width and whether the parameters are still the seeds or have been calibrated, and each refit prints its parameters and model width. The rule does not model inventory, adverse selection, execution probability, fees or optimal quoting ([details](cpp/THEORY.md#model-driven-quote-width)). A periodic line shows, as a labelled diagnostic the quotes do not use, the no-jump conditional mean over one funding interval: a model quantity that depends only on the parameters and the carry, not a fair value, mispricing or signal. Funding is context: the live rate, annualized from each contract’s interval (8h/4h/1h), enters only that diagnostic, not the quote width, and the horizon is the interval’s full length, not the time to the next payment. Default symbol is `BTCUSDT`. Selectable perps: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `XAUUSDT`. Optional historical warmup: set `MERTON_MARKET_MAKER_DATA_PATH` to a CSV/Parquet directory. ProfitView is not required.

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
just replay    # replay a seeded synthetic path (or recorded ticks) through the calibrator and the quote rule
just bench     # validate, then time Python, Numba, and C++ behind reflected and hand-written bindings and through cppyy
```

Supported combinations: GCC 16.2 (libstdc++) and the pinned clang-p2996 fork (libc++); nanobind and pybind11; CPython 3.14.8 (default) and 3.13.16.

Limits of the reflection layer, as built:

- classes and constructors are registered explicitly;
- Python defaults are not generated (omitting `r` from `no_jump_conditional_mean`, or from its former name `fair_value`, raises `TypeError`);
- calls hold the GIL; the calibrator is mutable and not synchronised across threads.

`tests/test_reference_agreement.py` compares the C++ calibrator with a line-by-line Python translation on the same seeded ticks, and `tests/test_numba_agreement.py` compares that translation with `scripts/merton_numba.py`, the same class with its likelihood compiled by Numba. `tests/test_cppyy_agreement.py` checks that `scripts/merton_cppyy.py`, which calls the same compiled C++ through cppyy, returns exactly what the nanobind module returns. Matching results show the implementations are consistent with each other. They do not validate the financial model.

`just bench` times that same algorithm in readable Python, in that Python with its likelihood compiled by Numba, and in C++ behind reflected bindings, equivalent hand-written nanobind bindings, and cppyy, which builds its bindings from the header at run time. The three C++ variants run the same core, compiled from the same source with the same flags (the benchmark checks that the machine code is identical), so they differ only in how Python calls it. The benchmark needs the GCC image, the only one with cppyy. A speedup is for this algorithm on the machine that ran the benchmark. There is no comparison with NumPy vectorisation, Cython or Pythran. The report also says whether cheap calls through the reflected module or through cppyy were slower than through the hand-written module by more than the run-to-run spread. On the machine used while writing these notes (AMD Ryzen 7 PRO 7840U, CPython 3.14.8 built without profile-guided or link-time optimisation, GCC 16.2, nanobind 2.12.0, Numba 0.68.0, cppyy 3.5.0), in the report stamped `e7a1ba7-dirty` (5 October 2026, before `fair_value` was renamed; the rename leaves the compiled core unchanged), one calibration was about 35× faster in C++ than in the Python reference, through any of the three bindings, and about 37× faster with Numba, which compiles for the host CPU where the C++ build targets generic x86-64. Through the reflected bindings, `fair_value` was 0.28 ns (0.5%) slower per call than through the hand-written module, more than its run-to-run spread of 0.11 ns; `update_tick` and `sample_count` differed by less than their spreads. Both modules run the same C++ through the same nanobind calls, so the report points to how the compiler inlined or laid out each shared library as a likelier cause than the binding approach, without establishing it. Through cppyy, each cheap call took about 10–20 ns longer than through the hand-written module. Rerun `just bench` before quoting a number.

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
